#!/usr/bin/env bash
# Builds the core crate as a static library for every target that links it rather than
# loads it, and places each archive where the binding that consumes it expects it.
#
#   build-static-libs.sh <crate> <Project> <repo-root> [rust-target ...]
#
# e.g. `build-static-libs.sh hyperuuid HyperUuid .` builds all nine targets below. Naming
# targets builds only those and leaves every other archive in the tree as it was. Runs on
# any host with rustup and python3: a static library is compiled and never linked, so every
# target — macOS and Windows included — cross-compiles from one machine with no C toolchain
# and no SDK for any of them.
#
# Who links what:
#
#   Swift   swift/{Project}Core.artifactbundle/{triple}/      Linux (glibc, musl), WebAssembly
#   Go      go/staticlib/{goos}_{goarch}/                     Linux, macOS — the cgo backend
#   C#      csharp/{Project}/staticlibs/{rid}/                all eight RIDs — Native AOT
#
# A tree that is not in the repository is skipped, so a caller without one of those
# bindings needs no flag for it.
#
# Why static at all. Each of these three produces, or can produce, an executable that has
# no business opening a shared library at run time: Swift's static Linux SDK and its
# WebAssembly SDK have no loader to open one with; a Go binary that links the core needs no
# embedded copies of every platform's library and no temp file to extract one into; a
# Native AOT executable with the core linked in is one file. Everything else — the JIT,
# the JVM, the interpreters — still loads the shared library, which is unchanged.
#
# Three things here are load-bearing:
#
#  * No std. `--no-default-features --features staticlib` on the `staticlib` profile
#    (panic = abort). Two Hyper* archives that each carried Rust's standard library cannot
#    be linked into one program — `duplicate symbol: rust_eh_personality` — which is the
#    same collision, and the same fix, as the Blazor WebAssembly archive.
#
#  * One object, not rustc's archive. What rustc writes is 3–5 MB: the crate's object plus
#    every member of core and compiler_builtins. With fat LTO the crate's object already
#    holds everything it uses from core, so the archive that ships is that one object
#    re-archived — 10 to 210 KB. What it still needs from outside is the C library and the
#    compiler's runtime helpers (128-bit division, the aarch64 outline atomics, __multi3 on
#    WebAssembly), and every toolchain that links one of these supplies them: gcc and clang
#    link libgcc or compiler-rt on their own.
#
#    Except MSVC, which has no 128-bit helpers at all. For the Windows targets the archive
#    keeps what a linker would have pulled: start from the crate's object, take whichever
#    member defines each symbol it leaves undefined, repeat until nothing more is taken.
#    That adds Rust's own helper objects and the import stub for the system random source.
#    The same closure is deliberately not used elsewhere: off Windows those helper objects
#    carry unwind tables naming `rust_eh_personality`, which a no_std archive does not
#    define and two of them could not both define — the Blazor collision again.
#
#  * llvm-ar and llvm-nm from the Rust toolchain. GNU ar cannot index a WebAssembly, Mach-O
#    or COFF object, and each of those linkers refuses an archive without the symbol table
#    its own format expects; llvm-ar writes the right one for whatever the members are.
set -euo pipefail

usage="usage: build-static-libs.sh <crate> <Project> <repo-root> [rust-target ...]"
crate="${1:?$usage}"
project="${2:?$usage}"
root="$(cd "${3:?$usage}" && pwd)"
shift 3

all_targets=(
  x86_64-unknown-linux-gnu
  aarch64-unknown-linux-gnu
  x86_64-unknown-linux-musl
  aarch64-unknown-linux-musl
  x86_64-apple-darwin
  aarch64-apple-darwin
  x86_64-pc-windows-msvc
  aarch64-pc-windows-msvc
  wasm32-wasip1
)
targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=("${all_targets[@]}")

# The file a target's linker expects: MSVC's convention has no `lib` prefix.
archive_name() {
  case "$1" in
    *-windows-msvc) echo "$crate.lib" ;;
    *) echo "lib$crate.a" ;;
  esac
}

