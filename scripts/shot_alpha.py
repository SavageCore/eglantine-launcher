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
        img.save(path)
        print(f"alpha: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
