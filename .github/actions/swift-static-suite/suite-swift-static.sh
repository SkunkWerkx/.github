#!/usr/bin/env bash
# Runs the Swift binding against the static libraries in its artifact bundle, inside the
# official Swift container for one toolchain version.
#
#   suite-swift-static.sh <Project> <repo-root> <swift-version> <check> [<check> ...]
#
# e.g. `suite-swift-static.sh HyperUuid . 6.4 smoke-musl test-wasm smoke-wasm`. The checks:
#
#   test         `swift test` on glibc — the binding's whole suite, core linked in
#   test-wasm    `swift test` for WebAssembly, run under the toolchain's own WasmKit
#   smoke-glibc  swift/StaticSmokeTest, a consumer of the root manifest, built and run
#   smoke-musl   the same executable through the static Linux SDK
#   smoke-wasm   the same executable for WebAssembly
#
# Runs on the host (a GitHub runner, or a developer's machine with Docker) and does the work
# in `docker run`, so a toolchain the runner image has no build of — every Swift before 6.4,
# on Ubuntu 26.04 — is still testable, which is how the declared floor gets a run. The
# container's architecture is the host's; the musl check builds for that architecture.
#
# Three things worth knowing:
#
#  * <swift-version> is resolved to its newest patch release through swift.org's own release
#    list, and both the image and the SDKs are that exact release. A Swift SDK only works
#    with the toolchain it was built for, so "6.4" for one and "6.4.0" for the other is a
#    module-version error waiting for the next patch.
#
#  * The SDKs are downloaded on the host, checked against the checksum swift.org publishes
#    for them, and installed in the container from the local file. The images ship no curl,
#    and a cache directory on the host (SWIFT_SDK_CACHE) survives between runs where a
#    container's home does not.
#
#  * musl gets the smoke executable, not `swift test`: the static Linux SDK ships no XCTest.
#    That is what swift/StaticSmokeTest is for.
#
# The source tree is copied into the container, not built in place: Docker runs as root, and
# a .build directory written through a bind mount would leave root-owned files in the
# checkout.
set -euo pipefail

usage="usage: suite-swift-static.sh <Project> <repo-root> <swift-version> <check> [<check> ...]"
project="${1:?$usage}"
root="$(cd "${2:?$usage}" && pwd)"
requested="${3:?$usage}"
shift 3
[ $# -gt 0 ] || { echo "$usage" >&2; exit 1; }
checks="$*"

need_static=false; need_wasm=false
for check in $checks; do
  case "$check" in
    test|smoke-glibc) ;;
    smoke-musl) need_static=true ;;
    test-wasm|smoke-wasm) need_wasm=true ;;
    *) echo "unknown check: $check" >&2; exit 1 ;;
  esac
done

# name, tag, static SDK version and checksum, wasm SDK checksum — for the newest release
# whose version is <swift-version> or a patch of it.
release="$(curl -sSfL --compressed https://www.swift.org/api/v1/install/releases.json | python3 -c '
import json, sys
want = sys.argv[1]
found = None
for entry in json.load(sys.stdin):
    name = entry["name"]
    if name == want or name.startswith(want + "."):
        found = entry
if found is None:
    sys.exit("no Swift release matches " + want)
sdk = {p["platform"]: p for p in found["platforms"] if "platform" in p}
static, wasm = sdk.get("static-sdk", {}), sdk.get("wasm-sdk", {})
print(found["name"], found["tag"], static.get("version", "-"), static.get("checksum", "-"), wasm.get("checksum", "-"))
' "$requested")"
read -r name tag static_version static_sum wasm_sum <<< "$release"
image="${SWIFT_IMAGE:-swift:$name}"
echo "Swift $requested -> $name ($tag), image $image; checks: $checks"

cache="${SWIFT_SDK_CACHE:-$HOME/.cache/hyper-swift-sdk}"
mkdir -p "$cache"
dir="$(printf '%s' "$tag" | tr '[:upper:]' '[:lower:]')"
fetch() { # <file> <url> <sha256>
  local file="$cache/$1"
  if [ ! -f "$file" ] || [ "$(sha256sum "$file" | cut -d' ' -f1)" != "$3" ]; then
    echo "downloading $1"
    curl -sSfL -o "$file.part" "$2"
    [ "$(sha256sum "$file.part" | cut -d' ' -f1)" = "$3" ] \
      || { echo "error: $1 does not match the checksum swift.org publishes" >&2; rm -f "$file.part"; exit 1; }
    mv "$file.part" "$file"
  fi
}
static_file=""; wasm_file=""
if $need_static; then
  [ "$static_sum" != "-" ] || { echo "error: Swift $name has no static Linux SDK" >&2; exit 1; }
  static_file="${tag}_static-linux-${static_version}.artifactbundle.tar.gz"
  fetch "$static_file" "https://download.swift.org/$dir/static-sdk/$tag/$static_file" "$static_sum"
fi
if $need_wasm; then
  [ "$wasm_sum" != "-" ] || { echo "error: Swift $name has no WebAssembly SDK" >&2; exit 1; }
  wasm_file="${tag}_wasm.artifactbundle.tar.gz"
  fetch "$wasm_file" "https://download.swift.org/$dir/wasm-sdk/$tag/$wasm_file" "$wasm_sum"
fi

docker run --rm \
  -e PROJECT="$project" -e CHECKS="$checks" -e TAG="$tag" \
  -e STATIC_FILE="$static_file" -e WASM_FILE="$wasm_file" \
  -v "$root":/src:ro -v "$cache":/sdk:ro \
  "$image" bash -euo pipefail -c '
    mkdir /work
    cd /src
    tar -cf - --exclude=.build --exclude=target Package.swift swift rust/Cargo.toml $([ -d corpus ] && echo corpus) \
      | tar -xf - -C /work
    swift --version

    [ -z "$STATIC_FILE" ] || swift sdk install "/sdk/$STATIC_FILE"
    [ -z "$WASM_FILE" ] || swift sdk install "/sdk/$WASM_FILE"
    wasm_sdk="${TAG}_wasm"
    musl_sdk="$(uname -m)-swift-linux-musl"

    # Builds swift/StaticSmokeTest for one target and runs what it built.
    smoke() { # <label> [swift build arguments ...]
      local label="$1"; shift
      echo "::group::$PROJECT smoke test — $label"
      cd /work/swift/StaticSmokeTest
      swift build "$@"
      local bin; bin="$(swift build "$@" --show-bin-path)"
      if [ -f "$bin/StaticSmokeTest.wasm" ]; then
        wasmkit run "$bin/StaticSmokeTest.wasm"
      else
        "$bin/StaticSmokeTest"
      fi
      echo "::endgroup::"
    }

    for check in $CHECKS; do
      case "$check" in
        test)
          echo "::group::$PROJECT swift test — glibc"
          cd /work/swift && swift test
          echo "::endgroup::" ;;
        test-wasm)
          echo "::group::$PROJECT swift test — WebAssembly"
          cd /work/swift && swift test --swift-sdk "$wasm_sdk"
          echo "::endgroup::" ;;
        smoke-glibc) smoke glibc ;;
        smoke-musl)  smoke "musl ($musl_sdk)" --swift-sdk "$musl_sdk" ;;
        smoke-wasm)  smoke WebAssembly --swift-sdk "$wasm_sdk" ;;
      esac
    done
    echo "all checks passed: $CHECKS"
  '
