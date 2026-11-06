"""Make near-magenta corners transparent (rounded CSD corners over backdrop),
then squeeze the PNG as small as a local ImgBot would: oxipng (or optipng)
when installed, else Pillow's own optimizer."""

import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

MAGENTA = (255, 0, 255)


def optimize(path):
    """Best local PNG squeeze available; never fails the screenshot run."""
    if shutil.which("oxipng"):
        cmd = ["oxipng", "-o2", "--strip", "safe", "--quiet", path]
    elif shutil.which("optipng"):
        cmd = ["optipng", "-o2", "-quiet", path]
    else:
        return False
    try:
        subprocess.run(cmd, timeout=120, check=True, capture_output=True)
    except Exception:
        return False
    return True


def main(paths):
    for path in paths:
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            ImageDraw.floodfill(img, xy, (0, 0, 0, 0), thresh=40)
        # import -window captures a 1px frame of the magenta backdrop
        # around the window edge; the floodfill above follows that
        # connected ring and clears it. The theme also draws its own
        # CSD frame inside the window (3px border + 1px outline), which
        # reads as a white/grey border around the shot. Crop the whole
        # 5px so the content fills the image edge to edge.
        if w > 10 and h > 10:
            img = img.crop((5, 5, w - 5, h - 5))
        img.save(path, optimize=True)
        if optimize(path):
            print(f"alpha+squeezed: {path}")
        else:
            print(f"alpha: {path} (install oxipng for smaller files)")


if __name__ == "__main__":
    main(sys.argv[1:])
