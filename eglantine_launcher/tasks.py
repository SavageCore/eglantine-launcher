import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from .config import config_dir as launcher_config_dir
from .config import save as save_config

_token_rejected = False

# Central API throttle: every GitHub/Forgejo JSON call goes through
# api_get_json(), which spaces requests per host so parallel bursts
# (e.g. per-branch lookups) cannot spam the API.
_api_lock = threading.Lock()
_last_api_time: dict[str, float] = {}
_API_MIN_INTERVAL_DEFAULT = 0.4
# Server-side rate limits must fail fast: callers already fall back to
# cached data, and sleeping here would stall workers (and the suite)
# for up to a minute per call. Only an explicit Retry-After is honored,
# bounded so one unlucky response cannot freeze a refresh.
_API_RATE_LIMIT_MAX_WAIT = 10.0


class BuildCancelled(RuntimeError):
    """Raised when a source build is stopped via the Stop button."""


def _api_headers(cfg=None):
    headers = {"User-Agent": "eglantine-launcher"}
    if not _token_rejected:
        token = os.environ.get("GITHUB_TOKEN") or (cfg or {}).get("github_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_host(url):
    """Hostname bucket for throttling; unknown URLs share a default bucket."""
    try:
        host = urllib.parse.urlsplit(url).hostname
        return host.lower() if host else "default"
    except Exception:
        return "default"


def _api_min_interval():
    try:
        return max(0.0, float(os.environ.get("EGLANTINE_API_MIN_INTERVAL", _API_MIN_INTERVAL_DEFAULT)))
    except Exception:
        return _API_MIN_INTERVAL_DEFAULT


def _reset_api_throttle():
    """Clear throttle state (tests)."""
    with _api_lock:
        _last_api_time.clear()


def _throttle_api(url):
    """Sleep so calls to the same host are spaced by the min interval."""
    interval = _api_min_interval()
    if interval <= 0:
        return
    host = _api_host(url)
    with _api_lock:
        now = time.monotonic()
        wait = interval - (now - _last_api_time.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_api_time[host] = now


def _http_error_headers(e):
    try:
        headers = getattr(e, "headers", None) or getattr(e, "hdrs", None)
        if headers is None:
            return {}
        if hasattr(headers, "items"):
            return {str(k).lower(): str(v) for k, v in headers.items()}
        return {}
    except Exception:
        return {}


def _rate_limit_delay(e):
    """Seconds to wait before retrying a 403/429, or 0 to fail fast.

    Only an explicit Retry-After (or a bare 429) waits, and only briefly.
    An exhausted primary budget (X-RateLimit-Reset, up to an hour away)
    never sleeps: callers fall back to cached data instead.
    """
    headers = _http_error_headers(e)
    try:
        retry_after = float(headers.get("retry-after", ""))
        if retry_after > 0:
            return min(retry_after, _API_RATE_LIMIT_MAX_WAIT)
    except Exception:
        pass
    if getattr(e, "code", None) == 429:
        return 2.0
    return 0.0


def _open_json(req, timeout):
    _throttle_api(req.full_url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_get_json(url, cfg=None, timeout=30):
    """GET JSON via the central throttled path.

    All GitHub/Forgejo API reads must go through here so bursts are
    spaced per host. Retries once after a 429/rate-limit response,
    then falls back to anonymous on a rejected token (401).
    """
    global _token_rejected
    req = urllib.request.Request(url, headers=_api_headers(cfg))
    try:
        try:
            return _open_json(req, timeout)
        except urllib.error.HTTPError as e:
            if getattr(e, "code", None) in (403, 429):
                wait = _rate_limit_delay(e)
                if wait > 0:
                    time.sleep(wait)
                    return _open_json(req, timeout)
            raise
    except urllib.error.HTTPError as e:
        if e.code != 401 or _token_rejected or "Authorization" not in dict(req.header_items()):
            raise
        _token_rejected = True
        # The token in use was rejected: flag it on real configs so
        # Settings can warn instead of re-verifying on every open.
        # Env tokens take precedence over the saved one, so only flag
        # the saved token when no env token was in play. The "projects"
        # check keeps throwaway dicts (e.g. verification probes) from
        # ever touching the config file.
        try:
            if (
                isinstance(cfg, dict)
                and "projects" in cfg
                and cfg.get("github_token")
                and not os.environ.get("GITHUB_TOKEN")
            ):
                cfg["github_token_rejected"] = True
                save_config(cfg)
        except Exception:
            pass
        req = urllib.request.Request(url, headers=_api_headers(cfg))
        return _open_json(req, timeout)


def token_accepted():
    """Call after a token verifies, so it is used again after a 401 fallback."""
    global _token_rejected
    _token_rejected = False


def project_info(cfg):
    pid = cfg.get("project")
    for p in cfg.get("projects", []):
        if p.get("id") == pid:
            return p
    projects = cfg.get("projects", [])
    return projects[0] if projects else {}


def is_pr_project(info):
    """True for one-off PR entries (stored as projects, built from source only)."""
    return bool((info or {}).get("is_pr"))


def get_project_last_built(cfg, info=None):
    """Last built commit for a project (per-project, with global fallback).

    Older configs only have the global last_source_commit; new builds record
    both, so the per-project value wins when present.
    """
    info = info if info is not None else project_info(cfg)
    if (info or {}).get("last_built_commit"):
        return info.get("last_built_commit")
    return cfg.get("last_source_commit")


def record_source_build(cfg, commit):
    """Record a successful source build globally and on the current project."""
    cfg["last_source_commit"] = commit
    _record(cfg, "built_commits", commit)
    pid = cfg.get("project")
    for p in cfg.get("projects", []):
        if p.get("id") == pid:
            p["last_built_commit"] = commit
            break
    save_config(cfg)


def is_windows():
    return sys.platform.startswith("win")


def arch():
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return m


def latest_release(cfg):
    tag, assets = None, []
    releases = list_releases(cfg)
    if releases:
        tag = releases[0].get("tag_name")
        assets = releases[0].get("assets", [])
    return tag, assets


def _releases_cache_path(info):
    pid = info.get("id") or "default"
    safe = "".join(c if c.isalnum() else "-" for c in pid)
    d = Path.home() / ".cache" / "eglantine-launcher"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"releases-{safe}.json"


def _normalize_gitlab_releases(data):
    """Map GitLab v4 release rows to the GitHub/Forgejo shape we use."""
    out = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        assets = []
        links = ((r.get("assets") or {}) if isinstance(r.get("assets"), dict) else {}).get("links", [])
        for link in links if isinstance(links, list) else []:
            if not isinstance(link, dict):
                continue
            link_url = link.get("direct_asset_url") or link.get("url")
            name = link.get("name") or (link_url or "").rsplit("/", 1)[-1]
            if link_url:
                assets.append({"name": name, "browser_download_url": link_url})
        out.append(
            {
                "tag_name": r.get("tag_name"),
                "name": r.get("name") or r.get("tag_name"),
                "body": r.get("description") or "",
                "prerelease": bool(r.get("upcoming_release")),
                "assets": assets,
            }
        )
    return out


def list_releases(cfg, limit=15):
    info = project_info(cfg)
    url = info.get("releases_url")
    if not url:
        return []
    try:
        param = "limit" if "/api/v1/" in url else "per_page"
        data = api_get_json(f"{url}?{param}={limit}", cfg, timeout=30)
        if not isinstance(data, list):
            data = [data]
        if "gitlab.com/api/v4" in url:
            data = _normalize_gitlab_releases(data)
        _releases_cache_path(info).write_text(json.dumps(data[:limit]))
    except Exception:
        try:
            data = json.loads(_releases_cache_path(info).read_text())
        except Exception:
            raise
    if not info.get("include_prerelease", True):
        data = [r for r in data if not r.get("prerelease")]
    keywords = [k.strip().lower() for k in (info.get("exclude_keywords") or "").split(",") if k.strip()]
    if keywords:

        def excluded(r):
            hay = " ".join([r.get("tag_name") or "", r.get("name") or "", r.get("body") or ""]).lower()
            return any(k in hay for k in keywords)

        data = [r for r in data if not excluded(r)]
    return data


def fetch_release(cfg, tag):
    info = project_info(cfg)
    url = info.get("releases_url")
    if not url:
        return None, []
    if tag is None:
        releases = list_releases(cfg, 1)
        if releases:
            return releases[0].get("tag_name"), releases[0].get("assets", [])
        return None, []
    # find in list first
    for rel in list_releases(cfg, 50):
        if rel.get("tag_name") == tag:
            return rel.get("tag_name"), rel.get("assets", [])
    # fallback direct
    direct = url.rsplit("/releases", 1)[0] + "/releases/tags/" + tag
    data = api_get_json(direct, cfg, timeout=30)
    return data.get("tag_name"), data.get("assets", [])


def parse_repo(source_url):
    """Return (host, owner, repo) from a git URL, or (None, None, None)."""
    import re

    s = (source_url or "").strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+)", s, re.IGNORECASE)
    if m:
        return "github", m.group(1), m.group(2)
    m = re.search(r"git\.eden-emu\.dev[:/]([^/]+)/([^/]+)", s, re.IGNORECASE)
    if m:
        return "forgejo", m.group(1), m.group(2)
    # generic https://host/owner/repo
    m = re.search(r"https?://([^/]+)/([^/]+)/([^/]+)", s)
    if m:
        return m.group(1).lower(), m.group(2), m.group(3)
    return None, None, None


def parse_pr_url(pr_url):
    """Parse a PR web URL into {host_kind, web_host, scheme, owner, repo, number}.

    Accepts GitHub /pull/N and Forgejo /pull/N or /pulls/N web URLs, with
    optional trailing path (/files, /commits), query or fragment. Returns
    None when the URL is not a recognized PR link.
    """
    import re

    s = (pr_url or "").strip()
    if not s:
        return None
    # strip query/fragment for matching, keep scheme/host from the URL itself
    bare = re.split(r"[?#]", s, maxsplit=1)[0].rstrip("/")
    m = re.match(
        r"(https?)://([^/]+)/([^/]+)/([^/]+?)/(?:pull|pulls)/(\d+)(?:/.*)?$",
        bare,
        re.IGNORECASE,
    )
    if not m:
        return None
    scheme, host, owner, repo, number = (
        m.group(1).lower(),
        m.group(2).lower(),
        m.group(3),
        m.group(4),
        m.group(5),
    )
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo or not number.isdigit():
        return None
    host_kind = "github" if host == "github.com" else "forgejo"
    if host_kind == "github":
        canonical = f"https://github.com/{owner}/{repo}/pull/{int(number)}"
    else:
        canonical = f"{scheme}://{host}/{owner}/{repo}/pulls/{int(number)}"
    return {
        "host_kind": host_kind,
        "web_host": host,
        "scheme": scheme,
        "owner": owner,
        "repo": repo,
        "number": int(number),
        "canonical": canonical,
    }


def pr_api_url(parsed):
    """API URL for PR details, or None when it cannot be derived."""
    if not parsed:
        return None
    owner, repo, number = parsed["owner"], parsed["repo"], parsed["number"]
    if parsed["host_kind"] == "github":
        return f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    return f"{parsed['scheme']}://{parsed['web_host']}/api/v1/repos/{owner}/{repo}/pulls/{number}"


def pr_default_name(details=None, number=None, owner=None, repo=None):
    """Default display name for a PR build: '{title} (#N)'."""
    title = (details or {}).get("title", "") if isinstance(details, dict) else ""
    number = number if number is not None else (details or {}).get("number")
    title = (title or "").strip()
    if title and number:
        return f"{title} (#{number})"
    if title:
        return title
    if owner and repo and number:
        return f"{owner}/{repo} #{number}"
    if number:
        return f"PR #{number}"
    return "PR build"


def fetch_pr_info(pr_url, cfg=None, timeout=20):
    """Fetch PR details from the GitHub or Forgejo API.

    Returns {title, state, merged, head_sha, head_ref, head_clone_url,
    base_clone_url, base_branch, number, owner, repo, host_kind, web_host,
    pr_url, api_url}. state is one of 'open', 'closed', 'merged'.
    Raises RuntimeError for bad URLs and propagates API errors.
    """
    parsed = parse_pr_url(pr_url)
    if parsed is None:
        raise RuntimeError("That does not look like a PR link (expected .../pull/NNN).")
    cache_key = parsed.get("canonical") or (pr_url or "").strip()
    hit = _lookup_cache_get(_pr_info_cache, cache_key)
    if hit is not None:
        return dict(hit)
    url = pr_api_url(parsed)
    data = api_get_json(url, cfg, timeout=timeout)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected PR response from the API.")
    title = data.get("title") or ""
    raw_state = str(data.get("state") or "").lower()
    merged = bool(data.get("merged") or data.get("merged_at"))
    if merged:
        state = "merged"
    elif raw_state in ("open", "closed", "merged"):
        state = raw_state
    else:
        state = raw_state or "open"
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    head_sha = head.get("sha") or data.get("head_sha") or ""
    head_ref = head.get("ref") or head.get("label") or ""
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    head_clone = head_repo.get("clone_url") or head_repo.get("ssh_url") or ""
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    base_clone = (
        base_repo.get("clone_url")
        or f"{parsed['scheme']}://{parsed['web_host']}/{parsed['owner']}/{parsed['repo']}.git"
    )
    base_branch = base.get("ref") or base.get("label") or ""
    result = {
        "title": title if isinstance(title, str) else "",
        "state": state,
        "merged": merged,
        "head_sha": head_sha if isinstance(head_sha, str) else "",
        "head_ref": head_ref if isinstance(head_ref, str) else "",
        "head_clone_url": head_clone if isinstance(head_clone, str) else "",
        "base_clone_url": base_clone if isinstance(base_clone, str) else "",
        "base_branch": base_branch if isinstance(base_branch, str) else "",
        "number": parsed["number"],
        "owner": parsed["owner"],
        "repo": parsed["repo"],
        "host_kind": parsed["host_kind"],
        "web_host": parsed["web_host"],
        "pr_url": parsed["canonical"],
        "api_url": url,
    }
    _lookup_cache_set(_pr_info_cache, cache_key, dict(result), _PR_INFO_TTL)
    return result


def make_pr_project(name, pr_url, details=None, existing=None):
    """Build a project dict for a one-off PR build (source-only).

    existing preserves the id/last_built_commit when editing. details is the
    fetch_pr_info() result used for base URL, title, state and head SHA.
    """
    import uuid

    details = details if isinstance(details, dict) else {}
    parsed = parse_pr_url(pr_url)
    if parsed is None:
        raise RuntimeError("That does not look like a PR link (expected .../pull/NNN).")
    number = parsed["number"]
    base_clone = details.get("base_clone_url") or (
        f"{parsed['scheme']}://{parsed['web_host']}/{parsed['owner']}/{parsed['repo']}.git"
    )
    title = (details.get("title") or "").strip()
    if existing and existing.get("id"):
        pid = existing["id"]
    else:
        slug = "".join(c.lower() if c.isalnum() else "-" for c in parsed["repo"]).strip("-")
        pid = f"pr-{slug}-{number}-{uuid.uuid4().hex[:6]}"
    project = {
        "id": pid,
        "name": (name or "").strip() or pr_default_name(details, number, parsed["owner"], parsed["repo"]),
        "source_url": base_clone,
        "branch": f"pr-{number}",
        "releases_url": None,
        "include_prerelease": True,
        "is_pr": True,
        "pr_url": parsed["canonical"],
        "pr_number": number,
        "pr_owner": parsed["owner"],
        "pr_repo": parsed["repo"],
        "pr_host": parsed["host_kind"],
        "pr_title": title,
        "pr_state": details.get("state") or "open",
        "pr_head_sha": details.get("head_sha") or "",
    }
    if existing and existing.get("last_built_commit"):
        project["last_built_commit"] = existing["last_built_commit"]
    if existing and existing.get("config_key"):
        project["config_key"] = existing["config_key"]
    if existing and existing.get("config_seeded"):
        project["config_seeded"] = True
    if isinstance(existing, dict):
        # Tags edited in the PR dialog survive re-saves/refreshes.
        for key in ("best_for", "tags"):
            items = existing.get(key)
            if items and key not in project:
                project[key] = [dict(i) if isinstance(i, dict) else i for i in items]
    return project


def refresh_pr_project(project, details):
    """Fold fresh fetch_pr_info() data into a PR project dict (in place)."""
    if not isinstance(details, dict):
        return project
    if details.get("base_clone_url"):
        project["source_url"] = details["base_clone_url"]
    if details.get("title"):
        project["pr_title"] = details["title"]
    if details.get("state"):
        project["pr_state"] = details["state"]
    if details.get("head_sha"):
        project["pr_head_sha"] = details["head_sha"]
    return project


def pr_version_entries(title, head_sha, state="", have=None, installed_sha=None, installed_present=False):
    """Version-dropdown rows for a PR head: (labels, shas, subs, latest_sha)."""
    have = set(have or [])
    head_sha = head_sha or ""
    if not head_sha:
        return [], [], [], None
    tick = "✓ " if head_sha in have else ""
    short = head_sha[:7]
    first = (title or "").strip().splitlines()[0] if (title or "").strip() else ""
    state_tag = f" ({state})" if state else ""
    labels = [f"{tick}{short} {first[:70]}{state_tag}".rstrip()]
    subs = [f"{head_sha}\n{(title or '').strip()}" if (title or "").strip() else head_sha]
    shas = [head_sha]
    if installed_sha and installed_sha not in shas and installed_present:
        shas.append(installed_sha)
        labels.append(f"✓ {installed_sha[:7]} (installed build)")
        subs.append(f"{installed_sha}\nCurrently installed build")
    return labels, shas, subs, head_sha


def _forgejo_api_base(source_url):
    """Return the Forgejo API base (scheme://host/api/v1) for a repo URL.

    The host always comes from the URL itself. Unparseable URLs return
    None and callers must skip the API call. Never guess a host.
    """
    s = (source_url or "").strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    try:
        parts = urllib.parse.urlsplit(s)
    except Exception:
        parts = None
    if parts is not None and parts.scheme in ("http", "https") and parts.hostname:
        host = parts.hostname.lower()
        if parts.port:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme}://{host}/api/v1"
    # scp-style: [user@]host:owner/repo
    tail = s.rsplit("@", 1)[-1]
    host, sep, path = tail.partition(":")
    if sep and host and "/" in path and " " not in host:
        return f"https://{host.lower()}/api/v1"
    return None


def infer_releases_url(source_url):
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return None
    if host == "github":
        return f"https://api.github.com/repos/{owner}/{repo}/releases"
    base = _forgejo_api_base(source_url)
    if base is None:
        return None
    return f"{base}/repos/{owner}/{repo}/releases"


# Short-lived in-memory caches for repo lookups. The add-source flow,
# the source editor, and background refreshes all ask for the same
# repo info / branches / PR data in bursts; without caching one dialog
# costs several identical API calls and a retry costs them again.
# Failures are never cached so the next attempt still tries the network.
_lookup_lock = threading.Lock()
_repo_info_cache: dict = {}
_branches_cache: dict = {}
_branch_dates_cache: dict = {}
_pr_info_cache: dict = {}
_remote_registry_cache: dict = {}
_REPO_INFO_TTL = 300.0
_BRANCHES_TTL = 300.0
_BRANCH_DATES_TTL = 600.0
_PR_INFO_TTL = 120.0
_REMOTE_REGISTRY_TTL = 3600.0
_ADD_LOOKUP_TTL = 300.0
_add_lookup_cache: dict = {}


def _lookup_cache_get(cache, key):
    try:
        with _lookup_lock:
            entry = cache.get(key)
        if entry is None:
            return None
        expires, value = entry
        if time.monotonic() >= expires:
            with _lookup_lock:
                cache.pop(key, None)
            return None
        return value
    except Exception:
        return None


def _lookup_cache_set(cache, key, value, ttl):
    try:
        with _lookup_lock:
            cache[key] = (time.monotonic() + max(0.0, float(ttl)), value)
    except Exception:
        pass


def clear_lookup_caches():
    """Drop all in-memory API caches (tests)."""
    with _lookup_lock:
        for cache in (
            _repo_info_cache,
            _branches_cache,
            _branch_dates_cache,
            _pr_info_cache,
            _remote_registry_cache,
            _add_lookup_cache,
        ):
            cache.clear()


def _repo_cache_key(source_url):
    """Normalized cache key for a repo URL, or None when unparseable."""
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return None
    host_norm = (host or "").lower()
    if host_norm == "github":
        return ("github", owner.lower(), repo.lower())
    base = _forgejo_api_base(source_url)
    return (host_norm, owner.lower(), repo.lower(), base or "")


def fetch_repo_info(source_url, cfg=None):
    """Return raw repo dict for a github or forgejo URL, or {} on failure."""
    host, owner, repo = parse_repo(source_url)
    if not repo:
        return {}
    key = _repo_cache_key(source_url)
    if key is not None:
        hit = _lookup_cache_get(_repo_info_cache, key)
        if hit is not None:
            return dict(hit)
    if host == "github":
        url = f"https://api.github.com/repos/{owner}/{repo}"
    else:
        base = _forgejo_api_base(source_url)
        if base is None:
            return {}
        url = f"{base}/repos/{owner}/{repo}"
    try:
        data = api_get_json(url, cfg, timeout=20)
        result = data if isinstance(data, dict) else {}
    except Exception:
        return {}
    if not result:
        return {}
    if key is not None:
        _lookup_cache_set(_repo_info_cache, key, dict(result), _REPO_INFO_TTL)
    return result


def get_default_branch(source_url, cfg=None):
    """Return the repo default branch name, or None if unknown."""
    info = fetch_repo_info(source_url, cfg)
    default = info.get("default_branch") if isinstance(info, dict) else None
    return default if isinstance(default, str) and default else None


def _parse_branch_timestamp(value):
    """Parse an ISO-8601 commit timestamp, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def sort_branch_names(names, dates=None, default=None):
    """Default branch first, then newest commit first, then API order.

    names: branch names in API order. dates: name -> datetime or None.
    Undated branches keep their relative API order after dated ones.
    """
    dates = dates or {}
    seen = set()
    unique = []
    for n in names or []:
        if n and n not in seen:
            seen.add(n)
            unique.append(n)
    ordered = []
    rest = []
    for n in unique:
        if default and n == default:
            continue
        rest.append(n)
    dated = [n for n in rest if dates.get(n) is not None]
    undated = [n for n in rest if dates.get(n) is None]
    dated.sort(key=lambda n: (dates[n], n.lower()), reverse=True)
    if default and default in unique:
        ordered.append(default)
    ordered.extend(dated)
    ordered.extend(undated)
    return ordered


def fetch_github_branch_dates(source_url, branches, cfg=None, max_workers=4, limit=500):
    """Map branch name -> commit datetime for github repos.

    One small commit lookup per branch, all via the central throttled
    api_get_json() so the burst is spaced per host. Failures are
    skipped so callers always get a partial dict, never an exception.
    Results are cached per repo+branch set; failures are not cached.
    """
    host, owner, repo = parse_repo(source_url)
    if host != "github" or not owner or not repo:
        return {}
    names = [b for b in (branches or []) if b][: max(int(limit or 500), 1)]
    if not names:
        return {}
    key = _repo_cache_key(source_url)
    cache_key = (key, tuple(sorted(names))) if key is not None else None
    if cache_key is not None:
        hit = _lookup_cache_get(_branch_dates_cache, cache_key)
        if hit is not None:
            return dict(hit)

    def _one(branch):
        try:
            sha = urllib.parse.quote(branch, safe="")
            url = f"https://api.github.com/repos/{owner}/{repo}/commits?sha={sha}&per_page=1"
            data = api_get_json(url, cfg, timeout=10)
            first = data[0] if isinstance(data, list) and data else None
            commit = (first.get("commit") or {}) if isinstance(first, dict) else {}
            for section in ("committer", "author"):
                section_data = commit.get(section) or {}
                dt = _parse_branch_timestamp(section_data.get("date"))
                if dt is not None:
                    return branch, dt
        except Exception:
            pass
        return branch, None

    dates = {}
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for branch, dt in pool.map(_one, names):
                if dt is not None:
                    dates[branch] = dt
    except Exception:
        pass
    if dates and cache_key is not None:
        _lookup_cache_set(_branch_dates_cache, cache_key, dict(dates), _BRANCH_DATES_TTL)
    return dates


def _fetch_all_pages(base_url, cfg, page_size=100, max_pages=10, timeout=20):
    """Follow page=1..N until a short page; return concatenated list items."""
    items = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in base_url else "?"
        try:
            data = api_get_json(f"{base_url}{sep}page={page}", cfg, timeout=timeout)
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        items.extend(b for b in data if isinstance(b, dict))
        if len(data) < page_size:
            break
    return items


def should_fetch_branch_dates(source_url, branches, cfg=None):
    """Gate for the per-branch GitHub commit-date lookup.

    That lookup costs one API call per branch, so without a token it
    would burn most of the 60/hour anonymous budget on branchy repos.
    Fetch when authenticated, or when the list is small enough to be cheap.
    """
    if parse_repo(source_url)[0] != "github":
        return False
    if (cfg or {}).get("github_token") or os.environ.get("GITHUB_TOKEN"):
        return True
    return len(branches or []) <= 30


def list_branches(source_url, limit=100, cfg=None):
    """Return branch names, default first then newest commit first.

    Forgejo timestamps come embedded in the branch list, so sorting is
    free. GitHub branch entries carry no date, so the initial return is
    default-first in API order; call fetch_github_branch_dates() in the
    background and re-sort with sort_branch_names() to match the web UI.
    Successful results are cached briefly; failures are not.
    """
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return []
    page_size = min(max(int(limit or 100), 1), 100)
    key = _repo_cache_key(source_url)
    cache_key = (key, page_size) if key is not None else None
    if cache_key is not None:
        hit = _lookup_cache_get(_branches_cache, cache_key)
        if hit is not None:
            return list(hit)
    if host == "github":
        url = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page={page_size}"
    else:
        base = _forgejo_api_base(source_url)
        if base is None:
            return []
        url = f"{base}/repos/{owner}/{repo}/branches?limit={page_size}"
    data = _fetch_all_pages(url, cfg, page_size=page_size)
    names = []
    dates = {}
    for b in data:
        n = b.get("name")
        if not n:
            continue
        names.append(n)
        commit = b.get("commit") or {}
        if isinstance(commit, dict):
            dt = _parse_branch_timestamp(commit.get("timestamp") or commit.get("created"))
            if dt is not None:
                dates[n] = dt
    try:
        default = get_default_branch(source_url, cfg)
    except Exception:
        default = None
    ordered = sort_branch_names(names, dates, default)
    if ordered and cache_key is not None:
        _lookup_cache_set(_branches_cache, cache_key, list(ordered), _BRANCHES_TTL)
    return ordered


def fetch_repo_name(source_url, cfg=None):
    _, owner, repo = parse_repo(source_url)
    if not repo:
        return ""
    info = fetch_repo_info(source_url, cfg)
    if not info:
        name = repo
    else:
        raw = info.get("name")
        name = raw if isinstance(raw, str) and raw else repo
    if name.strip().lower() == "eglantine" and (owner or "").strip():
        return owner.strip()
    return name


def lookup_repo_for_add(source_url, cfg=None):
    """Combined add-source lookup with a short cache.

    Returns (name, branches, default_branch, releases_url). One dialog
    previously cost three separate repo-info calls plus a branch list;
    the shared fetch_repo_info/list_branches caches already collapse
    those, and this wrapper caches the combined tuple so an immediate
    retry (cancel then Next again) costs no network at all.
    Failures propagate and are never cached.
    """
    key = _repo_cache_key(source_url)
    if key is not None:
        hit = _lookup_cache_get(_add_lookup_cache, key)
        if hit is not None:
            name, branches, releases_url, default_branch = hit
            return name, list(branches), releases_url, default_branch
    name = fetch_repo_name(source_url, cfg) or ""
    branches = list_branches(source_url, cfg=cfg)
    try:
        default_branch = get_default_branch(source_url, cfg)
    except Exception:
        default_branch = None
    releases_url = infer_releases_url(source_url)
    if key is not None and branches:
        _lookup_cache_set(
            _add_lookup_cache, key, (name, list(branches), releases_url, default_branch), _ADD_LOOKUP_TTL
        )
    return name, branches, releases_url, default_branch


def get_cached_repo_lookup(source_url):
    """Return the cached lookup_repo_for_add tuple, or None."""
    key = _repo_cache_key(source_url)
    if key is None:
        return None
    hit = _lookup_cache_get(_add_lookup_cache, key)
    if hit is None:
        return None
    name, branches, releases_url, default_branch = hit
    return name, list(branches), releases_url, default_branch


def release_platforms(cfg_or_project):
    """Return {'linux': bool, 'windows': bool} based on release assets."""
    info = cfg_or_project if "releases_url" in cfg_or_project else project_info(cfg_or_project)
    url = info.get("releases_url")
    if not url:
        return {"linux": False, "windows": False}
    try:
        rels = list_releases({"projects": [info], "project": info.get("id")}, limit=5)
    except Exception:
        return {"linux": False, "windows": False}
    has_linux = has_windows = False
    for rel in rels:
        for a in rel.get("assets", []):
            n = a.get("name", "")
            if n.endswith(".AppImage") and "linux-" in n:
                has_linux = True
            nl = n.lower()
            if nl.endswith(".zip") and ("windows" in nl or "win64" in nl or "-win" in nl):
                has_windows = True
    return {"linux": has_linux, "windows": has_windows}


def source_version_entries(commits, have=None, installed_sha=None, installed_present=False):
    """Build (labels, shas, subs, latest_sha) for the source version list.

    The installed commit gets a ticked row even when it has scrolled out
    of the fetched window, so the current build is always marked. It is
    appended at the end so shas[0] stays the newest upstream commit.
    latest_sha is the newest upstream commit, for the "Latest" tick and
    update tracking (falls back to the installed build when offline).
    """
    have = set(have or [])
    labels, shas, subs = [], [], []
    for c in commits or []:
        sha = c.get("sha") if isinstance(c, dict) else None
        if not sha and isinstance(c, dict):
            sha = c.get("id")
        if not sha:
            continue
        shas.append(sha)
        msg = ""
        if isinstance(c, dict):
            commit = c.get("commit", {})
            msg = commit.get("message", "") if isinstance(commit, dict) else ""
        first = msg.splitlines()[0] if msg else ""
        tick = "✓ " if sha in have else ""
        labels.append(f"{tick}{sha[:7]} {first[:70]}")
        subs.append(f"{sha}\n{msg.strip()}" if msg else sha)
    latest_sha = shas[0] if shas else None
    if installed_sha and installed_sha not in shas and installed_present:
        shas.append(installed_sha)
        labels.append(f"✓ {installed_sha[:7]} (installed build)")
        subs.append(f"{installed_sha}\nCurrently installed build")
        if latest_sha is None:
            latest_sha = installed_sha
    return labels, shas, subs, latest_sha


def list_commits(cfg, limit=15):
    info = project_info(cfg)
    branch = info.get("branch", "master")
    source_url = info.get("source_url", "")
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return []
    if host == "github":
        url = f"https://api.github.com/repos/{owner}/{repo}/commits?sha={branch}&per_page={limit}"
    else:
        base = _forgejo_api_base(source_url)
        if base is None:
            return []
        url = f"{base}/repos/{owner}/{repo}/commits?sha={branch}&limit={limit}"
    data = api_get_json(url, cfg, timeout=30)
    if isinstance(data, dict):
        data = data.get("commits") or data.get("data") or []
    return data


def _platform_match(name, a):
    n = name.lower()
    if is_windows():
        if not n.endswith(".zip"):
            return False
        return "windows" in n or "win64" in n or "-win" in n
    if not n.endswith(".appimage"):
        return False
    return f"linux-{a}" in n


def find_asset(tag, assets, pgo):
    a = arch()
    candidates = [x for x in assets if _platform_match(x["name"], a)]
    for asset in candidates:
        if ("-pgo" in asset["name"]) == bool(pgo):
            return asset
    # fall back to whatever matches the platform (e.g. no PGO variant published)
    return candidates[0] if candidates else None


def download(url, dest, progress_cb=None):
    """Download a release artifact. Binary DLs bypass the API throttle."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=_api_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)
    tmp.replace(dest)
    os.chmod(dest, 0o755)
    return dest


def _release_commit_map(cfg, project):
    """Map release tag -> commit sha via the tags API (exact, no guessing)."""
    source_url = (project or {}).get("source_url", "")
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return {}
    if host == "github":
        url = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100"
    else:
        base = _forgejo_api_base(source_url)
        if base is None:
            return {}
        url = f"{base}/repos/{owner}/{repo}/tags?limit=100"
    try:
        tags = api_get_json(url, cfg, timeout=20)
    except Exception:
        return {}
    result = {}
    for t in tags if isinstance(tags, list) else []:
        name = t.get("name") if isinstance(t, dict) else None
        commit = (t.get("commit") or {}) if isinstance(t, dict) else {}
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if name and sha:
            result[name] = sha
    return result


def find_release_for_commit(cfg, sha):
    """Newest release tag built from exactly this commit (or None to build).

    Resolves each release tag to its commit via the tags API and compares full
    SHAs - no filename guessing. If the newest matching release has no
    downloadable asset for this platform, returns None so the caller falls
    back to a source build instead of digging through older releases.
    """
    if not sha:
        return None
    project = project_info(cfg)
    commits = _release_commit_map(cfg, project)
    if not commits:
        return None
    try:
        rels = list_releases(cfg)
    except Exception:
        return None
    for rel in rels:
        tag = rel.get("tag_name", "")
        if not tag or commits.get(tag) != sha:
            continue
        if find_asset(tag, rel.get("assets", []), cfg.get("pgo", True)) is None:
            return None
        return tag
    return None


def release_appimage_path(cfg):
    base = Path(cfg["install_dir"]) / cfg.get("project", "eglantine")
    if is_windows():
        return base / "eglantine.exe"
    return base / "eglantine.AppImage"


def _record(cfg, key, value, limit=30):
    items = [v for v in cfg.get(key, []) if v != value]
    cfg[key] = [value, *items][:limit]


def applications_dir():
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "applications"


def desktop_dir():
    """User's Desktop dir (xdg-user-dir DESKTOP, fallback ~/Desktop)."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        candidate = (out.stdout or "").strip()
        if candidate:
            return Path(candidate)
    except Exception:
        pass
    return Path.home() / "Desktop"


def shortcut_filename(pid):
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (pid or "eglantine"))
    return f"eglantine-launcher-{safe}.desktop"


def _project_by_id(cfg, pid):
    return next((p for p in cfg.get("projects", []) if p.get("id") == pid), {})


def shortcut_name(project):
    return f"{project.get('name', project.get('id', 'Eglantine'))} (EL)"


def shortcut_path(cfg, pid):
    return applications_dir() / shortcut_filename(pid)


def desktop_shortcut_path(cfg, pid):
    return desktop_dir() / shortcut_filename(pid)


def project_icon(cfg, project):
    """Cached emulator icon for the shortcut, or None to use the launcher icon."""
    url = (project or {}).get("icon_url")
    if not url:
        return None
    dest = Path(cfg["install_dir"]) / "icons" / f"{project.get('id', 'emu')}.png"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        download(url, dest)
        return dest
    except Exception:
        return None


def quote_arg(arg):
    """Shell/.desktop/Steam quoting: double quotes only when needed.

    (shlex single-quotes are literal text in .desktop Exec lines and are
    not parsed by Steam either, so roll our own double-quote style.)
    """
    text = str(arg)
    if not text:
        return '""'
    import re

    if re.match(r"^[A-Za-z0-9_@%+=:,./-]+$", text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _running_from_appimage():
    """This process's AppImage path, or None.

    ``APPIMAGE`` alone is not proof: it can be inherited from a parent
    process (e.g. a shell running inside some *other* AppImage) while this
    code runs from a source checkout. Trust it only when ``__file__`` is
    inside the ``APPDIR`` mount it belongs to.
    """
    appimage = os.environ.get("APPIMAGE")
    appdir = os.environ.get("APPDIR")
    if not appimage or not appdir:
        return None
    try:
        here = str(Path(__file__).resolve())
    except Exception:
        return None
    if here == appdir or here.startswith(appdir.rstrip("/") + "/"):
        return appimage
    return None


def shortcut_argv(pid, app_root=None):
    """Launch command for a per-project shortcut, stable outside this process.

    The old code always emitted ``env PYTHONPATH=<app_root> <python> -m ...``
    where ``app_root`` is the running tree. From an AppImage that is the
    transient ``/tmp/.mount_*`` FUSE mount, so every shortcut died with it.
    Instead, re-launch through the installed bundle per install method.
    Returns argv (no shell quoting); callers quote as needed.
    """
    args = ["--auto", "--project", pid]
    try:
        method = detect_install_method()
    except Exception:
        method = "source"
    if method == "appimage":
        appimage = _running_from_appimage()
        if appimage:
            return [appimage, *args]
        # Inherited APPIMAGE from an unrelated mount: fall through to source.
        method = "source"
    if method == "flatpak":
        return ["flatpak", "run", FLATPAK_APP_ID, *args]
    if method in ("dnf", "apt", "manual-pkg"):
        return ["/usr/bin/eglantine-launcher", *args]
    if method == "windows":
        return [sys.executable, *args]
    root = app_root or Path(__file__).resolve().parents[1]
    return ["/usr/bin/env", f"PYTHONPATH={root}", sys.executable, "-m", "eglantine_launcher", *args]


def shortcut_start_dir(app_root=None):
    """Working dir for shortcuts; the mount is not a valid StartDir."""
    try:
        method = detect_install_method()
    except Exception:
        method = "source"
    if method == "source":
        return str(app_root or Path(__file__).resolve().parents[1])
    return str(Path.home())


def stable_icon(cfg, project, app_root):
    """Project icon, or the bundled icon staged somewhere mount-independent.

    The bundled ``data/icons`` tree lives inside the AppImage/Flatpak image,
    so referencing it directly leaves a dead Icon= once unmounted. Stage the
    fallback to ``~/.local/share/icons`` (visible to host and sandbox).
    """
    icon = project_icon(cfg, project)
    if icon is not None:
        return icon
    try:
        src = Path(app_root) / "data" / "icons" / "eglantine-launcher.png"
        dest = Path.home() / ".local" / "share" / "icons" / "eglantine-launcher.png"
        if src != dest and src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file():
                shutil.copyfile(src, dest)
            return dest
        if src.is_file():
            return src
    except OSError:
        pass
    return Path(app_root) / "data" / "icons" / "eglantine-launcher.png"


def render_desktop_entry(cfg, project, app_root):
    """Render the shared .desktop content for Desktop and Start Menu targets."""
    icon = stable_icon(cfg, project, app_root)
    quoted = " ".join(quote_arg(a) for a in shortcut_argv(project["id"], app_root))
    return f"""[Desktop Entry]
Type=Application
Name={shortcut_name(project)}
Comment=Check for updates, then launch {project.get("name", project.get("id"))}
Exec={quoted}
Icon={icon}
Terminal=false
Categories=Game;Emulator;
"""


def install_shortcut(cfg, pid, app_root, targets=("applications",)):
    """Create/update per-project shortcuts. Returns {target: Path}."""
    project = _project_by_id(cfg, pid or cfg.get("project"))
    if not project:
        raise RuntimeError("Unknown project")
    pid = project["id"]
    wanted = set(targets or ())
    if is_windows():
        # .desktop files are inert on Windows; Steam is the shortcut there.
        wanted -= {"applications", "desktop"}
    written = {}
    if wanted & {"applications", "desktop"}:
        content = render_desktop_entry(cfg, project, app_root)
    if "applications" in wanted:
        applications_dir().mkdir(parents=True, exist_ok=True)
        path = shortcut_path(cfg, pid)
        path.write_text(content)
        written["applications"] = path
    if "desktop" in wanted:
        try:
            desktop_dir().mkdir(parents=True, exist_ok=True)
            path = desktop_shortcut_path(cfg, pid)
            path.write_text(content)
            written["desktop"] = path
        except Exception:
            pass
    if written:
        try:
            subprocess.run(["update-desktop-database", str(applications_dir())], capture_output=True)
        except Exception:
            pass
    if "steam" in wanted:
        from . import steam_shortcuts

        icon = stable_icon(cfg, project, app_root)
        argv = shortcut_argv(pid, app_root)
        steam_shortcuts.add_shortcut(
            project, pid, app_root, icon=icon, argv=argv, start_dir=shortcut_start_dir(app_root)
        )
        # Record a marker path (first userdata file) so callers can report.
        files = steam_shortcuts.shortcuts_files()
        written["steam"] = files[0] if files else Path("shortcuts.vdf")
    return written


def remove_shortcut(cfg, pid, targets=("applications", "desktop", "steam")):
    """Remove per-project shortcuts for the given targets. True when all clear."""
    pid = pid or cfg.get("project")
    project = _project_by_id(cfg, pid)
    app_name = shortcut_name(project) if project else None
    wanted = set(targets or ())
    if is_windows():
        wanted -= {"applications", "desktop"}
    ok = True
    if "applications" in wanted:
        path = shortcut_path(cfg, pid)
        if path.exists():
            try:
                path.unlink()
            except Exception:
                ok = False
        ok = ok and not path.exists()
    if "desktop" in wanted:
        path = desktop_shortcut_path(cfg, pid)
        if path.exists():
            try:
                path.unlink()
            except Exception:
                ok = False
        ok = ok and not path.exists()
    if "steam" in wanted:
        try:
            from . import steam_shortcuts

            steam_shortcuts.remove_shortcut(pid, app_name)
        except Exception:
            ok = False
        else:
            try:
                ok = ok and not steam_shortcuts.shortcut_exists(pid, app_name)
            except Exception:
                pass
    return ok


def shortcut_state(cfg, pid):
    """Which shortcut targets exist for one project id."""
    pid = pid or cfg.get("project")
    project = _project_by_id(cfg, pid)
    app_name = shortcut_name(project) if project else None
    try:
        from . import steam_shortcuts

        steam = bool(steam_shortcuts.shortcut_exists(pid, app_name))
    except Exception:
        steam = False
    if is_windows():
        return {"applications": False, "desktop": False, "steam": steam}
    return {
        "applications": shortcut_path(cfg, pid).exists(),
        "desktop": desktop_shortcut_path(cfg, pid).exists(),
        "steam": steam,
    }


def remove_project_data(cfg, pid):
    """Delete on-disk artifacts for a project: downloads, source checkout, icon, shortcut."""
    if not pid or any(sep in pid for sep in ("/", "\\")) or pid in (".", ".."):
        return
    for base_key in ("install_dir", "source_dir"):
        base = cfg.get(base_key)
        if not base:
            continue
        target = Path(base) / pid
        try:
            if target.is_dir() and target.parent == Path(base):
                shutil.rmtree(target)
        except Exception:
            pass
    try:
        icon = Path(cfg.get("install_dir", "")) / "icons" / f"{pid}.png"
        if icon.is_file():
            icon.unlink()
    except Exception:
        pass
    try:
        remove_shortcut(cfg, pid)
    except Exception:
        pass
    # Per-source emulator settings go too, unless another project (a PR
    # build of the same fork, or a source sharing config_key) still uses them.
    try:
        info = next((p for p in cfg.get("projects", []) if p.get("id") == pid), None)
        if info is not None:
            key = source_key(info)
            shared = any(source_key(p) == key for p in cfg.get("projects", []) if p.get("id") != pid)
            home = source_config_home(info)
            if not shared and home.is_dir():
                shutil.rmtree(home)
    except Exception:
        pass


def remove_pr_project(cfg, pid):
    """Full cleanup for a deleted PR entry: dirs, shortcut, list entry, build state.

    Reads the checkout HEAD before deleting so built_commits and the global
    last_source_commit can be pruned. Resets cfg["project"] when it pointed
    at the deleted entry. Saves and returns True when anything changed.
    """
    if not pid:
        return False
    projects = cfg.get("projects", [])
    target = next((p for p in projects if p.get("id") == pid), None)
    if target is None:
        return False
    heads = {h for h in (target.get("last_built_commit"), target.get("pr_head_sha")) if h}
    source_base = cfg.get("source_dir") or ""
    if source_base:
        try:
            head = _host_check_output(
                ["git", "-C", str(Path(source_base) / pid), "rev-parse", "HEAD"], text=True
            ).strip()
            if head:
                heads.add(head)
        except Exception:
            pass
    remove_project_data(cfg, pid)
    cfg["projects"] = [p for p in projects if p.get("id") != pid]
    if pid not in cfg.get("deleted_projects", []):
        cfg.setdefault("deleted_projects", []).append(pid)
    if heads:
        cfg["built_commits"] = [c for c in cfg.get("built_commits", []) if c not in heads]
        if cfg.get("last_source_commit") in heads:
            cfg["last_source_commit"] = None
    if cfg.get("project") == pid:
        rest = cfg["projects"]
        cfg["project"] = rest[0].get("id") if rest else None
    save_config(cfg)
    return True


def dir_size(path):
    """Total file bytes under path (no symlink following). Missing dir -> 0."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def format_size(num):
    """Human size for the Storage tab, e.g. '1.2 GiB'."""
    n = float(max(int(num or 0), 0))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def project_storage(cfg, pid):
    """Rows describing a project's on-disk data for the Storage tab.

    Each row: kind ('release'|'source'), pid, label, detail, size
    (bytes or None), path, in_use. in_use means the current launcher
    mode launches this artifact: never offer to delete those.

    Sizes walk the tree, so call this off the UI thread.
    """
    rows = []
    if not pid:
        return rows
    in_release_use = pid == cfg.get("project") and cfg.get("mode", "release") == "release"
    in_source_use = pid == cfg.get("project") and cfg.get("mode", "release") == "source"
    install_base = cfg.get("install_dir")
    if install_base:
        app = Path(install_base) / pid / ("eglantine.exe" if is_windows() else "eglantine.AppImage")
        if app.is_file():
            try:
                size = app.stat().st_size
            except OSError:
                size = None
            tag = cfg.get("last_release_tag") if cfg.get("project") == pid else None
            rows.append(
                {
                    "kind": "release",
                    "pid": pid,
                    "label": f"Release download{(' (' + tag + ')') if tag else ''}",
                    "detail": str(app),
                    "size": size,
                    "path": str(app),
                    "in_use": bool(in_release_use),
                }
            )
    source_base = cfg.get("source_dir")
    if source_base:
        src = Path(source_base) / pid
        if (src / ".git").is_dir():
            try:
                head = _host_check_output(
                    ["git", "-C", str(src), "rev-parse", "--short", "HEAD"], text=True
                ).strip()
            except Exception:
                head = ""
            rows.append(
                {
                    "kind": "source",
                    "pid": pid,
                    "label": f"Source files{(' (' + head + ')') if head else ''}",
                    "detail": str(src),
                    "size": dir_size(src),
                    "path": str(src),
                    "in_use": bool(in_source_use),
                }
            )
    info = next((p for p in cfg.get("projects", []) if p.get("id") == pid), None)
    if info is not None:
        home = source_config_home(info)
        if home.is_dir():
            rows.append(
                {
                    "kind": "config",
                    "pid": pid,
                    "label": "Emulator config",
                    "detail": str(home),
                    "size": dir_size(home),
                    "path": str(home),
                    # Settings are safe to reset while the launcher is open:
                    # the emulator rewrites them from defaults next launch.
                    "in_use": False,
                    "confirm": (
                        f"Removes {home}. Games, saves and keys are kept; "
                        "the emulator starts with default settings next launch."
                    ),
                }
            )
    return rows


def delete_storage_row(cfg, row):
    """Delete one project_storage row and fix up config. Returns error str or None.

    Refuses in_use rows and paths outside the project data dirs.
    Missing files count as success (config is still fixed up).
    """
    if row.get("in_use"):
        return "That file is used by the current mode; switch modes first."
    kind, pid = row.get("kind"), row.get("pid")
    if kind == "release":
        base = cfg.get("install_dir") or ""
        target = Path(row.get("path") or "")
        if not pid or target.parent != Path(base) / pid or target.parent.parent != Path(base):
            return "Refusing: path is outside the project data dir."
        try:
            target.unlink(missing_ok=True)
        except OSError as e:
            return f"Could not delete: {e}"
        if cfg.get("project") == pid and cfg.get("last_release_tag"):
            cfg["downloaded_releases"] = [
                t for t in cfg.get("downloaded_releases", []) if t != cfg["last_release_tag"]
            ]
            cfg["last_release_tag"] = None
            save_config(cfg)
        return None
    if kind == "source":
        base = cfg.get("source_dir") or ""
        target = Path(row.get("path") or "")
        if not pid or target != Path(base) / pid or target.parent != Path(base):
            return "Refusing: path is outside the project data dir."
        try:
            head = _host_check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        except Exception:
            head = ""
        try:
            if target.is_dir():
                shutil.rmtree(target)
        except OSError as e:
            return f"Could not delete: {e}"
        if head:
            cfg["built_commits"] = [c for c in cfg.get("built_commits", []) if c != head]
            if cfg.get("last_source_commit") == head:
                cfg["last_source_commit"] = None
            for p in cfg.get("projects", []):
                if p.get("id") == pid and p.get("last_built_commit") == head:
                    p["last_built_commit"] = None
            save_config(cfg)
        return None
    if kind == "config":
        info = next((p for p in cfg.get("projects", []) if p.get("id") == pid), None)
        target = Path(row.get("path") or "")
        if info is None or target != source_config_home(info):
            return "Refusing: path is outside the project data dirs."
        try:
            if target.is_dir():
                shutil.rmtree(target)
        except OSError as e:
            return f"Could not delete: {e}"
        # Keep it deleted: without the flag the next launch re-seeds it
        # from the shared config the user was trying to get away from.
        info["config_seeded"] = True
        save_config(cfg)
        return None
    return "Unknown storage entry."


def source_dir(cfg):
    return Path(cfg["source_dir"]) / cfg.get("project", "eglantine")


# Canonical emulator data dir name. The 4f3de82 rename moved Eglantine from
# ``eden`` to ``eglantine``; other forks still use ``eden`` (and Lichii may
# move to ``lichii``). Every name in EMULATOR_DIR_ALIASES is kept as a symlink
# to the canonical dir so saves and settings stay shared no matter which
# build last ran. Nothing here needs per-build version detection.
EMULATOR_CONFIG_DIR = "eglantine"
EMULATOR_DIR_ALIASES = ("eden", "lichii")


def _config_slug(text):
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug or "source"


def source_key(info):
    """Folder name for a source's own emulator config.

    Keyed by repo identity rather than project id so a fork and its PR
    builds share one config, and renaming a source keeps its settings.
    ``config_key`` on the project overrides the derived name.
    """
    info = info or {}
    override = (info.get("config_key") or "").strip()
    if override:
        return _config_slug(override)
    host, owner, repo = parse_repo(info.get("source_url"))
    if host and owner and repo:
        return _config_slug(f"{host}-{owner}-{repo}")
    return _config_slug(f"project-{info.get('id') or 'default'}")


def known_config_keys(cfg, exclude_id=None):
    """Existing settings-folder names as [(key, [source names using it])].

    Union of every project's resolved ``source_key`` and every folder
    already on disk under ``.../sources``. Sorted by key. ``exclude_id``
    drops one project's name from the usage lists (the source being
    edited) without dropping the key itself.
    """
    users = {}

    def _note(key, name):
        names = users.setdefault(key, [])
        if name and name not in names:
            names.append(name)

    for project in (cfg or {}).get("projects") or []:
        if not isinstance(project, dict):
            continue
        try:
            key = source_key(project)
        except Exception:
            continue
        if project.get("id") != exclude_id:
            _note(key, (project.get("name") or project.get("id") or "").strip())
        else:
            users.setdefault(key, [])
    try:
        root = launcher_config_dir() / "sources"
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir():
                    users.setdefault(_config_slug(child.name), [])
    except OSError:
        pass
    return sorted(users.items())


def source_config_home(info):
    """XDG_CONFIG_HOME handed to the emulator for one source."""
    return launcher_config_dir() / "sources" / source_key(info)


def source_config_dir(info):
    """The emulator's own config folder inside a source's config home."""
    return source_config_home(info) / EMULATOR_CONFIG_DIR


def _merge_tree(src, dst):
    """Fold src into dst so nothing is lost; newer files win conflicts.

    Missing files/dirs are copied, files present on both sides keep the
    newer mtime. Inner symlinks are replicated, never followed.
    """
    src, dst = Path(src), Path(dst)
    for root, dirs, files in os.walk(src, followlinks=False):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        # Replicate symlinked dirs as links; never descend into them.
        for name in list(dirs):
            link = Path(root) / name
            if link.is_symlink():
                dirs.remove(name)
                dest = target_dir / name
                try:
                    if not dest.exists() and not dest.is_symlink():
                        dest.symlink_to(os.readlink(link))
                except OSError:
                    pass
        for name in files:
            s = Path(root) / name
            d = target_dir / name
            try:
                if s.is_symlink():
                    if not d.exists() and not d.is_symlink():
                        d.symlink_to(os.readlink(s))
                    continue
                if d.is_symlink():
                    d.unlink()
                if not d.exists():
                    shutil.copy2(s, d)
                    continue
                if s.stat().st_mtime > d.stat().st_mtime + 1:
                    shutil.copy2(s, d)
            except OSError:
                continue


def ensure_emulator_dir_links(base):
    """Point legacy/fork emulator dir names at the canonical one.

    ``base`` is a data/config home (``$XDG_DATA_HOME`` or a source's config
    home). After this, ``base/eden`` (and ``base/lichii``) are symlinks to
    ``base/eglantine``: old builds, new builds and not-yet-renamed forks all
    read and write the same tree, so saves and settings stay global.

    A real legacy dir is promoted when the canonical one is missing,
    otherwise merged (newer wins) and replaced. Never raises; returns the
    canonical path. No-op on Windows (portable ``user`` tree).
    """
    base = Path(base)
    canonical = base / EMULATOR_CONFIG_DIR
    try:
        if is_windows():
            return canonical
        base.mkdir(parents=True, exist_ok=True)
        if canonical.is_symlink():
            if canonical.exists():
                return canonical  # user-custom link; leave it alone
            canonical.unlink()  # broken link; rebuild below
        if canonical.exists() and not canonical.is_dir():
            return canonical  # a file blocks the name; leave it
        if not canonical.is_dir():
            for alias in EMULATOR_DIR_ALIASES:
                p = base / alias
                if p.is_symlink() or not p.is_dir():
                    continue
                try:
                    p.rename(canonical)
                except OSError:
                    continue
                break
            else:
                canonical.mkdir(parents=True, exist_ok=True)
        for alias in EMULATOR_DIR_ALIASES:
            p = base / alias
            try:
                if p.is_symlink():
                    try:
                        if p.resolve() == canonical.resolve():
                            continue
                    except OSError:
                        pass
                    if p.exists():
                        continue  # points elsewhere (user custom); leave it
                    p.unlink()  # broken link; repoint below
                elif p.is_dir():
                    _merge_tree(p, canonical)
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    continue  # a file, not a dir; leave it
                if not p.exists() and not p.is_symlink():
                    p.symlink_to(canonical.name)
            except OSError:
                continue
    except Exception:
        pass
    return canonical


def migrate_emulator_dirs(cfg=None):
    """One-time-per-launch cutover to the canonical emulator dir name.

    Links legacy/fork names to ``eglantine`` for global saves and every
    known per-source config home (existing homes only; new ones are linked
    by seed_source_config at first launch), then rewrites legacy data-dir
    spellings inside the emulator configs to match. Never raises.
    """
    try:
        if is_windows():
            return
        ensure_emulator_dir_links(_data_home())
        config_dirs = [
            launcher_config_dir().parent / EMULATOR_CONFIG_DIR,
            *(launcher_config_dir().parent / alias for alias in EMULATOR_DIR_ALIASES),
        ]
        projects = (cfg or {}).get("projects", []) if isinstance(cfg, dict) else []
        seen = set()
        for info in projects:
            try:
                home = source_config_home(info)
            except Exception:
                continue
            if home in seen:
                continue
            seen.add(home)
            try:
                if home.is_dir():
                    ensure_emulator_dir_links(home)
                    config_dirs.append(home / EMULATOR_CONFIG_DIR)
            except OSError:
                continue
        canonicalize_emulator_config_paths(config_dirs)
    except Exception:
        pass


# qt-config.ini keys whose default lives under the emulator data dir. A value
# spelling the legacy tree is rewritten to the canonical one (identical
# target through the symlink); custom locations elsewhere are never touched.
EMULATOR_DATA_PATH_KEYS = (
    "nand_directory",
    "sdmc_directory",
    "load_directory",
    "dump_directory",
    "tas_directory",
    "save_directory",
    "screenshot_path",
)


def canonicalize_emulator_config_paths(config_dirs):
    """Point legacy data-dir spellings at the canonical dir in emulator configs.

    Only rewrites values inside the old ``$XDG_DATA_HOME/eden`` tree (e.g. a
    custom NAND path saved when ``eden`` was canonical); a NAND on another
    drive keeps working untouched. Never raises.
    """
    try:
        old_prefix = str(_data_home() / "eden") + "/"
        new_prefix = str(_data_home() / EMULATOR_CONFIG_DIR) + "/"
        for config_dir in config_dirs:
            try:
                ini = Path(config_dir) / "qt-config.ini"
                text = ini.read_text()
            except OSError:
                continue
            lines = text.split("\n")
            changed = False
            for i, line in enumerate(lines):
                key, sep, value = line.partition("=")
                if not sep or key not in EMULATOR_DATA_PATH_KEYS:
                    continue
                if value.startswith(old_prefix):
                    lines[i] = key + "=" + new_prefix + value[len(old_prefix) :]
                    changed = True
            if changed:
                try:
                    ini.write_text("\n".join(lines))
                except OSError:
                    continue
    except Exception:
        pass


# Timestamped copies of the save tree kept here; newest SAVE_BACKUP_KEEP win.
SAVE_BACKUP_KEEP = 10


def format_backup_name(name):
    """UTC backup folder name in the user's local timezone and locale.

    Unknown formats pass through unchanged so a weird folder never breaks
    the list.
    """
    import re

    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)(-\d+)?$", name or "")
    if not m:
        return name
    try:
        stamp = datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
    except ValueError:
        return name
    text = stamp.astimezone().strftime("%c")
    return text + m.group(2) if m.group(2) else text


