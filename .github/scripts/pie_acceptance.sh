#!/bin/sh
# One hyper-pie-acceptance.yml leg, after the leg has PHP and the verified PIE phar and nothing
# else: PIE installs the released extension from the GitHub Release, then a fresh PHP process,
# with no -d flags, asserts what pie_acceptance.php checks. POSIX sh, because the musl legs run
# it in a bare alpine image (BusyBox sh); bash runs it unchanged on Linux, macOS and Windows.
#
#   pie_acceptance.sh <pie-php> <target-php> <pie.phar> <package> <extension> <package_version> \
#     [repository] [entry_sha256]
#
# <pie-php> runs PIE; <target-php> is the PHP it installs into and the one the checks run on.
# They differ only on osx-x64, whose x86_64 PHP is built without openssl, so PIE runs on the
# arm64 PHP of the same minor and targets it with --with-php-path. <package_version> is the bare
# X.Y.Z; the release tag `v<package_version>` is derived here and is the version PIE is asked
# for, as Composer's pretty version for that tag is what PIE looks the Release up by. An empty
# <repository> means Packagist; otherwise it is added as a `vcs` repository first. <extension> is
# the extension's name (the consumer's crate), and <entry_sha256>, when given, the sha256 of the
# file in this leg's zip on the Release, which the installed file must match.
set -eu

usage='usage: pie_acceptance.sh <pie-php> <target-php> <pie.phar> <package> <extension> <package_version> [repository] [entry_sha256]'
pie_php=${1:?$usage}
target_php=${2:?$usage}
phar=${3:?$usage}
package=${4:?$usage}
extension=${5:?$usage}
package_version=${6:?$usage}
repository=${7:-}
entry_sha256=${8:-}
if ! printf '%s\n' "$package_version" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+'; then
  echo "::error::package_version '$package_version' is not a bare X.Y.Z" >&2
  exit 2
fi
release_tag="v$package_version"
here=$(cd "$(dirname "$0")" && pwd)

# PIE keeps its repositories per target PHP, so both commands name the same target.
set --
if [ "$target_php" != "$pie_php" ]; then
  set -- "--with-php-path=$target_php"
fi

echo "== PIE $("$pie_php" "$phar" --version 2>/dev/null | head -1)"
echo "== target: $("$target_php" -v | head -1)"
if [ -n "$repository" ]; then
  "$pie_php" "$phar" repository:add "$@" vcs "$repository"
fi
"$pie_php" "$phar" install --no-interaction "$@" "$package:$release_tag"

# A fresh process with no -d flags: the extension is loaded only if PIE enabled it.
if [ -n "$entry_sha256" ]; then
  "$target_php" "$here/pie_acceptance.php" "$extension" "$package_version" "$entry_sha256"
else
  "$target_php" "$here/pie_acceptance.php" "$extension" "$package_version"
fi
