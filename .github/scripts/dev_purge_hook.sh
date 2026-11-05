#!/usr/bin/env bash
# A Claude Code PreToolUse hook on Bash: under the floor of free disk (DEV_PURGE_MIN_FREE_GB,
# default 50), a command is refused unless every part of it only looks around or frees space, so
# a session stops adding to a full disk and can still clean up. One session filled a 460 GB disk
# before anything stopped it; dev_purge.sh has that story.
#
# Exit 2 is the hook's refusal. Everything else lets the command through, deliberately: a broken
# check must not stop every command on the machine. This is a brake on a runaway, not a security
# boundary. It sees only what a command looks like, and a command already running keeps writing.
#
# settings.json: {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
#   "command": "<checkout>/.github/scripts/dev_purge_hook.sh"}]}]}}
set -uo pipefail

floor="${DEV_PURGE_MIN_FREE_GB:-50}"
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$scripts/dev_purge.sh" --check "$floor" >/dev/null 2>&1
[ $? -eq 1 ] || exit 0

command=$(jq -r '.tool_input.command // empty' 2>/dev/null)

# One simple command: its first words, against what can only inspect or free space.
frees_or_inspects() {
  local first second rest
  read -r first second rest <<<"$1"
  case "${first##*/}" in
    dev_purge.sh|df|du|ls|pwd|cd|cat|head|tail|wc|sort|uniq|cut|grep|find|ps|pgrep|lsof|stat|echo|true|rm|rmdir|kill|pkill) return 0 ;;
    bash|sh) case "${second##*/}" in dev_purge.sh) return 0 ;; esac ;;
    git) case "$second" in status|clean|gc|worktree|log|diff) return 0 ;; esac ;;
    docker)
      case "$second" in
        ps|images|info|rm|rmi|stop|kill) return 0 ;;
        system|builder|image|container) case "$rest" in df*|prune*|ls*|rm*|stop*|kill*) return 0 ;; esac ;;
      esac ;;
    brew) case "$second" in cleanup) return 0 ;; esac ;;
    go) case "$second" in clean) return 0 ;; esac ;;
    cargo) case "$second" in clean) return 0 ;; esac ;;
    npm) case "$second" in cache) return 0 ;; esac ;;
    dotnet) case "$second $rest" in "nuget locals"*) return 0 ;; esac ;;
    swift) case "$second $rest" in "package clean"*|"package reset"*) return 0 ;; esac ;;
    gradle|gradlew) case "$second" in --stop) return 0 ;; esac ;;
  esac
  return 1
}

allowed() {
  local text="$1" part
  [ -n "${text//[[:space:]]/}" ] || return 1
  text="${text//2>\/dev\/null/}"
  text="${text//2>&1/}"
  # Anything that could hide another command or write a file is not a simple cleanup.
  # shellcheck disable=SC2016 # these are the literal characters
  case "$text" in *'$('*|*'`'*|*'>'*|*'<'*|*'('*|*')'*) return 1 ;; esac
  text="${text//&&/$'\n'}"
  text="${text//||/$'\n'}"
  text="${text//;/$'\n'}"
  text="${text//|/$'\n'}"
  case "$text" in *'&'*) return 1 ;; esac
  while IFS= read -r part; do
    [ -n "${part//[[:space:]]/}" ] || continue
    frees_or_inspects "$part" || return 1
  done <<<"$text"
  return 0
}

allowed "$command" && exit 0

cat >&2 <<EOF
Blocked: under $floor GB is free on the disk holding $HOME. Stop adding to it. Only commands that
inspect or free space run now. See what can go with $scripts/dev_purge.sh and tell the user;
do not run it with --yes or --stop unasked. The floor is DEV_PURGE_MIN_FREE_GB in settings.json.
EOF
exit 2
