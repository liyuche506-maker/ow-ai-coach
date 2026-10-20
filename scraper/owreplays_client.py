"""Polite client for fetching public replay metadata and YouTube VOD URLs from owreplays.tv.

Note: owreplays.tv robots.txt explicitly allows User-agent: * on all replay endpoints,
and exposes a clean JSON API at /api/v2/replays.
"""

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

BASE_URL = "https://owreplays.tv"
HEADERS = {
    "User-Agent": "OWCoachPrototype/0.1 (Educational Research; Respecting robots.txt)"
}

# From /api/v2/heroes and /api/v2/tiers:
HERO_ANA_ID = 1
DIAMOND_TIER_IDS = [21, 22, 23, 24, 25]  # Diamond 5 through Diamond 1


def _get_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_replay_vod_cameras(code: str) -> List[Dict[str, Any]]:
    """Fetch the SSR initial state for a replay code to get its rendered YouTube VOD URLs."""
    url = f"{BASE_URL}/{code}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8")

    match = re.search(
        r'<script id="__INITIAL_STATE__" type="application/json">(.*?)</script>',
        html,
    )
    if not match:
        return []
    state = json.loads(match.group(1))
    vod_cameras = state.get("detail-replay-store", {}).get("vodCameras", [])
    cleaned = []
    for cam in vod_cameras:
        embed_url = cam.get("embedURL", "")
        vid_match = re.search(r"/embed/([a-zA-Z0-9_-]+)", embed_url)
        youtube_url = (
            f"https://www.youtube.com/watch?v={vid_match.group(1)}"
            if vid_match
            else embed_url
        )
        cleaned.append(
            {
                "player_name": cam.get("playerName"),
                "youtube_url": youtube_url,
                "status": cam.get("status"),
            }
        )
    return cleaned


def fetch_diamond_ana_samples(limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch top Diamond Ana replays that have rendered YouTube VODs."""
    tiers_param = ",".join(str(t) for t in DIAMOND_TIER_IDS)
    api_url = f"{BASE_URL}/api/v2/replays?heroes={HERO_ANA_ID}&tiers={tiers_param}"
    data = _get_json(api_url)

    maps_data = _get_json(f"{BASE_URL}/api/v2/maps")
    tiers_data = _get_json(f"{BASE_URL}/api/v2/tiers")
    map_lookup = {m["ID"]: m["map"] for m in maps_data}
    tier_lookup = {t["ID"]: t["title"] for t in tiers_data}

    samples: List[Dict[str, Any]] = []
    for r in data.get("replays", []):
        if not r.get("HasVod"):
            continue
        # Prefer pure Ana games (Heroes == [1]) for cleaner training data
        if r.get("Heroes") != [HERO_ANA_ID]:
            continue

        code = r["Code"]
        time.sleep(0.5)  # Polite rate limit
        vods = fetch_replay_vod_cameras(code)
        if not vods:
            continue

        samples.append(
            {
                "replay_code": code,
                "title": r.get("Title"),
                "player": r.get("Player"),
                "rank": tier_lookup.get(r.get("Tier"), str(r.get("Tier"))),
                "map": map_lookup.get(r.get("Map"), str(r.get("Map"))),
                "outcome": r.get("Outcome"),
                "duration": r.get("Duration"),
                "patch": r.get("PatchLevel"),
                "description": r.get("Description") or "",
                "owreplays_url": f"{BASE_URL}/{code}",
                "vod_cameras": vods,
            }
        )
        if len(samples) >= limit:
            break

    return samples


if __name__ == "__main__":
    out_path = Path(__file__).resolve().parent.parent / "data" / "diamond_ana_samples.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    samples = fetch_diamond_ana_samples(limit=5)
    out_path.write_text(json.dumps(samples, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(samples)} Diamond Ana VOD samples to {out_path}")
