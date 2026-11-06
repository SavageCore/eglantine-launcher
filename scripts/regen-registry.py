#!/usr/bin/env python3
"""Regenerate data/sources.json and data/games.json from eglantine_launcher/config.py.

config.py is the single source of truth (DEFAULT_PROJECTS, GAMES); the JSON
files are what the launcher fetches at startup, so edits must be regenerated
and committed. Run `make registry` or this script directly.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eglantine_launcher.config import DEFAULT_PROJECTS, GAMES

ROOT = Path(__file__).resolve().parents[1]


def main():
    sources = [{k: v for k, v in p.items() if k != "exclude_keywords"} for p in DEFAULT_PROJECTS]
    (ROOT / "data" / "sources.json").write_text(
        json.dumps({"sources": sources}, indent=2, ensure_ascii=False) + "\n"
    )
    games = [{"title_id": tid, "name": name} for tid, name in GAMES.items()]
    (ROOT / "data" / "games.json").write_text(
        json.dumps({"games": games}, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"wrote {len(sources)} sources, {len(games)} games")


if __name__ == "__main__":
    main()
