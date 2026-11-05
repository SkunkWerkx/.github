#!/usr/bin/env bash
# Takes a developer's machine back from local builds: agent scratch folders, Docker's disposable
# data, package and build caches, and the ignored build output in every checkout. Nothing here runs
# in CI. The Hyper* suites are heavy enough, in several checkouts at once, to fill a laptop; when
# they did, on a 460 GB disk, Docker's VM went read-only and every app that saves its state failed.
#
# It prints the plan unless given --yes, and everything it removes comes back from a rebuild or a
# download. It never removes tracked files, untracked source, toolchains, Docker volumes, browser
# caches or a scratch folder still in use. Stopping things is a separate decision: without --stop
# it only names what is running, and it refuses to clear caches or build output underneath it.
#
# usage: dev_purge.sh [--yes] [--stop] [--only LIST] [--root DIR]... [--idle-minutes N]
#        dev_purge.sh --check GB
#
#   --yes             remove things; without it, print what would go
#   --stop            also stop other agent sessions, Gradle and Kotlin daemons and running containers
#   --only LIST       comma-separated: scratch, docker, caches, builds (default: all four)
#   --root DIR        where to look for checkouts, repeatable (default: ~/Developer)
#   --idle-minutes N  a scratch folder written to within N minutes is in use (default: 60)
#   --check GB        change nothing; exit 1 when under GB of disk is free
set -euo pipefail

# Test seams: the tests must never reach a developer's real sessions, daemons or scratch.
agent_name="${DEV_PURGE_AGENT_NAME:-claude}"
daemon_pattern="${DEV_PURGE_DAEMON_PATTERN:-GradleDaemon|KotlinCompileDaemon}"
scratch_base="${DEV_PURGE_SCRATCH_ROOT:-}"

# Each must be a whole path component that a build makes. Anything else ignored stays: .env files,
# local settings and lockfiles are ignored too, and a rebuild does not bring those back.
build_dirs='^(target|\.build|node_modules|bin|obj|build|out|dist|\.gradle|__pycache__|\.venv|venv|\.pytest_cache|\.ruff_cache|\.mypy_cache|\.kotlin|\.swiftpm|coverage|TestResults|artifacts|BenchmarkDotNet\.Artifacts|\.cache)$'

apply=
stop=
only=scratch,docker,caches,builds
idle=60
check=
roots=()
total_kb=0
failures=0

die() { echo "dev_purge: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) apply=1 ;;
    --stop) stop=1 ;;
    --only) [ $# -ge 2 ] || die "--only needs a list"; only="$2"; shift ;;
    --root) [ $# -ge 2 ] || die "--root needs a directory"; roots+=("$2"); shift ;;
    --idle-minutes) [ $# -ge 2 ] || die "--idle-minutes needs a number"; idle="$2"; shift ;;
    --check) [ $# -ge 2 ] || die "--check needs a number of GB"; check="$2"; shift ;;
    -h|--help) sed -n '/^# usage:/,/^# *--check/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

case "$idle" in ''|*[!0-9]*) die "--idle-minutes takes a whole number" ;; esac
[ -z "$check" ] || case "$check" in *[!0-9]*) die "--check takes a whole number of GB" ;; esac
for part in ${only//,/ }; do
  case "$part" in scratch|docker|caches|builds) ;; *) die "unknown --only value: $part" ;; esac
done
[ -n "${HOME:-}" ] && [ "$HOME" != / ] && [ -d "$HOME" ] || die "HOME is not a usable directory"

selected() { case ",$only," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
free_kb() { df -Pk "$HOME" | awk 'END { print $4 }'; }
human() { awk -v kb="$1" 'BEGIN { if (kb >= 1048576) printf "%.1f GB", kb / 1048576; else printf "%.0f MB", kb / 1024 }'; }
section() { printf '\n== %s\n' "$1"; }

if [ -n "$check" ]; then
  free=$(free_kb)
  echo "dev_purge: $(human "$free") free on the disk holding $HOME (floor: $check GB)"
  [ "$free" -ge $((check * 1048576)) ] || { echo "dev_purge: under the floor; run dev_purge.sh to see what can go" >&2; exit 1; }
  exit 0
fi

# Every removal goes through here. Read-only module caches (Go's) need the chmod first.
remove() {
  local path="$1" kb
  [ -e "$path" ] || [ -L "$path" ] || return 0
  case "$path" in
    ''|/|"$HOME"|"$HOME/"|*/../*|*/..) die "refusing to remove '$path'" ;;
  esac
  kb=$(du -sk "$path" 2>/dev/null | awk 'END { print $1 + 0 }')
  total_kb=$((total_kb + kb))
  if [ -n "$apply" ]; then
    chmod -R u+w "$path" 2>/dev/null || true
    if rm -rf -- "$path"; then
      printf '  removed     %8s  %s\n' "$(human "$kb")" "$path"
    else
      failures=$((failures + 1))
      printf '  FAILED      %8s  %s\n' "$(human "$kb")" "$path"
    fi
  else
    printf '  would remove %8s  %s\n' "$(human "$kb")" "$path"
  fi
}

