# HyperForge

The shared foundry behind SkunkWerkx's Hyper* projects ([HyperUuid](https://github.com/SkunkWerkx/HyperUuid), [HyperCast](https://github.com/SkunkWerkx/HyperCast), and what's next): reusable 6-platform CI pipelines that prove one Rust core against every language binding's real test suite, scaffolding conventions for new bindings, and the build archaeology — learned once, banked here, never re-learned.

## The pipeline

[`hyper-build-native.yml`](.github/workflows/hyper-build-native.yml) is the canonical build: the Rust core compiled fresh on 6 real-hardware legs (linux/osx/windows × x64/arm64), then every binding's actual test suite run against that leg's freshly-built native library — proving the real FFI load/marshalling path per OS, which `cargo test` alone cannot exercise. Interpreted-tier bindings run twice per leg: once forced onto their zero-compile fallback backend (ctypes/Fiddle), once with the native-extension backend (PyO3 on every leg, Windows included; Magnus on the unix legs) built and staged.

A Hyper* repo's entire CI file:

```yaml
name: Build native libraries and test every binding
on:
  workflow_dispatch:
  push:
    branches: [master]
jobs:
  build-native:
    uses: SkunkWerkx/.github/.github/workflows/hyper-build-native.yml@master
    with:
      project: HyperCast
      crate: hypercast
      pure_env: HYPERCAST_PURE
      csharp_test_project: csharp/HyperCast.Tests/HyperCast.Tests.csproj
      dotnet_version: "11.0.x"
      dotnet_quality: "preview"
      go_windows: false   # cgo-only until the purego fallback lands
```

Packaging jobs (NuGet/Maven today; gems and wheels as publishing expands) stay in each repo, `needs: build-native`, consuming the `native-{rid}` artifacts the shared pipeline uploads — see HyperUuid's `build-packages.yml` for the working example.

Every input, with defaults, is documented in the workflow file itself. The load-bearing ones: `project`/`crate` (PascalCase and lowercase names everything derives from), `pure_env` (the force-the-fallback env var), and the per-binding toggles (`go_windows`, `magnus`, `pyo3`).

## The conventions

The pipeline works across repos because the repos agree on layout — this agreement is the actual shared asset, and new Hyper* repos should adopt it wholesale:

- **One Rust core** at `rust/` (`cdylib` + `rlib`, zero runtime deps), C-ABI exports, caller-owned out-buffers, no allocator exports. A counting-`#[global_allocator]` test proves any allocation-free claim; a shared conformance corpus (`corpus/*.json`) is replayed by the core and every binding.
- **Native library staging**: `native/{rid}/{lib}` (or the platform's own convention: NuGet `runtimes/{rid}/native/`, Swift `Sources/{Project}/NativeLibs/{rid}/`), with a dev-loop fallback probing `rust/target/release/` so local tests need no packaging step.
- **RIDs**: `linux-x64`, `linux-arm64`, `osx-x64`, `osx-arm64`, `win-x64`, `win-arm64` — .NET's names, used by every binding.
- **Dual backends** for Python and Ruby: the fast path is the Rust core linked into the VM as a native extension (`python/native/` PyO3, `ruby/native/` Magnus) named `{crate}_native`, which on load replaces the fallback's low-level functions **in place** — everything above them stays shared byte-for-byte between backends. The ctypes/Fiddle fallback remains fully supported: it's the zero-compile install and the Pyodide/WASM path. `{PURE_ENV}=1` forces it; a `BACKEND` constant reports which is live; cross-backend agreement tests compare deterministic outputs across a subprocess boundary.
- **Darwin extension crates** carry a `.cargo/config.toml` declaring `-undefined dynamic_lookup` (see the ledger below).
- **Verdicts over exceptions** (parsing repos): every parse returns a discriminated union — the platform's native one (Rust `Result` + closed enum, C# `[Union]` with CS8509-as-error, Swift `enum`, Java sealed interface, Python `match`, Ruby `Data` + `case/in`, PHP `Success|Fault`) — never a thrown exception for bad data. Exceptions are reserved for caller bugs.

## The ledger

Build archaeology, each item paid for once in a real red run (also commented at the workflow step that pays it):

| Lesson | Detail |
| --- | --- |
| Darwin extension linking | A Ruby/Python extension must **not** link the host runtime — `rb_*`/`Py_*` symbols resolve from the host process at load, which needs `-Wl,-undefined,dynamic_lookup`. maturin/rake-compiler arrange this; plain `cargo build` does not. Linux allows undefined symbols in shared libs by default, so this only ever fails on macOS. |
| Temurin × win-arm64 | Adoptium ships Windows ARM64 JDKs for **21 and 23 only** (no 24/25 assets). Hence the JDK 23 pin. |
| PHP × win-arm64 | PHP has never shipped a native Windows ARM64 build — it runs under x64 emulation, which can never load an ARM64 native library. Skip the leg; win-x64 covers PHP-as-x64 for real. |
| Windows extension staging | A CPython extension module is a renamed DLL: `.pyd`. Ruby's `DLEXT`: `.so` on Linux, `.bundle` on macOS. |
| PyO3 on Windows | Needs no extra toolchain: the MSVC linker the core already builds with, plus CPython's import library inside the setup-python installation. Magnus on Windows is different — RubyInstaller rubies are MinGW-built, so a compatible extension is a `windows-gnu`-target exercise. |
| Composer proxies on Windows | `vendor/bin/phpunit` is a shebang script pwsh can't execute — invoke through `php`. |
| macOS runner Xcode | Ruby native-extension builds (mkmf) can fail if `xcode-select` isn't pointed at full Xcode — the pipeline sets it defensively. |
| Runner labels | `macos-26-intel` is the current osx-x64 label (macos-13 retired Dec 2025; GitHub winds down x86_64 macOS entirely by Fall 2027). `windows-11-arm` is GA, free for public repos. |
| Swift on Darwin | Presenting Swift's `Duration` requires a `platforms: [.macOS(.v13)]` floor in Package.swift — Linux has no availability gates, so this only surfaces on macOS legs. |
| Benchmarking | `XDEBUG_MODE=off` for PHP (a loaded Xdebug inflates uniformly ~14x and looks plausible). Never run benchmarks concurrently with builds or each other. Time-based benchmarks carry explicit-timestamp variants — a wall-clock read is priced by the OS (WSL2: ~1µs syscall; bare metal: tens of ns via vDSO), not by the binding. |
| Allocation-proof tests | One `#[test]` fn per counting-allocator binary — `cargo test` spawns a thread per test fn, and thread creation itself allocates. Entropy sources can take a rare re-init slow path; treat a single one-off allocation flake as suspect-the-environment before suspect-the-code. |

## License

[MIT](LICENSE)
