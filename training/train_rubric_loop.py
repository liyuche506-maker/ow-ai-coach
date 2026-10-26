"""Expanded Offline Coach Agent Training & 3-Cohort Rubric Calibration Pipeline.

Curates a 15-match balanced dataset from owreplays.tv across 3 distinct cohorts:
1. `diamond_loss` (5 matches): Diamond Ana matches resulting in a match loss
2. `diamond_win` (5 matches): Diamond Ana matches resulting in a match win (including coaching requests)
3. `gm_win` (5 matches): Grandmaster Ana reference matches (GM5 - GM3)

Extracts structured HUD timelines via Gemini's native YouTube ingestion, computes
teamfight-level and match-level statistical distributions across all 3 cohorts,
evaluates Coach Agent outputs via the Evaluator Judge, and writes training/TRAINING_REPORT.md.
"""

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from agent.coach_agent import evaluate_timeline_with_rubric, render_markdown_report
from agent.timeline_translator import load_api_key, translate_vod_with_gemini
from scraper.owreplays_client import (
    BASE_URL,
    HERO_ANA_ID,
    _get_json,
    fetch_replay_vod_cameras,
)

DIAMOND_TIERS = [21, 22, 23, 24, 25]
MASTER_GM_TIERS = [26, 27, 28, 29, 30, 31, 32, 33, 34, 35]


