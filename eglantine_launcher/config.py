import copy
import json
import os
import tempfile
from pathlib import Path

from .gpu import gpu_vendors


def _base_home():
    # Resolved at call time (not import time) so tests can redirect HOME.
    return Path(os.environ.get("HOME") or str(Path.home()))


def config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "eglantine-launcher"
    return _base_home() / ".config" / "eglantine-launcher"


def config_file():
    override = os.environ.get("EGLANTINE_LAUNCHER_CONFIG")
    if override:
        return Path(override)
    return config_dir() / "config.json"


# Set on every dict returned by load(): the file it came from. save() writes
# back there even if the environment changed in between, and never stores it.
_CONFIG_PATH_KEY = "_config_path"

# install_dir/source_dir must never point into a temp tree that is not ours:
# pytest redirects HOME there, and a worker thread that outlives its test
# once rewrote the real file with another test's tmp paths (taking saves,
# backups and builds with it). Paths under the current home are fine, even
# on tmpfs; anything else in temp is pytest garbage. Nobody keeps a 100+ GB
# build tree in somebody else's tmpdir on purpose.
_PATH_KEYS = ("install_dir", "source_dir")


def _stray_tmp_path(path):
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = _base_home() / p
        if not p.is_relative_to(Path(tempfile.gettempdir())):
            return False
        return not p.is_relative_to(_base_home())
    except Exception:
        return False


def _scrub_paths(cfg, fallback):
    """Drop stray tmp-tree install/source dirs, keeping the fallback's values."""
    for key in _PATH_KEYS:
        if cfg.get(key) and _stray_tmp_path(cfg[key]):
            cfg[key] = fallback.get(key, cfg.get(key))
    return cfg


def _default_exclude_keywords():
    # Eglantine marks vendor-specific test builds in the release title/body
    # (e.g. "AMD Experimental Build"); hide the other vendor's builds.
    try:
        vendors = gpu_vendors()
    except Exception:
        return ""
    if "nvidia" in vendors and "amd" not in vendors:
        return "AMD Experimental"
    return ""


# Switch 2 title IDs (https://switchbrew.org/wiki/Switch_2:_Title_list/Games).
# Keys for best_for entries; names are display fallbacks when a project
# stores only the title_id.
GAMES = {
    "040074A01BF12000": "Donkey Kong Bananza",
    "040041E0212AE000": "Fast Fusion",
    "04007EF01F8AE000": "Fire Emblem: Fortune's Weave",
    "04003F40232DE000": "Hyrule Warriors: Age of Imprisonment",
    "040077201BE50000": "Kirby Air Riders",
    "0400C3F00006E000": "Mario Kart World",
    "0400D4201C6EA000": "Mario Tennis Fever",
    "04008E001F098000": "Nintendo Switch Sports Resort",
    "0100F43008C44000": "Pokémon Legends: Z-A",
    "0400A3A01EE4C000": "Pokémon Pokopia",
    "0400921025B32000": "Sonic Frontiers Definitive Edition",
    "0400380020056000": "Splatoon Raiders",
    "0400E88025B1A000": "Star Fox",
    "010015100B514000": "Super Mario Bros. Wonder",
    "0400245021D2C000": "Yoshi and the Mysterious Book",
    "0400486024DEC000": "Zelda: Ocarina of Time",
}


def normalize_title_id(raw):
    """Uppercase 16-hex-digit title ID, or None when invalid."""
    s = (raw or "").strip().upper()
    if s.startswith("0X"):
        s = s[2:]
    if len(s) == 16 and all(c in "0123456789ABCDEF" for c in s):
        return s
    return None


def game_catalog(cfg=None):
    """Shipped games, updated by the remote registry, plus the user's own.

    Precedence: shipped < remote < custom. The registry lets new games
    arrive without a rebuild; custom names always win.
    """
    catalog = dict(GAMES)
    for game in (cfg or {}).get("remote_games", []) or []:
        if isinstance(game, dict) and game.get("title_id") and game.get("name"):
            catalog[game["title_id"]] = game["name"]
    for game in (cfg or {}).get("custom_games", []) or []:
        if isinstance(game, dict) and normalize_title_id(game.get("title_id") or ""):
            tid = normalize_title_id(game["title_id"])
            catalog[tid] = (game.get("name") or "").strip() or catalog.get(tid, tid)
    return catalog


