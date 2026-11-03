# Fixtures for test_companion_origin.py

The output of the origin probes, recorded on 2026-11-01 (macOS, cargo 1.99.1, go 1.27.3)
against scratch copies of HyperTabular 9c95a38 with a pin of HyperCast 14df722 (1.0.0) laid
out as `.companions/hypercast/`. `@ROOT@` stands for the consumer checkout and `@HOME@` for
the home directory; the tests substitute their own.

Rust, `cargo tree -i hypercast --locked --all-features --depth 0 --prefix none` in `rust/`:

- `pinned.stdout`: with the patch in `$CARGO_HOME/config.toml` and `cargo update -p hypercast`
  run: the path crate.
- `registry.stdout`: with no patch: the crates.io copy, which prints no source.
- `unused.stdout`, `unused.stderr`: with the patch, and the requirement changed to `=0.9.0`,
  which the path crate's 1.0.0 doesn't meet: Cargo's "patch ... was not used" warning, and the
  registry's 0.9.0.
- `unused-color.stderr`: the same, with `CARGO_TERM_COLOR=always` (what `dtolnay/rust-toolchain`
  exports) and no `--color never`: the ANSI-coloured form.
- `unused-old.stderr`, `unused-old-color.stderr`: the same with cargo 1.85.1 (1.90 prints the
  same), uncoloured and coloured: "warning: Patch ... was not used in the crate graph.", capital
  P and a trailing period. `--color never` overrides `CARGO_TERM_COLOR=always` on 1.85, 1.90
  and 1.99.1 alike.
- `stale.stderr`: with the patch but without `cargo update`: `--locked` refuses the lock (exit
  101).

Go, `go list -m -json github.com/SkunkWerkx/HyperCast/go` in `go/`:

- `pinned.json`: after `go mod edit -replace github.com/SkunkWerkx/HyperCast/go=<abs>`.
- `registry.json`: with the requirement moved to v1.0.0, which the proxy has: no `Replace`.
- `missing.stderr`: as HyperTabular commits it, requiring v1.1.0, which the proxy lacks
  (exit 1).

Python, `<python> -m pip inspect` (pip 26.2.2; each entry's `metadata.description` dropped,
and the venvs, recorded under scratch, written as `@HOME@/venvs`), the wheels and sdist built
with maturin 1.15.0 from HyperCast 14df722 and staged as
`.companions/hypercast-artifacts/pypi-wheels-{osx-arm64,sdist}/`:

- `pinned.json`: CPython 3.14.10 after `pip install --no-deps --force-reinstall <the
  cp311-abi3 wheel>`: PEP 610's `direct_url.json`, a `file://` URL and `archive_info.hashes`.
- `sdist.json`: free-threaded CPython 3.14.10t after the same with the sdist, which pip built:
  the same shape, naming the sdist.
- `registry.json`: CPython 3.14.10 after `pip install hypercast==1.0.0` from PyPI: no
  `direct_url`.
- `report-abi3.json`, `report-abi3t.json`: `pip install --dry-run --ignore-installed --no-deps
  --no-index --only-binary=:all: --find-links <the wheels> --report - hypercast==1.0.0` on
  CPython 3.14.10 and on free-threaded 3.15.2t: pip's own choice among the pinned wheels.
- `nomatch.stderr`: the same on free-threaded 3.14.10t, which no pinned wheel fits (exit 1).

npm, the `node_modules/@skunkwerkx/hypercast` entry of HyperTabular's `typescript/package-lock.json`
(npm 11.20.0, Node 26.13.0):

- `pinned.json`: after the wiring packed HyperCast 14df722 with its osx-arm64 addon and pointed
  the devDependency at that tarball: `resolved` is `file:` relative to the lock, `integrity` the
  tarball's.
- `registry.json`: after a lock-only install of `@skunkwerkx/hypercast` from the registry, which
  holds only the owner's 0.0.1 placeholder: an `https://registry.npmjs.org` URL.

Ruby, `bundle exec ruby companion_ruby.rb hypercast` (the facts, one `key=value` per line) and
`bundle info hypercast --path` in `ruby/` (the Bundler each of Ruby 4.0.9 and 3.3.13 ships), with the
Magnus extensions and the Fiddle library built from HyperCast 14df722 (`ruby-ext`, `cargo
cdylib`) and placed in the pin as the wiring places them:

- `pinned-4.0.facts`: Ruby 4.0.9 after the wiring: the pin's `lib/hypercast.rb` on the native
  backend, its `4.0/hypercast_native.bundle`, with that file's sha256. No Fiddle load, so no
  library and no mappings.
- `pinned-3.3.facts`: Ruby 3.3.13, for which no extension is staged: the Fiddle backend, its
  load forced, on the pin's `native/osx-arm64/libhypercast.dylib` (what the gem's own
  `Runtime.library_path` answers), which dyld's image list (`observed=1`) shows as the one
  `libhypercast.dylib` mapped.
- `registry.facts`: Ruby 4.0.9 with no wiring: `bundle install` took HyperCast 1.0.0's
  arm64-darwin platform gem from RubyGems (the bundle path written as `@HOME@/vendor/bundle`).
- `pinned.path`, `registry.path`: `bundle info hypercast --path` in the same two checkouts.

PHP, `php [-d extension=<file>] -d display_startup_errors=1 -d display_errors=stderr
companion_php.php hypercast` (PHP 8.5.12, macOS; the extension built from HyperCast 14df722 with
`cargo php-ext`):