def _data_home():
    override = os.environ.get("XDG_DATA_HOME")
    if override:
        return Path(override)
    return Path(os.environ.get("HOME") or str(Path.home())) / ".local" / "share"


def _project_binary(cfg):
    """Best guess at the current project's emulator binary (for save location)."""
    if (cfg or {}).get("mode") == "source":
        name = "eglantine.exe" if is_windows() else "eglantine"
        return source_dir(cfg) / "build" / "release" / "bin" / name
    return release_appimage_path(cfg)


def emulator_save_dir(cfg=None, binary_path=None):
    """Live user game saves for the emulator about to launch.

    Linux keeps them in ``$XDG_DATA_HOME/eglantine/nand/user/save`` no matter
    which source runs (legacy ``eden``/``lichii`` names are symlinks there,
    see ensure_emulator_dir_links). On Windows they live in the portable
    ``user`` tree next to the executable, which is already per source.
    """
    if is_windows():
        exe = Path(binary_path) if binary_path else _project_binary(cfg or {})
        return exe.parent / "user" / "nand" / "user" / "save"
    return _data_home() / EMULATOR_CONFIG_DIR / "nand" / "user" / "save"


def save_backup_root(cfg):
    return Path(cfg["install_dir"]) / "save-backups"


def backup_saves(cfg, binary_path=None):
    """Copy the live save tree aside; keep the newest SAVE_BACKUP_KEEP.

    Runs before every launch. Never raises: a backup must not block a game.
    Returns the backup path, or None when there was nothing to back up.
    """
    try:
        ensure_emulator_dir_links(_data_home())
        src = emulator_save_dir(cfg, binary_path)
        if not src.is_dir():
            return None
        root = save_backup_root(cfg)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        dest = root / stamp
        if dest.exists():  # relaunched within the same second
            for i in range(2, 100):
                if not (root / f"{stamp}-{i}").exists():
                    dest = root / f"{stamp}-{i}"
                    break
        shutil.copytree(src, dest)
        for old in sorted(p.name for p in root.iterdir() if p.is_dir())[:-SAVE_BACKUP_KEEP]:
            shutil.rmtree(root / old, ignore_errors=True)
        return dest
    except OSError:
        return None