# Where one target's archive goes, one destination directory per line. The Swift names are
# the triples SwiftPM matches a variant against (the musl pair is Swift's own vendor
# spelling for the static Linux SDK). Go takes the musl build for Linux on both C
# libraries: cgo has no build constraint that tells them apart, and of the two objects it
# is the one that asks the C library for nothing but calls both have had for a decade.
destinations() {
  local swift="swift/${project}Core.artifactbundle" go="go/staticlib" csharp="csharp/$project/staticlibs"
  case "$1" in
    x86_64-unknown-linux-gnu)   echo "$swift/x86_64-unknown-linux-gnu";  echo "$csharp/linux-x64" ;;
    aarch64-unknown-linux-gnu)  echo "$swift/aarch64-unknown-linux-gnu"; echo "$csharp/linux-arm64" ;;
    x86_64-unknown-linux-musl)  echo "$swift/x86_64-swift-linux-musl";   echo "$csharp/linux-musl-x64";   echo "$go/linux_amd64" ;;
    aarch64-unknown-linux-musl) echo "$swift/aarch64-swift-linux-musl";  echo "$csharp/linux-musl-arm64"; echo "$go/linux_arm64" ;;
    x86_64-apple-darwin)        echo "$csharp/osx-x64";   echo "$go/darwin_amd64" ;;
    aarch64-apple-darwin)       echo "$csharp/osx-arm64"; echo "$go/darwin_arm64" ;;
    x86_64-pc-windows-msvc)     echo "$csharp/win-x64" ;;
    aarch64-pc-windows-msvc)    echo "$csharp/win-arm64" ;;
    wasm32-wasip1)              echo "$swift/wasm32-unknown-wasip1" ;;
    *) echo "unsupported rust target: $1" >&2; return 1 ;;
  esac
}

# Windows runners spell it without the 3.
python="$(command -v python3 || command -v python)" \
  || { echo "error: python3 is required for the archive trim" >&2; exit 1; }