- `bare.json`: no flag, nothing in the ini: not loaded.
- `pinned.json`: `-d extension=` the pinned build: loaded, version 1.0.0 (macOS has no
  /proc/self/maps, so `mapped` is empty).
- `ini.json`, `ini.stderr`: the same flag, with a PHP_INI_SCAN_DIR file already loading the
  extension (what PIE's install does): loaded, and PHP's startup warning that the module is
  already loaded, on stderr.

Java, `./gradlew -q --console=plain companionOrigin` in `java/` (Gradle 9.8.2, Temurin 25.0.5),
with the init script and companions.properties companion_wire.py writes, HyperTabular at 9c95a38
and the pin (HyperCast 14df722) published by `publishToMavenLocal` into `.companions/m2/` with
the osx-arm64 natives (`@SHA256@` stands for that jar's SHA-256, `@HOME@/.gradle` for
GRADLE_USER_HOME). The second column is the project whose classpath selects the module, or the
Test or JavaExec task that hands it to a JVM:

- `pinned.stdout`: every copy the suites load (`:`, `:test`, `:testFloor`, `:aot-smoke-test`,
  its `:test`, `:run` and `:runFloor`, `:benchmarks` and its `:test`), each the job-local jar.
- `files.stdout`, `files.stderr`: `dependencies { testRuntimeOnly(files("<Central's jar>")) }`
  added to the build (the jar at `@HOME@/central/`): no module classpath selects it, but
  `:test` and `:testFloor` load it beside the pin's jar, and the task fails (exit 1).
- `registry.stdout`, `registry.stderr`: the init script without its repository, in a minimal
  consumer on JDK 25 (Maven Central's 1.0.0 targets JDK 25 only, so HyperTabular's floor of 21
  cannot select it at all): Gradle's module cache's copy of Central's jar, and the task's
  failure (exit 1).
- `swapped.stdout`, `swapped.stderr`: the job-local jar replaced by Central's bytes after the
  publish: the pin's path with another SHA-256, and the failure (the root project's task fails
  first, so only its three lines).
- `unwired.stderr`: no init script: Gradle has no `companionOrigin` task (exit 1).

C#, after `dotnet restore` (SDK 11.0.100-rc.3) of HyperTabular's test project:

- `pinned.nupkg.metadata`: the global packages folder's `hypercast/1.0.0/.nupkg.metadata` with
  the wiring's nuget.config: `source` is the job-local feed, `contentHash` the packed nupkg's
  (`@CONTENT_HASH@`).
- `registry.nupkg.metadata`: the same after a restore from nuget.org alone: its `source` and
  nuget.org's 1.0.0 contentHash (that package is net11.0 only, so HyperTabular's net10.0 target
  fails NU1202, but the package is extracted all the same).
- `project.assets.json`: `obj/project.assets.json` of the pinned restore, trimmed to the
  `HyperCast/1.0.0` entries; `@NUGET_PACKAGES@` is the global packages folder.

Swift (6.4, Swift Build unless named), HyperTabular at 9c95a38 with HyperCast 14df722's core
(`cargo cdylib`, `local-core.sh`) as the pin; `@ROOT@` is the consumer's checkout:

- `pinned.json`: `swift package show-dependencies --format json` in swift/ after the wiring: the
  companion's `path` is `@ROOT@/.companions/hypercast`.
- `remote.json`: the same with the requirement left remote at `exact: "1.0.0"` (the one version
  that resolves): the GitHub URL, version 1.0.0 and its `.build/checkouts/HyperCast` path.
- `pinned-build.log`: `swift build -v --build-tests` in swift/, wired: the link line names the
  pin's `swift/HyperCastCore.artifactbundle/.../libhypercast.a` by path.
- `remote-build.log`: the same build remote at 1.0.0: it links the checkout's archive.
- `native-build.log`: the wired build with `--build-system native`: SwiftPM copies the pin's
  archive into `.build/arm64-apple-macosx/debug/` and links it as `-lhypercast` there.
- `xcodebuild-pinned.log`, `xcodebuild-remote.log`: **constructed, not recorded** (no xcodebuild
  runs here), in Xcode's log format: the pin's XCFramework processed into the products directory
  (`@PRODUCTS@`) and that copy linked by `-lhypercast` through `-L`, and the same with the
  XCFramework SwiftPM downloads into `SourcePackages/artifacts`. The Apple mobile legs' first CI
  run (companion-pins Task 19) replaces them with recorded logs.

R, `Rscript companion_r.R hypercast` (R 4.6.2, pak 0.11.1, macOS), with `R_LIBS` naming one
scratch library first (`@SCRATCH@` stands for the scratch root):

- `pinned.stdout`: after the wiring installed HyperCast 14df722's R package, its DESCRIPTION
  stamped: `Hyper-Pin` names the commit, and there is no `Repository` field.
- `url.stdout`: after pak's `url::` install of the pin's R tarball (`R CMD build` of the same
  source, served locally the way a GitHub Release serves it): `RemoteType url`, no `Hyper-Pin`.
- `cran.stdout`: the probe run for `jsonlite`, installed from CRAN in the site library, since CRAN
  has no hypercast: the `Repository: CRAN` a CRAN copy carries.
- `missing.stdout`: an empty library and no hypercast anywhere: the load error (exit 3).

C: `c/hypercastConfig.cmake` and `c/hypercastConfigVersion.cmake.in` are HyperCast 14df722's
`c/cmake/` files, which c_package.py places in every archive (the tests fill the version file as
it does). The CMake probe runs the host's real cmake against them.
