import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .config import save as save_config

_token_rejected = False


def _api_headers(cfg=None):
    headers = {"User-Agent": "eglantine-launcher"}
    if not _token_rejected:
        token = os.environ.get("GITHUB_TOKEN") or (cfg or {}).get("github_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def api_get_json(url, cfg=None, timeout=30):
    """GET JSON, falling back to anonymous if the token is rejected (401)."""
    global _token_rejected
    req = urllib.request.Request(url, headers=_api_headers(cfg))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code != 401 or _token_rejected or "Authorization" not in dict(req.header_items()):
            raise
        _token_rejected = True
        req = urllib.request.Request(url, headers=_api_headers(cfg))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())


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


def infer_releases_url(source_url):
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return None
    if host == "github":
        return f"https://api.github.com/repos/{owner}/{repo}/releases"
    if host == "forgejo" or "git.eden-emu.dev" in (source_url or ""):
        return f"https://git.eden-emu.dev/api/v1/repos/{owner}/{repo}/releases"
    return None


def list_branches(source_url, limit=100, cfg=None):
    """Return branch name list for a github or forgejo repo URL."""
    host, owner, repo = parse_repo(source_url)
    if not owner or not repo:
        return []
    if host == "github":
        url = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page={limit}"
    elif host == "forgejo" or "git.eden-emu.dev" in (source_url or ""):
        url = f"https://git.eden-emu.dev/api/v1/repos/{owner}/{repo}/branches?limit={limit}"
    else:
        return []
    data = api_get_json(url, cfg, timeout=20)
    names = []
    for b in data if isinstance(data, list) else []:
        n = b.get("name") if isinstance(b, dict) else None
        if n:
            names.append(n)
    return names


def fetch_repo_name(source_url, cfg=None):
    host, owner, repo = parse_repo(source_url)
    if not repo:
        return ""
    if host == "github":
        url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            return api_get_json(url, cfg, timeout=20).get("name", repo)
        except Exception:
            return repo
    if host == "forgejo" or "git.eden-emu.dev" in (source_url or ""):
        url = f"https://git.eden-emu.dev/api/v1/repos/{owner}/{repo}"
        try:
            return api_get_json(url, cfg, timeout=20).get("name", repo)
        except Exception:
            return repo
    return repo


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


def list_commits(cfg, limit=15):
    info = project_info(cfg)
    branch = info.get("branch", "master")
    host, owner, repo = parse_repo(info.get("source_url", ""))
    if not owner or not repo:
        return []
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?sha={branch}&per_page={limit}"
    # forgejo uses different API path
    if host == "forgejo" or "git.eden-emu.dev" in info.get("source_url", ""):
        # Forgejo API
        owner_repo = info["source_url"].split("git.eden-emu.dev/")[1].rstrip(".git")
        url = f"https://git.eden-emu.dev/api/v1/repos/{owner_repo}/commits?sha={branch}&limit={limit}"
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


def _project_by_id(cfg, pid):
    return next((p for p in cfg.get("projects", []) if p.get("id") == pid), {})


def shortcut_name(project):
    return f"{project.get('name', project.get('id', 'Eglantine'))} (EL)"


def shortcut_path(cfg, pid):
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (pid or "eglantine"))
    return applications_dir() / f"eglantine-launcher-{safe}.desktop"


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


def install_shortcut(cfg, pid, app_root):
    """Create/update the per-emu desktop shortcut. Returns its path."""
    project = _project_by_id(cfg, pid or cfg.get("project"))
    if not project:
        raise RuntimeError("Unknown project")
    icon = project_icon(cfg, project)
    if icon is None:
        icon = Path(app_root) / "data" / "icons" / "eglantine-launcher.png"
    content = f"""[Desktop Entry]
Type=Application
Name={shortcut_name(project)}
Comment=Check for updates, then launch {project.get("name", project.get("id"))}
Exec=env PYTHONPATH={app_root} {sys.executable} -m eglantine_launcher --auto --project {project["id"]}
Icon={icon}
Terminal=false
Categories=Game;Emulator;
"""
    applications_dir().mkdir(parents=True, exist_ok=True)
    path = shortcut_path(cfg, project["id"])
    path.write_text(content)
    try:
        subprocess.run(["update-desktop-database", str(applications_dir())], capture_output=True)
    except Exception:
        pass
    return path


def remove_shortcut(cfg, pid):
    path = shortcut_path(cfg, pid or cfg.get("project"))
    if path.exists():
        path.unlink()
    return not path.exists()


def source_dir(cfg):
    return Path(cfg["source_dir"]) / cfg.get("project", "eglantine")


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