def merge_remote_games(cfg, remote):
    """Fold the remote game registry into cfg. Returns True if changed.

    Only well-formed entries (16-hex-digit title ID, non-empty name) are
    stored under cfg["remote_games"]; anything else is ignored. Curated
    best_for/tags and the user's custom games are never touched.
    """
    games = []
    seen = set()
    for game in remote or []:
        if not isinstance(game, dict):
            continue
        tid = normalize_title_id(game.get("title_id") or "")
        name = (game.get("name") or "").strip()
        if not tid or not name or tid in seen:
            continue
        seen.add(tid)
        games.append({"title_id": tid, "name": name})
    if cfg.get("remote_games", []) == games:
        return False
    cfg["remote_games"] = games
    save(cfg)
    return True


def effective_best_for(project):
    """Curated best_for plus the user's own entries (user note wins)."""
    merged = []
    seen = set()
    for item in (project.get("best_for") or []) + (project.get("user_best_for") or []):
        if not isinstance(item, dict):
            continue
        key = item.get("title_id") or item.get("name")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    # user entries override curated notes for the same game
    user_notes = {
        (b.get("title_id") or b.get("name")): b
        for b in (project.get("user_best_for") or [])
        if isinstance(b, dict)
    }
    return [user_notes.get(b.get("title_id") or b.get("name"), b) for b in merged]


def effective_tags(project):
    """Curated tags plus the user's own entries (user tooltip wins)."""
    merged = []
    seen = set()
    for tag in (project.get("tags") or []) + (project.get("user_tags") or []):
        if not isinstance(tag, dict):
            continue
        key = tag.get("id") or tag.get("label")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    user_tips = {
        (t.get("id") or t.get("label")): t for t in (project.get("user_tags") or []) if isinstance(t, dict)
    }
    return [user_tips.get(t.get("id") or t.get("label"), t) for t in merged]


def best_for_summary(project):
    """Short 'Best for: A, B' line for the Project row."""
    items = effective_best_for(project)
    names = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or GAMES.get(item.get("title_id", ""), "")
        else:
            name = GAMES.get(item, item)
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def project_tooltip(project, include_tags=True):
    """Multi-line hover text: PR title, per-game reasons, generic tags."""
    sections = []
    if project.get("is_pr"):
        title = (project.get("pr_title") or "").strip()
        if title and title != (project.get("name") or "").strip():
            sections.append(title)
    best = []
    for item in effective_best_for(project):
        if not isinstance(item, dict):
            continue
        name = item.get("name") or GAMES.get(item.get("title_id", ""), item.get("title_id", ""))
        if not name:
            continue
        note = (item.get("note") or "").strip()
        best.append(f"- {name}: {note}" if note else f"- {name}")
    if best:
        sections.append("Best for:\n" + "\n".join(best))
    tags = []
    for tag in effective_tags(project) if include_tags else []:
        if not isinstance(tag, dict):
            continue
        label = tag.get("label") or tag.get("id", "")
        if not label:
            continue
        tip = (tag.get("tooltip") or "").strip()
        tags.append(f"- {label}: {tip}" if tip else f"- {label}")
    if tags:
        sections.append("Tags:\n" + "\n".join(tags))
    confidence = (project.get("confidence") or "").strip()
    if confidence:
        sections.append(f"Confidence: {confidence}")
    return "\n\n".join(sections)


