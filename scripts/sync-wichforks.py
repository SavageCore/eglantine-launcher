#!/usr/bin/env python3
"""Diff wichforks data/forks.json against our data/sources.json.

wichforks is the upstream use-case guide we derive our defaults from:
https://github.com/riyadhHDA/wichforks

Usage:
    python3 scripts/sync-wichforks.py [--fetch]

Without --fetch it reuses data/forks.json if present in CWD; with --fetch
it downloads the current upstream file first. Prints which upstream forks
are missing locally, which local wichforks entries no longer exist upstream,
and where best_for text differs (a hint that reasons need updating).

This script never edits anything; apply changes to
eglantine_launcher/config.py DEFAULT_PROJECTS, then regenerate
data/sources.json from it (see the --help output).
"""

import json
import sys
import urllib.request
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/riyadhHDA/wichforks/main/data/forks.json"

# Upstream wichforks ids we deliberately do not ship (native ports, not
# launchable through this launcher).
SKIP = {"banana-native", "eglantine-ps5"}

# Upstream repo URL -> our local source id.
REPO_TO_ID = {
    "https://github.com/Djihads80/Eglantine": "eglantine",
    "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork": "pokedinner",
    "https://github.com/SmileyTatsu/Eglantine": "smiley",
    "https://github.com/MythosOfs/Orchid": "orchid",
    "https://github.com/notsapinho/Eglantine": "notsapinho",
    "https://github.com/zortos293/Lichii": "lichii",
    "https://gitlab.com/seighttt/droplet": "droplet",
    "https://github.com/rdenno/Eglantine": "rdenno",
    "https://github.com/sylvieistaken/selevet-eglantine": "selevet",
    "https://github.com/ZendNevs/Eglantine-ZendNevs": "zendnevs",
    "https://github.com/astralbeexd/Eglantine-3060Ti-16GB": "3060ti",
}

ROOT = Path(__file__).resolve().parents[1]


def load_upstream(fetch):
    cache = Path("data/forks.json")
    if fetch or not cache.exists():
        req = urllib.request.Request(UPSTREAM, headers={"User-Agent": "eglantine-launcher"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        cache.write_text(json.dumps(data, indent=2))
        return data
    return json.loads(cache.read_text())


def main():
    fetch = "--fetch" in sys.argv
    if "--help" in sys.argv:
        print(__doc__)
        print("Regenerate data/sources.json and data/games.json from config.py:")
        print("  make registry")
        return 0
    upstream = load_upstream(fetch)
    local = json.loads((ROOT / "data" / "sources.json").read_text())["sources"]
    local_by_id = {s["id"]: s for s in local if s.get("origin") == "wichforks"}

    upstream_ids = set()
    for fork in upstream.get("forks", []):
        fid = fork.get("id")
        if fid in SKIP:
            continue
        lid = REPO_TO_ID.get((fork.get("repo") or "").rstrip("/"))
        if not lid:
            print(f"UNMAPPED upstream fork: {fid} {(fork.get('repo') or '')}")
            continue
        upstream_ids.add(lid)
        if lid not in local_by_id:
            print(f"MISSING locally: {lid} ({fork.get('repo')}) role={fork.get('role')}")
        else:
            up_best = ", ".join(fork.get("best_for", []) or [])
            local_best = ", ".join((b.get("name") or "") for b in (local_by_id[lid].get("best_for") or []))
            if up_best and up_best != local_best and fork.get("id") not in ():
                print(f"CHECK best_for {lid}:\n  upstream: {up_best}\n  local:    {local_best}")
    for lid in sorted(set(local_by_id) - upstream_ids):
        print(f"REMOVED upstream (consider hiding): {lid}")
    if not upstream_ids:
        print("No upstream forks parsed; check data/forks.json schema.")
        return 1
    print(f"Upstream forks tracked: {len(upstream_ids)}, local wichforks entries: {len(local_by_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
