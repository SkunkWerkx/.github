# HyperForge

The shared foundry behind SkunkWerkx's Hyper* projects ([HyperUuid](https://github.com/SkunkWerkx/HyperUuid), [HyperCast](https://github.com/SkunkWerkx/HyperCast), and the ingestion round — [HyperTabular](https://github.com/SkunkWerkx/HyperTabular), [HyperDelimited](https://github.com/SkunkWerkx/HyperDelimited), [HyperWorkbook](https://github.com/SkunkWerkx/HyperWorkbook)): reusable 6-platform CI pipelines that prove one Rust core against every language binding's real test suite, scaffolding conventions for new bindings, and the build archaeology — learned once, banked here, never re-learned.

## The pipeline

[`hyper-build-native.yml`](.github/workflows/hyper-build-native.yml) is the canonical build: the Rust core compiled fresh on 6 real-hardware legs (linux/osx/windows × x64/arm64), then every binding's actual test suite run against that leg's freshly-built native library — proving the real FFI load/marshalling path per OS, which `cargo test` alone cannot exercise. Ruby's suite runs twice per leg: once forced onto its zero-compile Fiddle fallback, once on the Magnus extension, built and staged on every leg — both Windows legs included, via the `gnullvm` targets. Python has no fallback: the PyO3 extension is built and tested per leg.

The shape of a Hyper* repo's CI file (HyperCast's, minus its comments):

```yaml
name: CI — build native, build wasm, test every binding
on:
  pull_request:
  workflow_dispatch:
jobs:
  build-native:
    uses: SkunkWerkx/.github/.github/workflows/hyper-build-native.yml@master
    permissions:            # attestation signs with OIDC; a reusable workflow
      contents: read        # can never escalate beyond its caller's grant
      id-token: write
      attestations: write
    with:
      project: HyperCast
      crate: hypercast
      pure_env: HYPERCAST_PURE
      csharp_test_project: csharp/HyperCast.Tests/HyperCast.Tests.csproj
      csharp_aot_project: csharp/HyperCast.AotSmokeTest/HyperCast.AotSmokeTest.csproj
      dotnet_version: "11.0.x"
      dotnet_quality: "preview"
      csharp_runtimes_dir: csharp/HyperCast/runtimes
      java_resources_dir: java/src/main/resources/native
      ruby_compat_version: "3.4"
  build-wasm:
    uses: SkunkWerkx/.github/.github/workflows/hyper-build-wasm.yml@master
    with:
      crate: hypercast
```

No `push` trigger, deliberately: the stage-native-binaries and prepare-release workflows direct-push mechanical commits, and a matrix run on each of those is wasted compute.