DEFAULT_PROJECTS = [
    {
        "id": "eglantine",
        "name": "Eglantine",
        "source_url": "https://github.com/Djihads80/Eglantine.git",
        "branch": "nx2",
        "releases_url": "https://api.github.com/repos/Djihads80/Eglantine/releases",
        "include_prerelease": True,
        "exclude_keywords": _default_exclude_keywords(),
        "icon_url": "https://github.com/Djihads80/Eglantine/raw/refs/heads/nx2/dist/Eglantine.png",
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "Default all-round pick; widest coverage",
            },
            {
                "title_id": "0400380020056000",
                "name": "Splatoon Raiders",
                "note": "Upstream baseline; variable performance",
            },
            {
                "title_id": "04003F40232DE000",
                "name": "Hyrule Warriors: Age of Imprisonment",
                "note": "Build 202+ fix set (46-49 FPS median reported)",
            },
        ],
        "tags": [
            {
                "id": "default",
                "label": "Default",
                "tooltip": "Central upstream; most tested, most forks rebase on it",
            },
        ],
    },
    {
        "id": "pokedinner",
        "name": "PokeDinner (Pokopia)",
        "source_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git",
        "branch": "master",
        "releases_url": "https://git.eden-emu.dev/api/v1/repos/Franseven/PokeDinner-a-Eglantine-Fork/releases",
        "include_prerelease": True,
        "icon_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/raw/branch/master/dist/Eglantine.png",
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "040074A01BF12000",
                "name": "Donkey Kong Bananza",
                "note": "2.5 with 30 FPS option (recent AMD result)",
            },
            {
                "title_id": "0400A3A01EE4C000",
                "name": "Pokemon Pokopia",
                "note": "Invisible-item and sky fixes",
            },
            {
                "title_id": "0400380020056000",
                "name": "Splatoon Raiders",
                "note": "Textbox, texture and HDR fixes",
            },
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "Listed as ingame",
            },
            {
                "title_id": "0400486024DEC000",
                "name": "Zelda: Ocarina of Time",
                "note": "Initial support",
            },
        ],
        "tags": [
            {
                "id": "lab",
                "label": "Compatibility lab",
                "tooltip": "Per-game fix builds; pick when a game boots better here",
            },
            {
                "id": "swap",
                "label": "Needs swap",
                "tooltip": "Author asks for 12 GB DRAM plus 32 GB pagefile/swap",
            },
        ],
    },
    {
        "id": "smiley",
        "name": "Eglantine (SmileyTatsu)",
        "source_url": "https://github.com/SmileyTatsu/Eglantine.git",
        "branch": "smileytatsu/dev-rebased",
        "releases_url": "https://api.github.com/repos/SmileyTatsu/Eglantine/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "Build 11/12; 45-50 FPS avg, near 60 docked on AMD",
            },
        ],
        "tags": [
            {
                "id": "mkw",
                "label": "MKW specialist",
                "tooltip": "Pipeline-cache changes and MKW frame-rate fix not all merged upstream",
            },
        ],
    },
    {
        "id": "orchid",
        "name": "Orchid (MythosOfs)",
        "source_url": "https://github.com/MythosOfs/Orchid.git",
        "branch": "main",
        "releases_url": "https://api.github.com/repos/MythosOfs/Orchid/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "0400486024DEC000",
                "name": "Zelda: Ocarina of Time",
                "note": "v0.7.4 exposure/fog/vegetation fixes; OoT perf unmeasured",
            },
            {
                "title_id": "0400D4201C6EA000",
                "name": "Mario Tennis Fever",
                "note": "60 FPS reported (5800X3D + RX 9070 XT)",
            },
            {
                "title_id": "010015100B514000",
                "name": "Super Mario Bros. Wonder",
                "note": "60 FPS reported",
            },
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "30-40 FPS with asset glitches",
            },
        ],
        "tags": [
            {
                "id": "amd",
                "label": "AMD",
                "tooltip": "AMD-focused shader/GPU fixes; developed on RX 9070 XT",
            },
        ],
    },
    {
        "id": "notsapinho",
        "name": "Eglantine (notsapinho)",
        "source_url": "https://github.com/notsapinho/Eglantine.git",
        "branch": "main",
        "releases_url": "https://api.github.com/repos/notsapinho/Eglantine/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "0400A3A01EE4C000",
                "name": "Pokemon Pokopia",
                "note": "Listed playable; 120fps mod, 80+ FPS on RTX 4060 Mobile",
            },
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "Listed as supported",
            },
            {
                "title_id": "0400380020056000",
                "name": "Splatoon Raiders",
                "note": "Listed as supported",
            },
        ],
        "tags": [
            {
                "id": "avx2",
                "label": "AVX2 required",
                "tooltip": "x86_64 builds need Haswell/Ryzen or newer",
            },
        ],
    },
    {
        "id": "lichii",
        "name": "Lichii (zortos293)",
        "source_url": "https://github.com/zortos293/Lichii.git",
        "branch": "nx2",
        "releases_url": "https://api.github.com/repos/zortos293/Lichii/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "community-reported",
        "best_for": [
            {
                "title_id": "0400486024DEC000",
                "name": "Zelda: Ocarina of Time",
                "note": "Build-80 playable with visual bugs; CPU-bound (20-60 FPS by CPU)",
            },
            {
                "title_id": "0400E88025B1A000",
                "name": "Star Fox",
                "note": "Community-reported game-patch build",
            },
            {
                "title_id": "0400D4201C6EA000",
                "name": "Mario Tennis Fever",
                "note": "Community-reported game-patch build",
            },
            {
                "title_id": "040074A01BF12000",
                "name": "Donkey Kong Bananza",
                "note": "Also attributed to this fork",
            },
        ],
        "tags": [
            {
                "id": "actions",
                "label": "Actions artifacts",
                "tooltip": "Newest builds are Actions artifacts (expire, need GitHub login); Releases lag behind",
            },
            {
                "id": "cpu",
                "label": "Fast CPU needed",
                "tooltip": "Single-core speed looks like the limit; try non-PGO on black screens",
            },
        ],
    },
    {
        "id": "droplet",
        "name": "Droplet (private online)",
        "source_url": "https://gitlab.com/seighttt/droplet.git",
        "branch": "main",
        "releases_url": "https://gitlab.com/api/v4/projects/seighttt%2Fdroplet/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "Private online via self-hosted Reservoir server",
            },
            {
                "title_id": "0400380020056000",
                "name": "Splatoon Raiders",
                "note": "Private online via self-hosted Reservoir server",
            },
        ],
        "tags": [
            {
                "id": "online",
                "label": "Private online",
                "tooltip": "Reservoir replaces Nintendo NPLN; emulators only, host your own server",
            },
            {
                "id": "arch",
                "label": "Arch binary",
                "tooltip": "Upstream Linux binary targets Arch; other distros build from source",
            },
        ],
    },
    {
        "id": "rdenno",
        "name": "Eglantine nx2-merged (rdenno)",
        "source_url": "https://github.com/rdenno/Eglantine.git",
        "branch": "nx2",
        "releases_url": "https://api.github.com/repos/rdenno/Eglantine/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "community-reported",
        "best_for": [
            {
                "title_id": "04003F40232DE000",
                "name": "Hyrule Warriors: Age of Imprisonment",
                "note": "Second build of the 202+ fix set; compare against upstream",
            },
        ],
        "tags": [],
    },
    {
        "id": "selevet",
        "name": "Selevet Eglantine",
        "source_url": "https://github.com/sylvieistaken/selevet-eglantine.git",
        "branch": "main",
        "releases_url": "https://api.github.com/repos/sylvieistaken/selevet-eglantine/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "040074A01BF12000",
                "name": "Donkey Kong Bananza",
                "note": "30 FPS cap and 60 smooth motion reported",
            },
        ],
        "tags": [
            {
                "id": "zen3",
                "label": "Zen 3 tuned",
                "tooltip": "PGO + ThinLTO build tuned for Zen 3, faster GPU mapping",
            },
        ],
    },
    {
        "id": "zendnevs",
        "name": "Eglantine ZendNevs",
        "source_url": "https://github.com/ZendNevs/Eglantine-ZendNevs.git",
        "branch": "main",
        "releases_url": "https://api.github.com/repos/ZendNevs/Eglantine-ZendNevs/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "documented",
        "best_for": [
            {
                "title_id": "0400C3F00006E000",
                "name": "Mario Kart World",
                "note": "Self-reported ~56 FPS avg with DynamicFPS",
            },
            {
                "title_id": "040074A01BF12000",
                "name": "Donkey Kong Bananza",
                "note": "Self-reported ~33 FPS avg; hitches up to 0.6 s",
            },
        ],
        "tags": [
            {
                "id": "framegen",
                "label": "Frame generation",
                "tooltip": "FSR 3 / LSFG frame generation plus DynamicFPS and 30 FPS lock",
            },
            {
                "id": "caution",
                "label": "Release-only",
                "tooltip": "2-commit repo, no license, self-reported numbers; independent reports favor SmileyTatsu/Selevet",
            },
        ],
    },
    {
        "id": "3060ti",
        "name": "Eglantine 3060Ti 16GB",
        "source_url": "https://github.com/astralbeexd/Eglantine-3060Ti-16GB.git",
        "branch": "main",
        "releases_url": "https://api.github.com/repos/astralbeexd/Eglantine-3060Ti-16GB/releases",
        "include_prerelease": True,
        "origin": "wichforks",
        "confidence": "unverified",
        "best_for": [],
        "tags": [
            {
                "id": "niche",
                "label": "RTX 3060 Ti 16GB",
                "tooltip": "Patch set plus binary for one GPU; not a full source tree, unverified",
            },
        ],
    },
]


