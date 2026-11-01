"""Fullscreen magenta backdrop for screenshots (override-redirect, kept at bottom)."""

import sys
import time

from Xlib import X, display

d = display.Display(sys.argv[1] if len(sys.argv) > 1 else ":99")
screen = d.screen()
root = screen.root
magenta = screen.default_colormap.alloc_named_color("#FF00FF").pixel
geom = root.get_geometry()
win = root.create_window(
    0,
    0,
    geom.width,
    geom.height,
    0,
    screen.root_depth,
    X.InputOutput,
    X.CopyFromParent,
    background_pixel=magenta,
    override_redirect=True,
)
win.map()
win.configure(stack_mode=X.Below)
d.sync()
# stay alive until killed; re-lower if anything restacks above us is not needed
# since app windows always stack above override-redirect windows by default
while True:
    time.sleep(3600)
