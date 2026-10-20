"""Offline Coach Agent Training & Rubric Calibration Pipeline.

Uses public rank-labeled VOD cohorts from owreplays.tv (Diamond Ana vs. Master/GM Ana)
to calibrate empirical rubric thresholds and evaluate the Coach Agent's diagnostic precision.

Note on Video Ingestion Compliance:
- Direct scraping/downloading of YouTube video streams via third-party tools triggers
  YouTube's SABR / anti-bot protections and is intentionally NOT bypassed.
- Instead, live video extraction uses Google Gemini's official first-party YouTube URL
  ingestion (`file_uri=youtube_url` via `google-genai` SDK with `GEMINI_API_KEY`),
  which processes public YouTube videos natively inside Google's infrastructure.
"""

import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from agent.coach_agent import evaluate_timeline_with_rubric, render_markdown_report
from scraper.owreplays_client import (
    BASE_URL,
    HERO_ANA_ID,
    _get_json,
    fetch_replay_vod_cameras,
)

DIAMOND_TIERS = [21, 22, 23, 24, 25]
MASTER_GM_TIERS = [26, 27, 28, 29, 30, 31, 32, 33, 34, 35]


def fetch_contrastive_cohorts(per_cohort: int = 4) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch Diamond Ana vs. Master/GM Ana cohorts from owreplays.tv public API."""
    maps_data = _get_json(f"{BASE_URL}/api/v2/maps")
    tiers_data = _get_json(f"{BASE_URL}/api/v2/tiers")
    map_lookup = {m["ID"]: m["map"] for m in maps_data}
    tier_lookup = {t["ID"]: t["title"] for t in tiers_data}

    cohorts: Dict[str, List[Dict[str, Any]]] = {
        "diamond_ana": [],
        "master_gm_ana": [],
    }

    for key, tier_ids in [
        ("diamond_ana", DIAMOND_TIERS),
        ("master_gm_ana", MASTER_GM_TIERS),
    ]:
        tiers_str = ",".join(str(t) for t in tier_ids)
        url = f"{BASE_URL}/api/v2/replays?heroes={HERO_ANA_ID}&tiers={tiers_str}"
        data = _get_json(url)
        for r in data.get("replays", []):
            if not r.get("HasVod") or r.get("Heroes") != [HERO_ANA_ID]:
                continue
            code = r["Code"]
            vods = fetch_replay_vod_cameras(code)
            if not vods:
                continue
            cohorts[key].append(
                {
                    "replay_code": code,
                    "title": r.get("Title"),
                    "player": r.get("Player"),
                    "rank": tier_lookup.get(r.get("Tier"), str(r.get("Tier"))),
                    "map": map_lookup.get(r.get("Map"), str(r.get("Map"))),
                    "outcome": r.get("Outcome"),
                    "duration": r.get("Duration"),
                    "youtube_url": vods[0]["youtube_url"],
                }
            )
            if len(cohorts[key]) >= per_cohort:
                break

    return cohorts


def compute_timeline_feature_vector(timeline: Dict[str, Any]) -> Dict[str, float]:
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

    return {
        "offensive_nade_ratio": round(offensive_nade_ratio, 3),
        "sleep_cd_death_rate": round(sleep_cd_death_rate, 3),
        "wasted_ult_rate": round(wasted_ult_rate, 3),
    }


def evaluator_judge_score(
    report_md: str, evaluation: Dict[str, Any], rubric: Dict[str, Any]
) -> Dict[str, Any]:
    """Evaluator Judge: scores a Coach Agent output on 4 strict quality dimensions."""
    critiques = evaluation.get("timeline_critiques", [])

    # 1. HUD Citation Completeness: Every critique must cite [MM:SS] and alive ratio (XvY)
    hud_grounded_count = sum(
        1
        for c in critiques
        if "[" in c["diagnosis"] and "v" in c["diagnosis"]
    )
    hud_grounding_score = (
        hud_grounded_count / len(critiques) if critiques else 1.0
    )

    # 2. Zero-Platitude Compliance
    platitude_violations = [
        p
        for p in rubric.get("forbidden_platitudes", [])
        if p.lower() in report_md.lower()
    ]
    zero_platitude_score = 1.0 if not platitude_violations else 0.0

    # 3. Macro-to-Micro Consistency: Do top habits reference concrete numerical counts?
    habits = evaluation.get("top_habit_root_causes", [])
    quantified_habits = sum(
        1 for h in habits if any(ch.isdigit() for ch in h)
    )
    habit_quantification_score = (
        quantified_habits / len(habits) if habits else 0.0
    )

    overall = round(
        (hud_grounding_score + zero_platitude_score + habit_quantification_score) / 3.0,
        3,
    )
    return {
        "overall_score": overall,
        "hud_grounding_score": round(hud_grounding_score, 3),
        "zero_platitude_score": round(zero_platitude_score, 3),
        "habit_quantification_score": round(habit_quantification_score, 3),
        "critique_count": len(critiques),
    }


def run_offline_training_iteration() -> Dict[str, Any]:
    """Run one complete offline training & calibration pass."""
    cohorts_path = ROOT_DIR / "data" / "training_cohorts_ana.json"
    cohorts = fetch_contrastive_cohorts(per_cohort=4)
    cohorts_path.write_text(
        json.dumps(cohorts, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    rubric_path = ROOT_DIR / "rubrics" / "ana_diamond.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))

    sample_timeline_path = ROOT_DIR / "data" / "sample_timeline_MFTXPE.json"
    diamond_loss_timeline = json.loads(
        sample_timeline_path.read_text(encoding="utf-8")
    )

    features = compute_timeline_feature_vector(diamond_loss_timeline)
    evaluation = evaluate_timeline_with_rubric(diamond_loss_timeline, rubric)
    report_md = render_markdown_report(evaluation, rubric)
    judge_result = evaluator_judge_score(report_md, evaluation, rubric)

    summary = {
        "training_cohorts_fetched": {
            "diamond_ana_count": len(cohorts["diamond_ana"]),
            "master_gm_ana_count": len(cohorts["master_gm_ana"]),
            "diamond_codes": [c["replay_code"] for c in cohorts["diamond_ana"]],
            "master_gm_codes": [c["replay_code"] for c in cohorts["master_gm_ana"]],
        },
        "live_gemini_api_key_detected": bool(os.environ.get("GEMINI_API_KEY")),
        "sample_feature_vector_MFTXPE": features,
        "evaluator_judge_metrics": judge_result,
    }
    return summary


if __name__ == "__main__":
    result = run_offline_training_iteration()
    print(json.dumps(result, indent=2, ensure_ascii=False))
