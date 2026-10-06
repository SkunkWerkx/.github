#!/usr/bin/env bash
# Builds the core crate as a static library for every target that links it rather than
# loads it, and places each archive where the binding that consumes it expects it.
#
#   build-static-libs.sh <crate> <Project> <repo-root> [rust-target ...]
#
# e.g. `build-static-libs.sh hyperuuid HyperUuid .` builds all nine targets below. Naming
# targets builds only those and leaves every other archive in the tree as it was. With
# APPLE_MOBILE=1 in the environment the default set is those nine and the four Apple mobile
# targets (iOS, the iOS simulator on Apple silicon, and Mac Catalyst on both architectures);
# a repository opts in, so one whose bindings do not link them yet ships none. Runs on
# any host with rustup and python3: a static library is compiled and never linked, so every
# target — macOS and Windows included — cross-compiles from one machine with no C toolchain
# and no SDK for any of them.
#
# Who links what:
#
#   Swift   swift/{Project}Core.artifactbundle/{triple}/      all eight RIDs, WebAssembly
#   Go      go/staticlib/{goos}_{goarch}/                     Linux, macOS, Windows — cgo
#           go/staticlib/wasm/                                WebAssembly — TinyGo's cgo
#   C#      csharp/{Project}/staticlibs/{rid}/                all eight RIDs — Native AOT
#
# and with APPLE_MOBILE=1:
#
#   Swift   swift/{Project}CoreApple.xcframework/{slice}/     iOS, its simulator, Mac Catalyst
#   C#      csharp/{Project}/staticlibs/{rid}/                ios-arm64, iossimulator-arm64,
#                                                             maccatalyst-arm64, maccatalyst-x64
#
# Swift takes those three from an XCFramework and not from the artifact bundle because an iOS
# or Catalyst app is built by Xcode, which has linked a static library out of an XCFramework
# since Xcode 12 and is not known to read a static-library artifact bundle at all. Its
# Catalyst slice is arm64 only: a slice holding two architectures is one universal file, and
# nothing here can write one (lipo is Apple's). C# takes plain archives, one per RID, so it
# has the x86_64 Catalyst one as well.
#
# A tree that is not in the repository is skipped, so a caller without one of those
# bindings needs no flag for it.
#
# Why static at all. Each of these three produces, or can produce, an executable that has
# no business opening a shared library at run time: Swift's static Linux SDK and its
# WebAssembly SDK have no loader to open one with, and a SwiftPM executable that links the
# core has no resource bundle to deploy beside it; a Go binary that links the core needs no
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
#    Go's Windows copy leaves the helper objects out again and keeps only the crate's object
#    and the import stubs. Go links with MinGW — gcc on amd64, llvm-mingw's clang on arm64 —
#    whose libgcc or compiler-rt supplies the helpers, so Go never called the archive's
#    copies; and Rust emits them as COFF weak externals (`__divti3` defaulting to
#    `.weak.__divti3.default`), which GNU ld, unlike lld, does not settle on the first
#    archive's: it pulls the same helper out of every Hyper* archive in the program and then
#    refuses the identical `.weak.*.default` globals as a multiple definition. A Go program
#    that imports two cores — HyperTabular's module imports HyperCast's — failed to link on
#    windows/amd64 without `-Wl,--allow-multiple-definition`, a flag that silences every
#    duplicate symbol in the program, not only these.
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
apple_mobile_targets=(
  aarch64-apple-ios
  aarch64-apple-ios-sim
  aarch64-apple-ios-macabi
  x86_64-apple-ios-macabi
)
[ "${APPLE_MOBILE:-}" = 1 ] && all_targets+=("${apple_mobile_targets[@]}")
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
# the triples SwiftPM matches a variant against, which compares vendor as well as arch, OS
# and environment: the musl pair is Swift's own vendor spelling for the static Linux SDK,
# Windows is `unknown` where Rust says `pc`, and macOS is `macosx` (arm64 and aarch64 are
# one architecture to both build systems). Go takes the musl build for Linux on both C
# libraries: cgo has no build constraint that tells them apart, and of the two objects it
# is the one that asks the C library for nothing but calls both have had for a decade.
# Go under TinyGo (browser and WASI) takes the wasm32-wasip1 build, the same bytes as
# Swift's: even TinyGo's browser target is wasm32-wasi underneath, and its wasm_exec.js
# supplies the one WASI call the archive makes (random_get). Go on Windows links the MSVC
# archive C# does, less Rust's 128-bit helpers (see the trim above): MinGW's linker (gcc, or
# llvm-mingw's lld on arm64) reads MSVC's COFF objects, and the archive carries its own
# import stub for the system random source, so cgo needs no extra library for it. Go's tree
# spells every archive lib{crate}.a, the name its cgo lines use on every platform.
# The Apple mobile slices are named the way `xcodebuild -create-xcframework` names them
# (platform, architectures, then the variant), and the C# directories by .NET's RIDs.
destinations() {
  local swift="swift/${project}Core.artifactbundle" go="go/staticlib" csharp="csharp/$project/staticlibs"
  local apple="swift/${project}CoreApple.xcframework"
  case "$1" in
    x86_64-unknown-linux-gnu)   echo "$swift/x86_64-unknown-linux-gnu";  echo "$csharp/linux-x64" ;;
    aarch64-unknown-linux-gnu)  echo "$swift/aarch64-unknown-linux-gnu"; echo "$csharp/linux-arm64" ;;
    x86_64-unknown-linux-musl)  echo "$swift/x86_64-swift-linux-musl";   echo "$csharp/linux-musl-x64";   echo "$go/linux_amd64" ;;
    aarch64-unknown-linux-musl) echo "$swift/aarch64-swift-linux-musl";  echo "$csharp/linux-musl-arm64"; echo "$go/linux_arm64" ;;
    x86_64-apple-darwin)        echo "$swift/x86_64-apple-macosx";           echo "$csharp/osx-x64";   echo "$go/darwin_amd64" ;;
    aarch64-apple-darwin)       echo "$swift/arm64-apple-macosx";            echo "$csharp/osx-arm64"; echo "$go/darwin_arm64" ;;
    x86_64-pc-windows-msvc)     echo "$swift/x86_64-unknown-windows-msvc";  echo "$csharp/win-x64";   echo "$go/windows_amd64" ;;
    aarch64-pc-windows-msvc)    echo "$swift/aarch64-unknown-windows-msvc"; echo "$csharp/win-arm64"; echo "$go/windows_arm64" ;;
    wasm32-wasip1)              echo "$swift/wasm32-unknown-wasip1"; echo "$go/wasm" ;;
    aarch64-apple-ios)          echo "$apple/ios-arm64";              echo "$csharp/ios-arm64" ;;
    aarch64-apple-ios-sim)      echo "$apple/ios-arm64-simulator";    echo "$csharp/iossimulator-arm64" ;;
    aarch64-apple-ios-macabi)   echo "$apple/ios-arm64-maccatalyst";  echo "$csharp/maccatalyst-arm64" ;;
    x86_64-apple-ios-macabi)    echo "$csharp/maccatalyst-x64" ;;
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

  mkdir -p "$work/$target/go"
  trimmed="$work/$target/$name"
  go_windows="$work/$target/go/lib$crate.a"
  cp "$full" "$trimmed"

  # The trim. llvm-nm's posix format puts the member in brackets, which survives the
  # member names a Windows archive has (`C:\a\rust\...\ucmpti2.o`, with a colon, backslashes
  # and a forward slash). Members are removed from a copy rather than the survivors being
  # extracted and re-archived, for the same reason: those names do not make files.
  "$python" - "$tools" "$trimmed" "$crate" "$target" "$go_windows" <<'PY'
