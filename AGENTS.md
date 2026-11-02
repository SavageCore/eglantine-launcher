# eglantine-launcher - agent notes

## AppImage refresh

After finishing work, rebuild `~/eglantine-launcher.AppImage` from the working tree:

```sh
make appimage
```

This runs `packaging/build-appimage.sh` with a clean environment.
`env -u APPDIR` is required, not optional. This session inherits
`APPDIR=/tmp/.mount_*` from the host AppImage runtime, and the script's
`${APPDIR:-/tmp/opencode/launcher-appdir}` default resolves to that
read-only mount, so a bare run tries to `rm -rf` it and aborts.

Never build to a `-new` suffixed file. If the build fails with
`Text file busy`, the launcher is currently running: stop and tell the
user to quit it, then rebuild in place.

## Committing work

Commit as you go: one commit per finished feature, straight on the
working branch, with the suite and `ruff check` / `ruff format --check`
green. Larger work goes on a feature branch; when done and tested it
gets squashed and merged. Never batch unrelated features into a single
end-of-session commit.

Commit subjects must be conventional - the `commit-msg` hook rejects
anything else, so get it right the first time:
`type(scope): summary` (e.g. `fix(source): ...`), a plain version
(`0.1.3`), or `Merge ...`. Allowed types: feat, fix, refactor, perf,
docs, chore, style, test, ci, build.

## Release

```sh
make release          # patch bump (0.1.6 -> 0.1.7)
make release patch    # same as above
make release minor    # 0.1.6 -> 0.2.0
make release major    # 0.1.6 -> 1.0.0
make release V=0.2.0  # explicit version (cannot combine with a bump type)
```

`scripts/release.sh` requires main with a clean tree, runs `make check`,
bumps `__version__` in `eglantine_launcher/__init__.py`, commits (message
is the version only), pushes main, tags `v<version>`, and pushes the tag.
CI builds the release from the tag. Patch bumps are the norm here, even
for feats.