def list_save_backups(cfg):
    """Newest-first [{name, path, size}]; empty when there are none."""
    root = save_backup_root(cfg)
    try:
        names = sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        return []
    return [{"name": n, "path": str(root / n), "size": dir_size(root / n)} for n in names]


def delete_save_backup(cfg, name):
    target = save_backup_root(cfg) / (name or "")
    if not name or target.parent != save_backup_root(cfg):
        return "Refusing: path is outside the save backups."
    shutil.rmtree(target, ignore_errors=True)
    return None


def restore_save_backup(cfg, name, binary_path=None):
    """Copy a backup over the live saves. Files only in live stay put.

    The pre-launch backups already record recent states, so restoring an
    older one is always undoable by restoring a newer one.
    """
    src = save_backup_root(cfg) / (name or "")
    if not name or src.parent != save_backup_root(cfg) or not src.is_dir():
        return "Unknown backup."
    try:
        ensure_emulator_dir_links(_data_home())
        dst = emulator_save_dir(cfg, binary_path)
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    except OSError as e:
        return f"Could not restore: {e}"
    return None


def move_source_config(cfg, info, new_key):
    """Rename a source's config folder when its key changes.

    Only moves a folder nobody else uses, and never over an existing one:
    pointing a source at another source's folder is how settings are shared.
    """
    old = source_config_home(info)
    if not old.is_dir():
        return
    new = launcher_config_dir() / "sources" / new_key
    if new.exists():
        return
    key = source_key(info)
    if any(p.get("id") != info.get("id") and source_key(p) == key for p in cfg.get("projects", [])):
        return
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))


