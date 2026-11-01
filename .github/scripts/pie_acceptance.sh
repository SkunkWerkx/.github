#!/bin/sh
# One hyper-pie-acceptance.yml leg, after the leg has PHP and the verified PIE phar and nothing
# else: PIE installs the released extension from the GitHub Release, then a fresh PHP process,
# with no -d flags, asserts what pie_acceptance.php checks. POSIX sh, because the musl legs run
# it in a bare alpine image (BusyBox sh); bash runs it unchanged on Linux, macOS and Windows.
#
#   pie_acceptance.sh <pie-php> <target-php> <pie.phar> <package> <extension> <package_version> \
#     [repository] [entry_sha256] [companions]
#
# <pie-php> runs PIE; <target-php> is the PHP it installs into and the one the checks run on.
# Every leg passes the same PHP for both today; the split stays for a leg whose PIE would need
# an extension its target lacks, targeted with --with-php-path. <package_version> is the bare
# X.Y.Z; the release tag `v<package_version>` is derived here and is the version PIE is asked
# for, as Composer's pretty version for that tag is what PIE looks the Release up by. An empty
# <repository> means Packagist; otherwise it is added as a `vcs` repository first. <extension> is
# the extension's name (the consumer's crate), and <entry_sha256>, when given, the sha256 of the
# file in this leg's zip on the Release, which the installed file must match. <companions>, when
# given, are Composer packages of extensions this one needs loaded (HyperTabular's:
# skunkwerkx/hypercast), space-separated: PIE installs each first, from Packagist at its newest
# release, as a consumer's own `pie install` would have to before this package's ext- requirement
# is met. Their bytes are their own release's business: only this extension's are checked.
set -eu

usage='usage: pie_acceptance.sh <pie-php> <target-php> <pie.phar> <package> <extension> <package_version> [repository] [entry_sha256] [companions]'
pie_php=${1:?$usage}
target_php=${2:?$usage}
phar=${3:?$usage}
package=${4:?$usage}
extension=${5:?$usage}
package_version=${6:?$usage}
repository=${7:-}
entry_sha256=${8:-}
companions=${9:-}
if ! printf '%s\n' "$package_version" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+'; then
  echo "::error::package_version '$package_version' is not a bare X.Y.Z" >&2
  exit 2
fi
release_tag="v$package_version"
here=$(cd "$(dirname "$0")" && pwd)

# PIE elevates with sudo when the target's extension_dir is not writable (a distribution PHP
# on Linux keeps /usr/lib/php/<api> root's) and, with no terminal to prompt on and not root,
# refuses: "PIE needed to elevate privileges with /usr/bin/sudo, but you are running in a
# non-interactive terminal" took v1.1.0's six glibc legs. Under exactly PIE's own condition
# (src/File/Sudo.php) the whole run moves to root instead, before any PIE call, so PIE's
# per-user repositories, cache and ini writes all happen as one user: `sudo -n` never prompts
# (the hosted runners' passwordless sudo, or a clean failure), `-E` keeps the environment,
# Composer's GitHub token and HOME included. Interactive runs, root (the Alpine container) and
# user-owned PHPs (Homebrew, Windows) are untouched.
if [ "$(id -u)" != 0 ] && [ ! -t 0 ] && [ ! -w "$("$target_php" -r 'echo ini_get("extension_dir");')" ]; then
  echo "== extension_dir is not writable and there is no terminal: re-running as root (sudo -n -E)"
  exec sudo -n -E "$0" "$@"
fi

# PIE keeps its repositories per target PHP, so both commands name the same target.
set --
if [ "$target_php" != "$pie_php" ]; then
  set -- "--with-php-path=$target_php"
fi

echo "== PIE $("$pie_php" "$phar" --version 2>/dev/null | head -1)"
echo "== target: $("$target_php" -v | head -1)"
for companion in $companions; do
  echo "== companion: $companion"
  "$pie_php" "$phar" install --no-interaction "$@" "$companion"
done
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