def _data_home():
    return _base_home() / ".local" / "share" / "eglantine-launcher"


def default_config():
    return {
        "project": "eglantine",
        "mode": "release",
        # Windows users get downloads only by default; the toolchain list
        # scares people off. They can opt into source builds in Settings.
        # Source builds are opt-in: the toolchain list scares people off.
        # Turning the Settings switch on runs a dependency check first.
        "source_enabled": False,
        "pgo": True,
        "install_dir": str(_data_home()),
        "source_dir": str(_data_home() / "src"),
        "last_release_tag": None,
        "last_source_commit": None,
        "downloaded_releases": [],
        "built_commits": [],
        "hidden_sources": [],
        # Window behavior. None means "ask on first use, then remember".
        # minimize_on_launch: "taskbar" | "tray" | "none" | None
        # close_behavior: "quit" | "tray" | None
        "minimize_on_launch": None,
        "close_behavior": None,
        # Main window size; updated whenever the window closes or hides.
        # Matches the documented screenshots (630x450: the 1px
        # backdrop frame plus the 4px theme CSD frame are cropped
        # in scripts/shot_alpha.py).
        "window_width": 640,
        "window_height": 460,
        # The "Running in the background" notification only shows once.
        "tray_notice_shown": False,
        "projects": copy.deepcopy(DEFAULT_PROJECTS),
    }