def seed_source_config(cfg, info):
    """Give a source its own emulator config the first time it launches.

    Copies the shared config (``~/.config/eglantine``, falling back to the
    pre-rename ``~/.config/eden``) so a fork starts from settings the user
    already tuned, then diverges. Runs once per source: ``config_seeded``
    stops a config deleted from Storage being restored on the next launch.
    """
    home = source_config_home(info)
    home.mkdir(parents=True, exist_ok=True)
    ensure_emulator_dir_links(home)
    if (info or {}).get("config_seeded"):
        return home
    dst = source_config_dir(info)
    try:
        if not dst.is_dir() or not any(dst.iterdir()):
            for legacy_name in (EMULATOR_CONFIG_DIR, *EMULATOR_DIR_ALIASES):
                legacy = launcher_config_dir().parent / legacy_name
                if legacy.is_dir():
                    shutil.copytree(legacy, dst, dirs_exist_ok=True)
                    break
        canonicalize_emulator_config_paths([dst])
    except OSError:
        pass
    try:
        info["config_seeded"] = True
        if isinstance(cfg, dict) and "projects" in cfg:
            save_config(cfg)
    except OSError:
        pass
    return home


def check_release(cfg, progress_cb=None, status_cb=None, tag=None):
    info = project_info(cfg)
    plats = release_platforms(info)
    need = "windows" if is_windows() else "linux"
    if not plats.get(need):
        raise RuntimeError(f"{info.get('name', 'Project')} has no {need} release; use 'Build from source'.")
    if status_cb:
        status_cb("Checking latest release...")
    if tag is None:
        # pick the newest release that actually has a matching asset
        rels = list_releases(cfg, 15)
        for rel in rels:
            if find_asset(rel.get("tag_name"), rel.get("assets", []), cfg["pgo"]):
                tag, assets = rel.get("tag_name"), rel.get("assets", [])
                break
        else:
            raise RuntimeError("No release with a matching download for this platform")
        newest = rels[0].get("tag_name") if rels else None
        if newest and newest != tag and status_cb:
            os_name = "Windows" if is_windows() else "Linux"
            status_cb(
                f"Note: {newest} has no {os_name} build; using {tag} (or Build from source for newer code)."
            )
    else:
        tag, assets = fetch_release(cfg, tag)
    if status_cb:
        status_cb(f"Latest release: {tag}")
    asset = find_asset(tag, assets, cfg["pgo"])
    if asset is None:
        raise RuntimeError("No matching download for this architecture/PGO choice")
    appimage = release_appimage_path(cfg)
    if cfg.get("last_release_tag") == tag and appimage.exists():
        if status_cb:
            status_cb("Release already up to date.")
        _record(cfg, "downloaded_releases", tag)
        save_config(cfg)
        return appimage
    if status_cb:
        status_cb(f"Downloading {asset['name']}...")
    dest = Path(cfg["install_dir"]) / cfg.get("project", "eglantine")
    dest.mkdir(parents=True, exist_ok=True)
    if is_windows():
        zip_path = dest / asset["name"]
        download(asset["browser_download_url"], zip_path, progress_cb)
        if status_cb:
            status_cb("Extracting...")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)
        zip_path.unlink(missing_ok=True)
    else:
        download(asset["browser_download_url"], appimage, progress_cb)
    cfg["last_release_tag"] = tag
    _record(cfg, "downloaded_releases", tag)
    save_config(cfg)
    if status_cb:
        status_cb("Download complete.")
    return appimage