import re, shutil, subprocess, sys

tools, archive, crate, target, go_windows = sys.argv[1:6]
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

def listing(path=archive):
    return subprocess.run([f"{tools}/llvm-ar", "t", path],
                          check=True, capture_output=True, text=True).stdout.splitlines()

def drop_members(path, keep):
    # A name can repeat (the import members of one DLL all carry its name), and `d` removes
    # one occurrence of each name it is given per run.
    remaining = listing(path)
    while True:
        drop = sorted({m for m in remaining if m not in keep})
        if not drop:
            return remaining
        subprocess.run([f"{tools}/llvm-ar", "dD", path, *drop], check=True)
        remaining = listing(path)

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

remaining = drop_members(archive, keep)

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

# Go's Windows copy: the crate's object and the import stubs (members named for their DLL),
# without Rust's helper objects, which MinGW's own runtime library supplies (see above).
if target.endswith("-windows-msvc"):
    shutil.copyfile(archive, go_windows)
    go_keep = {m for m in keep if m in own or m.lower().endswith(".dll")}
    go_remaining = drop_members(go_windows, go_keep)
    go_still = {s for m in go_keep for s in undefined.get(m, ()) if defined.get(s) not in go_keep}
    go_stray = sorted(s for s in go_still if mangled.match(s))
    if go_stray:
        sys.exit(f"error: {target} Go archive needs Rust symbols it does not carry: {' '.join(go_stray)}")
    print(f"{target} (Go): {len(go_remaining)} members kept; needs: {' '.join(sorted(go_still))}")
