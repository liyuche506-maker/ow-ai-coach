"""Stage 1: Video-to-Timeline Translator for Overwatch 2 POV VODs.

Translates a raw YouTube VOD URL (from owreplays.tv) or local MP4 file into a
structured, objective MatchTimeline JSON focused on HUD facts:
- Top-right Killfeed -> alive_ratio (e.g., "5v5", "4v5", "3v5") & dead_allies
- Bottom-left HUD -> player_hp
- Bottom-center HUD -> ult_charge_pct
- Bottom-right HUD -> sleep_dart_state ("ready" | "cd:Xs"), biotic_nade_state ("ready" | "cd:Xs")
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_MODEL = "models/gemini-3.8-flash"

TIMELINE_EXTRACTION_SYSTEM_PROMPT = """You are an objective Overwatch 2 HUD & Fight Telemetry Extractor.
Do NOT give coaching advice. Your ONLY job is to read the 16:9 first-person HUD and output a factual JSON timeline of teamfights.

For each teamfight in the video segment, inspect these 4 fixed screen regions:
1. Top-Right (Killfeed): Track who died on each team to maintain the live `alive_ratio` (e.g. "5v5", "4v5", "3v5") and list `dead_allies`.
2. Bottom-Left (Health Bar): Track `player_hp` (0 to 250) and whether the player is purple (anti-healed).
3. Bottom-Center (Ultimate Meter): Track `ult_charge_pct` (0 to 100).
4. Bottom-Right (Ability Icons):
   - Shift (Sleep Dart): Track `sleep_dart_used`, whether it was a long-range poke/lottery shot (`is_poke_lottery_sleep`: true/false), and if the player dies (`player_death`), whether Sleep Dart was on cooldown (`sleep_on_cd_when_dove`: true/false, `sleep_cd_remaining_sec`).
   - E (Biotic Grenade): When thrown (`biotic_nade_used`), classify `nade_usage` strictly into one of: `"offensive_anti"`, `"defensive_heal"`, or `"self_heal"`.
   - Also flag `overheal_tunnel` when the player stays hard-scoped >3.5s shooting allies who are already >85% HP while enemies are exposed.

Return ONLY valid JSON matching the exact schema requested."""

USER_EXTRACTION_PROMPT_TEMPLATE = """Analyze this Overwatch 2 Ana POV (Replay Code: {replay_code}, Rank: {rank}, Map: {map}, Match Outcome: {outcome}).
Output a JSON object with this exact structure:
{{
  "match_meta": {{
    "replay_code": "{replay_code}",
    "title": "{title}",
    "player": "{player}",
    "hero": "Ana",
    "rank": "{rank}",
    "map": "{map}",
    "outcome": "{outcome}",
    "duration": "{duration}",
    "youtube_url": "{youtube_url}",
    "ally_comp_observed": ["Hero1", "Hero2", ...],
    "enemy_threats": ["EnemyHero1", "EnemyHero2", ...]
  }},
  "fights": [
    {{
      "fight_id": 1,
      "time_range": "MM:SS - MM:SS",
      "outcome": "WON" | "LOST",
      "events": [
        {{
          "t": "MM:SS",
          "alive_ratio": "5v5",
          "ally_alive": 5,
          "enemy_alive": 5,
          "dead_allies": [],
          "player_hp": 250,
          "ult_charge_pct": 45,
          "event_type": "sleep_dart_used" | "biotic_nade_used" | "ult_used" | "player_death" | "overheal_tunnel",
          "target": "concise factual description of target and context",
          "is_poke_lottery_sleep": false,
          "nade_usage": "offensive_anti" | "defensive_heal" | "self_heal" | null,
          "ult_target": "ally hero name if ult_used else null",
          "killed_by": "enemy hero name if player_death else null",
          "sleep_on_cd_when_dove": false,
          "sleep_cd_remaining_sec": 0,
          "ally_hp_pct": 90,
          "duration_sec": 4.0,
          "exposed_enemy": "enemy description if overheal_tunnel else null"
        }}
      ]
    }}
  ]
}}"""


def load_api_key(explicit_key: Optional[str] = None) -> str:
    """Load GEMINI_API_KEY from argument, environment, or local .env file."""
    if explicit_key:
        return explicit_key
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()

    raise RuntimeError(
        "GEMINI_API_KEY not found in environment or .env file."
    )


def translate_vod_with_gemini(
    replay_meta: Dict[str, Any],
    start_offset: str = "30s",
    end_offset: str = "330s",
    fps: float = 1.0,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Call Gemini REST API natively with an owreplays.tv YouTube URL to extract MatchTimeline JSON."""
    key = load_api_key(api_key)
    youtube_url = replay_meta.get("youtube_url") or replay_meta["vod_cameras"][0]["youtube_url"]

    prompt_text = USER_EXTRACTION_PROMPT_TEMPLATE.format(
        replay_code=replay_meta.get("replay_code", "UNKNOWN"),
        title=replay_meta.get("title", ""),
        player=replay_meta.get("player", "Player"),
        rank=replay_meta.get("rank", "Diamond"),
        map=replay_meta.get("map", "Unknown Map"),
        outcome=replay_meta.get("outcome", "unknown"),
        duration=replay_meta.get("duration", ""),
        youtube_url=youtube_url,
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={key}"
    payload = {
        "systemInstruction": {
            "parts": [{"text": TIMELINE_EXTRACTION_SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "parts": [
                    {
                        "fileData": {
                            "fileUri": youtube_url,
                        },
                        "videoMetadata": {
                            "startOffset": start_offset,
                            "endOffset": end_offset,
                            "fps": fps,
                        },
                    },
                    {"text": prompt_text},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    raw_json = body["candidates"][0]["content"]["parts"][0]["text"]
    timeline = json.loads(raw_json)
    timeline["_token_usage"] = body.get("usageMetadata", {})
    return timeline
