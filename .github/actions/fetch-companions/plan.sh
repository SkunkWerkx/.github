#!/usr/bin/env bash
# fetch-companions' first step: the Python every later step runs, then the plan.
#
# The interpreter is resolved once, here, and handed on as the `python` output: python3, then
# python, then `py -3`, the first that is a real Python 3.11 or newer (tomllib) and not the
# WindowsApps alias, by the path its own sys.executable names. A Windows runner without
# setup-python may have no python3, or only the alias, which exits without running anything.
#
# Without one, a leg with no `companions` block in .github/hyper.json still passes: it has
# nothing to fetch, so it reports has_block=false and the action's later steps are skipped, as
# on a leg that never ran Python before this action. Only a leg with a block needs Python, and
# fails, naming what it tried.
#
# Environment: FORGE_SCRIPTS, ROOT, LEG, ECOSYSTEMS (the action's inputs), GITHUB_OUTPUT.
set -euo pipefail

# The interpreter "$@" names, by its own sys.executable, when it is Python 3.11 or newer.
probe() {
  "$@" -c 'import sys; assert sys.version_info >= (3, 11); print(sys.executable)' 2>/dev/null | tr -d '\r'
}

python=''
for candidate in python3 python 'py -3'; do
  name=${candidate%% *}
  path=$(command -v "$name" 2>/dev/null) || continue
  case "$path" in *WindowsApps*) continue ;; esac
  if [ "$name" = py ]; then
    found=$(probe "$path" -3) || continue
  else
    found=$(probe "$path") || continue
  fi
  [ -n "$found" ] || continue
  # Git Bash on Windows: sys.executable is C:\...; every step runs it from bash.
  if command -v cygpath >/dev/null 2>&1; then found=$(cygpath -u "$found"); fi
  python=$found
  break
done

if [ -z "$python" ]; then
  config="$ROOT/.github/hyper.json"
  if [ ! -f "$config" ]; then
    echo "::error::$config is missing: every Hyper* repository describes itself there"
    exit 1
  fi
  if grep -Eq '"companions"[[:space:]]*:' "$config"; then
    echo "::error::$config names companions, and fetching them needs Python 3.11 or newer: none of python3, python or py -3 on PATH is one (the WindowsApps alias is not)"
    exit 1
  fi
  echo "No Python 3.11 or newer on PATH, and no companions block in $config: nothing to fetch."
  echo "has_block=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

echo "python=$python" >> "$GITHUB_OUTPUT"
"$python" "$FORGE_SCRIPTS/companion_fetch.py" plan --root "$ROOT" --leg="$LEG" --ecosystems="$ECOSYSTEMS"