def ensure_source(cfg, status_cb=None, commit=None, cancelled=None, log_cb=None, progress_cb=None):
    info = project_info(cfg)
    src = source_dir(cfg)
    src.parent.mkdir(parents=True, exist_ok=True)
    if is_pr_project(info):
        return _ensure_pr_source(cfg, info, src, status_cb, commit, cancelled, log_cb, progress_cb)
    if not (src / ".git").exists():
        if status_cb:
            status_cb("Cloning source...")
        _run_logged(
            ["git", "clone", "--progress", "--branch", info["branch"], info["source_url"], str(src)],
            log_cb=log_cb,
            cancelled=cancelled,
            progress_cb=progress_cb,
        )
    else:
        if status_cb:
            status_cb("Fetching source updates...")
        _run_logged(
            ["git", "-C", str(src), "fetch", "--progress", "origin", info["branch"]],
            log_cb=log_cb,
            cancelled=cancelled,
            progress_cb=progress_cb,
        )
    if cancelled and cancelled():
        raise BuildCancelled("Build cancelled.")
    if commit:
        _run_logged(
            ["git", "-C", str(src), "checkout", commit],
            log_cb=log_cb,
            cancelled=cancelled,
            progress_cb=progress_cb,
        )
    else:
        _run_logged(
            ["git", "-C", str(src), "checkout", info["branch"]],
            log_cb=log_cb,
            cancelled=cancelled,
            progress_cb=progress_cb,
        )
        _run_logged(
            ["git", "-C", str(src), "pull", "--ff-only", "origin", info["branch"]],
            log_cb=log_cb,
            cancelled=cancelled,
            progress_cb=progress_cb,
            check=False,
        )
    commit = _host_check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
    return src, commit


