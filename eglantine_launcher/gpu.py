import shutil
import subprocess


def gpu_vendors():
    """Return a set like {'nvidia'}, {'amd'}, {'intel'} (best effort)."""
    vendors = set()
    try:
        lspci = shutil.which("lspci")
        if lspci:
            out = subprocess.check_output([lspci, "-nn"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                # only GPU/display controllers, not chipsets/USB/audio
                if not any(
                    k in line for k in ("VGA compatible controller", "3D controller", "Display controller")
                ):
                    continue
                low = line.lower()
                if "nvidia" in low:
                    vendors.add("nvidia")
                if "amd" in low or "advanced micro devices" in low:
                    vendors.add("amd")
                if "intel" in low:
                    vendors.add("intel")
            if vendors:
                return vendors
    except Exception:
        pass
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Video") as _:
            pass
    except Exception:
        pass
    return vendors
