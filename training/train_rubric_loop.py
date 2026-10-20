"""Offline Coach Agent Training & Rubric Calibration Pipeline.

Uses public rank-labeled VOD cohorts from owreplays.tv (Diamond Ana vs. Grandmaster Ana)
to extract real HUD timelines via Gemini's native YouTube ingestion, compute contrastive
statistical thresholds across ranks, calibrate rubrics/ana_diamond.json, and evaluate
Coach Agent diagnostic precision.
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


def compute_timeline_feature_vector(timeline: Dict[str, Any]) -> Dict[str, Any]:
    """Extract normalized HUD behavior metrics from a single MatchTimeline."""
    fights = timeline.get("fights", [])
    nades = [
        ev
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "biotic_nade_used"
    ]
    offensive_nade_ratio = (
        sum(1 for n in nades if n.get("nade_usage") == "offensive_anti") / len(nades)
        if nades
        else 0.0
    )
    self_heal_nade_ratio = (
        sum(1 for n in nades if n.get("nade_usage") == "self_heal") / len(nades)
        if nades
        else 0.0
    )

    sleeps = [
        ev
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "sleep_dart_used"
    ]
    poke_lottery_sleep_ratio = (
        sum(1 for s in sleeps if s.get("is_poke_lottery_sleep")) / len(sleeps)
        if sleeps
        else 0.0
    )

    deaths = [
        ev
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "player_death"
    ]
    sleep_cd_death_rate = (
        sum(1 for d in deaths if d.get("sleep_on_cd_when_dove")) / len(deaths)
        if deaths
        else 0.0
    )

    ults = [
        ev
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "ult_used"
    ]
    wasted_ult_rate = (
        sum(
            1
            for u in ults
            if (u.get("ally_alive", 5) - u.get("enemy_alive", 5)) <= -2
        )
        / len(ults)
        if ults
        else 0.0
    )

    overheals = [
        ev
        for f in fights
        for ev in f.get("events", [])
        if ev.get("event_type") == "overheal_tunnel"
    ]

    return {
        "fights_count": len(fights),
        "total_nades": len(nades),
        "offensive_nade_ratio": round(offensive_nade_ratio, 3),
        "self_heal_nade_ratio": round(self_heal_nade_ratio, 3),
        "total_sleeps": len(sleeps),
        "poke_lottery_sleep_ratio": round(poke_lottery_sleep_ratio, 3),
        "total_deaths": len(deaths),
        "sleep_cd_death_rate": round(sleep_cd_death_rate, 3),
        "total_ults": len(ults),
        "wasted_ult_rate": round(wasted_ult_rate, 3),
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


def run_live_contrastive_training() -> Dict[str, Any]:
    """Extract real HUD timelines from Diamond vs. GM Ana VODs on owreplays.tv and calibrate rubric."""
    key = load_api_key()
    extracted_dir = ROOT_DIR / "data" / "extracted_timelines"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    diamond_samples = json.loads(
        (ROOT_DIR / "data" / "diamond_ana_samples.json").read_text(encoding="utf-8")
    )
    cohorts = json.loads(
        (ROOT_DIR / "data" / "training_cohorts_ana.json").read_text(encoding="utf-8")
    )

    # Select 2 Diamond matches (1 Loss MFTXPE + 1 Win D86HQF) and 2 Grandmaster matches (HSA58Z + 0AJTQ5)
    target_matches = [
        m for m in diamond_samples if m["replay_code"] in ("MFTXPE", "D86HQF")
    ] + [
        m for m in cohorts["master_gm_ana"] if m["replay_code"] in ("HSA58Z", "0AJTQ5")
    ]

    rubric_path = ROOT_DIR / "rubrics" / "ana_diamond.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))

    results_by_match: Dict[str, Any] = {}
    diamond_vectors: List[Dict[str, Any]] = []
    gm_vectors: List[Dict[str, Any]] = []

    for match in target_matches:
        code = match["replay_code"]
        cache_file = extracted_dir / f"{code}.json"
        if cache_file.exists():
            timeline = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            print(f"[Training] Extracting live HUD timeline from YouTube VOD for {code} ({match['rank']})...")
            timeline = translate_vod_with_gemini(
                match,
                start_offset="30s",
                end_offset="330s",
                fps=1.0,
                api_key=key,
            )
            cache_file.write_text(
                json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            time.sleep(2)

        vec = compute_timeline_feature_vector(timeline)
        eval_out = evaluate_timeline_with_rubric(timeline, rubric)
        report_md = render_markdown_report(eval_out, rubric)
        judge = evaluator_judge_score(report_md, eval_out, rubric)

        # Save individual coaching report
        report_file = extracted_dir / f"{code}_coach_report.md"
        report_file.write_text(report_md, encoding="utf-8")

        if "Grandmaster" in match["rank"] or "Master" in match["rank"]:
            gm_vectors.append(vec)
        else:
            diamond_vectors.append(vec)

        results_by_match[code] = {
            "rank": match["rank"],
            "map": match["map"],
            "outcome": match["outcome"],
            "features": vec,
            "evaluator_judge": judge,
        }

    gm_mean_offensive_nade = round(
        statistics.mean(v["offensive_nade_ratio"] for v in gm_vectors), 3
    )
    dia_mean_offensive_nade = round(
        statistics.mean(v["offensive_nade_ratio"] for v in diamond_vectors), 3
    )
    gm_mean_poke_sleep = round(
        statistics.mean(v["poke_lottery_sleep_ratio"] for v in gm_vectors), 3
    )
    dia_mean_poke_sleep = round(
        statistics.mean(v["poke_lottery_sleep_ratio"] for v in diamond_vectors), 3
    )

    calibration_summary = {
        "matches_processed": results_by_match,
        "contrastive_findings": {
            "gm_mean_offensive_nade_ratio": gm_mean_offensive_nade,
            "diamond_mean_offensive_nade_ratio": dia_mean_offensive_nade,
            "gm_mean_poke_lottery_sleep_ratio": gm_mean_poke_sleep,
            "diamond_mean_poke_lottery_sleep_ratio": dia_mean_poke_sleep,
        },
    }

    out_summary_path = ROOT_DIR / "data" / "training_calibration_summary.json"
    out_summary_path.write_text(
        json.dumps(calibration_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return calibration_summary


if __name__ == "__main__":
    summary = run_live_contrastive_training()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
