#!/usr/bin/env bash
# Builds the core crate's `ruby` feature as a Magnus extension for one Ruby ABI on musl,
# inside that Ruby's own official Alpine image, then runs the binding's suite through it on
# a second, bare copy of the same image, and writes {crate}_native.so to an output directory.
#
#   build-magnus-musl.sh <crate> <rid> <ruby-abi> <repo-root> <musl-lib-dir> <out-dir>
#
# <rid> is linux-musl-x64 or linux-musl-arm64 and has to match the container's architecture.
# <musl-lib-dir> holds the lib{crate}.so musl-native/build-musl.sh produced. The suite loads
# it as well as the extension: native_backend_spec.rb's agreement checks run a Fiddle
# subprocess beside the extension and compare the two.
#
# The musl counterpart of ruby-magnus/build-magnus.sh, and the reason it is a script of its
# own rather than a branch there: setup-ruby has no Alpine story, so the Ruby this binds
# against has to be the `ruby:<abi>-alpine` image's — rb-sys reads the headers and rbconfig
# of the interpreter it runs under, and that interpreter must be the musl build a consumer's
# Dockerfile starts FROM.
#
# What carries over from build-musl.sh, for the same measured reasons it gives there:
#
#  * `-C target-feature=-crt-static`, without which no *-linux-musl target yields a cdylib.
#  * The unwinder linked statically, through a `libgcc_s` linker script naming libgcc_eh.a
#    and libgcc.a. A plain build leaves a NEEDED entry for libgcc_s.so.1, and the official
#    ruby:*-alpine images do not promise libgcc to a consumer. The readelf gate below holds
#    the extension to musl's libc and nothing else — no libruby either: like every Linux
#    extension it resolves the rb_* symbols from the process that loads it.
#  * The source is copied into the container, never built in place (root-owned files in the
#    caller's checkout, and a collision with the glibc build's rust/target).
#
# Two containers, like the Python wheel's: the first has a compiler, a Rust toolchain and
# libclang (rb-sys generates its bindings with bindgen); the second is the image exactly as
# a consumer pulls it — the suite there proves the extension needs nothing the build added.
# (Not "no libgcc", as the Python check asserts: the ruby:*-alpine images ship libgcc for
# Ruby itself. The readelf gate is what holds the extension to musl's libc alone.)
# The second asserts BACKEND == :native before running rspec, because native_backend_spec.rb
# skips itself rather than failing when the extension did not load.
#
# DOCKER_PLATFORM overrides the host's architecture, as in build-musl.sh.
set -euo pipefail

usage='usage: build-magnus-musl.sh <crate> <rid> <ruby-abi> <repo-root> <musl-lib-dir> <out-dir>'
crate=${1:?$usage}
rid=${2:?$usage}
abi=${3:?$usage}
repo_root=$(cd "${4:?$usage}" && pwd)
lib_dir=$(cd "${5:?$usage}" && pwd)
mkdir -p "${6:?$usage}"
out_dir=$(cd "$6" && pwd)
[ -f "$lib_dir/lib$crate.so" ] || { echo "::error::no $lib_dir/lib$crate.so — run build-musl.sh first"; exit 1; }

image="ruby:$abi-alpine"
platform_args=()
[ -n "${DOCKER_PLATFORM:-}" ] && platform_args=(--platform "$DOCKER_PLATFORM")

