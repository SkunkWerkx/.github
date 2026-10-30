#!/usr/bin/env bash
# An x86_64 PHP for the osx-x64 PHP extension leg, built from php.net's source on an Apple silicon
# Mac and run under Rosetta 2.
#
#   php_macos_x86_64.sh resolve <minor>             prints "<version> <sha256>" of its newest release
#   php_macos_x86_64.sh build <version> <sha256> <prefix>
#
# Why it is built at all: Homebrew bottles nothing for Intel macOS any more (core's and the
# shivammathur/php tap's php formulae list arm64 and Linux bottles only), so `brew install` on
# an Intel runner compiles PHP and every dependency from source, and setup-php installs from that
# same tap. An extension is pinned to one PHP ABI, so the leg needs a PHP of its own minor to
# build against, load into and test under, and a CLI with the extensions the suite and its
# Composer packages use is one configure and one make away.
#
# What is in it, and why nothing else: the CLI, with the extensions PHPUnit, the dev
# dependencies' platform check and the suite need (dom, xml and friends over the SDK's libxml2,
# mbstring without its oniguruma regex, tokenizer, ctype, filter, session, pcntl and posix), and
# phar for PIE's own code, which the packer loads. No JIT: 8.5 always builds opcache, and Xcode's
# clang rejects an inline-asm constraint in its x86 JIT backend; nothing here runs hot. No
# openssl: Composer runs on the leg's arm64 PHP of the same minor, and the vendor tree it writes
# is PHP source, the same for either.
#
# The compiler is told `-arch x86_64` outright: under `arch -x86_64` configure sees an x86_64 host
# and its test programs run through Rosetta, but `cc` is an xcrun trampoline that still starts
# the arm64 clang, which compiles for arm64 by default (measured: the first build's php was a
# Mach-O arm64 executable). The result is checked with lipo before it is used.
set -euo pipefail

usage='usage: php_macos_x86_64.sh resolve <minor> | build <version> <sha256> <prefix>'
case "${1:?$usage}" in
  resolve)
    minor=${2:?$usage}
    curl -fsSL --retry 3 "https://www.php.net/releases/index.php?json&version=$minor" | python3 -c '
import json, sys
release = json.load(sys.stdin)
version = release.get("version", "")
if not version.startswith(sys.argv[1] + "."):
    sys.exit(f"php.net has no release of PHP {sys.argv[1]}: {release}")
xz = [s for s in release["source"] if s["filename"] == f"php-{version}.tar.xz"]
print(version, xz[0]["sha256"])
' "$minor"
    ;;
  build)
    version=${2:?$usage}
    sha256=${3:?$usage}
    mkdir -p "${4:?$usage}"
    prefix=$(cd "$4" && pwd)
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    curl -fsSL --retry 3 -o "$work/php.tar.xz" "https://www.php.net/distributions/php-$version.tar.xz"
    echo "$sha256  $work/php.tar.xz" | shasum -a 256 -c -
    tar -xJf "$work/php.tar.xz" -C "$work"
    sdk=$(xcrun --show-sdk-path)
    cd "$work/php-$version"
    arch -x86_64 /bin/bash -euo pipefail -c '
      ./configure --prefix="$0" CC="cc -arch x86_64" CXX="c++ -arch x86_64" \
        --disable-all --disable-cgi --disable-phpdbg \
        --enable-ctype --enable-dom --enable-filter --enable-mbstring --disable-mbregex \
        --enable-pcntl --enable-phar --enable-posix --enable-session --enable-simplexml \
        --enable-tokenizer --enable-xml --enable-xmlreader --enable-xmlwriter --with-libxml \
        --disable-opcache-jit \
        LIBXML_CFLAGS="-I$1/usr/include/libxml2" LIBXML_LIBS="-lxml2" > configure.log \
        || { tail -50 configure.log; exit 1; }
      make -j"$(sysctl -n hw.ncpu)" > make.log 2>&1 || { tail -50 make.log; exit 1; }
      make install > /dev/null
    ' "$prefix" "$sdk"
    archs=$(lipo -archs "$prefix/bin/php")
    if [ "$archs" != x86_64 ]; then
      echo "::error::$prefix/bin/php is built for $archs, not x86_64"
      exit 1
    fi
    "$prefix/bin/php" -v
    ;;
  *)
    echo "$usage" >&2
    exit 2
    ;;
esac
