# OW2 AI Coach Agent (`ow-ai-coach`)

An automated *Overwatch 2* VOD analysis & coaching platform that eliminates generic AI platitudes by separating **HUD-Grounded Timeline Extraction** from **Rank-Specific Rubric Evaluation & Match Aggregation**.

## Two-Phase Workflow: Offline Training vs. Online Runtime

* **Phase A — Offline Coach Agent Training (`owreplays.tv`)**:
  Uses public, rank-labeled VODs from `owreplays.tv` as an **offline training and calibration dataset** to mine statistical thresholds across ranks (e.g., Diamond vs. Master Ana), refine HUD extraction accuracy, and iteratively train the `Rubrics` and `CoachAgent`.
* **Phase B — Online Player Runtime (Local `Insights.gg`)**:
  During actual gameplay, players do **not** use `owreplays.tv`. `Insights.gg` runs locally in the background, automatically recording matches and marking kill/death timestamps. The pre-trained Coach Agent ingests the local `Insights.gg` output directly to generate post-match coaching reports.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete system architecture and training loop design.

## Repository Structure

* `scraper/owreplays_client.py` — Fetches rank-labeled training VODs and YouTube POV links from `owreplays.tv`.
* `data/diamond_ana_samples.json` — Curated offline training batch of Diamond Ana VODs from `owreplays.tv`.
* `rubrics/ana_diamond.json` — Calibrated Diamond Ana evaluation rubric & anti-platitude guardrails.
* `agent/timeline_translator.py` — Stage 1: Converts raw gameplay video into objective HUD `MatchTimeline.json`.
* `agent/coach_agent.py` — Stage 2: Runs per-fight micro audits and whole-match macro habit aggregation.

## Quick Start (Offline Training & Prototype Test)

```bash
# 1. Fetch Diamond Ana training VOD samples from owreplays.tv
python3 scraper/owreplays_client.py

# 2. Run the Coach Agent against a structured match timeline
python3 agent/coach_agent.py data/sample_timeline_MFTXPE.json
```
