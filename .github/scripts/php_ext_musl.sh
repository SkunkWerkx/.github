#!/usr/bin/env bash
# The musl legs of hyper-build-php-extension.yml: builds the crate's PHP extension for one PHP
# minor on Alpine, load-checks it, runs the suite through it and packs its PIE zip.
#
#   php_ext_musl.sh <crate> <rid> <minor> <repo-root> <forge-root> <pie-phar> <out-dir>
#
# with PACKAGE_VERSION (the crate's bare X.Y.Z, which the load check asserts) and RELEASE_TAG
# ("v" + it, the zip name's version segment) in the environment. <rid> is linux-musl-x64 or
# linux-musl-arm64 and has to match the host's architecture (DOCKER_PLATFORM overrides it).
# <forge-root> holds the packer and the load check; <pie-phar> is the pinned PIE the workflow
# fetched and verified. The zip lands in <out-dir>.
#
# Two containers, as in ruby-magnus-musl's build-magnus-musl.sh:
#
#  * The build runs on the pinned Alpine release with that minor's php8X-dev package, Alpine's
#    own PHP build, and a Rust toolchain from rustup. The flags are build-musl.sh's: without
#    `-crt-static` no *-linux-musl target yields a cdylib, and the unwinder is linked
#    statically through a `libgcc_s` linker script naming libgcc_eh.a and libgcc.a, because
#    a plain build keeps a NEEDED entry for libgcc_s.so.1, which the php:*-alpine images do
#    not promise. The readelf gate holds the extension to musl's libc and nothing else: like
#    every Linux extension it resolves the PHP symbols from the process that loads it.
#  * The load check, the suite and the pack run on php:<minor>-cli-alpine, PHP's official
#    image and what a consumer's Dockerfile starts FROM: a different PHP build from the one
#    the extension compiled against, of the same minor, so the leg also proves the zip is not
#    tied to one distribution's PHP. Composer comes from its installer, checked against the
#    published signature, and the packer needs python3; the image is otherwise as pulled.
#
# The source is copied into each container, never built or tested in place: Docker runs as
# root, and a read-write mount leaves root-owned files in the caller's checkout.
set -euo pipefail

usage='usage: php_ext_musl.sh <crate> <rid> <minor> <repo-root> <forge-root> <pie-phar> <out-dir>'
crate=${1:?$usage}
rid=${2:?$usage}
minor=${3:?$usage}
repo_root=$(cd "${4:?$usage}" && pwd)
forge_root=$(cd "${5:?$usage}" && pwd)
pie=$(cd "$(dirname "${6:?$usage}")" && pwd)/$(basename "$6")
mkdir -p "${7:?$usage}"
out_dir=$(cd "$7" && pwd)
: "${PACKAGE_VERSION:?PACKAGE_VERSION is the bare crate version}" "${RELEASE_TAG:?RELEASE_TAG is the release tag}"
[ -f "$pie" ] || { echo "::error::no PIE phar at $pie"; exit 1; }
case "$minor" in
  [0-9].[0-9]|[0-9].[0-9][0-9]) ;;
  *) echo "::error::$minor is not a PHP X.Y minor"; exit 1 ;;
esac
apk_php="php${minor/./}"
# The suite's variables are named for the crate, derived as hyper-build-php-extension.yml derives
# them: <CRATE>_PHP_EXTENSION names the library to load.
env_prefix=$(printf '%s' "$crate" | tr '[:lower:]-' '[:upper:]_')

platform_args=()
[ -n "${DOCKER_PLATFORM:-}" ] && platform_args=(--platform "$DOCKER_PLATFORM")
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT

# Alpine 3.24 is pinned: the current release, and it carries php83-dev, php84-dev and
# php85-dev (measured 2026-10-30: 3.23 is the oldest release with all three; 3.22 has no
# php85-dev). It moves when 8.3 leaves php_versions or Alpine drops php83, whichever is first.
echo "::group::build against Alpine's $apk_php-dev — $rid"
docker run --rm "${platform_args[@]}" \
  -v "$repo_root:/src:ro" -v "$build_dir:/out" \
  -e CRATE="$crate" -e APK_PHP="$apk_php" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  alpine:3.24 sh -euc '
    apk add --no-cache build-base curl clang-dev "$APK_PHP-dev" >/dev/null
    curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable >/dev/null 2>&1
    . "$HOME/.cargo/env"
    export PHP="/usr/bin/$APK_PHP" PHP_CONFIG="/usr/bin/php-config${APK_PHP#php}"
    echo "== $("$PHP" -v | head -1), Alpine $(cat /etc/alpine-release), $(rustc -V)"

    mkdir /work
    (cd /src && tar cf - --exclude=./rust/target ./rust) | tar xf - -C /work
    cd /work/rust

    mkdir /unwind
    echo "GROUP ( $(gcc -print-file-name=libgcc_eh.a) $(gcc -print-file-name=libgcc.a) )" > /unwind/libgcc_s.so
    export RUSTFLAGS="-C target-feature=-crt-static -L native=/unwind"

    # The same alias as every other leg: the release cdylib with the php feature, in target/php.
    cargo php-ext

    ext="target/php/release/lib$CRATE.so"
    [ -f "$ext" ] || { echo "::error::no $ext — the cdylib was not produced, is crt-static back on?"; exit 1; }
    readelf -Ws --dyn-syms "$ext" | grep -q " get_module$" \
      || { echo "::error::$ext exports no get_module — not a PHP extension"; exit 1; }
    needed=$(readelf -d "$ext" | sed -n "s/.*(NEEDED).*\[\(.*\)\]/\1/p" | sort | tr "\n" " ")
    echo "NEEDED: $needed"
    case "$needed" in
      "libc.musl-"*".so.1 ") ;;
      *) echo "::error::$ext must depend on musl libc and nothing else, found: $needed"; exit 1 ;;
    esac
    cp "$ext" "/out/$CRATE.so"
    chown "$HOST_UID:$HOST_GID" "/out/$CRATE.so"
  '
echo "::endgroup::"

echo "::group::load-check, test and pack on php:$minor-cli-alpine — $rid"
docker run --rm "${platform_args[@]}" \
  -v "$repo_root:/src:ro" -v "$build_dir:/ext:ro" -v "$forge_root:/forge:ro" -v "$pie:/pie.phar:ro" \
  -v "$out_dir:/dist" \
  -e CRATE="$crate" -e PACKAGE_VERSION="$PACKAGE_VERSION" -e RELEASE_TAG="$RELEASE_TAG" \
  -e "${env_prefix}_PHP_EXTENSION=/ext/$crate.so" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "php:$minor-cli-alpine" sh -euc '
    echo "== $(php -v | head -1), Alpine $(cat /etc/alpine-release)"
    EXTENSION="/ext/$CRATE.so"
    php -d "extension=$EXTENSION" /forge/.github/scripts/php_ext_load_check.php "$CRATE" "$PACKAGE_VERSION"

    apk add --no-cache git unzip python3 >/dev/null
    php -r "copy(\"https://getcomposer.org/installer\", \"/tmp/composer-setup.php\");"
    sig=$(php -r "echo trim(file_get_contents(\"https://composer.github.io/installer.sig\"));")
    [ "$(php -r "echo hash_file(\"sha384\", \"/tmp/composer-setup.php\");")" = "$sig" ] \
      || { echo "::error::the Composer installer does not match its published signature"; exit 1; }
    php /tmp/composer-setup.php --quiet --install-dir=/usr/local/bin --filename=composer

    mkdir -p /work/rust
    cp /src/rust/Cargo.toml /work/rust/
    if [ -d /src/corpus ]; then cp -r /src/corpus /work/corpus; fi
    cp -r /src/php /work/php
    rm -rf /work/php/vendor /work/php/composer.lock
    cd /work/php
    composer install --no-interaction --prefer-dist --no-progress
    php tools/run-tests.php

    python3 /forge/.github/scripts/php_ext_package.py pack --library "$EXTENSION" --php php \
      --pie /pie.phar --version "$RELEASE_TAG" --extension "$CRATE" --out /dist
    chown -R "$HOST_UID:$HOST_GID" /dist
  '
echo "::endgroup::"
ls -l "$out_dir"
