"""Zero-Prior Self-Supervised Rubric Discovery Engine (`auto_discover_rubric.py`).

Takes ONLY raw (State, Action, Outcome) trajectories from `data/extracted_timelines/*.json`
with ZERO human-written coaching rules or hints, computes conditional win/death probabilities
across state-action contexts, and uses an unsupervised causal induction prompt to discover
when and how the hero should act to maximize fight win rate.
"""

import json
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT_DIR))
from agent.timeline_translator import DEFAULT_MODEL, FALLBACK_MODELS, load_api_key

DIVE_HEROES = {
    "Winston",
    "Doomfist",
    "D.Va",
    "Wrecking Ball",
    "Tracer",
    "Genji",
    "Sombra",
    "Reaper",
    "Venture",
    "Pharah",
}


def extract_universal_sao_trajectories() -> Dict[str, Any]:
    """Convert all 15 extracted match timelines into universal (State, Action, Outcome) tuples."""
    extracted_dir = ROOT_DIR / "data" / "extracted_timelines"
    cohorts_manifest = json.loads(
        (ROOT_DIR / "data" / "training_cohorts_ana.json").read_text(encoding="utf-8")
    )
    code_to_cohort = {}
    for c_name, items in cohorts_manifest.items():
        for item in items:
            code_to_cohort[item["replay_code"]] = c_name

    all_fights: List[Dict[str, Any]] = []
    bucket_stats: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"count": 0, "fights_won": 0, "player_died_in_fight": 0}
    )

    for json_path in sorted(extracted_dir.glob("*.json")):
        code = json_path.stem
        if code not in code_to_cohort:
            continue
        cohort = code_to_cohort[code]
        tier_group = "GM" if cohort == "gm_win" else "Diamond"

        tl = json.loads(json_path.read_text(encoding="utf-8"))
        meta = tl.get("match_meta", {})
        enemy_threats = set(meta.get("enemy_threats", []))
        has_dive_threat = bool(enemy_threats.intersection(DIVE_HEROES))

        fights = tl.get("fights", [])
        for f_idx, f in enumerate(fights):
            outcome = str(f.get("outcome", "UNKNOWN")).upper()
            won_int = 1 if outcome == "WON" else 0
            events = f.get("events", [])
            died_in_fight = any(
                e.get("event_type") == "player_death" for e in events
            )

            # Parse fight start time if possible to measure relative timing inside fight
            f_range = f.get("time_range", "00:00 - 00:00").split("-")[0].strip()

            normalized_events = []
            for ev_idx, ev in enumerate(events):
                etype = ev.get("event_type")
                alive_ratio = ev.get("alive_ratio", "5v5")
                ally_n = ev.get("ally_alive", 5)
                enemy_n = ev.get("enemy_alive", 5)
                num_state = (
                    "numbers_advantage"
                    if ally_n > enemy_n
                    else ("numbers_disadvantage" if ally_n < enemy_n else "even_numbers")
                )
                hp = ev.get("player_hp", 250)
                hp_state = (
                    "critical_hp(<120)"
                    if (hp is not None and hp < 120)
                    else "healthy_hp(>=120)"
                )
                ult_pct = ev.get("ult_charge_pct", 0)
                seq_pos = "first_action_in_fight" if ev_idx == 0 else "mid_or_late_fight_action"

                action_desc = etype
                if etype == "biotic_nade_used":
                    action_desc = f"nade:{ev.get('nade_usage')}"
                elif etype == "sleep_dart_used":
                    action_desc = (
                        "sleep:long_range_poke"
                        if ev.get("is_poke_lottery_sleep")
                        else "sleep:close_or_mid_target"
                    )
                elif etype == "ult_used":
                    action_desc = f"ult_on:{ev.get('ult_target') or ev.get('target')}"

                if etype != "player_death":
                    key_str = (
                        f"[{tier_group}] state=({num_state}, {hp_state}, {seq_pos}, dive_threat={has_dive_threat}) "
                        f"-> action=({action_desc})"
                    )
                    bucket_stats[key_str]["count"] += 1
                    bucket_stats[key_str]["fights_won"] += won_int
                    bucket_stats[key_str]["player_died_in_fight"] += (
                        1 if died_in_fight else 0
                    )

                normalized_events.append(
                    {
                        "t": ev.get("t"),
                        "seq_pos": seq_pos,
                        "alive_ratio": alive_ratio,
                        "num_state": num_state,
                        "player_hp": hp,
                        "ult_pct": ult_pct,
                        "action": action_desc,
                        "target_context": ev.get("target"),
                        "sleep_on_cd_when_dove": ev.get("sleep_on_cd_when_dove"),
                        "dead_allies": ev.get("dead_allies", []),
                    }
                )

            if not events:
                key_str = f"[{tier_group}] state=(no_ability_logged_in_fight, dive_threat={has_dive_threat}) -> action=(none)"
                bucket_stats[key_str]["count"] += 1
                bucket_stats[key_str]["fights_won"] += won_int
                bucket_stats[key_str]["player_died_in_fight"] += (
                    1 if died_in_fight else 0
                )

            all_fights.append(
                {
                    "replay_code": code,
                    "cohort": cohort,
                    "rank": meta.get("rank"),
                    "map": meta.get("map"),
                    "ally_comp": meta.get("ally_comp_observed", []),
                    "enemy_comp": meta.get("enemy_threats", []),
                    "has_dive_threat": has_dive_threat,
                    "fight_id": f.get("fight_id"),
                    "time_range": f.get("time_range"),
                    "fight_outcome": outcome,
                    "player_died": died_in_fight,
                    "trajectory": normalized_events,
                }
            )

    # Summarize empirical conditional probabilities for buckets with count >= 2
    empirical_table = []
    for k, v in sorted(
        bucket_stats.items(), key=lambda x: (-x[1]["count"], x[0])
    ):
        if v["count"] >= 2:
            empirical_table.append(
                {
                    "state_action_pattern": k,
                    "occurrences": v["count"],
                    "fight_win_rate": round(v["fights_won"] / v["count"], 3),
                    "player_death_rate_in_fight": round(
                        v["player_died_in_fight"] / v["count"], 3
                    ),
                }
            )

    return {
        "total_fights": len(all_fights),
        "empirical_state_action_table": empirical_table,
        "raw_fight_trajectories": all_fights,
    }


