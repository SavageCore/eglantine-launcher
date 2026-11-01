#!/bin/sh
# System-wide launcher: sources live under /usr/lib/eglantine-launcher
exec /usr/bin/python3 -c "import sys; sys.path.insert(0, '/usr/lib/eglantine-launcher'); from eglantine_launcher.main import main; raise SystemExit(main())" "$@"
