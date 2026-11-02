#!/usr/bin/env bash
# Cut a release: requires main + clean tree, gates on `make check`,
# bumps eglantine_launcher/__init__.py (default patch), commits (message
# is the version only), pushes main, tags v<version>, pushes the tag.
# CI builds the release from the tag.
#
#   make release          # 0.1.6 -> 0.1.7
#   make release patch    # same as above
#   make release minor    # 0.1.6 -> 0.2.0
#   make release major    # 0.1.6 -> 1.0.0
#   make release V=0.2.0  # explicit version (cannot combine with a bump type)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

[ "$(git branch --show-current)" = "main" ] || { echo "error: releases cut from main" >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "error: working tree dirty" >&2; exit 1; }

V="${1:-}"
TYPE="${2:-}"
if [ -n "$V" ] && [ -n "$TYPE" ]; then
  echo "error: pass V=0.2.0 or major|minor|patch, not both" >&2
  exit 1
fi
TYPE="${TYPE:-patch}"
case "$TYPE" in
  major|minor|patch) ;;
  *) echo "error: bad bump type '$TYPE' (want major, minor or patch)" >&2; exit 1 ;;
esac

make check

CUR="$(python3 -c 'import re; print(re.search(r"__version__ = \"([^\"]+)\"", open("eglantine_launcher/__init__.py").read()).group(1))')"
if [ -z "$V" ]; then
  V="$(python3 -c "v='$CUR'.split('.'); t='$TYPE'; print('.'.join([str(int(v[0]) + 1), '0', '0'] if t == 'major' else [v[0], str(int(v[1]) + 1), '0'] if t == 'minor' else v[:2] + [str(int(v[2]) + 1)]))")"
fi
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "error: bad version '$V'" >&2; exit 1; }
git rev-parse "v$V" >/dev/null 2>&1 && { echo "error: tag v$V already exists" >&2; exit 1; }

sed -i "s/^__version__ = .*/__version__ = \"$V\"/" eglantine_launcher/__init__.py
grep "^__version__ = \"$V\"$" eglantine_launcher/__init__.py
git add eglantine_launcher/__init__.py
git commit -m "$V"

git push origin main
git tag "v$V"
git push origin "v$V"
[ "$(git tag --points-at HEAD)" = "v$V" ] || { echo "error: tag check failed" >&2; exit 1; }
echo "released $V (tag pushed; release workflow running)"
