# Shared helpers for the SkunkWerkx dev image install scripts. Sourced, never run.
set -euo pipefail
DEB_ARCH=$(dpkg --print-architecture)   # amd64 | arm64
UNAME_ARCH=$(uname -m)                  # x86_64 | aarch64
# Latest release tag WITHOUT the GitHub API (no token, no 60/h rate limit): follow the /releases/latest redirect.
gh_latest_tag() { curl -fsSLI --retry 3 -o /dev/null -w '%{url_effective}' "https://github.com/$1/releases/latest" | sed 's#.*/tag/##'; }
fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }
# install_bin URL NAME: download an archive, find the file NAME in it, install it to /usr/local/bin.
install_bin() {
  local url=$1 name=$2 d
  d=$(mktemp -d)
  fetch "$url" "$d/archive"
  mkdir "$d/x"
  case "$url" in
    *.zip) unzip -q "$d/archive" -d "$d/x" ;;
    *) tar -xf "$d/archive" -C "$d/x" ;;
  esac
  sudo install -m 0755 "$(find "$d/x" -type f -name "$name" | head -1)" "/usr/local/bin/$name"
  rm -rf "$d"
}