def _default_by_id():
    return {p.get("id"): p for p in DEFAULT_PROJECTS}


# Curated fields stamped from shipped defaults. name/source_url/branch/
# releases_url/icon_url track upstream; best_for/tags/confidence/origin are
# our curation. include_prerelease and exclude_keywords stay user-controlled.
MANAGED_FIELDS = (
    "name",
    "source_url",
    "branch",
    "releases_url",
    "icon_url",
    "origin",
    "confidence",
    "best_for",
    "tags",
)


def ensure_default_sources(cfg):
    """Merge new shipped defaults in on app start. Returns True if changed.

    Missing default ids are added (unless the user hid them). Managed
    entries (origin == wichforks) get curated fields refreshed.
    Pre-existing entries from before origins existed are adopted by filling
    only what is missing, so earlier user edits to name/source/branch
    survive; the next start then refreshes them normally. Entries the user
    added manually are stamped manual and never touched.
    """
    hidden = set(cfg.get("hidden_sources", []))
    by_id = {p.get("id"): p for p in cfg.get("projects", [])}
    defaults = _default_by_id()
    changed = False
    for pid, default in defaults.items():
        if pid in hidden:
            continue
        if pid not in by_id:
            cfg.setdefault("projects", []).append(copy.deepcopy(default))
            changed = True
            continue
        p = by_id[pid]
        if p.get("is_pr"):
            continue
        if p.get("origin") == "manual":
            continue
        if not p.get("origin"):
            for field in MANAGED_FIELDS:
                if field not in p and field in default:
                    p[field] = copy.deepcopy(default[field])
                    changed = True
            if not p.get("origin"):
                p["origin"] = default.get("origin", "wichforks")
                changed = True
            continue
        for field in MANAGED_FIELDS:
            if field in default and p.get(field) != default[field]:
                p[field] = copy.deepcopy(default[field])
                changed = True
    known = set(defaults)
    for p in cfg.get("projects", []):
        if p.get("is_pr") or p.get("origin") or p.get("id") in known or p.get("remote"):
            continue
        p["origin"] = "manual"
        changed = True
    return changed


def load():
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        cfg = default_config()
        cfg[_CONFIG_PATH_KEY] = str(path)
        save(cfg)
        return cfg
    try:
        cfg = {**default_config(), **json.loads(path.read_text())}
        # A config remembers the file it came from, so a worker thread that
        # saves after the process environment changed (tests redirecting
        # HOME/XDG) writes back where it read from, never to the real file.
        cfg.pop(_CONFIG_PATH_KEY, None)
        cfg[_CONFIG_PATH_KEY] = str(path)
        # Heal a file a stale process clobbered with tmp paths (see save()).
        _scrub_paths(cfg, default_config())
        if not cfg.get("projects"):
            cfg["projects"] = copy.deepcopy(DEFAULT_PROJECTS)
            save(cfg)
        else:
            # backfill fields for built-in sources (e.g. PokeDinner gained Forgejo releases)
            changed = False
            for p in cfg["projects"]:
                if not p.get("releases_url"):
                    for default in DEFAULT_PROJECTS:
                        if default["id"] == p.get("id") and default.get("releases_url"):
                            p["releases_url"] = default["releases_url"]
                            changed = True
                if "exclude_keywords" not in p and p.get("id") == "eglantine":
                    p["exclude_keywords"] = _default_exclude_keywords()
                    changed = True
            try:
                if ensure_default_sources(cfg):
                    changed = True
            except Exception:
                pass
            # seed history lists from legacy single-value keys
            if not cfg.get("downloaded_releases") and cfg.get("last_release_tag"):
                cfg["downloaded_releases"] = [cfg["last_release_tag"]]
                changed = True
            if not cfg.get("built_commits") and cfg.get("last_source_commit"):
                cfg["built_commits"] = [cfg["last_source_commit"]]
                changed = True
            if changed:
                save(cfg)
        return cfg
    except Exception:
        cfg = default_config()
        cfg[_CONFIG_PATH_KEY] = str(path)
        return cfg


