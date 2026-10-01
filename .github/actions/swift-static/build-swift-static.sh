#!/usr/bin/env bash
# Builds the core crate as the static libraries the Swift binding links in, and writes them
# into the binding's artifact bundle.
#
#   build-swift-static.sh <crate> <Project> <repo-root> [rust-target ...]
#
# e.g. `build-swift-static.sh hyperuuid HyperUuid .` fills
# swift/HyperUuidCore.artifactbundle/{triple}/libhyperuuid.a for every triple below and
# rewrites the bundle's info.json. Naming targets builds only those; info.json always lists
# all five, since the others are already in the tree. Runs on any host with rustup: a static
# library is never linked, so all five are cross-compiled from one machine with no C
# toolchain for any of them.
#
# Why static at all. Swift on Linux and on WebAssembly can link a per-triple archive
# declared as a SwiftPM binary target (SE-0482, Swift 6.2), and two of those targets have no
# other option: the static Linux SDK (musl) and WASI produce executables with no dynamic
# loader, so there is nothing a bundled shared library could be opened by. On glibc the
# same mechanism means nothing has to ship beside the consumer's executable.
#
# Three things here are load-bearing:
#
#  * No std. `--no-default-features --features staticlib` on the `staticlib` profile
#    (panic = abort). Two Hyper* archives that each carried Rust's standard library cannot
#    be linked into one program — `duplicate symbol: rust_eh_personality` — which is the
#    same collision, and the same fix, as the Blazor WebAssembly archive.
#
#  * One object, not rustc's archive. The archive rustc writes is 3–5 MB: the crate's own
#    object plus every member of core and compiler_builtins. With fat LTO the crate object
#    already contains everything it uses from core, so it is taken out and re-archived on
#    its own — about 20 KB, which is what makes committing five of them per release
#    reasonable. What it still needs from outside is the C library and the compiler's
#    runtime helpers (__udivti3, the aarch64 outline atomics, __multi3 on wasm), all of
#    which the toolchain linking the final executable supplies. The gate below fails the
#    build if the object wants anything else from Rust, and the Swift suites link and run
#    every triple.
#
#  * llvm-ar, not ar. GNU ar cannot index a WebAssembly object, and wasm-ld refuses an
#    archive without a symbol table. `D` zeroes timestamps and ids so the same object
#    always gives the same archive.
set -euo pipefail

crate="${1:?usage: build-swift-static.sh <crate> <Project> <repo-root> [rust-target ...]}"
project="${2:?usage: build-swift-static.sh <crate> <Project> <repo-root> [rust-target ...]}"
root="$(cd "${3:?usage: build-swift-static.sh <crate> <Project> <repo-root> [rust-target ...]}" && pwd)"
shift 3

# rust target -> the triple SwiftPM matches a variant against. The musl pair is Swift's own
# vendor spelling for the static Linux SDK.
all_targets=(
  x86_64-unknown-linux-gnu
  aarch64-unknown-linux-gnu
  x86_64-unknown-linux-musl
  aarch64-unknown-linux-musl
  wasm32-wasip1
)
swift_triple() {
  case "$1" in
    x86_64-unknown-linux-gnu)   echo x86_64-unknown-linux-gnu ;;
    aarch64-unknown-linux-gnu)  echo aarch64-unknown-linux-gnu ;;
    x86_64-unknown-linux-musl)  echo x86_64-swift-linux-musl ;;
    aarch64-unknown-linux-musl) echo aarch64-swift-linux-musl ;;
    wasm32-wasip1)              echo wasm32-unknown-wasip1 ;;
    *) echo "unsupported rust target: $1" >&2; return 1 ;;
  esac
}

targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=("${all_targets[@]}")

bundle="$root/swift/${project}Core.artifactbundle"
[ -f "$bundle/include/$crate.h" ] && [ -f "$bundle/include/module.modulemap" ] \
  || { echo "error: $bundle/include/ must hold $crate.h and module.modulemap" >&2; exit 1; }

# llvm-ar and llvm-nm from the Rust toolchain itself, so the tools match the objects.
host="$(rustc -vV | sed -n 's/^host: //p')"
tools="$(rustc --print sysroot)/lib/rustlib/$host/bin"
[ -x "$tools/llvm-ar" ] || rustup component add llvm-tools >/dev/null
[ -x "$tools/llvm-ar" ] || { echo "error: llvm-ar not found in $tools after adding llvm-tools" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

for target in "${targets[@]}"; do
  triple="$(swift_triple "$target")"
  rustup target add "$target" >/dev/null 2>&1 || rustup target add "$target"

  (cd "$root/rust" && cargo rustc --profile staticlib --target "$target" \
      --crate-type staticlib --no-default-features --features staticlib)

  archive="$root/rust/target/$target/staticlib/lib$crate.a"
  [ -f "$archive" ] || { echo "error: $archive was not produced" >&2; exit 1; }

  # Exactly one member is the crate's own (fat LTO, one codegen unit).
  members="$("$tools/llvm-ar" t "$archive" | grep "^$crate-" || true)"
  [ "$(printf '%s\n' "$members" | grep -c .)" = 1 ] \
    || { echo "error: expected one $crate object in $archive, found: ${members:-none}" >&2; exit 1; }

  mkdir -p "$work/$target"
  (cd "$work/$target" && "$tools/llvm-ar" x "$archive" "$members" && mv "$members" "$crate.o")
  object="$work/$target/$crate.o"

  # The object may lean on libc and the compiler runtime, never on another Rust object: a
  # mangled undefined symbol means something from core was not inlined into it and would be
  # missing at the consumer's link. The one exception is wasm, where getrandom's WASI import
  # is a Rust-named symbol the linker turns into a module import rather than resolving.
  undefined="$("$tools/llvm-nm" --undefined-only "$object" | awk '{print $NF}')"
  stray="$(printf '%s\n' "$undefined" | grep -E '^(_ZN|_R)' | grep -v 'random_get$' || true)"
  [ -z "$stray" ] || { echo "error: $target object needs Rust symbols it does not carry:" >&2; echo "$stray" >&2; exit 1; }

  mkdir -p "$bundle/$triple"
  rm -f "$bundle/$triple/lib$crate.a"
  "$tools/llvm-ar" rcsD "$bundle/$triple/lib$crate.a" "$object"
  echo "$triple: lib$crate.a $(wc -c < "$bundle/$triple/lib$crate.a") bytes; needs: $(printf '%s\n' "$undefined" | grep -v -E '^(_ZN|_R)' | sort | tr '\n' ' ')"
done

# info.json: one variant per triple, all sharing the header and module map. The version is
# the crate's, so a bundle staged for a release says which release it is.
version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/rust/Cargo.toml" | head -1)"
{
  printf '{\n  "schemaVersion": "1.0",\n  "artifacts": {\n    "%sCore": {\n      "type": "staticLibrary",\n      "version": "%s",\n      "variants": [\n' "$project" "$version"
  last=$(( ${#all_targets[@]} - 1 ))
  for i in "${!all_targets[@]}"; do
    triple="$(swift_triple "${all_targets[$i]}")"
    sep=","; [ "$i" = "$last" ] && sep=""
    printf '        {\n          "path": "%s/lib%s.a",\n          "supportedTriples": ["%s"],\n          "staticLibraryMetadata": {\n            "headerPaths": ["include"],\n            "moduleMapPath": "include/module.modulemap"\n          }\n        }%s\n' \
      "$triple" "$crate" "$triple" "$sep"
  done
  printf '      ]\n    }\n  }\n}\n'
} > "$bundle/info.json"

echo "bundle: $bundle (version $version)"
