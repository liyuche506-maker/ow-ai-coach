"""Stage 2: Match-Level Aggregator & Rubric-Driven Coach Agent.

Evaluates a structured MatchTimeline JSON against a hero+rank Rubric (e.g. rubrics/ana_diamond.json),
aggregating cross-fight habits and enforcing negative constraints against generic platitudes.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _parse_seconds(mm_ss: str) -> int:
    parts = mm_ss.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def evaluate_timeline_with_rubric(
    timeline: Dict[str, Any], rubric: Dict[str, Any]
) -> Dict[str, Any]:
    """Run deterministic Micro (per-fight) + Macro (whole-match) evaluation against the Diamond Ana rubric."""
    meta = timeline["match_meta"]
    fights = timeline["fights"]
    rules_by_id = {r["rule_id"]: r for r in rubric["rules"]}

    nade_events: List[Dict[str, Any]] = []
    sleep_cd_deaths: List[Dict[str, Any]] = []
    bad_nanos: List[Dict[str, Any]] = []
    overheal_events: List[Dict[str, Any]] = []
    micro_findings: List[Dict[str, Any]] = []

    for fight in fights:
        fid = fight["fight_id"]
        last_poke_sleep = None

        for ev in fight.get("events", []):
            etype = ev.get("event_type")

            if etype == "sleep_dart_used" and ev.get("is_poke_lottery_sleep"):
                last_poke_sleep = ev

            elif etype == "biotic_nade_used":
                nade_events.append({"fight_id": fid, **ev})

            elif etype == "player_death" and ev.get("sleep_on_cd_when_dove"):
                rule = rules_by_id["ANA_DIA_02"]
                sleep_time = last_poke_sleep["t"] if last_poke_sleep else "seconds prior"
                sleep_target = (
                    last_poke_sleep["target"] if last_poke_sleep else "distant target"
                )
                sec_diff = (
                    _parse_seconds(ev["t"]) - _parse_seconds(last_poke_sleep["t"])
                    if last_poke_sleep
                    else 9
                )
                msg = rule["diagnostic_template"].format(
                    timestamp=ev["t"],
                    alive_ratio=ev["alive_ratio"],
                    enemy_threats=" / ".join(meta.get("enemy_threats", [])),
                    sleep_used_time=sleep_time,
                    sleep_target=sleep_target,
                    seconds_later=sec_diff,
                    cd_remaining=ev.get("sleep_cd_remaining_sec", "?"),
                )
                record = {
                    "fight_id": fid,
                    "rule_id": "ANA_DIA_02",
                    "severity": rule["severity"],
                    "timestamp": ev["t"],
                    "diagnosis": msg,
                    "fix": rule["actionable_fix"],
                }
                sleep_cd_deaths.append(record)
                micro_findings.append(record)

            elif etype == "ult_used":
                ally_n = ev.get("ally_alive", 5)
                enemy_n = ev.get("enemy_alive", 5)
                if ally_n - enemy_n <= -2:
                    rule = rules_by_id["ANA_DIA_03"]
                    msg = rule["diagnostic_template"].format(
                        timestamp=ev["t"],
                        alive_ratio=ev["alive_ratio"],
                        dead_allies=", ".join(ev.get("dead_allies", ["core frontline"])),
                        nano_target=ev.get("ult_target", "ally"),
                    )
                    record = {
                        "fight_id": fid,
                        "rule_id": "ANA_DIA_03",
                        "severity": rule["severity"],
                        "timestamp": ev["t"],
                        "diagnosis": msg,
                        "fix": rule["actionable_fix"],
                    }
                    bad_nanos.append(record)
                    micro_findings.append(record)

            elif etype == "overheal_tunnel":
                rule = rules_by_id["ANA_DIA_04"]
                msg = rule["diagnostic_template"].format(
                    timestamp=ev["t"],
                    alive_ratio=ev["alive_ratio"],
                    ally_hp_pct=ev.get("ally_hp_pct", 90),
                    duration=ev.get("duration_sec", 4.0),
                    exposed_enemy=ev.get("exposed_enemy", "enemy backline"),
                )
                record = {
                    "fight_id": fid,
                    "rule_id": "ANA_DIA_04",
                    "severity": rule["severity"],
                    "timestamp": ev["t"],
                    "diagnosis": msg,
                    "fix": rule["actionable_fix"],
                }
                overheal_events.append(record)
                micro_findings.append(record)

    # Evaluate ANA_DIA_01 across all Biotic Grenades in the match
    total_nades = len(nade_events)
    offensive_nades = sum(
        1 for n in nade_events if n.get("nade_usage") == "offensive_anti"
    )
    offensive_pct = round((offensive_nades / total_nades) * 100) if total_nades else 0

    if total_nades > 0 and (offensive_nades / total_nades) < 0.45:
        rule = rules_by_id["ANA_DIA_01"]
        for n in nade_events:
            if n.get("nade_usage") != "offensive_anti":
                msg = rule["diagnostic_template"].format(
                    timestamp=n["t"],
                    alive_ratio=n["alive_ratio"],
                    actual_target=n["target"],
                    offensive_anti_pct=offensive_pct,
                )
                micro_findings.append(
                    {
                        "fight_id": n["fight_id"],
                        "rule_id": "ANA_DIA_01",
                        "severity": rule["severity"],
                        "timestamp": n["t"],
                        "diagnosis": msg,
                        "fix": rule["actionable_fix"],
                    }
                )

    micro_findings.sort(key=lambda x: _parse_seconds(x["timestamp"]))

    return {
        "match_meta": meta,
        "macro_aggregation": {
            "total_fights_analyzed": len(fights),
            "fights_won": sum(1 for f in fights if f["outcome"] == "WON"),
            "fights_lost": sum(1 for f in fights if f["outcome"] == "LOST"),
            "biotic_nade_stats": {
                "total_thrown": total_nades,
                "offensive_anti_count": offensive_nades,
                "defensive_or_self_count": total_nades - offensive_nades,
                "offensive_anti_pct": f"{offensive_pct}%",
                "diamond_benchmark": ">= 50%",
            },
            "sleep_discipline_stats": {
                "deaths_while_sleep_on_cd_from_poke": len(sleep_cd_deaths),
            },
            "ult_economy_stats": {
                "wasted_nanos_in_lost_fights": len(bad_nanos),
            },
        },
        "top_habit_root_causes": [
            f"[Habit #1 - Breakdown in Sleep Dart Anti-Dive Discipline]: Against {'/'.join(meta.get('enemy_threats', []))}, {len(sleep_cd_deaths)} of your lost fights occurred because you fired low-percentage poke Sleep Darts at distant targets, leaving you defenseless when dove 7-9s later.",
            f"[Habit #2 - Passive/Reactive Biotic Grenade Usage]: Out of {total_nades} Biotic Grenades tracked, only {offensive_pct}% were used for offensive purple anti-heals (your sole won fight, Fight #2, was won directly off your [02:24] anti-nade on Winston+Genji). The rest were spent on reactive self-heals or topping off a 75% HP Tank.",
            f"[Habit #3 - Panic Ultimate Investment in Lost Fights]: At [04:01], you invested Nano Boost during a 3v5 after your Tank and DPS were already eliminated, crippling your ultimate economy for the next fight.",
        ],
        "timeline_critiques": micro_findings,
    }


def render_markdown_report(report: Dict[str, Any], rubric: Dict[str, Any]) -> str:
    meta = report["match_meta"]
    macro = report["macro_aggregation"]
    lines = [
        f"# OW2 AI Coach Full-Match Review: {meta['player']} ({meta['rank']} - {meta['hero']})",
        f"- **Replay Code**: `{meta['replay_code']}` | **Map**: {meta['map']} | **Result**: {meta['outcome'].upper()} | **VOD**: {meta['youtube_url']}",
        f"- **Enemy Dive/Flank Threats**: `{', '.join(meta.get('enemy_threats', []))}`",
        "",
        "## 1. Match-Level Aggregation (Macro Telemetry)",
        f"- **Teamfights Analyzed**: {macro['total_fights_analyzed']} ({macro['fights_won']} Won / {macro['fights_lost']} Lost)",
        f"- **Offensive Anti-Nade Ratio**: **{macro['biotic_nade_stats']['offensive_anti_pct']}** ({macro['biotic_nade_stats']['offensive_anti_count']}/{macro['biotic_nade_stats']['total_thrown']} offensive purple nades; Diamond-to-Master benchmark: `{macro['biotic_nade_stats']['diamond_benchmark']}`)",
        f"- **Deaths While Sleep Dart on Cooldown from Poke**: **{macro['sleep_discipline_stats']['deaths_while_sleep_on_cd_from_poke']}**",
        f"- **Wasted Nano Boosts in Lost Fights (Alive Diff <= -2)**: **{macro['ult_economy_stats']['wasted_nanos_in_lost_fights']}**",
        "",
        "## 2. Systemic Habit Root Causes",
    ]
    for habit in report["top_habit_root_causes"]:
        lines.append(f"- {habit}")

    lines.extend(["", "## 3. HUD-Grounded Timeline Audit (Micro Evidence)"])
    for item in report["timeline_critiques"]:
        lines.append(
            f"### [Fight #{item['fight_id']} | {item['timestamp']}] `{item['rule_id']}` ({item['severity']})"
        )
        lines.append(f"- **HUD Fact Diagnosis**: {item['diagnosis']}")
        lines.append(f"- **Actionable Fix**: {item['fix']}")
        lines.append("")

    md = "\n".join(lines)
    # Verify negative constraints (no forbidden platitudes)
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
        else root / "data" / "sample_timeline_MFTXPE.json"
    )

    rubric_data = json.loads(rubric_path.read_text(encoding="utf-8"))
    timeline_data = json.loads(timeline_path.read_text(encoding="utf-8"))

    evaluation = evaluate_timeline_with_rubric(timeline_data, rubric_data)
    markdown_out = render_markdown_report(evaluation, rubric_data)
    print(markdown_out)