def fetch_balanced_3_cohorts(per_cohort: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """Scan owreplays.tv pages to build a balanced 3-cohort Ana dataset (Diamond Loss, Diamond Win, GM Win)."""
    maps_data = _get_json(f"{BASE_URL}/api/v2/maps")
    tiers_data = _get_json(f"{BASE_URL}/api/v2/tiers")
    map_lookup = {m["ID"]: m["map"] for m in maps_data}
    tier_lookup = {t["ID"]: t["title"] for t in tiers_data}

    cohorts: Dict[str, List[Dict[str, Any]]] = {
        "diamond_loss": [],
        "diamond_win": [],
        "gm_win": [],
    }

    # 1. Scan Diamond pages for 5 pure-Ana Losses and 5 pure-Ana Wins
    dia_tiers_str = ",".join(str(t) for t in DIAMOND_TIERS)
    for page in range(1, 20):
        if (
            len(cohorts["diamond_loss"]) >= per_cohort
            and len(cohorts["diamond_win"]) >= per_cohort
        ):
            break
        url = f"{BASE_URL}/api/v2/replays?heroes={HERO_ANA_ID}&tiers={dia_tiers_str}&page={page}"
        data = _get_json(url)
        for r in data.get("replays", []):
            if not r.get("HasVod") or r.get("Heroes") != [HERO_ANA_ID]:
                continue
            outcome = r.get("Outcome")
            bucket = (
                "diamond_loss"
                if outcome == "loss"
                else ("diamond_win" if outcome == "win" else None)
            )
            if not bucket or len(cohorts[bucket]) >= per_cohort:
                continue
            code = r["Code"]
            if any(x["replay_code"] == code for x in cohorts[bucket]):
                continue
            time.sleep(0.25)
            vods = fetch_replay_vod_cameras(code)
            if not vods:
                continue
            cohorts[bucket].append(
                {
                    "replay_code": code,
                    "title": r.get("Title"),
                    "player": r.get("Player"),
                    "rank": tier_lookup.get(r.get("Tier"), str(r.get("Tier"))),
                    "map": map_lookup.get(r.get("Map"), str(r.get("Map"))),
                    "outcome": outcome,
                    "duration": r.get("Duration"),
                    "description": r.get("Description") or "",
                    "youtube_url": vods[0]["youtube_url"],
                }
            )

    # 2. Scan Master/GM pages for 5 pure-Ana Wins
    gm_tiers_str = ",".join(str(t) for t in MASTER_GM_TIERS)
    for page in range(1, 10):
        if len(cohorts["gm_win"]) >= per_cohort:
            break
        url = f"{BASE_URL}/api/v2/replays?heroes={HERO_ANA_ID}&tiers={gm_tiers_str}&page={page}"
        data = _get_json(url)
        for r in data.get("replays", []):
            if (
                not r.get("HasVod")
                or r.get("Heroes") != [HERO_ANA_ID]
                or r.get("Outcome") != "win"
            ):
                continue
            code = r["Code"]
            if any(x["replay_code"] == code for x in cohorts["gm_win"]):
                continue
            time.sleep(0.25)
            vods = fetch_replay_vod_cameras(code)
            if not vods:
                continue
            cohorts["gm_win"].append(
                {
                    "replay_code": code,
                    "title": r.get("Title"),
                    "player": r.get("Player"),
                    "rank": tier_lookup.get(r.get("Tier"), str(r.get("Tier"))),
                    "map": map_lookup.get(r.get("Map"), str(r.get("Map"))),
                    "outcome": r.get("Outcome"),
                    "duration": r.get("Duration"),
                    "description": r.get("Description") or "",
                    "youtube_url": vods[0]["youtube_url"],
                }
            )
            if len(cohorts["gm_win"]) >= per_cohort:
                break

    return cohorts


def compute_timeline_feature_vector(timeline: Dict[str, Any]) -> Dict[str, Any]:
    """Extract rich HUD behavior & conversion metrics from a single MatchTimeline."""
    fights = timeline.get("fights", [])
    won_fights = sum(
        1 for f in fights if str(f.get("outcome", "")).upper() == "WON"
    )
    lost_fights = sum(
        1 for f in fights if str(f.get("outcome", "")).upper() == "LOST"
    )

    nades = [
        {"fight_outcome": str(f.get("outcome", "")).upper(), **ev}
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "biotic_nade_used"
    ]
    offensive_nades = [n for n in nades if n.get("nade_usage") == "offensive_anti"]
    won_offensive_nades = [
        n for n in offensive_nades if n["fight_outcome"] == "WON"
    ]
    unconverted_poke_nades = [
        n for n in offensive_nades if n["fight_outcome"] == "LOST"
    ]
    self_heal_nades = [n for n in nades if n.get("nade_usage") == "self_heal"]
    defensive_heal_nades = [
        n for n in nades if n.get("nade_usage") == "defensive_heal"
    ]

    sleeps = [
        {"fight_outcome": str(f.get("outcome", "")).upper(), **ev}
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "sleep_dart_used"
    ]
    poke_sleeps = [s for s in sleeps if s.get("is_poke_lottery_sleep")]
    disciplined_sleeps = [s for s in sleeps if not s.get("is_poke_lottery_sleep")]
    clutch_won_sleeps = [
        s for s in disciplined_sleeps if s["fight_outcome"] == "WON"
    ]

    deaths = [
        {"fight_outcome": str(f.get("outcome", "")).upper(), **ev}
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "player_death"
    ]
    sleep_cd_deaths = [d for d in deaths if d.get("sleep_on_cd_when_dove")]

    ults = [
        {"fight_outcome": str(f.get("outcome", "")).upper(), **ev}
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "ult_used"
    ]
    won_ults = [u for u in ults if u["fight_outcome"] == "WON"]
    disadv_lost_ults = [
        u
        for u in ults
        if (u.get("ally_alive", 5) - u.get("enemy_alive", 5)) <= -1
        and u["fight_outcome"] == "LOST"
    ]

    overheals = [
        ev
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "overheal_tunnel"
    ]

    return {
        "fights_count": len(fights),
        "fight_win_rate": round(won_fights / len(fights), 3) if fights else 0.0,
        "total_nades": len(nades),
        "offensive_nade_ratio": round(len(offensive_nades) / len(nades), 3) if nades else 0.0,
        "offensive_nade_win_conversion_rate": (
            round(len(won_offensive_nades) / len(offensive_nades), 3)
            if offensive_nades
            else 0.0
        ),
        "unconverted_poke_nade_ratio": (
            round(len(unconverted_poke_nades) / len(nades), 3) if nades else 0.0
        ),
        "self_heal_nade_ratio": round(len(self_heal_nades) / len(nades), 3) if nades else 0.0,
        "defensive_ally_nade_ratio": (
            round(len(defensive_heal_nades) / len(nades), 3) if nades else 0.0
        ),
        "total_sleeps": len(sleeps),
        "poke_lottery_sleep_ratio": round(len(poke_sleeps) / len(sleeps), 3) if sleeps else 0.0,
        "disciplined_sleep_win_conversion": (
            round(len(clutch_won_sleeps) / len(disciplined_sleeps), 3)
            if disciplined_sleeps
            else 0.0
        ),
        "total_deaths": len(deaths),
        "sleep_cd_deaths": len(sleep_cd_deaths),
        "sleep_cd_death_rate": (
            round(len(sleep_cd_deaths) / len(deaths), 3) if deaths else 0.0
        ),
        "total_ults": len(ults),
        "ult_fight_win_conversion": round(len(won_ults) / len(ults), 3) if ults else 0.0,
        "unconverted_disadv_ult_rate": (
            round(len(disadv_lost_ults) / len(ults), 3) if ults else 0.0
        ),
        "overheal_tunnel_count": len(overheals),
    }


def evaluator_judge_score(
    report_md: str, evaluation: Dict[str, Any], rubric: Dict[str, Any]
) -> Dict[str, Any]:
    """Evaluator Judge: scores a Coach Agent output on 3 strict quality dimensions."""
    critiques = evaluation.get("timeline_critiques", [])
    hud_grounded_count = sum(
        1 for c in critiques if "[" in c["diagnosis"] and "v" in c["diagnosis"]
    )
    hud_grounding_score = (
        hud_grounded_count / len(critiques) if critiques else 1.0
    )

    platitude_violations = [
        p
        for p in rubric.get("forbidden_platitudes", [])
        if p.lower() in report_md.lower()
    ]
    zero_platitude_score = 1.0 if not platitude_violations else 0.0

    habits = evaluation.get("top_habit_root_causes", [])
    quantified_habits = sum(
        1 for h in habits if any(ch.isdigit() for ch in h)
    )
    habit_quantification_score = (
        quantified_habits / len(habits) if habits else 0.0
    )

    overall = round(
        (hud_grounding_score + zero_platitude_score + habit_quantification_score)
        / 3.0,
        3,
    )
    return {
        "overall_score": overall,
        "hud_grounding_score": round(hud_grounding_score, 3),
        "zero_platitude_score": round(zero_platitude_score, 3),
        "habit_quantification_score": round(habit_quantification_score, 3),
        "critique_count": len(critiques),
    }


def _cohort_means(vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not vectors:
        return {}
    keys = [
        "fights_count",
        "fight_win_rate",
        "offensive_nade_ratio",
        "offensive_nade_win_conversion_rate",
        "unconverted_poke_nade_ratio",
        "self_heal_nade_ratio",
        "defensive_ally_nade_ratio",
        "poke_lottery_sleep_ratio",
        "disciplined_sleep_win_conversion",
        "total_deaths",
        "sleep_cd_death_rate",
        "ult_fight_win_conversion",
        "unconverted_disadv_ult_rate",
    ]
    out: Dict[str, Any] = {
        "matches_count": len(vectors),
        "total_teamfights": sum(v["fights_count"] for v in vectors),
        "total_nades_analyzed": sum(v["total_nades"] for v in vectors),
        "total_sleeps_analyzed": sum(v["total_sleeps"] for v in vectors),
        "total_deaths_analyzed": sum(v["total_deaths"] for v in vectors),
        "total_ults_analyzed": sum(v["total_ults"] for v in vectors),
    }
    for k in keys:
        out[f"mean_{k}"] = round(statistics.mean(v[k] for v in vectors), 3)
    return out


def run_expanded_contrastive_training(per_cohort: int = 5) -> Dict[str, Any]:
    key = load_api_key()
    extracted_dir = ROOT_DIR / "data" / "extracted_timelines"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    cohorts_path = ROOT_DIR / "data" / "training_cohorts_ana.json"
    if cohorts_path.exists():
        cohorts = json.loads(cohorts_path.read_text(encoding="utf-8"))
        if all(len(cohorts.get(k, [])) >= per_cohort for k in ("diamond_loss", "diamond_win", "gm_win")):
            print(f"[Training] Loaded existing {per_cohort * 3}-match cohort manifest from {cohorts_path.name}")
        else:
            cohorts = fetch_balanced_3_cohorts(per_cohort=per_cohort)
            cohorts_path.write_text(
                json.dumps(cohorts, indent=2, ensure_ascii=False), encoding="utf-8"
            )
    else:
        print(f"[Training] Curating {per_cohort * 3} balanced matches (3 cohorts) from owreplays.tv...")
        cohorts = fetch_balanced_3_cohorts(per_cohort=per_cohort)
        cohorts_path.write_text(
            json.dumps(cohorts, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    rubric_path = ROOT_DIR / "rubrics" / "ana_diamond.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))

    results_by_match: Dict[str, Any] = {}
    vectors_by_cohort: Dict[str, List[Dict[str, Any]]] = {
        "diamond_loss": [],
        "diamond_win": [],
        "gm_win": [],
    }

    for cohort_name, matches in cohorts.items():
        for match in matches:
            code = match["replay_code"]
            cache_file = extracted_dir / f"{code}.json"
            if cache_file.exists():
                print(f"[Cached] Loaded timeline for {code} ({cohort_name}: {match['rank']})")
                timeline = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                print(
                    f"[Live Extraction] Calling Gemini on {code} ({cohort_name}: {match['rank']} - {match['map']})..."
                )
                try:
                    timeline = translate_vod_with_gemini(
                        match,
                        start_offset="40s",
                        end_offset="220s",
                        fps=1.0,
                        api_key=key,
                    )
                    cache_file.write_text(
                        json.dumps(timeline, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    time.sleep(3)
                except Exception as exc:
                    print(f"[Warning] Failed extracting {code}: {exc}")
                    continue

            vec = compute_timeline_feature_vector(timeline)
            eval_out = evaluate_timeline_with_rubric(timeline, rubric)
            report_md = render_markdown_report(eval_out, rubric)
            judge = evaluator_judge_score(report_md, eval_out, rubric)

            report_file = extracted_dir / f"{code}_coach_report.md"
            report_file.write_text(report_md, encoding="utf-8")

            vectors_by_cohort[cohort_name].append(vec)
            results_by_match[code] = {
                "cohort": cohort_name,
                "rank": match["rank"],
                "map": match["map"],
                "outcome": match["outcome"],
                "title": match["title"],
                "features": vec,
                "evaluator_judge": judge,
            }

    cohort_aggregates = {
        c_name: _cohort_means(vecs)
        for c_name, vecs in vectors_by_cohort.items()
    }

    summary = {
        "total_matches_evaluated": len(results_by_match),
        "total_teamfights_evaluated": sum(
            c.get("total_teamfights", 0) for c in cohort_aggregates.values()
        ),
        "cohort_aggregates": cohort_aggregates,
        "matches_processed": results_by_match,
    }

    out_summary_path = ROOT_DIR / "data" / "training_calibration_summary.json"
    out_summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    summary = run_expanded_contrastive_training(per_cohort=5)
    print(
        json.dumps(
            {
                "total_matches_evaluated": summary["total_matches_evaluated"],
                "total_teamfights_evaluated": summary["total_teamfights_evaluated"],
                "cohort_aggregates": summary["cohort_aggregates"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
