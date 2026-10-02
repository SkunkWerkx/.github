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
#   browser-wasm the same WebAssembly executable in headless Chrome, through a WASI shim
#
# browser-wasm is the one check that runs partly on the host: the container builds the
# module and copies it out, and the host serves it on 127.0.0.1 beside browser/index.html,
# which runs it with @bjorn3/browser_wasi_shim (installed from npm at the version pinned
# below) and prints its output and exit code into the page. Chrome dumps the page and the
# check fails unless it ends `EXIT 0`. The host needs npm, python3 and google-chrome
# ($CHROME overrides the browser). The module is the same one wasmkit runs — a plain
# wasm32-wasip1 command whose only imports are wasi_snapshot_preview1, randomness
# included (random_get, from crypto.getRandomValues in the shim).
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

need_static=false; need_wasm=false; need_browser=false
for check in $checks; do
  case "$check" in
    test|smoke-glibc) ;;
    smoke-musl) need_static=true ;;
    test-wasm|smoke-wasm) need_wasm=true ;;
    browser-wasm) need_wasm=true; need_browser=true ;;
    *) echo "unknown check: $check" >&2; exit 1 ;;
  esac
done

# The WASI shim the browser check runs the module with, pinned.
browser_wasi_shim="@bjorn3/browser_wasi_shim@0.4.2"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out=""
if $need_browser; then
  chrome="${CHROME:-google-chrome}"
  for tool in npm python3 "$chrome"; do
    command -v "$tool" >/dev/null || { echo "error: browser-wasm needs $tool on the host" >&2; exit 1; }
  done
  out="$(mktemp -d)"
  trap 'rm -rf "$out"' EXIT
fi

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
  -v "$root":/src:ro -v "$cache":/sdk:ro ${out:+-v "$out":/out} \
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
        # The same build smoke-wasm makes (a no-op after it), copied out for the host.
        browser-wasm)
          echo "::group::$PROJECT smoke test — WebAssembly module for the browser"
          cd /work/swift/StaticSmokeTest
          swift build --swift-sdk "$wasm_sdk"
          install -m 644 "$(swift build --swift-sdk "$wasm_sdk" --show-bin-path)/StaticSmokeTest.wasm" /out/smoke.wasm
          echo "::endgroup::" ;;
      esac
    done
    echo "container checks passed: $CHECKS"
  '

if $need_browser; then
  echo "::group::$project smoke test — WebAssembly in headless Chrome"
  site="$out/site"
  mkdir -p "$site"
  cp "$here/browser/index.html" "$site/"
  mv "$out/smoke.wasm" "$site/smoke.wasm"
  (cd "$site" && npm install --no-save --no-audit --no-fund --silent "$browser_wasi_shim")
  echo "smoke.wasm: $(wc -c < "$site/smoke.wasm") bytes"
  port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  (cd "$site" && exec python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1) &
  server=$!
  trap 'kill "$server" 2>/dev/null; rm -rf "$out"' EXIT
  for _ in $(seq 50); do
    curl -sf -o /dev/null "http://127.0.0.1:$port/index.html" && break
    sleep 0.2
  done
  "$chrome" --headless=new --no-sandbox --disable-gpu --user-data-dir="$out/profile" \
    --virtual-time-budget=120000 --dump-dom "http://127.0.0.1:$port/index.html" \
    2>/dev/null > "$out/dom.html" || true
  sed -n '/<pre id="out"/,/<\/pre>/p' "$out/dom.html"
  echo "::endgroup::"
  if ! grep -Eq '(^|>)EXIT 0</pre>' "$out/dom.html"; then
    echo "::error::The $project smoke executable did not exit 0 in headless Chrome."
    exit 1
  fi
fi
echo "all checks passed: $checks"
