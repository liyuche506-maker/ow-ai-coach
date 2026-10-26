"""Stage 1: Video-to-Timeline Translator for Overwatch 2 POV VODs.

Translates a raw YouTube VOD URL (from owreplays.tv) or local MP4 file into a
structured, objective MatchTimeline JSON focused on HUD facts:
- Top-right Killfeed -> alive_ratio (e.g., "5v5", "4v5", "3v5") & dead_allies
- Bottom-left HUD -> player_hp
- Bottom-center HUD -> ult_charge_pct
- Bottom-right HUD -> shift_cd_state ("ready" | "cd:Xs"), e_cd_state ("ready" | "cd:Xs")
- Audio Cues -> Sleep Dart firing/hit audio, Biotic Grenade splash/anti audio, Ultimate voice lines
- Deterministic Cooldown Physics Post-Processor -> Enforces exact 14s Sleep Dart & 12s Biotic Grenade cooldown timers
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_MODEL = "models/gemini-3.5-flash"
FALLBACK_MODELS = [
    "models/gemini-3.5-flash",
    "models/gemini-3.1-flash-lite",
    "models/gemini-3.8-flash",
]

SLEEP_DART_COOLDOWN_SEC = 14
BIOTIC_NADE_COOLDOWN_SEC = 12

TIMELINE_EXTRACTION_SYSTEM_PROMPT = """You are an objective Overwatch 2 HUD & Audio-Visual Fight Telemetry Extractor.
Do NOT give coaching advice. Your ONLY job is to read the 16:9 first-person HUD + listen to game audio cues and output a factual JSON timeline of teamfights.

For each teamfight in the video segment, inspect these 4 fixed screen regions and fuse with audio cues:
1. Top-Right (Killfeed + Elimination Audio): Track who died on each team to maintain the live `alive_ratio` (e.g. "5v5", "5v4", "4v5") and list `dead_allies`.
2. Bottom-Left (Health Bar): Track `player_hp` (0 to 250) and whether the player is purple (anti-healed).
3. Bottom-Center (Ultimate Meter): Track `ult_charge_pct` (0 to 100).
4. Bottom-Right (Ability Icons + Ability Sound Effects):
   - Continuously report `shift_cd_state` ("ready" or "cd:Xs") and `e_cd_state` ("ready" or "cd:Xs") at every logged event.
   - Shift (Sleep Dart, 14s cooldown): Use both the dart gun firing sound and HUD icon to log `sleep_dart_used`, whether it was a long-range poke/lottery shot (`is_poke_lottery_sleep`: true/false), and if the player dies (`player_death`), whether Sleep Dart was on cooldown (`sleep_on_cd_when_dove`: true/false, `sleep_cd_remaining_sec`).
   - E (Biotic Grenade, 12s cooldown): Use both the glass shatter/splash sound and HUD icon to log `biotic_nade_used`, classifying `nade_usage` strictly into: `"offensive_anti"`, `"defensive_heal"`, or `"self_heal"`.
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
          "shift_cd_state": "ready" | "cd:Xs",
          "e_cd_state": "ready" | "cd:Xs",
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


def _to_sec(mm_ss: Any) -> Optional[float]:
    if not mm_ss or ":" not in str(mm_ss):
        return None
    parts = str(mm_ss).strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None


def enforce_cooldown_physics(timeline: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically verify and backfill `shift_cd_state`, `e_cd_state`, and `sleep_on_cd_when_dove`
    using Overwatch 2's exact 14s Sleep Dart and 12s Biotic Grenade cooldown timers."""
    last_sleep_sec: Optional[float] = None
    last_nade_sec: Optional[float] = None

    for fight in timeline.get("fights", []):
        for ev in fight.get("events", []):
            cur_sec = _to_sec(ev.get("t"))
            etype = ev.get("event_type")

            if cur_sec is not None:
                # Compute true cooldown state at the moment just before this event
                if last_sleep_sec is not None and (cur_sec - last_sleep_sec) >= 0:
                    rem_s = round(SLEEP_DART_COOLDOWN_SEC - (cur_sec - last_sleep_sec), 1)
                    if rem_s > 0:
                        ev["shift_cd_state"] = f"cd:{int(round(rem_s))}s"
                        if etype == "player_death":
                            ev["sleep_on_cd_when_dove"] = True
                            ev["sleep_cd_remaining_sec"] = int(round(rem_s))
                    else:
                        ev["shift_cd_state"] = "ready"
                        if etype == "player_death":
                            ev["sleep_on_cd_when_dove"] = False
                            ev["sleep_cd_remaining_sec"] = 0
                else:
                    ev.setdefault("shift_cd_state", "ready")

                if last_nade_sec is not None and (cur_sec - last_nade_sec) >= 0:
                    rem_e = round(BIOTIC_NADE_COOLDOWN_SEC - (cur_sec - last_nade_sec), 1)
                    if rem_e > 0:
                        ev["e_cd_state"] = f"cd:{int(round(rem_e))}s"
                    else:
                        ev["e_cd_state"] = "ready"
                else:
                    ev.setdefault("e_cd_state", "ready")

                # Update last used timestamps when abilities are triggered
                if etype == "sleep_dart_used":
                    last_sleep_sec = cur_sec
                    ev["shift_cd_state"] = f"used->cd:{SLEEP_DART_COOLDOWN_SEC}s"
                elif etype == "biotic_nade_used":
                    last_nade_sec = cur_sec
                    ev["e_cd_state"] = f"used->cd:{BIOTIC_NADE_COOLDOWN_SEC}s"
                elif etype == "player_death":
                    # Respawn resets cooldown tracking for next fight
                    last_sleep_sec = None
                    last_nade_sec = None

    return timeline


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
    start_offset: str = "40s",
    end_offset: str = "240s",
    fps: float = 1.0,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Call Gemini REST API natively with an owreplays.tv YouTube URL to extract MatchTimeline JSON."""
    key = load_api_key(api_key)
    youtube_url = (
        replay_meta.get("youtube_url")
        or replay_meta["vod_cameras"][0]["youtube_url"]
    )

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

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        active_model = (
            FALLBACK_MODELS[(attempt - 1) % len(FALLBACK_MODELS)]
            if model == DEFAULT_MODEL
            else model
        )
        active_url = f"https://generativelanguage.googleapis.com/v1beta/{active_model}:generateContent?key={key}"
        try:
            req = urllib.request.Request(
                active_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            raw_json = body["candidates"][0]["content"]["parts"][0]["text"]
            timeline = json.loads(raw_json)
            timeline = enforce_cooldown_physics(timeline)
            timeline["_token_usage"] = body.get("usageMetadata", {})
            timeline["_model_used"] = active_model
            return timeline
        except Exception as exc:
            last_err = exc
            wait_sec = 5 * attempt
            print(
                f"  [Retry {attempt}/{max_retries} on {active_model}] Error on {replay_meta.get('replay_code')}: {exc}. Waiting {wait_sec}s..."
            )
            time.sleep(wait_sec)

    raise RuntimeError(f"Failed after {max_retries} retries: {last_err}")