def _ensure_pr_source(
    cfg, info, src, status_cb=None, commit=None, cancelled=None, log_cb=None, progress_cb=None
):
    """Checkout a PR head into its isolated source dir.

    Uses the base repo as origin and fetches the PR ref (refs/pull/N/head on
    both GitHub and Forgejo). Fork-head PRs work because the base repo
    exposes the pull ref; the head clone URL is never fetched directly.
    Git transfer progress streams to the log via _run_logged.
    """
    number = info.get("pr_number")
    source_url = info.get("source_url")
    if not number or not source_url:
        raise RuntimeError("PR entry is missing its number or source URL; edit it and try again.")
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise RuntimeError("PR entry has an invalid number; edit it and try again.")

    def _fetch_pr_ref():
        fetched = False
        for ref in (f"pull/{number}/head", f"pulls/{number}/head"):
            try:
                _run_logged(
                    ["git", "-C", str(src), "fetch", "--progress", "origin", f"+{ref}:pr-{number}"],
                    log_cb=log_cb,
                    cancelled=cancelled,
                    progress_cb=progress_cb,
                )
                fetched = True
                break
            except Exception:
                continue
        return fetched

    if not (src / ".git").exists():
        if status_cb:
            status_cb(f"Cloning PR #{number} base...")
        _run_logged(
            ["git", "clone", "--progress", source_url, str(src)],
            log_cb=log_cb,
            cancelled=cancelled,
            progress_cb=progress_cb,
        )
    else:
        if status_cb:
            status_cb(f"Fetching PR #{number} updates...")
        try:
            current = _host_check_output(
                ["git", "-C", str(src), "remote", "get-url", "origin"], text=True
            ).strip()
        except Exception:
            current = ""
        if current and current != source_url:
            _host_run(["git", "-C", str(src), "remote", "set-url", "origin", source_url], check=True)
        if not _fetch_pr_ref():
            raise RuntimeError(f"Could not fetch PR #{number} (is it still open?).")
    if not (src / ".git").exists():
        raise RuntimeError("PR clone failed.")
    # fresh clones still need the PR ref
    try:
        _host_check_output(["git", "-C", str(src), "rev-parse", "--verify", f"pr-{number}"], text=True)
    except Exception:
        if not _fetch_pr_ref():
            raise RuntimeError(f"Could not fetch PR #{number} (is it still open?).")
    if cancelled and cancelled():
        raise BuildCancelled("Build cancelled.")
    ref = f"pr-{number}"
    requested = commit or info.get("pr_head_sha")
    target = requested or ref
    if requested and requested != ref:
        try:
            _host_check_output(["git", "-C", str(src), "cat-file", "-e", requested], text=True)
        except Exception:
            try:
                latest = _host_check_output(["git", "-C", str(src), "rev-parse", ref], text=True).strip()
            except Exception:
                latest = ""
            short_old = requested[:7] if len(requested) >= 7 else requested
            short_new = latest[:7] if len(latest) >= 7 else ref
            msg = f"Cached PR head {short_old} missing (PR was rebased?); using latest {short_new} instead."
            if status_cb:
                try:
                    status_cb(msg)
                except Exception:
                    pass
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass
            if latest:
                info["pr_head_sha"] = latest
                try:
                    save_config(cfg)
                except Exception:
                    pass
            target = ref
    _run_logged(
        ["git", "-C", str(src), "checkout", target],
        log_cb=log_cb,
        cancelled=cancelled,
        progress_cb=progress_cb,
    )
    commit = _host_check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
    return src, commit


PGO_PROFILE_URL = "https://github.com/Eden-CI/PGO/releases/latest/download/eden.profdata"
SOURCES_URL = "https://raw.githubusercontent.com/SavageCore/eglantine-launcher/main/data/sources.json"
GAMES_URL = "https://raw.githubusercontent.com/SavageCore/eglantine-launcher/main/data/games.json"


def fetch_remote_sources(cfg=None, timeout=20):
    """Source registry from the repo. Returns [] offline (callers keep shipped defaults)."""
    hit = _lookup_cache_get(_remote_registry_cache, "sources")
    if hit is not None:
        return [dict(s) for s in hit]
    try:
        data = api_get_json(SOURCES_URL, cfg, timeout=timeout)
        sources = data.get("sources", []) if isinstance(data, dict) else []
        result = [s for s in sources if isinstance(s, dict) and s.get("id") and s.get("source_url")]
    except Exception:
        return []
    if result:
        _lookup_cache_set(_remote_registry_cache, "sources", [dict(s) for s in result], _REMOTE_REGISTRY_TTL)
    return result


def fetch_remote_games(cfg=None, timeout=20):
    """Game registry from the repo. Returns [] offline (callers keep shipped games)."""
    hit = _lookup_cache_get(_remote_registry_cache, "games")
    if hit is not None:
        return [dict(g) for g in hit]
    try:
        data = api_get_json(GAMES_URL, cfg, timeout=timeout)
        games = data.get("games", []) if isinstance(data, dict) else []
        result = [g for g in games if isinstance(g, dict) and g.get("title_id") and g.get("name")]
    except Exception:
        return []
    if result:
        _lookup_cache_set(_remote_registry_cache, "games", [dict(g) for g in result], _REMOTE_REGISTRY_TTL)
    return result


def _find_pgo_clang():
    """(cc, cxx) for a PGO source build, or (None, None). Prefers ~/opt/llvm23."""
    opt_cxx = str(Path.home() / "opt" / "llvm23" / "bin" / "clang++")
    opt_cc = str(Path.home() / "opt" / "llvm23" / "bin" / "clang")
    if _host_which(opt_cxx):
        return opt_cc, opt_cxx
    cxx = _host_which("clang++")
    if cxx:
        return _host_which("clang") or cxx, cxx
    return None, None


def _ensure_pgo_profile(status_cb=None):
    dest = Path.home() / ".cache" / "eglantine-launcher" / "eden.profdata"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if status_cb:
        status_cb("Downloading PGO profile...")
    try:
        download(PGO_PROFILE_URL, dest)
        return dest
    except Exception as e:
        if status_cb:
            status_cb(f"PGO profile download failed ({e}); doing a regular build.")
        return None


