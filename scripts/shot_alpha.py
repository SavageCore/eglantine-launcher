"""Make near-magenta corners transparent (rounded CSD corners over backdrop)."""

import sys

from PIL import Image, ImageDraw

MAGENTA = (255, 0, 255)


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
        img.save(path)
        print(f"alpha: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