SELF_DISCOVERY_SYSTEM_PROMPT = """You are an Unsupervised Game-Theory & Causal Policy Discovery Engine.
You are given raw (State, Action, Fight_Outcome) trajectories from 41 teamfights across 15 real Overwatch 2 matches, along with an empirical conditional probability table computed directly from the trajectories.

CRITICAL CONSTRAINT:
- Do NOT rely on generic Overwatch textbooks or pre-written human rules.
- Derive EVERY rule and insight strictly from the empirical patterns, sequence orders, team composition interactions, and timing differences present in these 41 trajectories.

Your task:
1. Analyze the exact sequence of actions within each fight (`first_action_in_fight` vs `mid_or_late_fight_action`, `num_state` = `5v5` vs `5v4` vs `4v5` vs `3v5`, `player_hp`, `ally_comp` vs `enemy_comp`, and `ult_pct`).
2. Discover non-obvious temporal & contextual laws that separate WON fights from LOST fights—especially patterns where:
   - The SAME ability used at a different sequence timing, numbers state (`5v4` snowball vs `5v5` neutral vs `4v5` deficit), or HP threshold produces opposite outcomes.
   - How team composition / enemy threats change which action is optimal vs fatal.
   - Action combinations / chains (e.g. Action X followed within N seconds by Action Y) that create high win rates.
3. Output a JSON object with:
   - `discovered_temporal_laws`: 5 concrete, data-backed laws discovered purely from these 41 trajectories (cite specific replay codes, fight IDs, timestamps, and empirical win rates from the data).
   - `contextual_interaction_laws`: How enemy/ally team context in the data alters what the player should do (e.g. why a certain play works in one context but fails in another).
   - `auto_generated_rubric`: A complete structured rubric (`rules` list with `rule_id`, `discovered_pattern_name`, `state_precondition`, `optimal_action_vs_fatal_action`, `empirical_evidence_from_dataset`, `coaching_trigger`) generated 100% by your own induction."""


def induce_rubric_from_trajectories(dataset: Dict[str, Any]) -> Dict[str, Any]:
    key = load_api_key()
    prompt_data = json.dumps(dataset, ensure_ascii=False)

    payload = {
        "systemInstruction": {
            "parts": [{"text": SELF_DISCOVERY_SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Below is the complete 41-fight (State, Action, Outcome) dataset and empirical state-action table. "
                            "Perform zero-prior causal and temporal policy discovery and return the JSON result:\n\n"
                            + prompt_data
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    for model_name in FALLBACK_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={key}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw_text = body["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(raw_text)
        except Exception as exc:
            print(f"[Retry] Model {model_name} failed: {exc}")

    raise RuntimeError("All models failed during self-discovery induction.")


if __name__ == "__main__":
    dataset = extract_universal_sao_trajectories()
    sao_path = ROOT_DIR / "data" / "universal_sao_dataset.json"
    sao_path.write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Extracted {dataset['total_fights']} fight trajectories and {len(dataset['empirical_state_action_table'])} recurring (State, Action) patterns."
    )
    print("Top Empirical Patterns:")
    for row in dataset["empirical_state_action_table"][:12]:
        print(" ", row)

    print("\nRunning Zero-Prior AI Rule Induction on the 41 trajectories...")
    discovered = induce_rubric_from_trajectories(dataset)
    out_path = ROOT_DIR / "rubrics" / "ana_auto_discovered.json"
    out_path.write_text(
        json.dumps(discovered, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Saved AI self-discovered rubric to {out_path}")
    print(json.dumps(discovered, indent=2, ensure_ascii=False))