docker run --rm "${platform_args[@]}" \
  -v "$repo_root:/src:ro" -v "$out_dir:/out" \
  -e CRATE="$crate" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "$image" sh -euc '
    apk add --no-cache build-base curl clang-dev >/dev/null
    curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable >/dev/null 2>&1
    . "$HOME/.cargo/env"
    echo "== $(ruby -v), Alpine $(cat /etc/alpine-release), $(rustc -V)"

    mkdir /work
    (cd /src && tar cf - --exclude=./rust/target ./rust) | tar xf - -C /work
    cd /work/rust

    mkdir /unwind
    echo "GROUP ( $(gcc -print-file-name=libgcc_eh.a) $(gcc -print-file-name=libgcc.a) )" > /unwind/libgcc_s.so
    export RUSTFLAGS="-C target-feature=-crt-static -L native=/unwind"

    # The same invocation as build-magnus.sh on Linux and macOS.
    cargo rustc --release --crate-type cdylib --features ruby

    ext="target/release/lib$CRATE.so"
    [ -f "$ext" ] || { echo "::error::no $ext — the cdylib was not produced, is crt-static back on?"; exit 1; }
    readelf -Ws --dyn-syms "$ext" | grep -q " Init_${CRATE}_native$" \
      || { echo "::error::$ext exports no Init_${CRATE}_native — not a Ruby extension"; exit 1; }

    needed=$(readelf -d "$ext" | sed -n "s/.*(NEEDED).*\[\(.*\)\]/\1/p" | sort | tr "\n" " ")
    echo "NEEDED: $needed"
    case "$needed" in
      "libc.musl-"*".so.1 ") ;;
      *) echo "::error::$ext must depend on musl libc and nothing else, found: $needed"; exit 1 ;;
    esac

    # Cargo names it lib{crate}.so; require needs {crate}_native.so, the name Ruby derives
    # the Init_ function from.
    cp "$ext" "/out/${CRATE}_native.so"
    chown "$HOST_UID:$HOST_GID" "/out/${CRATE}_native.so"
  '

# The suite, on the bare image: the binding copied out of the read-only checkout, the glibc
# libraries a tree may carry removed, the musl library placed for the Fiddle side of the
# agreement checks, and the extension at the fat-gem path lib/hyperuuid.rb tries first.
# rspec runs without Bundler for the reason suite-musl.sh's Fiddle run gives: Bundler would
# resolve fiddle from rubygems.org and compile it, and this image has no compiler.
# The gem's own runtime dependencies (HyperTabular's on hypercast), from its gemspec, since
# Bundler is not used: each a plain `gem install`, --conservative so that one an image
# already has (its default or bundled fiddle) is kept rather than rebuilt from source.
docker run --rm "${platform_args[@]}" \
  -v "$repo_root:/src:ro" -v "$lib_dir:/musl:ro" -v "$out_dir:/ext:ro" \
  -e CRATE="$crate" -e ABI="$abi" -e RID="$rid" \
  "$image" sh -euc '
    mkdir -p /work/rust
    cp /src/rust/Cargo.toml /work/rust/
    if [ -d /src/corpus ]; then cp -r /src/corpus /work/corpus; fi
    cp -r /src/ruby /work/ruby
    cd /work/ruby
    rm -rf "lib/$CRATE/native/linux-x64" "lib/$CRATE/native/linux-arm64" lib/"$CRATE"_native.* lib/"$CRATE"/*/"$CRATE"_native.*
    mkdir -p "lib/$CRATE/native/$RID" "lib/$CRATE/$ABI"
    cp "/musl/lib$CRATE.so" "lib/$CRATE/native/$RID/"
    cp "/ext/${CRATE}_native.so" "lib/$CRATE/$ABI/"
    rm -f Gemfile Gemfile.lock
    gem install rspec -v "~> 3.13" --no-document --silent
    ruby -e "Gem::Specification.load(Dir[%q(*.gemspec)].first).runtime_dependencies.each { |d| system(%q(gem), %q(install), d.name, %q(-v), d.requirement.to_s, %q(--conservative), %q(--no-document), %q(--silent)) or abort(%(could not install #{d.name} #{d.requirement})) }"
    ruby -Ilib -e "
      require ENV[%q(CRATE)]
      mod = Object.const_get(Object.constants.find { |c| c.to_s.downcase == ENV[%q(CRATE)] })
      puts %(== #{RUBY_DESCRIPTION}: BACKEND=#{mod::BACKEND})
      exit(mod::BACKEND == :native ? 0 : 1)
    " || { echo "::error::the Magnus extension did not load on Ruby $ABI"; exit 1; }
    rspec
  '

ls -l "$out_dir/${crate}_native.so"
