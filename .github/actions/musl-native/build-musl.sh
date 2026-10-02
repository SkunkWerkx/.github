#!/usr/bin/env bash
# Builds the core crate as a musl shared library inside a real Alpine container, runs the
# crate's own test suite there, and writes lib{crate}.so to an output directory.
#
#   build-musl.sh <crate> <repo-root> <out-dir>
#
# Runs on the host (a GitHub runner, or a developer's machine with Docker) and does the
# work in `docker run`, so the result is the same file wherever it is produced. The
# container's architecture is the host's unless DOCKER_PLATFORM says otherwise
# (linux/arm64 under emulation is how an x64 box proves the arm64 leg locally).
#
# Three things here are load-bearing, and each was a real failure first:
#
#  * `-C target-feature=-crt-static`. Every *-linux-musl target defaults to a statically
#    linked C runtime, and under that default rustc cannot produce a cdylib at all. Asked
#    for one by name, as below, cargo stops with "cannot produce cdylib ... the target does
#    not support these crate types"; a manifest that listed the crate type only got a
#    "dropping unsupported crate type" warning and exit 0, which is why the .so is still
#    checked for below.
#
#  * The unwinder is linked statically. With a dynamic CRT, Rust's std links `-lgcc_s`,
#    which leaves a NEEDED entry for libgcc_s.so.1 — a library the bare `alpine`,
#    `python:*-alpine` and `golang:*-alpine` images do not ship. Measured: that build fails
#    to load there with "Error loading shared library libgcc_s.so.1". `-static-libgcc` does
#    not help, because rustc names gcc_s explicitly. What does: a directory, searched first,
#    in which `libgcc_s` is a linker script naming gcc's static libgcc_eh.a and libgcc.a.
#    Both, not just the first: on aarch64 the unwinder calls libgcc's outline-atomics
#    helpers, and a bare symlink to libgcc_eh.a failed there with "undefined reference to
#    `__aarch64_swp8_acq_rel'" while x86_64 linked fine. The result needs musl's libc and
#    nothing else, which the readelf gate at the end enforces.
#
#  * The source tree is copied into the container, not built in place. Docker runs as
#    root, and a bind-mounted target/ would leave root-owned files in the caller's
#    checkout; it would also collide with the glibc build's rust/target on the same leg.
#    What is copied is rust/ (minus target/) and, when the repo has one, corpus/ — the
#    conformance vectors a core's own tests replay from ../corpus.
#
# The Alpine tag is the oldest release still in upstream support, the same reason the PyPI
# wheels build against the oldest manylinux: a library built against an older libc loads on
# a newer one, not the other way round. Raise it when that release reaches end of life.
set -euo pipefail

usage='usage: build-musl.sh <crate> <repo-root> <out-dir>'
crate=${1:?$usage}
repo_root=$(cd "${2:?$usage}" && pwd)
mkdir -p "${3:?$usage}"
out_dir=$(cd "$3" && pwd)

image=${ALPINE_IMAGE:-alpine:3.22}
platform_args=()
[ -n "${DOCKER_PLATFORM:-}" ] && platform_args=(--platform "$DOCKER_PLATFORM")

docker run --rm "${platform_args[@]}" \
  -v "$repo_root:/src:ro" -v "$out_dir:/out" \
  -e CRATE="$crate" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "$image" sh -euc '
    apk add --no-cache build-base curl >/dev/null
    curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable >/dev/null 2>&1
    . "$HOME/.cargo/env"
    rustc -vV | grep -E "^(host|release)"

    mkdir /work
    (cd /src && tar cf - --exclude=./rust/target ./rust $([ -d corpus ] && echo ./corpus)) | tar xf - -C /work
    cd /work/rust

    mkdir /unwind
    echo "GROUP ( $(gcc -print-file-name=libgcc_eh.a) $(gcc -print-file-name=libgcc.a) )" > /unwind/libgcc_s.so
    export RUSTFLAGS="-C target-feature=-crt-static -L native=/unwind"

    cargo rustc --release --crate-type cdylib
    cargo test --release

    lib="target/release/lib$CRATE.so"
    [ -f "$lib" ] || { echo "::error::no $lib — the cdylib was not produced, is crt-static back on?"; exit 1; }

    needed=$(readelf -d "$lib" | sed -n "s/.*(NEEDED).*\[\(.*\)\]/\1/p" | sort | tr "\n" " ")
    echo "NEEDED: $needed"
    case "$needed" in
      "libc.musl-"*".so.1 ") ;;
      *) echo "::error::$lib must depend on musl libc and nothing else, found: $needed"; exit 1 ;;
    esac

    cp "$lib" /out/
    chown "$HOST_UID:$HOST_GID" "/out/lib$CRATE.so"
  '

ls -l "$out_dir/lib$crate.so"
