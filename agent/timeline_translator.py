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
from pathlib import Path
from typing import Any, Dict, Optional

TIMELINE_EXTRACTION_SYSTEM_PROMPT = """You are an objective Overwatch 2 HUD & Fight Telemetry Extractor.
Do NOT give coaching advice. Your ONLY job is to read the 16:9 first-person HUD and output a factual JSON timeline of teamfights.

For each teamfight in the video segment, inspect these 4 fixed screen regions:
1. Top-Right (Killfeed): Track who died on each team to maintain the live `alive_ratio` (e.g. "5v5", "4v5", "3v5") and list `dead_allies`.
2. Bottom-Left (Health Bar): Track `player_hp` (0 to 250) and whether the player is purple (anti-healed).
3. Bottom-Center (Ultimate Meter): Track `ult_charge_pct` (0 to 100, or "READY").
4. Bottom-Right (Ability Icons):
   - Shift (Sleep Dart): Is the icon bright ("ready") or on cooldown with a number ("cd:8s")?
   - E (Biotic Grenade): When thrown, did it land on enemies ("offensive_anti"), allies ("defensive_heal"), or self at feet ("self_heal")?

Return ONLY valid JSON matching the MatchTimeline schema."""


def build_gemini_translation_payload(
    youtube_url: str,
    replay_meta: Dict[str, Any],
    start_offset: str = "0s",
    end_offset: str = "600s",
) -> Dict[str, Any]:
    """Build the Gemini multimodal API request payload for direct YouTube URL ingestion."""
    return {
        "model": "gemini-2.5-flash",
        "system_instruction": TIMELINE_EXTRACTION_SYSTEM_PROMPT,
        "video_input": {
            "file_uri": youtube_url,
            "mime_type": "video/mp4",
            "video_metadata": {
                "start_offset": start_offset,
                "end_offset": end_offset,
                "fps": 1,
            },
        },
        "user_prompt": (
            f"Extract the structured MatchTimeline JSON for player '{replay_meta.get('player')}' "
            f"playing Ana ({replay_meta.get('rank')}) on map '{replay_meta.get('map')}' "
            f"(Replay Code: {replay_meta.get('replay_code')})."
        ),
    }


def translate_vod_with_gemini(
    youtube_url: str,
    replay_meta: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Call Gemini API directly with an owreplays.tv YouTube URL if GEMINI_API_KEY is set."""
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Export GEMINI_API_KEY to run live multimodal video extraction, "
            "or use the sample timeline in data/sample_timeline_MFTXPE.json to test the Coach Agent."
        )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    payload = build_gemini_translation_payload(youtube_url, replay_meta)
    response = client.models.generate_content(
        model=payload["model"],
        contents=[
            types.Content(
                parts=[
                    types.Part(
                        file_data=types.FileData(
                            file_uri=youtube_url,
                            mime_type="video/mp4",
                        ),
                        video_metadata=types.VideoMetadata(
                            start_offset="0s",
                            end_offset="600s",
                            fps=1.0,
                        ),
                    ),
                    types.Part.from_text(text=payload["user_prompt"]),
                ]
            )
        ],
        config=types.GenerateContentConfig(
            system_instruction=TIMELINE_EXTRACTION_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)