PY

  while IFS= read -r dir; do
    # Only into a binding the repository has.
    case "$dir" in
      swift/*)  [ -d "$root/swift/${project}Core.artifactbundle/include" ] || continue ;;
      go/*)     [ -d "$root/go" ] || continue ;;
      csharp/*) [ -d "$root/csharp/$project" ] || continue ;;
    esac
    dest="$name" source="$trimmed"
    case "$dir" in go/*) dest="lib$crate.a" ;; esac
    case "$dir" in go/staticlib/windows_*) source="$go_windows" ;; esac
    mkdir -p "$root/$dir"
    install -m 644 "$source" "$root/$dir/$dest"
    echo "  -> $dir/$dest ($(wc -c < "$source") bytes)"
  done < <(destinations "$target")
done

# The Swift bundle's info.json: one variant per triple whose archive is in the bundle, all
# sharing the header and module map, in a fixed order so the staged file diffs cleanly.
# Listing only what is there keeps a partial build usable: a CI leg that builds its own
# triple gets a bundle naming that archive and whatever the tree already carries, never one
# naming an archive that is missing. The full build (no targets named) lists all nine. The
# version is the crate's, so a bundle staged for a release says which one it is.
bundle="$root/swift/${project}Core.artifactbundle"
if [ -d "$bundle/include" ]; then
  [ -f "$bundle/include/$crate.h" ] && [ -f "$bundle/include/module.modulemap" ] \
    || { echo "error: $bundle/include/ must hold $crate.h and module.modulemap" >&2; exit 1; }
  version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/rust/Cargo.toml" | head -1)"
  variants=()
  for triple in x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu \
                x86_64-swift-linux-musl aarch64-swift-linux-musl \
                arm64-apple-macosx x86_64-apple-macosx \
                x86_64-unknown-windows-msvc aarch64-unknown-windows-msvc \
                wasm32-unknown-wasip1; do
    case "$triple" in
      *-windows-msvc) file="$crate.lib" ;;
      *) file="lib$crate.a" ;;
    esac
    [ -f "$bundle/$triple/$file" ] && variants+=("$triple/$file")
  done
  [ ${#variants[@]} -gt 0 ] || { echo "error: $bundle has no archive for any triple" >&2; exit 1; }
  {
    printf '{\n  "schemaVersion": "1.0",\n  "artifacts": {\n    "%sCore": {\n      "type": "staticLibrary",\n      "version": "%s",\n      "variants": [\n' "$project" "$version"
    last=$(( ${#variants[@]} - 1 ))
    for i in "${!variants[@]}"; do
      sep=","; [ "$i" = "$last" ] && sep=""
      printf '        {\n          "path": "%s",\n          "supportedTriples": ["%s"],\n          "staticLibraryMetadata": {\n            "headerPaths": ["include"],\n            "moduleMapPath": "include/module.modulemap"\n          }\n        }%s\n' \
        "${variants[$i]}" "${variants[$i]%%/*}" "$sep"
    done
    printf '      ]\n    }\n  }\n}\n'
  } > "$bundle/info.json"
  echo "swift bundle: $bundle (version $version, ${#variants[@]} variants)"
fi

# The Swift XCFramework for iOS, its simulator and Mac Catalyst: one slice per archive that
# is there, each with its own copy of the header and module map, and the Info.plist that
# names them. Written by hand because `xcodebuild -create-xcframework` only exists on a Mac
# and this runs anywhere; the format is a property list with one entry per slice. As with
# info.json, only what is present is listed, in a fixed order.
#
# The header and module map go in Headers/{Project}Core/, not in Headers/ itself. Xcode
# copies every XCFramework's Headers into one include directory per build, so two packages
# that each put a module.modulemap at the top of theirs (this one and a sibling Hyper*
# package in the same app) fail with "multiple commands produce module.modulemap". Clang
# finds a module map in a directory named for its module, which is what this is.
xcframework="$root/swift/${project}CoreApple.xcframework"
slices=()
if [ -d "$bundle/include" ]; then
  for slice in ios-arm64 ios-arm64-simulator ios-arm64-maccatalyst; do
    [ -f "$xcframework/$slice/lib$crate.a" ] || continue
    slices+=("$slice")
    headers="$xcframework/$slice/Headers/${project}Core"
    mkdir -p "$headers"
    install -m 644 "$bundle/include/$crate.h" "$headers/$crate.h"
    install -m 644 "$bundle/include/module.modulemap" "$headers/module.modulemap"
  done
fi
if [ ${#slices[@]} -gt 0 ]; then
  {
    printf '<?xml version="1.0" encoding="UTF-8"?>\n'
    printf '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    printf '<plist version="1.0">\n<dict>\n\t<key>AvailableLibraries</key>\n\t<array>\n'
    for slice in "${slices[@]}"; do
      printf '\t\t<dict>\n'
      printf '\t\t\t<key>BinaryPath</key>\n\t\t\t<string>lib%s.a</string>\n' "$crate"
      printf '\t\t\t<key>HeadersPath</key>\n\t\t\t<string>Headers</string>\n'
      printf '\t\t\t<key>LibraryIdentifier</key>\n\t\t\t<string>%s</string>\n' "$slice"
      printf '\t\t\t<key>LibraryPath</key>\n\t\t\t<string>lib%s.a</string>\n' "$crate"
      printf '\t\t\t<key>SupportedArchitectures</key>\n\t\t\t<array>\n\t\t\t\t<string>arm64</string>\n\t\t\t</array>\n'
      printf '\t\t\t<key>SupportedPlatform</key>\n\t\t\t<string>ios</string>\n'
      case "$slice" in
        *-simulator)   printf '\t\t\t<key>SupportedPlatformVariant</key>\n\t\t\t<string>simulator</string>\n' ;;
        *-maccatalyst) printf '\t\t\t<key>SupportedPlatformVariant</key>\n\t\t\t<string>maccatalyst</string>\n' ;;
      esac
      printf '\t\t</dict>\n'
    done
    printf '\t</array>\n\t<key>CFBundlePackageType</key>\n\t<string>XFWK</string>\n'
    printf '\t<key>XCFrameworkFormatVersion</key>\n\t<string>1.0</string>\n</dict>\n</plist>\n'
  } > "$xcframework/Info.plist"
  echo "swift xcframework: $xcframework (${#slices[@]} slices: ${slices[*]})"
fi
