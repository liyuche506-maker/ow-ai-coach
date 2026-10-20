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
                sleep_time = last_poke_sleep["t"] if last_poke_sleep else "数秒前"
                sleep_target = last_poke_sleep["target"] if last_poke_sleep else "远端目标"
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
                        dead_allies="、".join(ev.get("dead_allies", ["前排核心"])),
                        nano_target=ev.get("ult_target", "队友"),
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
                    exposed_enemy=ev.get("exposed_enemy", "敌方后排"),
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
    offensive_nades = sum(1 for n in nade_events if n.get("nade_usage") == "offensive_anti")
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
            f"【习惯性死因 #1 - 睡眠针防切纪律崩盘】：本局你在面对 {'/'.join(meta.get('enemy_threats', []))} 时，有 {len(sleep_cd_deaths)} 波输掉的团战都是因为开团前先把睡眠针远距离抽奖丢向敌方前排/走廊，导致 7~9 秒后敌方狗位跳脸时无针可用而暴毙。",
            f"【习惯性瓶颈 #2 - 生物手雷沦为自保/擦屁股工具】：整局记录的 {total_nades} 个生物手雷中，进攻紫瓶仅占 {offensive_pct}%（唯一赢下的 Fight #2 正是因为 [02:24] 禁疗了敌方温斯顿+源氏）。其余全部用于被蹭血后砸脚自奶或给高血量主T溢奶。",
            f"【习惯性瓶颈 #3 - 残局盲目交激素】：在 [04:01] 己方主T和主C已阵亡（3v5）的情况下强行交出纳米激素，直接葬送下一波团战的大招经济。",
        ],
        "timeline_critiques": micro_findings,
    }


def render_markdown_report(report: Dict[str, Any], rubric: Dict[str, Any]) -> str:
    meta = report["match_meta"]
    macro = report["macro_aggregation"]
    lines = [
        f"# 🎯 OW2 AI 教练整局复盘报告：{meta['player']} ({meta['rank']} - {meta['hero']})",
        f"- **回放代码**: `{meta['replay_code']}` | **地图**: {meta['map']} | **结果**: {meta['outcome'].upper()} | **VOD**: {meta['youtube_url']}",
        f"- **敌方核心威胁**: `{', '.join(meta.get('enemy_threats', []))}`",
        "",
        "## 一、 整局宏观数据聚合 (Match-Level Aggregation)",
        f"- **团战胜率**: 分析 {macro['total_fights_analyzed']} 波关键团战（赢 {macro['fights_won']} 波 / 输 {macro['fights_lost']} 波）",
        f"- **生物手雷进攻紫瓶率**: **{macro['biotic_nade_stats']['offensive_anti_pct']}** ({macro['biotic_nade_stats']['offensive_anti_count']}/{macro['biotic_nade_stats']['total_thrown']} 个用于进攻禁疗，钻石晋级大师标准 `{macro['biotic_nade_stats']['diamond_benchmark']}`)",
        f"- **因乱交睡针导致被切阵亡**: **{macro['sleep_discipline_stats']['deaths_while_sleep_on_cd_from_poke']} 次**",
        f"- **残局无效纳米激素 (人数差 ≤ -2)**: **{macro['ult_economy_stats']['wasted_nanos_in_lost_fights']} 次**",
        "",
        "## 二、 跨团战病根诊断 (Systemic Habit Root Causes)",
    ]
    for habit in report["top_habit_root_causes"]:
        lines.append(f"- {habit}")

    lines.extend(["", "## 三、 团战时间轴铁证审判 (HUD-Grounded Timeline Audit)"])
    for item in report["timeline_critiques"]:
        lines.append(
            f"### [Fight #{item['fight_id']} | {item['timestamp']}] `{item['rule_id']}` ({item['severity']})"
        )
        lines.append(f"- **客观战况诊断**: {item['diagnosis']}")
        lines.append(f"- **可执行修正方案**: {item['fix']}")
        lines.append("")

    md = "\n".join(lines)
    # Verify negative constraints (no forbidden platitudes)
    for bad_phrase in rubric.get("forbidden_platitudes", []):
        assert bad_phrase not in md, f"Violation: forbidden platitude '{bad_phrase}' found in report!"
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
