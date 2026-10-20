"""Stage 2: Match-Level Aggregator & Calibrated Rubric-Driven Coach Agent.

Evaluates a structured MatchTimeline JSON against a calibrated hero+rank Rubric
(rubrics/ana_diamond.json), aggregating cross-fight habits, surfacing positive
win-condition plays, and enforcing negative constraints against generic platitudes.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _parse_seconds(mm_ss: str) -> int:
    if not mm_ss or ":" not in str(mm_ss):
        return 0
    parts = str(mm_ss).strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        return int(parts[0]) * 60 + int(float(parts[1]))
    except ValueError:
        return 0


def evaluate_timeline_with_rubric(
    timeline: Dict[str, Any], rubric: Dict[str, Any]
) -> Dict[str, Any]:
    """Run deterministic Micro (per-fight) + Macro (whole-match) evaluation against the v2 calibrated Diamond Ana rubric."""
    meta = timeline["match_meta"]
    fights = timeline["fights"]
    rules_by_id = {r["rule_id"]: r for r in rubric["rules"]}

    nade_events: List[Dict[str, Any]] = []
    unconverted_poke_nades: List[Dict[str, Any]] = []
    self_heal_nades: List[Dict[str, Any]] = []
    sleep_violations: List[Dict[str, Any]] = []
    clutch_sleeps: List[Dict[str, Any]] = []
    ult_violations: List[Dict[str, Any]] = []
    micro_findings: List[Dict[str, Any]] = []

    for fight in fights:
        fid = fight["fight_id"]
        f_outcome = str(fight.get("outcome", "UNKNOWN")).upper()
        last_poke_sleep = None

        for ev in fight.get("events", []):
            etype = ev.get("event_type")
            t_str = ev.get("t", "00:00")
            alive_str = ev.get("alive_ratio", "5v5")

            if etype == "biotic_nade_used":
                record_nade = {"fight_id": fid, "fight_outcome": f_outcome, **ev}
                nade_events.append(record_nade)
                usage = ev.get("nade_usage")
                if usage == "offensive_anti" and f_outcome == "LOST":
                    unconverted_poke_nades.append(record_nade)
                elif usage == "self_heal":
                    self_heal_nades.append(record_nade)

            elif etype == "sleep_dart_used":
                if ev.get("is_poke_lottery_sleep"):
                    last_poke_sleep = ev
                    if f_outcome == "LOST":
                        rule = rules_by_id["ANA_DIA_02"]
                        detail = (
                            f"you fired a low-percentage poke Sleep Dart at '{ev.get('target')}', "
                            f"putting your primary anti-dive tool on a 14s cooldown before the fight was decided"
                        )
                        msg = rule["diagnostic_template"].format(
                            timestamp=t_str,
                            alive_ratio=alive_str,
                            fight_id=fid,
                            fight_outcome=f_outcome,
                            enemy_threats=" / ".join(meta.get("enemy_threats", [])),
                            sleep_event_detail=detail,
                        )
                        item = {
                            "fight_id": fid,
                            "rule_id": "ANA_DIA_02",
                            "severity": rule["severity"],
                            "timestamp": t_str,
                            "diagnosis": msg,
                            "fix": rule["actionable_fix"],
                        }
                        sleep_violations.append(item)
                        micro_findings.append(item)
                else:
                    if f_outcome == "WON":
                        clutch_sleeps.append(
                            {
                                "fight_id": fid,
                                "timestamp": t_str,
                                "target": ev.get("target"),
                                "player_hp": ev.get("player_hp"),
                            }
                        )

            elif etype == "player_death" and ev.get("sleep_on_cd_when_dove"):
                rule = rules_by_id["ANA_DIA_02"]
                prior_note = (
                    f"after firing a poke Sleep Dart at [{last_poke_sleep['t']}] ({last_poke_sleep.get('target')})"
                    if last_poke_sleep
                    else "while Sleep Dart was still on cooldown"
                )
                detail = (
                    f"you were eliminated by {ev.get('killed_by') or ev.get('target')} {prior_note} "
                    f"({ev.get('sleep_cd_remaining_sec', '?')}s cooldown remaining at death)"
                )
                msg = rule["diagnostic_template"].format(
                    timestamp=t_str,
                    alive_ratio=alive_str,
                    fight_id=fid,
                    fight_outcome=f_outcome,
                    enemy_threats=" / ".join(meta.get("enemy_threats", [])),
                    sleep_event_detail=detail,
                )
                item = {
                    "fight_id": fid,
                    "rule_id": "ANA_DIA_02",
                    "severity": rule["severity"],
                    "timestamp": t_str,
                    "diagnosis": msg,
                    "fix": rule["actionable_fix"],
                }
                sleep_violations.append(item)
                micro_findings.append(item)

            elif etype == "ult_used":
                ally_n = ev.get("ally_alive", 5)
                enemy_n = ev.get("enemy_alive", 5)
                # Only penalize disadvantage Nano if the fight was actually LOST
                if (ally_n - enemy_n <= -1) and f_outcome == "LOST":
                    rule = rules_by_id["ANA_DIA_03"]
                    dead_list = ev.get("dead_allies") or ["core frontline"]
                    detail = (
                        f"you invested Nano Boost onto {ev.get('ult_target') or ev.get('target')} "
                        f"while down {alive_str} (dead: {', '.join(str(x) for x in dead_list)}), "
                        f"failing to turn the fight and losing ultimate economy"
                    )
                    msg = rule["diagnostic_template"].format(
                        timestamp=t_str,
                        alive_ratio=alive_str,
                        fight_id=fid,
                        fight_outcome=f_outcome,
                        ult_event_detail=detail,
                    )
                    item = {
                        "fight_id": fid,
                        "rule_id": "ANA_DIA_03",
                        "severity": rule["severity"],
                        "timestamp": t_str,
                        "diagnosis": msg,
                        "fix": rule["actionable_fix"],
                    }
                    ult_violations.append(item)
                    micro_findings.append(item)

    # Evaluate ANA_DIA_01: Unconverted poke offensive nades in LOST fights + high self-heal nades
    total_nades = len(nade_events)
    offensive_nades = sum(
        1 for n in nade_events if n.get("nade_usage") == "offensive_anti"
    )
    won_offensive_nades = sum(
        1
        for n in nade_events
        if n.get("nade_usage") == "offensive_anti" and n["fight_outcome"] == "WON"
    )
    self_heal_ratio = round(len(self_heal_nades) / total_nades, 2) if total_nades else 0.0

    rule_01 = rules_by_id["ANA_DIA_01"]
    for n in unconverted_poke_nades:
        summary_str = (
            f"{len(unconverted_poke_nades)} of your {offensive_nades} offensive nades were thrown in fights you still LOST "
            f"(only {won_offensive_nades}/{offensive_nades} converted into a fight win)"
        )
        msg = rule_01["diagnostic_template"].format(
            timestamp=n.get("t", "00:00"),
            alive_ratio=n.get("alive_ratio", "5v5"),
            fight_id=n["fight_id"],
            fight_outcome=n["fight_outcome"],
            actual_target=n.get("target", "enemy frontline"),
            nade_usage="unconverted early/poke offensive_anti",
            unconverted_or_self_summary=summary_str,
        )
        micro_findings.append(
            {
                "fight_id": n["fight_id"],
                "rule_id": "ANA_DIA_01",
                "severity": rule_01["severity"],
                "timestamp": n.get("t", "00:00"),
                "diagnosis": msg,
                "fix": rule_01["actionable_fix"],
            }
        )

    if self_heal_ratio >= 0.30:
        for n in self_heal_nades:
            summary_str = (
                f"{len(self_heal_nades)}/{total_nades} ({round(self_heal_ratio*100)}%) of your Biotic Grenades "
                f"were forced into self-healing at {n.get('player_hp', '?')} HP"
            )
            msg = rule_01["diagnostic_template"].format(
                timestamp=n.get("t", "00:00"),
                alive_ratio=n.get("alive_ratio", "5v5"),
                fight_id=n["fight_id"],
                fight_outcome=n["fight_outcome"],
                actual_target=n.get("target", "self"),
                nade_usage="forced self_heal",
                unconverted_or_self_summary=summary_str,
            )
            micro_findings.append(
                {
                    "fight_id": n["fight_id"],
                    "rule_id": "ANA_DIA_01",
                    "severity": rule_01["severity"],
                    "timestamp": n.get("t", "00:00"),
                    "diagnosis": msg,
                    "fix": rule_01["actionable_fix"],
                }
            )

    micro_findings.sort(key=lambda x: _parse_seconds(x["timestamp"]))

    habits: List[str] = []
    if sleep_violations:
        habits.append(
            f"[Habit #1 - Sleep Dart Anti-Dive Discipline]: {len(sleep_violations)} critical Sleep Dart violation(s) occurred in lost fights against {'/'.join(meta.get('enemy_threats', []))} (firing low-percentage poke darts down main corridors or dying with Sleep Dart on cooldown)."
        )
    if unconverted_poke_nades:
        habits.append(
            f"[Habit #2 - Unconverted Early Poke Nades]: You threw {len(unconverted_poke_nades)} offensive Biotic Grenade(s) during poke phases in fights your team still LOST (only {won_offensive_nades}/{offensive_nades} offensive nades converted to a fight win). Time purple nades with your Tank's actual dive/brawl commitment."
        )
    if self_heal_nades:
        habits.append(
            f"[Habit #3 - Forced Defensive Self-Nades]: {len(self_heal_nades)}/{total_nades} ({round(self_heal_ratio*100)}%) of your Biotic Grenades were spent self-healing after taking poke/dive damage."
        )
    if ult_violations:
        habits.append(
            f"[Habit #4 - Unconverted Disadvantage Nano Boost]: {len(ult_violations)} Nano Boost(s) were invested at a numbers disadvantage in fights that still resulted in a loss."
        )
    if not habits:
        habits.append(
            "[Clean Execution]: 0 major cooldown or ultimate economy violations detected across the analyzed fights."
        )

    return {
        "match_meta": meta,
        "macro_aggregation": {
            "total_fights_analyzed": len(fights),
            "fights_won": sum(
                1 for f in fights if str(f.get("outcome", "")).upper() == "WON"
            ),
            "fights_lost": sum(
                1 for f in fights if str(f.get("outcome", "")).upper() == "LOST"
            ),
            "biotic_nade_stats": {
                "total_thrown": total_nades,
                "offensive_anti_count": offensive_nades,
                "won_fight_offensive_anti_count": won_offensive_nades,
                "unconverted_lost_fight_offensive_nades": len(unconverted_poke_nades),
                "self_heal_count": len(self_heal_nades),
            },
            "sleep_discipline_stats": {
                "sleep_violations_in_lost_fights": len(sleep_violations),
                "clutch_fight_winning_sleeps": len(clutch_sleeps),
            },
            "ult_economy_stats": {
                "unconverted_disadvantage_nanos": len(ult_violations),
            },
        },
        "top_habit_root_causes": habits,
        "clutch_highlights": clutch_sleeps,
        "timeline_critiques": micro_findings,
    }


def render_markdown_report(report: Dict[str, Any], rubric: Dict[str, Any]) -> str:
    meta = report["match_meta"]
    macro = report["macro_aggregation"]
    n_stats = macro["biotic_nade_stats"]
    s_stats = macro["sleep_discipline_stats"]

    lines = [
        f"# OW2 AI Coach Full-Match Review: {meta['player']} ({meta['rank']} - {meta['hero']})",
        f"- **Replay Code**: `{meta['replay_code']}` | **Map**: {meta['map']} | **Result**: {str(meta['outcome']).upper()} | **VOD**: {meta['youtube_url']}",
        f"- **Ally Comp**: `{', '.join(meta.get('ally_comp_observed', []))}` | **Enemy Threats**: `{', '.join(meta.get('enemy_threats', []))}`",
        "",
        "## 1. Match-Level Aggregation (Macro Telemetry)",
        f"- **Teamfights Analyzed**: {macro['total_fights_analyzed']} ({macro['fights_won']} Won / {macro['fights_lost']} Lost)",
        f"- **Biotic Grenade Breakdown**: {n_stats['total_thrown']} total ({n_stats['won_fight_offensive_anti_count']} fight-winning anti-nades, {n_stats['unconverted_lost_fight_offensive_nades']} unconverted poke nades in lost fights, {n_stats['self_heal_count']} self-heals)",
        f"- **Sleep Dart Discipline**: {s_stats['sleep_violations_in_lost_fights']} violation(s) in lost fights | {s_stats['clutch_fight_winning_sleeps']} clutch fight-winning sleep(s)",
        f"- **Unconverted Disadvantage Nano Boosts**: {macro['ult_economy_stats']['unconverted_disadvantage_nanos']}",
        "",
        "## 2. Systemic Habit Root Causes",
    ]
    for habit in report["top_habit_root_causes"]:
        lines.append(f"- {habit}")

    if report.get("clutch_highlights"):
        lines.extend(["", "## 3. High-Impact Winning Plays (Positive Reinforcement)"])
        for ch in report["clutch_highlights"]:
            lines.append(
                f"- **[Fight #{ch['fight_id']} | {ch['timestamp']}]** Landed disciplined Sleep Dart on `{ch['target']}` (at {ch.get('player_hp', '?')} HP) to secure the fight win."
            )

    lines.extend(["", "## 4. HUD-Grounded Timeline Audit (Micro Evidence)"])
    for item in report["timeline_critiques"]:
        lines.append(
            f"### [Fight #{item['fight_id']} | {item['timestamp']}] `{item['rule_id']}` ({item['severity']})"
        )
        lines.append(f"- **HUD Fact Diagnosis**: {item['diagnosis']}")
        lines.append(f"- **Actionable Fix**: {item['fix']}")
        lines.append("")

    md = "\n".join(lines)
    for bad_phrase in rubric.get("forbidden_platitudes", []):
        assert (
            bad_phrase.lower() not in md.lower()
        ), f"Violation: forbidden platitude '{bad_phrase}' found in report!"
    return md


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    rubric_path = root / "rubrics" / "ana_diamond.json"
    timeline_path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else root / "data" / "extracted_timelines" / "MFTXPE.json"
    )

    rubric_data = json.loads(rubric_path.read_text(encoding="utf-8"))
    timeline_data = json.loads(timeline_path.read_text(encoding="utf-8"))

    evaluation = evaluate_timeline_with_rubric(timeline_data, rubric_data)
    markdown_out = render_markdown_report(evaluation, rubric_data)
    print(markdown_out)