# Fields the remote registry is allowed to update on entries it manages.
# User-tuned fields (include_prerelease, exclude_keywords, branch overrides
# are upstream-managed though) are never clobbered: include_prerelease and
# exclude_keywords stay user-controlled.
REMOTE_FIELDS = (
    "name",
    "source_url",
    "branch",
    "releases_url",
    "icon_url",
    "origin",
    "confidence",
    "best_for",
    "tags",
)


def merge_remote_sources(cfg, remote):
    """Fold the remote registry into cfg. Returns True if anything changed.

    New ids are added (unless hidden by the user); known remote entries get
    their upstream fields refreshed. User-tuned flags are left alone.
    """
    hidden = set(cfg.get("hidden_sources", []))
    by_id = {p.get("id"): p for p in cfg.get("projects", [])}
    changed = False
    for entry in remote:
        pid = entry.get("id")
        if not pid or pid in hidden:
            continue
        if pid in by_id:
            p = by_id[pid]
            if p.get("is_pr"):
                continue
            if p.get("origin") == "manual":
                continue
            for field in REMOTE_FIELDS:
                if entry.get(field) and p.get(field) != entry[field]:
                    p[field] = copy.deepcopy(entry[field])
                    changed = True
            if not p.get("remote"):
                p["remote"] = True
                changed = True
        else:
            new = {
                k: copy.deepcopy(v)
                for k, v in entry.items()
                if k in ("id", *REMOTE_FIELDS, "include_prerelease")
            }
            new.setdefault("include_prerelease", True)
            new["remote"] = True
            cfg.setdefault("projects", []).append(new)
            changed = True
    if changed:
        save(cfg)
    return changed


def _union_projects(disk_projects, mem_projects, tombstoned):
    """Merge project lists by id so a stale writer can never drop entries.

    Entries only on disk (added by another process) are kept; the in-memory
    copy wins field-by-field for shared ids (it holds the saver's intent).
    Explicitly deleted ids stay out. Re-added ids clear their tombstone.
    """
    by_id = {}
    for p in disk_projects or []:
        if isinstance(p, dict) and p.get("id"):
            by_id[p["id"]] = p
    for p in mem_projects or []:
        if isinstance(p, dict) and p.get("id"):
            by_id[p["id"]] = p
    for tid in tombstoned or []:
        by_id.pop(tid, None)
    return list(by_id.values())


def save(cfg):
    # Merge over defaults and the file on disk so a partial or stale dict
    # from any caller can never wipe out keys it doesn't know about (the
    # GitHub token, a setting saved by another window, ...).
    path = Path(cfg.get(_CONFIG_PATH_KEY) or config_file())
    try:
        on_disk = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        on_disk = {}
    merged = {**default_config(), **on_disk, **cfg}
    tombstoned = set(cfg.get("deleted_projects") or []) | set(on_disk.get("deleted_projects") or [])
    mem_ids = {p.get("id") for p in merged.get("projects", []) if isinstance(p, dict)}
    # Only the saver's own tombstone plus presence counts as a deliberate
    # re-add. A stale copy merely holding the id must not clear a tombstone
    # written elsewhere after it loaded.
    tombstoned -= {pid for pid in mem_ids if pid in set(cfg.get("deleted_projects") or [])}
    merged["projects"] = _union_projects(on_disk.get("projects"), merged.get("projects"), tombstoned)
    merged["deleted_projects"] = sorted(tombstoned)[-200:]
    # A stale in-memory dict (long-running process, pre-fix file) must not
    # push tmp-tree paths back into the file: prefer what is on disk, else
    # defaults. The same scrub runs on load, so both directions are covered.
    _scrub_paths(merged, {**default_config(), **on_disk})
    merged.pop(_CONFIG_PATH_KEY, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2))
