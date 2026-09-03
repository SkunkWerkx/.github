#!/usr/bin/env bash
set -euo pipefail
crate="$CRATE"
abi="$ABI"

# A separate target dir per ABI is load-bearing, not hygiene. rb-sys generates its
# bindings from the *active* Ruby's headers, but nothing in the crate's own sources
# changes between trios — so a shared target/ can hand the second trio the first
# trio's cached artifact, shipping two byte-identical extensions labelled as
# different ABIs. Nothing downstream would catch it: the wrong one installs, loads,
# and passes its suite on the Ruby it was really built for.
export CARGO_TARGET_DIR="target/ruby-$abi"

case "$RUNNER_OS" in
  Windows)
    # Everything in this branch follows from RubyInstaller's Ruby being a
    # MinGW/UCRT build rather than MSVC: rb-sys links lib<arch>-ucrt-ruby<ver>.dll.a,
    # a MinGW import lib, so the *target* can never be MSVC. Only the target — the
    # runner's default host toolchain stays MSVC and build scripts/proc-macros
    # still link with link.exe, which never reaches the shipped extension.
    #
    # The two Windows architectures need different answers, because rb-sys maps
    # them to different Rust targets in its own data/toolchains.json: x64-mingw-ucrt
    # to x86_64-pc-windows-gnu (GCC-based) and aarch64-mingw-ucrt to
    # aarch64-pc-windows-gnullvm (LLVM-based), both supported: true. What it does
    # *not* support, on either arch, is the MSVC target.
    #
    # `shell: bash` on a Windows runner is Git Bash, so command -v hands back MSYS
    # paths (/c/...). cargo, clang.exe and gcc.exe are all native Windows binaries
    # that cannot read those — cygpath -m gives the mixed form they can.
    win() { cygpath -m "$1"; }

    if [ "$RUNNER_ARCH" = ARM64 ]; then
      rust_target=aarch64-pc-windows-gnullvm
      rustup target add "$rust_target"

      # rustc already picks aarch64-w64-mingw32-clang as the linker driver for this
      # target by name, so there is no linker override to set — but its prefix has
      # to be located for the flags below, and a toolchain that isn't there should
      # fail here with something readable rather than deep inside lld. setup-ruby
      # provides it on windows-11-arm (it fetches msys2-clangarm64.7z and exports
      # MSYSTEM=CLANGARM64); RubyInstaller's ARM devkit installs the same thing
      # locally. Resolve the executable before dirname, not after — `dirname ""`
      # is ".", so testing the dirname result would never catch a missing tool.
      clang_exe="$(command -v aarch64-w64-mingw32-clang || command -v clang || true)"
      [ -n "$clang_exe" ] || { echo "::error::no CLANGARM64 toolchain found — aarch64-pc-windows-gnullvm cannot build or link"; exit 1; }
      clang_bin="$(dirname "$clang_exe")"

      # libunwind must be linked statically. rustc emits its own -lunwind inside
      # the linker's *dynamic* section, and CLANGARM64 ships both libunwind.a and
      # libunwind.dll.a in the same directory, where lld prefers the import lib.
      # Left alone the extension acquires a runtime dependency on libunwind.dll —
      # a file no consumer installing this gem would have. Verified by objdump
      # rather than assumed: with this flag the built DLL imports only
      # aarch64-ucrt-ruby<ver>.dll, api-ms-win-crt-* and Windows system DLLs.
      # `-l static=` does not search the linker's default directories, so the -L
      # is required alongside it, not decoration.
      export RUSTFLAGS="-L native=$(win "$clang_bin/../lib") -l static=unwind"
      export LIBCLANG_PATH="${LIBCLANG_PATH:-$(win "$clang_bin")}"

      # Same shape as the x64 branch, and for a sharper reason. bindgen hands clang
      # the *Rust* target triple whenever cargo is cross-compiling, and clang does
      # not understand `aarch64-pc-windows-gnullvm` — it fails with
      # "version 'llvm' in target triple ... is invalid", then can't find stdalign.h
      # because it never got as far as its own resource dir. `aarch64-w64-mingw32`
      # is the same ABI spelled the way clang accepts, exactly as x64 spells its
      # gnu target `x86_64-w64-mingw32` below.
      #
      # This was briefly believed unnecessary, from a local run where it genuinely
      # was: with a gnullvm *host* toolchain, host == target, cargo isn't
      # cross-compiling, and bindgen injects no --target at all. The runner's host
      # is MSVC, so it does. A local build that skips this flag proves nothing
      # about CI unless its host triple matches the runner's.
      clang_inc="$(win "$("$clang_exe" -print-resource-dir)")/include"
      mingw_inc="$(win "$(dirname "$clang_bin")")/include"
      export BINDGEN_EXTRA_CLANG_ARGS="--target=aarch64-w64-mingw32 -isystem \"$clang_inc\" -isystem \"$mingw_inc\""
    else
      # gnullvm, not gnu — a deliberate divergence from rb-sys's own table, which
      # maps x64-mingw-ucrt to x86_64-pc-windows-gnu. That mapping describes the
      # toolchain rb-sys cross-compiles with, not an ABI requirement: both targets
      # emit the same mingw-w64/UCRT ABI, so RubyInstaller's GCC-built Ruby loads
      # either. Taking the LLVM one instead is what closes the size gap the gnu
      # target could not:
      #
      #   gnu + LTO + strip   1,040,384 bytes   .text 792,704
      #   gnullvm + LTO       342,016 bytes     .text 264,742   (-67%)
      #
      # .text lands next to the arm64 leg's ~232 KB, which is the whole story: the
      # GCC target statically links libgcc, the LLVM one uses compiler-rt. An
      # earlier note here called that "inherent to the target, not a setting" — true
      # of the gnu target, but the target is itself the setting.
      #
      # Two things had to be true for this to be safe, and both were tested on real
      # win-x64 hardware rather than reasoned about, because both are the kind of
      # thing that builds clean and fails at runtime:
      #   * a compiler-rt extension loading into RubyInstaller's *GCC*-built UCRT64
      #     Ruby — 55/55 green on both ABIs, imports only x64-ucrt-ruby<ver>.dll and
      #     api-ms-win-crt-*;
      #   * unwinding across the boundary, since magnus turns Rust panics into Ruby
      #     exceptions and this swaps the unwinder — TimestampOutOfRangeError raises
      #     and the process survives.
      # Performance is unchanged (interleaved A/B, within noise on v4 and v7).
      rust_target=x86_64-pc-windows-gnullvm
      rustup target add "$rust_target"

      # rustc picks x86_64-w64-mingw32-clang as this target's linker driver by name,
      # so it has to be findable. Unlike arm64 — where setup-ruby installs CLANGARM64
      # for its own reasons — nothing puts CLANG64 on a windows-2025 runner's PATH,
      # so the step above pacman-installs it and this prepends it to *this script's*
      # PATH rather than to GITHUB_PATH. That scoping is deliberate: every other
      # binding (C#, Java, Go, Swift, PHP) shares this job, and dropping a second
      # clang/lld ahead of them on PATH is an excellent way to break one of them for
      # reasons that take a day to find.
      command -v x86_64-w64-mingw32-clang >/dev/null 2>&1 || export PATH="/c/msys64/clang64/bin:$PATH"
      clang_exe="$(command -v x86_64-w64-mingw32-clang || true)"
      [ -n "$clang_exe" ] || { echo "::error::no CLANG64 toolchain found — x86_64-pc-windows-gnullvm cannot build or link"; exit 1; }
      clang_bin="$(dirname "$clang_exe")"

      # Static libunwind, for exactly the reason the arm64 branch documents: rustc
      # emits its own -lunwind, CLANG64 ships libunwind.a and libunwind.dll.a side by
      # side, and lld prefers the import lib — leaving the extension with a runtime
      # dependency on a DLL no gem consumer has. Verified by objdump: with this flag
      # the built DLL imports zero libunwind.
      #
      # -C strip=symbols is kept, though it is close to a no-op now. It existed
      # because GNU ld retained an 8,295-entry COFF symbol table that -Wl,--strip-debug
      # does not touch; lld does not retain it, which is why the arm64 branch never
      # needed the flag. Kept because 342,016 bytes is the number actually measured
      # *with* it, and a flag that costs nothing is not worth re-measuring to remove.
      export RUSTFLAGS="-L native=$(win "$clang_bin/../lib") -l static=unwind -C strip=symbols"
      export LIBCLANG_PATH="${LIBCLANG_PATH:-$(win "$clang_bin")}"

      # Same bindgen redirect the arm64 branch needs, for the same reason: cargo is
      # cross-compiling (the runner's host is MSVC), so bindgen hands clang the Rust
      # triple, and clang rejects `x86_64-pc-windows-gnullvm` outright.
      # `x86_64-w64-mingw32` is the same ABI spelled the way clang accepts.
      #
      # The inner quotes are load-bearing: bindgen splits this variable on
      # whitespace, and a prefix containing a space loses the resource include, with
      # the build then dying much further in on a missing mm_malloc.h. That is how
      # it was found, so it is a regression test, not a precaution.
      clang_inc="$(win "$("$clang_exe" -print-resource-dir)")/include"
      mingw_inc="$(win "$(dirname "$clang_bin")")/include"
      export BINDGEN_EXTRA_CLANG_ARGS="--target=x86_64-w64-mingw32 -isystem \"$clang_inc\" -isystem \"$mingw_inc\""
    fi

    cargo build --release --features ruby --target "$rust_target"
    built="$CARGO_TARGET_DIR/$rust_target/release/$crate.dll"
    # No `lib` prefix on a gnu/gnullvm-target cdylib, and mingw Ruby's DLEXT is
    # `so`, not `dll` — `require` looks for exactly {crate}_native.so there.
    ext=so
    ;;
  macOS)
    cargo build --release --features ruby
    built="$CARGO_TARGET_DIR/release/lib$crate.dylib"
    ext=bundle
    ;;
  *)
    cargo build --release --features ruby
    built="$CARGO_TARGET_DIR/release/lib$crate.so"
    ext=so
    ;;
esac

# Two staged copies, both wanted. The versioned path is the fat-gem layout the
# consumer-side loader tries first, so this job exercises the real release load
# path rather than only the dev one. The flat path is what the upload step globs,
# and what a plain local `cargo build --release --features ruby` produces.
mkdir -p "../ruby/lib/$crate/$abi"
cp "$built" "../ruby/lib/$crate/$abi/${crate}_native.$ext"
cp "$built" "../ruby/lib/${crate}_native.$ext"