# This shell's ancestors, so an agent running this script never stops itself.
ancestors=" "
pid=$$
while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
  ancestors="$ancestors$pid "
  pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ') || break
done
is_ancestor() { case "$ancestors" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

others() {
  local p
  for p in $(pgrep -x "$agent_name" 2>/dev/null || true) $(pgrep -f "$daemon_pattern" 2>/dev/null || true); do
    is_ancestor "$p" || [ "$p" = "$$" ] || echo "$p"
  done | sort -un
}

descendants() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null || true); do
    echo "$child"
    descendants "$child"
  done
}

stop_processes() {
  section "Running processes"
  local targets p tree="" waited=0
  targets=$(others)
  if [ -z "$targets" ]; then
    echo "  no other agent session or build daemon"
  else
    for p in $targets; do
      printf '  %s %6s  %s\n' "$( [ -n "$stop" ] && [ -n "$apply" ] && echo stopping || echo running )" "$p" \
        "$(ps -o command= -p "$p" 2>/dev/null | cut -c1-90)"
      tree="$tree $p $(descendants "$p" | tr '\n' ' ')"
    done
    if [ -n "$stop" ] && [ -n "$apply" ]; then
      # shellcheck disable=SC2086 # the PIDs are meant to split
      kill -TERM $tree 2>/dev/null || true
      while [ "$waited" -lt 5 ]; do
        local alive=
        for p in $tree; do kill -0 "$p" 2>/dev/null && alive=1; done
        [ -n "$alive" ] || break
        sleep 1
        waited=$((waited + 1))
      done
      for p in $tree; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null; done
    elif [ -z "$stop" ]; then
      echo "  left running; --stop stops them"
    fi
  fi

  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || return 0
  local containers
  containers=$(docker ps -q 2>/dev/null || true)
  [ -n "$containers" ] || return 0
  docker ps --size --format '  container  {{.Names}}  {{.Image}}  {{.Size}}' 2>/dev/null || true
  if [ -n "$stop" ] && [ -n "$apply" ]; then
    # shellcheck disable=SC2086 # the IDs are meant to split
    docker rm -f $containers >/dev/null 2>&1 || { failures=$((failures + 1)); echo "  FAILED to remove the containers"; }
  elif [ -z "$stop" ]; then
    echo "  left running; --stop removes them"
  fi
}

purge_scratch() {
  section "Agent scratch folders"
  local base project session recent holders
  base="$scratch_base"
  if [ -z "$base" ]; then
    base="/tmp/claude-$(id -u)"
    [ -d "/private$base" ] && base="/private$base"
  fi
  [ -d "$base" ] || { echo "  none"; return 0; }
  # Only the per-project folders (-Users-...); the agent keeps its own working files beside them.
  for project in "$base"/-*/; do
    [ -d "$project" ] || continue
    for session in "$project"*/; do
      [ -d "$session" ] || continue
      session="${session%/}"
      recent=$(find "$session" -xdev -type f -mmin "-$idle" -print -quit 2>/dev/null || true)
      # Docker's VM keeps every directory it has ever shared open, long after its container is
      # gone, so an open handle from com.apple.Virtualization says nothing about use.
      holders=$(lsof -Fc +D "$session" 2>/dev/null | grep '^c' | grep -v '^ccom.apple' || true)
      if [ -n "$recent" ] || [ -n "$holders" ]; then
        printf '  in use, kept          %s\n' "$session"
      else
        remove "$session"
      fi
    done
    [ -z "$apply" ] || rmdir "${project%/}" 2>/dev/null || true
  done
}

docker_step() {
  local output
  if output=$(docker "$@" 2>&1); then
    echo "  docker $*: $(printf '%s\n' "$output" | tail -1)"
  else
    failures=$((failures + 1))
    echo "  FAILED docker $*: $(printf '%s\n' "$output" | tail -1)"
    case "$output" in
      *read-only*) echo "  Docker's VM disk went read-only: free space on the Mac, restart Docker, run this again" ;;
    esac
  fi
}

# Volumes stay: they are the one thing Docker holds that a rebuild does not bring back.
purge_docker() {
  section "Docker"
  command -v docker >/dev/null 2>&1 || { echo "  not installed"; return 0; }
  docker info >/dev/null 2>&1 || { echo "  not running, skipped"; return 0; }
  if [ -n "$apply" ]; then
    docker_step container prune -f
    docker_step image prune -af
    docker_step builder prune -af
  else
    docker system df 2>/dev/null | sed 's/^/  /' || true
    echo "  would prune stopped containers, unused images and the build cache"
  fi
}