def _profile_readable(cxx, profile):
    """Definitive compatibility check: can this clang actually use the profile?"""
    try:
        proc = _host_run(
            [
                cxx,
                "-fsyntax-only",
                "-x",
                "c++",
                "-",
                f"-fprofile-use={profile}",
                "-Wno-backend-plugin",
                "-Wno-profile-instr-unprofiled",
                "-Wno-profile-instr-out-of-date",
            ],
            input="int main(){return 0;}\n",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _cpu_flags():
    """CPU feature flags from /proc/cpuinfo, or empty when unavailable."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("flags"):
                    return line.split(":", 1)[1]
    except Exception:
        pass
    return ""


def _has_all(flags, *names):
    """True when every NAME is in FLAGS (abm also matches lzcnt)."""
    for name in names:
        if name == "abm":
            if " abm " not in flags and " lzcnt " not in flags:
                return False
        elif f" {name} " not in flags:
            return False
    return True


def detect_host_cpu_level():
    """Host x86-64 microarchitecture level (1-4)."""
    import re

    for ld in ("/lib64/ld-linux-x86-64.so.2", "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"):
        try:
            if not os.path.exists(ld):
                continue
            out = _host_check_output([ld, "--help"], text=True, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            continue
        levels = re.findall(r"x86-64-v([234]) \(supported", out)
        if levels:
            return max(int(v) for v in levels)
        if "x86-64-v" in out:
            return 1
    flags = f" {_cpu_flags()} "
    level = 1
    if _has_all(flags, "cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"):
        level = 2
    if level == 2 and _has_all(flags, "avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"):
        level = 3
    if level == 3 and _has_all(flags, "avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"):
        level = 4
    return level


def _cc_accepts(compiler, *flags):
    """True when COMPILER compiles a trivial program with FLAGS.

    Unverifiable compilers (binary not found, e.g. mocked paths in tests)
    return True so flag selection never blocks on probing alone; the real
    configure step still fails loudly on a truly missing compiler.
    """
    if _host_which(compiler) is None and not Path(str(compiler)).exists():
        return True
    try:
        proc = _host_run(
            [compiler, *flags, "-x", "c", "-", "-o", "/dev/null"],
            input="int main(void){return 0;}\n",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        return proc.returncode == 0
    except Exception:
        return False


def select_arch_flags(cc, cpu_level="auto"):
    """(ARCH_FLAGS, LABEL) for -march=x86-64-vN plus -mtune.

    cpu_level is auto, native, or 1-4. Native tunes for this exact machine
    but the binary may not run elsewhere; vN levels stay portable to any
    machine supporting that level.
    """
    level = str(cpu_level or "auto").lower().lstrip("v")
    if level == "native":
        if _cc_accepts(cc, "-march=native"):
            return "-march=native -mtune=native", "native"
        level = "auto"
    if level not in ("auto", "1", "2", "3", "4"):
        raise RuntimeError(f"Unknown CPU level: {cpu_level} (expected auto, native, or 1-4)")
    host = detect_host_cpu_level()
    want = host if level == "auto" else int(level)
    if want > host and level != "auto":
        raise RuntimeError(f"Host CPU only runs x86-64-v{host}; pick v{host} or lower, not v{want}.")
    march = f"x86-64-v{want}" if want > 1 else "x86-64"
    while not _cc_accepts(cc, f"-march={march}"):
        want -= 1
        if want < 1:
            raise RuntimeError(f"{cc} rejects -march=x86-64")
        march = f"x86-64-v{want}" if want > 1 else "x86-64"
    tune = "native" if _cc_accepts(cc, "-mtune=native") else "generic"
    return f"-march={march} -mtune={tune}", f"v{want}"


def pick_build_linker(cc, cxx, want_lto=False):
    """(LINK_ARGS, lto_ok): linker flags plus whether LTO objects link.

    lld is preferred with clang (it brings its own LTO support); mold next.
    Candidates are full paths, version-matched sibling first: clang resolves
    a bare -fuse-ld=lld to its own directory first, which silently picks a
    broken bundled lld over a working system one. The LLVM gold plugin bfd
    needs for clang LTO is often missing, so when LTO is wanted every
    candidate is verified with a trial link first. When nothing links LTO
    objects, lto_ok is False and the caller disables IPO.
    """
    cands = _linker_candidates(cxx)
    if want_lto:
        for ld, ok, _ in probe_lto_linkers(cc, cxx):
            if ok:
                return (_fuse_ld_args(ld) if ld else [], True)
        return [], False
    for ld in cands:
        if _cc_accepts(cc, f"-fuse-ld={ld}"):
            return _fuse_ld_args(ld), True
    return [], True


def first_working_lto_linker(cc, cxx):
    """Full -fuse-ld= value that links LTO objects, "" for default, None if none."""
    for ld, ok, _ in probe_lto_linkers(cc, cxx):
        if ok:
            return ld
    return None


def describe_lto_support(cfg):
    """(available, reason): whether LTO objects link with the build toolchain.

    Mirrors the compiler choice in build_source: PGO clang when enabled,
    else the system compiler. Reason suits a tooltip, with one line per
    linker tried so a broken toolchain explains itself.
    """
    cc = cxx = None
    if (cfg or {}).get("pgo", True):
        cc, cxx = _find_pgo_clang()
    if not cxx:
        cxx = _host_which("clang++") or _host_which("g++") or "c++"
        cc = _host_which("clang") or _host_which("gcc") or "cc"
    report = probe_lto_linkers(cc, cxx)
    for ld, ok, _ in report:
        if ok:
            return True, f"LTO objects link with {ld or 'the default linker'} ({cxx})."
    lines = [f"- {ld or 'default linker'}: {err}" for ld, _, err in report]
    return False, f"LTO objects fail to link with {cxx}:\n" + "\n".join(lines)


def _linker_candidates(cxx):
    """Full -fuse-ld= values to try: version-matched sibling first, then PATH."""
    cands = []
    if _cxx_is_clang(cxx):
        for p in (_sibling_tool_path(cxx, "ld.lld"), _host_which("ld.lld")):
            if p and p not in cands:
                cands.append(p)
    mold = _host_which("mold")
    if mold and mold not in cands:
        cands.append(mold)
    return cands


def _cxx_is_clang(cxx):
    """True when CXX reports itself as clang."""
    try:
        out = _host_check_output([cxx, "--version"], text=True, stderr=subprocess.STDOUT, timeout=30)
        return "clang" in out.lower()
    except Exception:
        return False


def _sibling_tool(compiler, tool):
    """True when TOOL sits next to COMPILER (e.g. ld.lld beside clang++)."""
    return _sibling_tool_path(compiler, tool) is not None


def _sibling_tool_path(compiler, tool):
    """Full path of TOOL next to COMPILER, or None."""
    try:
        base = _host_which(compiler)
        path = Path(base) if base else Path(str(compiler))
        if not path.is_absolute():
            return None
        cand = path.parent / tool
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    except Exception:
        pass
    return None


def _lto_flag(cxx):
    """LTO flag CMake's IPO uses for this compiler."""
    return "-flto=thin" if _cxx_is_clang(cxx) else "-flto"


def _fuse_ld_args(ld):
    return [
        f"-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld={ld}",
        f"-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld={ld}",
        f"-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld={ld}",
    ]


def _link_result(cc, *flags):
    """(ok, err): trial-link a trivial program; err is the first stderr line.

    Unverifiable compilers (binary not found, e.g. mocked paths in tests)
    return True so selection never blocks on probing alone; the real
    configure step still fails loudly on a truly missing compiler.
    """

    def _short(text):
        line = next((ln.strip() for ln in text.strip().splitlines() if ln.strip()), "")
        return line[:200] or "link failed"

    if _host_which(cc) is None and not Path(str(cc)).exists():
        return True, ""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            proc = _host_run(
                [cc, *flags, "-x", "c", "-", "-o", os.path.join(tmp, "probe")],
                input="int main(void){return 0;}\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=180,
            )
    except Exception as e:
        return False, str(e)[:200]
    if proc.returncode == 0:
        return True, ""
    return False, _short(proc.stderr or "")


def _link_accepts(cc, *flags):
    """True when CC links a trivial program with FLAGS."""
    ok, _ = _link_result(cc, *flags)
    return ok


def probe_lto_linkers(cc, cxx):
    """[(label, ok, err)] per linker tried, preference order; "" is default."""
    lto = _lto_flag(cxx)
    report = []
    for ld in _linker_candidates(cxx):
        ok, err = _link_result(cc, lto, f"-fuse-ld={ld}")
        report.append((ld, ok, err))
    ok, err = _link_result(cc, lto)
    report.append(("", ok, err))
    return report


def _mem_gb():
    """Total RAM in GiB, falling back to 8 when unreadable."""
    try:
        with open("/proc/meminfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return max(1, int(line.split()[1]) // 1024 // 1024)
    except Exception:
        pass
    return 8


def pick_build_jobs(use_lto):
    """Parallel jobs from core count and RAM (LTO needs ~3 GB per job)."""
    cores = os.cpu_count() or 1
    per_job = 3 if use_lto else 2
    return max(1, min(cores, _mem_gb() // per_job))


def _cmake_cached_cxx(build_dir):
    """CMAKE_CXX_COMPILER from a previous configure, or None."""
    try:
        for line in (Path(build_dir) / "CMakeCache.txt").read_text().splitlines():
            if line.startswith("CMAKE_CXX_COMPILER:"):
                return line.split("=", 1)[1].strip() or None
    except Exception:
        pass
    return None


def _kill_proc(proc):
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _stream_proc(proc, log_cb=None, cancelled=None, on_proc=None):
    """Stream proc stdout to log_cb; raise BuildCancelled when cancelled.

    The Stop button also terminates the stored proc directly (for quiet
    periods with no output), so this drains until EOF then checks the flag
    before the caller inspects the return code.
    """
    if on_proc:
        try:
            on_proc(proc)
        except Exception:
            pass
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if cancelled and cancelled():
                    _kill_proc(proc)
                    raise BuildCancelled("Build cancelled.")
                if log_cb:
                    log_cb(line.rstrip())
        proc.wait()
        if cancelled and cancelled():
            raise BuildCancelled("Build cancelled.")
    finally:
        if on_proc:
            try:
                on_proc(None)
            except Exception:
                pass


def _git_progress_fraction(line):
    """Determinate fraction from a git transfer progress line, or None.

    Only the local phases drive the bar: Receiving maps to 0-90% and
    Resolving deltas to 90-100%. Remote-side phases (Counting/Compressing)
    return None so the bar never jumps backwards between phases.
    """
    import re

    m = re.search(
        r"(?:remote:\s*)?(Receiving objects|Resolving deltas)[^\d]*(\d+)%(?:\s*\((\d+)/(\d+)\))?",
        line,
    )
    if not m:
        return None
    phase, pct, done, total = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    if done is not None and total is not None and int(total) > 0:
        frac = int(done) / int(total)
    else:
        frac = pct / 100.0
    frac = min(max(frac, 0.0), 1.0)
    return frac * 0.9 if phase == "Receiving objects" else 0.9 + frac * 0.1


def _git_noisy_line(line):
    """True for per-percent git progress spam worth sampling, not logging raw."""
    import re

    return bool(
        re.search(
            r"(?:remote:\s*)?(Counting objects|Compressing objects|Receiving objects|Resolving deltas).*?\d+%",
            line,
        )
    )


def _run_logged(cmd, log_cb=None, cancelled=None, check=True, progress_cb=None):
    """Run cmd, streaming merged stdout/stderr to log_cb line by line.

    Logs the command first so the scrolling log shows what ran. Git writes
    its transfer progress to stderr with carriage returns; in text mode
    those read as separate lines. Per-percent updates are sampled (every
    10% per phase) instead of logged raw, and Receiving/Resolving updates
    drive progress_cb with a 0-1 fraction when given. Raises BuildCancelled
    when cancelled, RuntimeError on nonzero exit when check is set.
    """
    import re

    if log_cb:
        try:
            log_cb("$ " + " ".join(str(c) for c in cmd))
        except Exception:
            pass
    proc = _host_popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logged_buckets = set()
    if proc.stdout is not None:
        for line in proc.stdout:
            if cancelled and cancelled():
                _kill_proc(proc)
                raise BuildCancelled("Build cancelled.")
            text = line.rstrip()
            if progress_cb is not None:
                try:
                    frac = _git_progress_fraction(text)
                except Exception:
                    frac = None
                if frac is not None:
                    try:
                        progress_cb(frac)
                    except Exception:
                        pass
            if log_cb:
                if _git_noisy_line(text):
                    m = re.search(
                        r"(Counting objects|Compressing objects|Receiving objects|Resolving deltas).*?(\d+)%",
                        text,
                    )
                    bucket = (m.group(1), int(m.group(2)) // 10) if m else (text, 0)
                    if bucket in logged_buckets:
                        continue
                    logged_buckets.add(bucket)
                log_cb(text)
    proc.wait()
    if cancelled and cancelled():
        raise BuildCancelled("Build cancelled.")
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed (exit {proc.returncode}); see log above.")
    return proc


def build_source(
    cfg, status_cb=None, log_cb=None, commit=None, cancelled=None, on_proc=None, progress_cb=None
):
    src, commit = ensure_source(
        cfg, status_cb, commit=commit, cancelled=cancelled, log_cb=log_cb, progress_cb=progress_cb
    )
    win = is_windows()
    binary_name = "eglantine.exe" if win else "eglantine"
    for tool in ("cmake", "ninja", "git", "g++"):
        if _host_which(tool) is None:
            raise RuntimeError(f"Missing required tool: {tool}")

    # CPU tuning (Linux only): detect the host x86-64 level and build for
    # it. Each level gets its own build dir so differently-tuned objects
    # never share a CMake cache.
    arch_flags, level_label = "", "generic"
    if not win:
        probe_cc, probe_cxx = _find_pgo_clang() if cfg.get("pgo", True) else (None, None)
        if not probe_cxx:
            probe_cxx = _host_which("clang++") or _host_which("g++") or "c++"
            probe_cc = _host_which("clang") or _host_which("gcc") or "cc"
        arch_flags, level_label = select_arch_flags(probe_cc, cfg.get("cpu_level", "auto"))
        if status_cb:
            status_cb(f"CPU target: x86-64-{level_label} ({arch_flags}).")

    build_dir = src / "build" / ("release" if win else f"release-{level_label}")
    binary = build_dir / "bin" / binary_name
    if get_project_last_built(cfg) == commit and binary.exists():
        if status_cb:
            status_cb("Source already up to date.")
        record_source_build(cfg, commit)
        return binary
    flags = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_TESTING=OFF",
        "-DYUZU_TESTS=OFF",
        "-DYUZU_CMD=ON",
        "-DYUZU_ROOM=OFF",
        "-DYUZU_ROOM_STANDALONE=OFF",
        "-DYUZU_USE_BUNDLED_SDL3=ON",
        "-DYUZU_USE_BUNDLED_FFMPEG=ON",
        "-DVulkanHeaders_FORCE_BUNDLED=ON",
        "-DVulkanUtilityLibraries_FORCE_BUNDLED=ON",
        "-DENABLE_UPDATE_CHECKER=OFF",
        "-DUSE_DISCORD_PRESENCE=OFF",
        "-DENABLE_WERROR=OFF",
        "-DYUZU_BUILD_PRESET=custom",
    ]
    if win:
        flags.append("-DYUZU_USE_BUNDLED_QT=ON")
        if shutil.which("clang-cl") or shutil.which("clang-cl.exe"):
            flags += ["-DCMAKE_C_COMPILER=clang-cl", "-DCMAKE_CXX_COMPILER=clang-cl"]
        qt = os.environ.get("QT_ROOT") or os.environ.get("QTDIR") or os.environ.get("CMAKE_PREFIX_PATH")
        if qt:
            flags.append(f"-DCMAKE_PREFIX_PATH={qt}")
        vulkan = os.environ.get("VULKAN_SDK")
        if vulkan:
            flags.append(f"-DCMAKE_PREFIX_PATH={vulkan};{qt}" if qt else f"-DCMAKE_PREFIX_PATH={vulkan}")
    else:
        flags.append("-DYUZU_USE_BUNDLED_QT=OFF")

    explicit_cxx = None
    pgo_active = False
    if cfg.get("pgo", True) and not win:
        cc, cxx = _find_pgo_clang()
        if not cxx:
            if status_cb:
                status_cb("PGO requested but no clang++ found; doing a regular build.")
        else:
            profile = _ensure_pgo_profile(status_cb)
            if profile is None:
                pass  # message already shown; regular build
            elif not _profile_readable(cxx, profile):
                if status_cb:
                    status_cb(f"clang can't read the PGO profile ({profile.name}); doing a regular build.")
                if log_cb:
                    log_cb(
                        "PGO profile unreadable by this clang; "
                        f"delete {profile} to re-download, or turn PGO off."
                    )
            else:
                explicit_cxx = cxx
                flags += [f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}"]
                # NOTE: -fuse-ld goes in linker flags only. In CFLAGS it warns
                # "unused during compilation" on every TU, which is fatal for
                # externals built with -Werror (e.g. sirit).
                c_flags = f"{arch_flags} " if arch_flags else ""
                prof = (
                    f"-fprofile-use={profile} "
                    "-Wno-backend-plugin -Wno-profile-instr-unprofiled "
                    "-Wno-profile-instr-out-of-date"
                )
                flags += [
                    f"-DCMAKE_C_FLAGS={c_flags}{prof}",
                    f"-DCMAKE_CXX_FLAGS={c_flags}{prof}",
                ]
                pgo_active = True
                if status_cb:
                    status_cb(f"PGO build with {cxx}, profile {profile.name}.")
    elif cfg.get("pgo", True) and win and status_cb:
        status_cb("PGO source builds are Linux-only for now; doing a regular build.")

    if not pgo_active and arch_flags:
        flags += [
            f"-DCMAKE_C_FLAGS={arch_flags}",
            f"-DCMAKE_CXX_FLAGS={arch_flags}",
        ]

    use_lto = bool(cfg.get("lto", True))
    link_args, lto_ok = [], True
    if not win:
        link_cc = explicit_cxx or probe_cc or "cc"
        link_cxx = explicit_cxx or probe_cxx or "c++"
        link_args, lto_ok = pick_build_linker(link_cc, link_cxx, use_lto)
        flags += link_args
        if use_lto and not lto_ok and status_cb:
            status_cb("LTO off: no linker here links LTO objects; doing a non-LTO build.")
    if use_lto and lto_ok:
        flags.append("-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON")
    else:
        flags += ["-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF", "-DENABLE_LTO=OFF"]

    if not win:
        if _host_which("ccache"):
            flags += [
                "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
                "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
            ]
        else:
            # Clear a launcher cached by an earlier run; the binary may be gone.
            flags += [
                "-DCMAKE_C_COMPILER_LAUNCHER=",
                "-DCMAKE_CXX_COMPILER_LAUNCHER=",
            ]

    build_jobs = pick_build_jobs(use_lto or pgo_active)
    if status_cb:
        status_cb(f"Building with {build_jobs} jobs...")

    # tool checks
    for tool in ("cmake", "ninja", "git"):
        if _host_which(tool) is None and _host_which(tool + ".exe") is None:
            raise RuntimeError(f"Missing required tool: {tool}")
    if win:
        if not (
            shutil.which("cl")
            or shutil.which("cl.exe")
            or shutil.which("clang-cl")
            or shutil.which("clang-cl.exe")
        ):
            raise RuntimeError(
                "Missing C++ compiler (cl.exe or clang-cl.exe). Run from a Developer Command Prompt."
            )

    # CMake caches the compiler on the first configure and ignores CC/CXX
    # after that. If this build dir was configured with a different
    # compiler, drop the cache so flags and linker below still match.
    if explicit_cxx and (build_dir / "CMakeCache.txt").exists():
        cached = _cmake_cached_cxx(build_dir)
        try:
            same = cached and os.path.realpath(cached) == os.path.realpath(
                _host_which(explicit_cxx) or explicit_cxx
            )
        except Exception:
            same = False
        if not same:
            if status_cb:
                status_cb(f"Build dir used {cached}; resetting CMake cache for {explicit_cxx}.")
            try:
                (build_dir / "CMakeCache.txt").unlink()
            except FileNotFoundError:
                pass
            shutil.rmtree(build_dir / "CMakeFiles", ignore_errors=True)

    if status_cb:
        status_cb("Configuring source build...")
    if cancelled and cancelled():
        raise BuildCancelled("Build cancelled.")
    cmake_cmd = ["cmake", "-S", str(src), "-B", str(build_dir), "-G", "Ninja", *flags]
    proc = _host_popen(
        cmake_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _stream_proc(proc, log_cb=log_cb, cancelled=cancelled, on_proc=on_proc)
    if proc.returncode != 0:
        raise RuntimeError(f"Configure failed (exit {proc.returncode}); see log above.")
    # Final guard: the compiler CMake actually used must be the one planned.
    # (Skipped when there is no cache to read, e.g. a failed configure.)
    if explicit_cxx:
        used_cxx = _cmake_cached_cxx(build_dir)
        if used_cxx:
            try:
                mismatch = os.path.realpath(used_cxx) != os.path.realpath(
                    _host_which(explicit_cxx) or explicit_cxx
                )
            except Exception:
                mismatch = True
            if mismatch:
                raise RuntimeError(
                    f"CMake is using {used_cxx}, expected {explicit_cxx}; delete {build_dir} and rebuild."
                )
    if status_cb:
        status_cb("Building (this may take a while)...")
    if cancelled and cancelled():
        raise BuildCancelled("Build cancelled.")
    build_cmd = [
        "cmake",
        "--build",
        str(build_dir),
        "--target",
        "yuzu",
        "yuzu-cmd",
        "--parallel",
        str(build_jobs),
    ]
    proc = _host_popen(
        build_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _stream_proc(proc, log_cb=log_cb, cancelled=cancelled, on_proc=on_proc)
    if proc.returncode != 0:
        if cancelled and cancelled():
            raise BuildCancelled("Build cancelled.")
        raise RuntimeError("Build failed")
    record_source_build(cfg, commit)
    if status_cb:
        status_cb("Build complete.")
    return binary


def _which(name):
    return shutil.which(name) or shutil.which(name + ".exe")


def _in_flatpak():
    return bool(os.environ.get("FLATPAK_ID"))


LAUNCHER_REPO = "SavageCore/eglantine-launcher"
LAUNCHER_API_LATEST = f"https://api.github.com/repos/{LAUNCHER_REPO}/releases/latest"
REPOS_PAGE = "https://savagecore.github.io/eglantine-launcher/"
FLATPAK_APP_ID = "dev.savagecore.EglantineLauncher"
DNF_REPO_PATH = "/etc/yum.repos.d/eglantine-launcher.repo"
APT_SOURCE_PATH = "/etc/apt/sources.list.d/eglantine-launcher.list"
APPIMAGE_ASSET_NAME = "eglantine-launcher-x86_64.AppImage"
WINDOWS_ASSET_NAME = "eglantine-launcher-windows.zip"
UPDATE_COMMANDS = {
    "dnf": "sudo dnf upgrade eglantine-launcher",
    "apt": "sudo apt update && sudo apt install eglantine-launcher",
    "flatpak": f"flatpak update {FLATPAK_APP_ID}",
}


def detect_install_method():
    """Return how this copy of the launcher was installed.

    One of "windows", "flatpak", "appimage", "dnf", "apt",
    "manual-pkg" (standalone .deb/.rpm with no repo configured) or
    "source" (git checkout, pip, anything else).
    """
    if getattr(sys, "frozen", False) or is_windows():
        return "windows"
    if os.environ.get("FLATPAK_ID"):
        return "flatpak"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    try:
        here = str(Path(__file__).resolve())
    except Exception:
        return "source"
    if here.startswith("/app/"):
        return "flatpak"
    if "/usr/lib/eglantine-launcher" in here:
        if Path(DNF_REPO_PATH).exists():
            return "dnf"
        if Path(APT_SOURCE_PATH).exists():
            return "apt"
        return "manual-pkg"
    return "source"


def launcher_update_asset(assets, method):
    """Pick the release asset dict for a one-click update, or None."""
    if not assets:
        return None
    if method == "appimage":
        for a in assets:
            if a.get("name") == APPIMAGE_ASSET_NAME:
                return a
        for a in assets:
            if str(a.get("name", "")).endswith(".AppImage"):
                return a
        return None
    if method == "windows":
        for a in assets:
            if a.get("name") == WINDOWS_ASSET_NAME:
                return a
        for a in assets:
            if str(a.get("name", "")).endswith(".zip") and "windows" in str(a.get("name", "")).lower():
                return a
        return None
    return None


def self_update_dialog_text(method, tag):
    """Return (title, body) for the update dialog for an install method."""
    title = f"Launcher update available: {tag}"
    if method == "dnf":
        body = (
            "This copy is managed by dnf, so update it from the terminal:\n\n"
            f"{UPDATE_COMMANDS['dnf']}\n\n"
            "Do not download files from GitHub for this install."
        )
    elif method == "apt":
        body = (
            "This copy is managed by apt, so update it from the terminal:\n\n"
            f"{UPDATE_COMMANDS['apt']}\n\n"
            "Do not download files from GitHub for this install."
        )
    elif method == "flatpak":
        body = (
            "This copy is managed by Flatpak, so update it from the terminal:\n\n"
            f"{UPDATE_COMMANDS['flatpak']}\n\n"
            "Do not download files from GitHub for this install."
        )
    elif method == "manual-pkg":
        body = (
            "This copy was installed from a standalone package file, so it "
            "cannot update itself.\n\n"
            "For automatic updates, set up the package repo once:\n"
            f"{REPOS_PAGE}\n\n"
            "Or download the new package from the release page and install it manually."
        )
    else:
        body = (
            "Download the new release and install it manually.\n\n"
            "Tip: the dnf/apt repos and the Flatpak remote update automatically - "
            "see:\n"
            f"{REPOS_PAGE}"
        )
    return title, body


def download_url(url, dest, progress_cb=None, timeout=120):
    """Download url to dest (Path). Binary DLs bypass the API throttle."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "eglantine-launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = r.headers.get("Content-Length")
        total = int(total) if total and str(total).isdigit() else 0
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    try:
                        progress_cb(min(1.0, done / total))
                    except Exception:
                        pass
    return dest


def stage_appimage_swap(current, staged):
    """Atomically replace the running AppImage with a downloaded one."""
    staged = Path(staged)
    os.chmod(staged, 0o755)
    os.replace(staged, current)
    return Path(current)


def build_windows_updater(install_dir, staged_dir, exe_name="eglantine-launcher.exe"):
    """Return a cmd script that swaps in a staged Windows update.

    The app spawns this detached and quits; the script waits for the
    parent PID (%1), mirrors staged_dir over install_dir, restarts the
    exe, removes the staging dir and deletes itself.
    """
    return (
        "@echo off\r\n"
        "set PID=%1\r\n"
        ":wait\r\n"
        'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul && '
        "(timeout /t 1 /nobreak >nul & goto wait)\r\n"
        f'robocopy "{staged_dir}" "{install_dir}" /MIR /NJH /NJS /NDL\r\n'
        f'start "" "{install_dir}\\{exe_name}"\r\n'
        f'rmdir /s /q "{staged_dir}"\r\n'
        'del "%~f0"\r\n'
    )


def windows_install_dir():
    """Install dir of the frozen Windows build (the COLLECT folder)."""
    return Path(sys.executable).resolve().parent


def open_url(url):
    """Open url in a browser. Works on Windows, Linux and in the Flatpak sandbox."""
    try:
        if _in_flatpak() and shutil.which("flatpak-spawn"):
            subprocess.Popen(["flatpak-spawn", "--host", "xdg-open", url])
            return True
        if is_windows():
            try:
                os.startfile(url)  # noqa: S606
                return True
            except Exception:
                pass
        import webbrowser

        webbrowser.open(url)
        return True
    except Exception:
        try:
            subprocess.Popen(["xdg-open", url])
            return True
        except Exception:
            return False


def _host_which(name):
    """Locate a tool, on the host when sandboxed."""
    if not _in_flatpak():
        return _which(name)
    try:
        out = subprocess.check_output(
            ["flatpak-spawn", "--host", "sh", "-c", f"command -v {name} || command -v {name}.exe"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).strip()
        return out.splitlines()[0] if out else None
    except Exception:
        return None


def _host_run(cmd, **kwargs):
    """subprocess.run wrapper that escapes the flatpak sandbox via the host."""
    return subprocess.run(_on_host(cmd), **kwargs)


def _host_popen(cmd, **kwargs):
    return subprocess.Popen(_on_host(cmd), **kwargs)


def _host_check_output(cmd, **kwargs):
    if _in_flatpak() and shutil.which("flatpak-spawn"):
        cmd = ["flatpak-spawn", "--host", *cmd]
    return subprocess.check_output(cmd, **kwargs)


def check_build_deps():
    """Return [(name, ok, detail, required)] for the current platform."""
    results = []

    def tool(name, min_version=None, hint=""):
        path = _host_which(name)
        if not path:
            return (name, False, "not found" + (f" ({hint})" if hint else ""), True)
        try:
            out = _host_check_output([path, "--version"], text=True, stderr=subprocess.STDOUT, timeout=10)
            first = out.strip().splitlines()[0] if out.strip() else "found"
        except Exception:
            first = "found"
        return (name, True, f"{first} [{path}]", True)

    results.append(tool("cmake", hint="https://cmake.org/download"))
    results.append(tool("ninja", hint="https://ninja-build.org"))
    results.append(tool("git", hint="https://git-scm.com/downloads"))
    results.append(tool("python3" if not is_windows() else "python", hint="https://www.python.org/downloads"))

    if is_windows():
        has_cl = bool(_host_which("cl") or _host_which("clang-cl"))
        if has_cl:
            cc = _host_which("cl") or _host_which("clang-cl")
            results.append(("C++ compiler (cl/clang-cl)", True, f"found [{cc}]", True))
        else:
            results.append(
                (
                    "C++ compiler (cl/clang-cl)",
                    False,
                    "not found - run from a Developer Command Prompt or install clang-cl",
                    True,
                )
            )
        qt = os.environ.get("QT_ROOT") or os.environ.get("CMAKE_PREFIX_PATH")
        qt_ok = bool(qt) or Path("C:/Qt").exists()
        results.append(
            (
                "Qt 6 (MSVC)",
                qt_ok,
                f"found [{qt or 'C:/Qt'}]" if qt_ok else "not found - set QT_ROOT or install to C:\\Qt",
                True,
            )
        )
        vulkan = os.environ.get("VULKAN_SDK") or (Path("C:/VulkanSDK").exists() and "C:/VulkanSDK")
        results.append(
            (
                "Vulkan SDK",
                bool(vulkan),
                f"found [{vulkan}]"
                if vulkan
                else "not found - set VULKAN_SDK (tools/windows/install-vulkan-sdk.ps1)",
                True,
            )
        )
    else:
        cc = _host_which("g++") or _host_which("clang++")
        if cc:
            results.append(("C++ compiler (g++/clang++)", True, f"found [{cc}]", True))
        else:
            results.append(
                ("C++ compiler (g++/clang++)", False, "not found - install gcc-c++ or clang", True)
            )
        results.append(tool("pkg-config", hint="pkgconf or pkg-config package"))
        clang = _find_pgo_clang()[1]
        results.append(
            (
                "clang++ (PGO source builds)",
                bool(clang),
                f"found [{clang}]" if clang else "not found - PGO builds fall back to regular builds",
                False,
            )
        )
        qmake = _host_which("qmake6") or _host_which("qmake")
        qt_ok = False
        detail = "not found - install Qt 6 base + qtbase-private dev files"
        if qmake:
            qt_ok = True
            detail = f"found [{qmake}]"
        else:
            for cand in ("/usr/lib64/cmake/Qt6", "/usr/lib/x86_64-linux-gnu/cmake/Qt6", "/usr/lib/cmake/Qt6"):
                if Path(cand, "Qt6Config.cmake").exists():
                    qt_ok = True
                    detail = f"found [{cand}]"
                    break
        results.append(("Qt 6 dev files", qt_ok, detail, True))
        glslang = _host_which("glslangValidator")
        results.append(
            (
                "glslangValidator",
                bool(glslang),
                f"found [{glslang}]" if glslang else "not found - install glslang-tools (warning only)",
                False,
            )
        )
        ccache = _host_which("ccache")
        results.append(
            (
                "ccache",
                bool(ccache),
                f"found [{ccache}]" if ccache else "not found - optional, speeds up rebuilds",
                False,
            )
        )
    return results


# XDG_CONFIG_HOME is computed per source at launch time and always wins;
# users cannot override it via custom_env.
RESERVED_ENV_KEYS = frozenset({"XDG_CONFIG_HOME"})


def is_valid_env_name(name):
    """POSIX-ish env name: letters, digits and underscore, not starting with a digit."""
    import re

    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""))


def emulator_env(cfg):
    """User-configured extra env vars for the emulator child process.

    Returns a plain NAME -> value dict with invalid names and reserved
    keys dropped. Values are coerced to str; non-dict storage yields {}.
    """
    raw = (cfg or {}).get("custom_env") if isinstance(cfg, dict) else None
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not is_valid_env_name(name):
            continue
        if name in RESERVED_ENV_KEYS:
            continue
        if value is None:
            continue
        clean[name] = value if isinstance(value, str) else str(value)
    return clean


def _on_host(cmd, env=None):
    """Run cmd on the host when sandboxed (flatpak), else directly.

    ``env`` is forwarded explicitly for flatpak: flatpak-spawn does not
    pass the caller's environment through to the host process.
    """
    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        flags = [f"--env={k}={v}" for k, v in (env or {}).items()]
        return ["flatpak-spawn", "--host", *flags, *cmd]
    return cmd


def launch(path, cfg=None):
    """Start the emulator with this source's own config folder.

    Returns the child process, or None when it cannot be tracked.
    """
    path = Path(path)
    info = project_info(cfg) if isinstance(cfg, dict) else {}
    try:
        # Link legacy/fork emulator dirs first so the snapshot below reads
        # the canonical tree even when an old build ran last. backup_saves
        # never raises, so a broken backup dir can't block play.
        if isinstance(cfg, dict):
            migrate_emulator_dirs(cfg)
            backup_saves(cfg, path)
        home = seed_source_config(cfg, info) if isinstance(cfg, dict) else source_config_home(info)
    except OSError:
        # A config folder that cannot be written must not block the launch.
        home = source_config_home(info)
    if is_windows():
        # Windows has no XDG. Portable mode keys off a ``user`` dir next to
        # the exe, and the launcher already keeps one exe dir per source.
        try:
            (path.parent / "user").mkdir(exist_ok=True)
        except OSError:
            pass
        os.startfile(str(path))
        return None
    os.chmod(path, 0o755)
    env = {**emulator_env(cfg), "XDG_CONFIG_HOME": str(home)}
    return subprocess.Popen(
        _on_host([str(path)], env=env),
        env={**os.environ, **env},
        start_new_session=True,
    )
