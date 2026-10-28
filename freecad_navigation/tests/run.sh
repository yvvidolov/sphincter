#!/usr/bin/env bash
# Runs tests/gui_test.py inside a headless FreeCAD with a throwaway copy of the
# user config, so your real preferences are not modified.
# Requires the add-on to be linked into FreeCAD's Mod dir (see README).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
freecad="${FREECAD:-freecad}"
userdir="${FREECAD_USER_DIR:-$HOME/snap/freecad/common}"   # snap default
work="$userdir/temp/modo_navigation_test"
mkdir -p "$work"
cp "$userdir/user.cfg" "$work/user.cfg" 2>/dev/null || : > "$work/user.cfg"
rm -f "$work/result.txt"
cp "$here/gui_test.py" "$work/gui_test.py"   # snap FreeCAD can't read outside its dirs reliably
MODO_TEST_OUT="$work/result.txt" QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
  timeout 180 "$freecad" -u "$work/user.cfg" "$work/gui_test.py" >"$work/freecad.log" 2>&1 || true
if [[ ! -s "$work/result.txt" ]]; then
  echo "no result; FreeCAD log:"; tail -40 "$work/freecad.log"; exit 2
fi
cat "$work/result.txt"
grep -q "^RESULT OK" "$work/result.txt"
