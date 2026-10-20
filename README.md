# OW2 AI Coach Agent (`ow-ai-coach`)

An automated Overwatch 2 VOD analysis & coaching pipeline that eliminates generic AI platitudes by separating **HUD-Grounded Timeline Extraction** from **Rank-Specific Rubric Evaluation & Match Aggregation**.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design, zero-friction input pipelines (`Insights.gg` & `owreplays.tv`), HUD-grounded timeline translation, rank-stratified rubric generation, and Map-Reduce match coaching architecture.

1. **Data / Replay Ingestion (`scraper/owreplays_client.py`)**
   - Polls public replay metadata and rendered first-person YouTube VOD links from `owreplays.tv` (or local `Insights.gg` auto-recorded matches).
2. **Stage 1: Video-to-Timeline Translator (`agent/timeline_translator.py`)**
   - Uses multimodal video understanding (`1 FPS` HUD region tracking: Killfeed alive ratio, HP bar, Ultimate meter, Ability cooldown icons) to convert raw gameplay into an objective `MatchTimeline.json`.
3. **Stage 2: Rubric-Driven Match Aggregator & Coach (`agent/coach_agent.py`)**
   - Evaluates each teamfight (`Map`) and aggregates whole-match systemic habits (`Reduce`) against rank- and hero-specific rubrics (`rubrics/ana_diamond.json`).
   - Enforces negative constraints against generic advice ("find cover", "aim better").

## Quick Start

```bash
# 1. Fetch Diamond Ana replay samples & YouTube VOD links from owreplays.tv
python3 scraper/owreplays_client.py

# 2. Run the Coach Agent on a structured match timeline
python3 agent/coach_agent.py data/sample_timeline_MFTXPE.json
```