The other reusable workflows are the release side, called from each repo's `release.yml` at tag time — the only moment a version is genuinely known, so nothing here bakes one in: [`hyper-build-wasm.yml`](.github/workflows/hyper-build-wasm.yml) builds the `wasm32` staticlib once for the NuGet package's browser-wasm asset; [`hyper-pack-nuget.yml`](.github/workflows/hyper-pack-nuget.yml) packs and attests the `.nupkg` but never pushes it (nuget.org's Trusted Publishing validates the OIDC token against wherever the *executing* workflow file lives, so the push must stay in the caller); [`hyper-publish-crate.yml`](.github/workflows/hyper-publish-crate.yml) and [`hyper-publish-maven.yml`](.github/workflows/hyper-publish-maven.yml) package, attest, and publish the crate and the jar. Gems and wheels are packed in the caller directly. Either project's `release.yml` is the working example.

Every input, with defaults, is documented in the workflow file itself. The load-bearing ones: `project`/`crate` (PascalCase and lowercase names everything derives from), `pure_env` (the force-the-fallback env var), `csharp_aot_project` (turns the AOT claim into a per-platform receipt), and the per-binding toggles (`go`, `go_windows`, `magnus`).

## The conventions

The pipeline works across repos because the repos agree on layout — this agreement is the actual shared asset, and new Hyper* repos should adopt it wholesale:

- **One Rust core** at `rust/` (`cdylib` + `rlib`, zero runtime deps), C-ABI exports, caller-owned out-buffers, no allocator exports. A counting-`#[global_allocator]` test proves any allocation-free claim; a shared conformance corpus (`corpus/*.json`) is replayed by the core and every binding.
- **Native library staging**: `native/{rid}/{lib}` (or the platform's own convention: NuGet `runtimes/{rid}/native/`, Swift `Sources/{Project}/NativeLibs/{rid}/`), with a dev-loop fallback probing `rust/target/release/` so local tests need no packaging step.
- **RIDs**: `linux-x64`, `linux-arm64`, `osx-x64`, `osx-arm64`, `win-x64`, `win-arm64` — .NET's names, used by every binding.
- **Native extensions** for the interpreted tier, where the FFI mechanism itself was the cost. Python is PyO3 only: the extension lives in the core crate behind a `python` feature, maturin builds one `abi3` wheel per platform, and the `ctypes` fallback is retired in both projects (its Pyodide proof went with it). Ruby is dual-backend: the Magnus extension (`{crate}_native`, behind a `ruby` feature) on load replaces the Fiddle fallback's low-level functions **in place** — everything above them stays shared byte-for-byte — and ships as precompiled platform gems, fat across supported Ruby minors, with Fiddle as the zero-compile install anywhere else. `{PURE_ENV}=1` forces the fallback; a `BACKEND` constant reports which is live; a cross-backend agreement test compares deterministic outputs across a subprocess boundary.
- **The core crate** carries a `rust/.cargo/config.toml` declaring `-undefined dynamic_lookup` for the extension builds on Darwin (see the ledger below).
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
| Trusted Publishing bootstrap | A crates.io Trusted Publisher configuration **can only be created after the crate already exists** — [RFC 3691](https://rust-lang.github.io/rfcs/3691-trusted-publishing-cratesio.html): *"A Trusted Publisher Configuration can only be created after an initial manual publishing of a crate."* There is no equivalent of PyPI's **pending publishers**, which do let you pre-register a project that has never been published. So a brand-new crate is a two-step bootstrap: publish once by hand with an API token (a `0.0.0` placeholder is enough), register the trusted publisher on the crate's settings page, then every release after that is tokenless. RubyGems has the same ordering constraint; PyPI is the exception. Budget for it when standing up a new Hyper\* repo — it is the one publishing step that cannot be automated from an empty registry. |
| Attest before the irreversible step | Order attestation *before* whatever cannot be undone. A crates.io publish is permanent — yank hides a version, never deletes it, and the number can never be reused — so `cargo package` → attest → `cargo publish` means an attestation failure costs a re-run instead of stranding a published, unsigned artifact. Safe because a `.crate` is byte-identical wherever it is produced from the same commit (the commit sha rides inside `.cargo_vcs_info.json`, so a *different* commit yields different bytes). NuGet is the deliberate exception: nuget.org injects a repository signature into the `.nupkg` during validation, so the published bytes genuinely differ from the packed ones and it needs two attestations, one either side. |
| Doc gates need a CI invocation | A compiler-enforced doc gate only enforces where CI actually runs it. C# (`CS1591` + `TreatWarningsAsErrors`) and Rust (`#![deny(missing_docs)]`) fire on every PR because `dotnet test` and `cargo build` compile there. Java's `javadoc -Xwerror` fires only when the `javadoc` task runs — which for a long time was the Maven publish and nothing else, so undocumented members sailed through every PR and failed the *release*. Run `javadoc` alongside the Java suite, not as a separate lint job: like C#, it is enforced by compilation, so it belongs on the leg that already compiles. |
| Allocation-proof tests | One `#[test]` fn per counting-allocator binary — `cargo test` spawns a thread per test fn, and thread creation itself allocates. Entropy sources can take a rare re-init slow path; treat a single one-off allocation flake as suspect-the-environment before suspect-the-code. |

## License

[MIT](LICENSE)