host="$(rustc -vV | sed -n 's/^host: //p')"
tools="$(rustc --print sysroot)/lib/rustlib/$host/bin"
[ -x "$tools/llvm-ar" ] || rustup component add llvm-tools >/dev/null
[ -x "$tools/llvm-ar" ] || { echo "error: llvm-ar not found in $tools after adding llvm-tools" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

for target in "${targets[@]}"; do
  destinations "$target" >/dev/null
  rustup target add "$target" >/dev/null 2>&1 || rustup target add "$target"

  (cd "$root/rust" && cargo rustc --profile staticlib --target "$target" \
      --crate-type staticlib --no-default-features --features staticlib)

  name="$(archive_name "$target")"
  full="$root/rust/target/$target/staticlib/$name"
  [ -f "$full" ] || { echo "error: $full was not produced" >&2; exit 1; }

  mkdir -p "$work/$target"
  trimmed="$work/$target/$name"
  cp "$full" "$trimmed"

  # The trim. llvm-nm's posix format puts the member in brackets, which survives the
  # member names a Windows archive has (`C:\a\rust\...\ucmpti2.o`, with a colon, backslashes
  # and a forward slash). Members are removed from a copy rather than the survivors being
  # extracted and re-archived, for the same reason: those names do not make files.
  "$python" - "$tools" "$trimmed" "$crate" "$target" <<'PY'
import re, subprocess, sys

tools, archive, crate, target = sys.argv[1:5]
row = re.compile(r"^.*?\[(?P<member>.*)\]: (?P<symbol>\S+) (?P<kind>\S)")

def symbols(flag):
    out = subprocess.run([f"{tools}/llvm-nm", "-A", "--format=posix", flag, archive],
                         check=True, capture_output=True, text=True).stdout
    for line in out.splitlines():
        match = row.match(line)
        if match:
            yield match["member"], match["symbol"]

defined = {}
for member, symbol in symbols("--defined-only"):
    defined.setdefault(symbol, member)
undefined = {}
for member, symbol in symbols("--undefined-only"):
    undefined.setdefault(member, set()).add(symbol)

def listing():
    return subprocess.run([f"{tools}/llvm-ar", "t", archive],
                          check=True, capture_output=True, text=True).stdout.splitlines()

members = listing()
own = [m for m in members if m.startswith(f"{crate}-")]
if len(own) != 1:
    sys.exit(f"error: expected exactly one {crate} object in {archive}, found {own or 'none'}")

keep, queue = set(own), list(own)
while queue and target.endswith("-windows-msvc"):
    for symbol in undefined.get(queue.pop(), ()):
        provider = defined.get(symbol)
        if provider is not None and provider not in keep:
            keep.add(provider)
            queue.append(provider)

# A name can repeat (the import members of one DLL all carry its name), and `d` removes one
# occurrence of each name it is given per run.
remaining = members
while True:
    drop = sorted({m for m in remaining if m not in keep})
    if not drop:
        break
    subprocess.run([f"{tools}/llvm-ar", "dD", archive, *drop], check=True)
    remaining = listing()

# What the consumer's toolchain still has to supply: the C library and the compiler's
# runtime helpers, and nothing from Rust.
# A mangled name left undefined means something from core was neither inlined into the
# crate's object nor pulled in above, and would be missing at the consumer's link. Mach-O
# prefixes every symbol with an underscore. The one exception is WebAssembly, where
# getrandom's WASI import is a Rust-named symbol the linker turns into a module import.
mangled = re.compile(r"^_?(_ZN|_R)")
still = {s for m in keep for s in undefined.get(m, ()) if defined.get(s) not in keep}
stray = sorted(s for s in still
               if mangled.match(s) and not (target.startswith("wasm32") and s.endswith("random_get")))
if stray:
    sys.exit(f"error: {target} archive needs Rust symbols it does not carry: {' '.join(stray)}")
plain = sorted(s for s in still if not mangled.match(s))
print(f"{target}: {len(remaining)} of {len(members)} members kept; needs: {' '.join(plain)}")
PY

  while IFS= read -r dir; do
    # Only into a binding the repository has.
    case "$dir" in
      swift/*)  [ -d "$root/swift/${project}Core.artifactbundle/include" ] || continue ;;
      go/*)     [ -d "$root/go" ] || continue ;;
      csharp/*) [ -d "$root/csharp/$project" ] || continue ;;
    esac
    mkdir -p "$root/$dir"
    install -m 644 "$trimmed" "$root/$dir/$name"
    echo "  -> $dir/$name ($(wc -c < "$trimmed") bytes)"
  done < <(destinations "$target")
done

# The Swift bundle's info.json: one variant per triple, all sharing the header and module
# map. The version is the crate's, so a bundle staged for a release says which one it is.
bundle="$root/swift/${project}Core.artifactbundle"
if [ -d "$bundle/include" ]; then
  [ -f "$bundle/include/$crate.h" ] && [ -f "$bundle/include/module.modulemap" ] \
    || { echo "error: $bundle/include/ must hold $crate.h and module.modulemap" >&2; exit 1; }
  version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/rust/Cargo.toml" | head -1)"
  triples=(x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu x86_64-swift-linux-musl aarch64-swift-linux-musl wasm32-unknown-wasip1)
  {
    printf '{\n  "schemaVersion": "1.0",\n  "artifacts": {\n    "%sCore": {\n      "type": "staticLibrary",\n      "version": "%s",\n      "variants": [\n' "$project" "$version"
    last=$(( ${#triples[@]} - 1 ))
    for i in "${!triples[@]}"; do
      sep=","; [ "$i" = "$last" ] && sep=""
      printf '        {\n          "path": "%s/lib%s.a",\n          "supportedTriples": ["%s"],\n          "staticLibraryMetadata": {\n            "headerPaths": ["include"],\n            "moduleMapPath": "include/module.modulemap"\n          }\n        }%s\n' \
        "${triples[$i]}" "$crate" "${triples[$i]}" "$sep"
    done
    printf '      ]\n    }\n  }\n}\n'
  } > "$bundle/info.json"
  echo "swift bundle: $bundle (version $version)"
fi