def ensure_source(cfg, status_cb=None, commit=None):
    info = project_info(cfg)
    src = source_dir(cfg)
    src.parent.mkdir(parents=True, exist_ok=True)
    if not (src / ".git").exists():
        if status_cb:
            status_cb("Cloning source...")
        _host_run(["git", "clone", "--branch", info["branch"], info["source_url"], str(src)], check=True)
    else:
        if status_cb:
            status_cb("Fetching source updates...")
        _host_run(["git", "-C", str(src), "fetch", "origin", info["branch"]], check=True)
    if commit:
        _host_run(["git", "-C", str(src), "checkout", commit], check=True)
    else:
        _host_run(["git", "-C", str(src), "checkout", info["branch"]], check=True, capture_output=True)
        _host_run(
            ["git", "-C", str(src), "pull", "--ff-only", "origin", info["branch"]],
            check=False,
            capture_output=True,
        )
    commit = _host_check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
    return src, commit


PGO_PROFILE_URL = "https://github.com/Eden-CI/PGO/releases/latest/download/eden.profdata"
SOURCES_URL = "https://raw.githubusercontent.com/SavageCore/eglantine-launcher/main/data/sources.json"


def fetch_remote_sources(cfg=None, timeout=20):
    """Source registry from the repo. Returns [] offline (callers keep shipped defaults)."""
    try:
        data = api_get_json(SOURCES_URL, cfg, timeout=timeout)
        sources = data.get("sources", []) if isinstance(data, dict) else []
        return [s for s in sources if isinstance(s, dict) and s.get("id") and s.get("source_url")]
    except Exception:
        return []


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


def build_source(cfg, status_cb=None, log_cb=None, commit=None):
    src, commit = ensure_source(cfg, status_cb, commit=commit)
    binary_name = "eglantine.exe" if is_windows() else "eglantine"
    binary = src / "build" / "release" / "bin" / binary_name
    if cfg.get("last_source_commit") == commit and binary.exists():
        if status_cb:
            status_cb("Source already up to date.")
        cfg["last_source_commit"] = commit
        _record(cfg, "built_commits", commit)
        save_config(cfg)
        return binary
    for tool in ("cmake", "ninja", "git", "g++"):
        if _host_which(tool) is None:
            raise RuntimeError(f"Missing required tool: {tool}")
    build_dir = src / "build" / "release"
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
    ]
    if is_windows():
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

    build_jobs = None
    if cfg.get("pgo", True) and not is_windows():
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
                flags += [f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}"]
                # NOTE: -fuse-ld goes in linker flags only. In CFLAGS it warns
                # "unused during compilation" on every TU, which is fatal for
                # externals built with -Werror (e.g. sirit).
                prof = (
                    f"-fprofile-use={profile} "
                    "-Wno-backend-plugin -Wno-profile-instr-unprofiled "
                    "-Wno-profile-instr-out-of-date"
                )
                flags += [
                    f"-DCMAKE_C_FLAGS={prof}",
                    f"-DCMAKE_CXX_FLAGS={prof}",
                    "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=bfd",
                    "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=bfd",
                    "-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=bfd",
                ]
                if (os.cpu_count() or 1) > 6:
                    build_jobs = 6  # PGO TUs use far more RAM; avoids swap death
                if status_cb:
                    status_cb(f"PGO build with {cxx}, profile {profile.name}.")
    elif cfg.get("pgo", True) and is_windows() and status_cb:
        status_cb("PGO source builds are Linux-only for now; doing a regular build.")

    # tool checks
    for tool in ("cmake", "ninja", "git"):
        if _host_which(tool) is None and _host_which(tool + ".exe") is None:
            raise RuntimeError(f"Missing required tool: {tool}")
    if is_windows():
        if not (
            shutil.which("cl")
            or shutil.which("cl.exe")
            or shutil.which("clang-cl")
            or shutil.which("clang-cl.exe")
        ):
            raise RuntimeError(
                "Missing C++ compiler (cl.exe or clang-cl.exe). Run from a Developer Command Prompt."
            )

    if status_cb:
        status_cb("Configuring source build...")
    cmake_cmd = ["cmake", "-S", str(src), "-B", str(build_dir), "-G", "Ninja", *flags]
    proc = _host_popen(
        cmake_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if log_cb:
        for line in proc.stdout:
            log_cb(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Configure failed (exit {proc.returncode}); see log above.")
    if status_cb:
        status_cb("Building (this may take a while)...")
    build_cmd = ["cmake", "--build", str(build_dir), "--target", "yuzu", "yuzu-cmd", "--parallel"]
    if build_jobs:
        build_cmd.append(str(build_jobs))
    proc = _host_popen(
        build_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if log_cb:
        for line in proc.stdout:
            log_cb(line.rstrip())
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("Build failed")
    cfg["last_source_commit"] = commit
    _record(cfg, "built_commits", commit)
    save_config(cfg)
    if status_cb:
        status_cb("Build complete.")
    return binary


def _which(name):
    return shutil.which(name) or shutil.which(name + ".exe")


def _in_flatpak():
    return bool(os.environ.get("FLATPAK_ID"))


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


def _on_host(cmd):
    """Run cmd on the host when sandboxed (flatpak), else directly."""
    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", *cmd]
    return cmd


def launch(path):
    path = Path(path)
    if is_windows():
        os.startfile(str(path))
        return
    os.chmod(path, 0o755)
    subprocess.Popen(_on_host([str(path)]), start_new_session=True)