# Caches only. Toolchains (.rustup, .dotnet, .sdkman, Xcode, pyenv) and installed tools
# (.cargo/bin) cost far more to get back and are not what fills a disk. Browser and game caches
# are the user's, not the build's.
purge_caches() {
  section "Package and build caches"
  local name
  for name in \
    .nuget/packages .local/share/NuGet/http-cache .local/share/NuGet/v3-cache \
    .local/share/NuGet/plugin-cache .local/share/NuGet/plugins-cache .cargo/registry .cargo/git \
    go/pkg/mod .npm .bun/install/cache .gradle/caches .gradle/wrapper .cache \
    Library/Developer/Xcode/DerivedData; do
    remove "$HOME/$name"
  done
  for name in \
    Homebrew go-build pip pipx pyodide-build java-db tinygo BytecodeAlliance.wasmtime electron \
    org.swift.swiftpm ms-playwright; do
    remove "$HOME/Library/Caches/$name"
  done
}

# The directory to remove for an ignored path: its outermost component that is a build directory
# and is ignored as a whole. `git clean -ndX` lists children when a nested repository (a SwiftPM
# checkout) stops it at the parent, and a single ignored object file inside a directory that also
# holds untracked source lists only that file, so being named is not enough.
build_dir_of() {
  local repo="$1" path="${2%/}" prefix="" part rest
  rest="$path/"
  while [ -n "$rest" ]; do
    part="${rest%%/*}"
    rest="${rest#*/}"
    prefix="${prefix:+$prefix/}$part"
    printf '%s\n' "$part" | grep -E "$build_dirs" >/dev/null || continue
    git -C "$repo" check-ignore -q -- "$prefix" || continue
    [ -z "$(git --literal-pathspecs -C "$repo" ls-files -- "$prefix" | head -1)" ] || continue
    printf '%s\n' "$prefix"
    return 0
  done
  return 1
}

purge_builds() {
  section "Build output in checkouts"
  local root git_dir repo line path dirs kept before after dir
  [ ${#roots[@]} -gt 0 ] || roots=("$HOME/Developer")
  for root in "${roots[@]}"; do
    [ -d "$root" ] || { echo "  no such directory: $root"; continue; }
    while IFS= read -r git_dir; do
      repo="${git_dir%/.git}"
      case "$repo/" in */.build/*|*/node_modules/*|*/target/*) continue ;; esac
      [ -d "$repo" ] || continue
      before=$(git -C "$repo" ls-files -d | wc -l)
      dirs=""
      kept=0
      while IFS= read -r line; do
        case "$line" in
          "Would remove "*) path="${line#Would remove }" ;;
          "Would skip repository "*) path="${line#Would skip repository }" ;;
          *) continue ;;
        esac
        # git quotes a name it cannot print plainly; such a name is left alone.
        case "$path" in \"*) kept=$((kept + 1)); continue ;; esac
        if dir=$(build_dir_of "$repo" "$path"); then
          dirs="$dirs$dir"$'\n'
        else
          kept=$((kept + 1))
        fi
      done < <(git -c core.quotePath=false -C "$repo" clean -ndX 2>/dev/null)
      while IFS= read -r dir; do
        [ -z "$dir" ] || remove "$repo/$dir"
      done < <(printf '%s' "$dirs" | sort -u)
      [ "$kept" -eq 0 ] || printf '  kept %d other ignored paths in %s\n' "$kept" "$repo"
      after=$(git -C "$repo" ls-files -d | wc -l)
      if [ "$after" -gt "$before" ]; then
        failures=$((failures + 1))
        echo "  FAILED: tracked files went missing in $repo; check 'git -C $repo status'"
      fi
    done < <(find "$root" -maxdepth 4 -name .git -not -path '*/.build/*' -not -path '*/node_modules/*' 2>/dev/null)
  done
}

start_free=$(free_kb)
[ -n "$apply" ] || echo "dev_purge: dry run, nothing changes without --yes"
echo "dev_purge: $(human "$start_free") free"

stop_processes

# Clearing a cache or a target directory under a running build corrupts that build.
if [ -n "$apply" ] && [ -z "$stop" ] && { selected caches || selected builds; } && [ -n "$(others)" ]; then
  echo >&2
  echo "dev_purge: other sessions or daemons are running; pass --stop, or --only scratch,docker" >&2
  exit 2
fi

if selected scratch; then purge_scratch; fi
if selected docker; then purge_docker; fi
if selected caches; then purge_caches; fi
if selected builds; then purge_builds; fi

section "Result"
if [ -n "$apply" ]; then
  end_free=$(free_kb)
  echo "  $(human "$end_free") free, up from $(human "$start_free")"
else
  echo "  about $(human "$total_kb") could go, not counting Docker; run again with --yes"
  echo "  (APFS clones share blocks, so the real figure can be lower)"
fi
if [ "$failures" -gt 0 ]; then
  echo "dev_purge: $failures step(s) failed" >&2
  exit 1
fi
