#!/usr/bin/env bash
# Publishes this placeholder crate under another name as 0.0.1, then yanks it, so that
# crates.io Trusted Publishing can be set up before the crate's first real release.
# Usage: reserve-crate.sh <crate-name> <owner/repo> [--dry-run]
set -euo pipefail

name=${1:?usage: reserve-crate.sh <crate-name> <owner/repo> [--dry-run]}
repo=${2:?usage: reserve-crate.sh <crate-name> <owner/repo> [--dry-run]}
dry_run=${3:-}

here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [ "$dry_run" != "--dry-run" ]; then
  : "${CARGO_REGISTRY_TOKEN:?set CARGO_REGISTRY_TOKEN to a crates.io token with publish-new and yank}"
  if curl -sf -A "SkunkWerkx reserve-crate.sh" "https://crates.io/api/v1/crates/$name" > /dev/null; then
    echo "$name already exists on crates.io; nothing to reserve." >&2
    exit 1
  fi
fi

cp -R "$here/Cargo.toml" "$here/README.md" "$here/src" "$work/"
sed -i.bak \
  -e "s|^name = .*|name = \"$name\"|" \
  -e "s|^repository = .*|repository = \"https://github.com/$repo\"|" \
  -e "/^publish = false$/d" \
  "$work/Cargo.toml"
rm "$work/Cargo.toml.bak"

if [ "$dry_run" = "--dry-run" ]; then
  cargo publish --manifest-path "$work/Cargo.toml" --allow-dirty --dry-run
  exit 0
fi

cargo publish --manifest-path "$work/Cargo.toml" --allow-dirty
if ! cargo yank --version 0.0.1 "$name"; then
  echo "Published, but the yank failed (the token needs the yank scope). Yank 0.0.1 from" >&2
  echo "https://crates.io/crates/$name/versions instead." >&2
fi

cat <<EOF
Published and yanked $name 0.0.1. Next:
  1. https://crates.io/crates/$name/settings: add a GitHub trusted publisher for
     $repo, workflow release.yml.
  2. Revoke the token you used.
  3. Tag the release.
EOF
