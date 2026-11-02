# eglantine-launcher - agent notes

## AppImage refresh

After finishing work, rebuild `~/eglantine-launcher.AppImage` from the working tree:

```sh
env -u APPDIR bash packaging/build-appimage.sh
```

`env -u APPDIR` is required, not optional. This session inherits
`APPDIR=/tmp/.mount_*` from the host AppImage runtime, and the script's
`${APPDIR:-/tmp/opencode/launcher-appdir}` default resolves to that
read-only mount, so a bare run tries to `rm -rf` it and aborts.

Never build to a `-new` suffixed file. If the build fails with
`Text file busy`, the launcher is currently running: stop and tell the
user to quit it, then rebuild in place.
