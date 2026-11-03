# Fixtures for test_companion_wire.py

HyperTabular's own manifests, copied unchanged from its `hyper_conversion` branch at 9c95a38
(2026-11-01), one directory per ecosystem laid out as in the checkout:

- `rust/rust/Cargo.toml`, `rust/rust/Cargo.lock`: the core's manifest and lock. The lock
  resolves `hypercast` 1.0.0 from crates.io, the copy a Cargo patch has to displace.
- `rust/typescript/Cargo.toml`, `rust/typescript/Cargo.lock`: the napi addon's workspace. It
  requires `hypercast` 1.1.0, the release its code needs, while the pin is HyperCast's master at
  1.0.0: the wiring rewrites the requirement to the pin's exact version (`=1.0.0`), in the
  job's tree only.
- `python/python/pyproject.toml`: the Python binding's manifest. It requires
  `hypercast>=1.1.0,<2`, which PyPI doesn't have; the wiring rewrites that requirement to the
  pin's exact version (`hypercast==1.0.0`), which the pinned wheel it installs meets.
- `go/go/go.mod`, `go/go/go.sum`: the Go binding's module. It requires
  `github.com/SkunkWerkx/HyperCast/go` v1.1.0, which the module proxy doesn't have, so only a
  `replace` makes it build.

- `npm/typescript/package.json`, `npm/typescript/package-lock.json`: the napi package. It takes
  `@skunkwerkx/hypercast` 1.1.0 as a devDependency and `^1.1.0` as a peer, and its committed lock
  has no entry for it; npm has only the owner's 0.0.1 placeholder. The wiring points the
  devDependency at the tarball it packs (`file:`) and the peer at the pin's exact version.
- `npm/typescript/browser-smoke/package.json`, `.../package-lock.json`: the browser page, which
  takes `@skunkwerkx/hypercast-wasm32-wasip1` 1.1.0.

- `ruby/ruby/Gemfile`, `ruby/ruby/hypertabular.gemspec`: the gem. The gemspec requires
  `hypercast` `~> 1.0` (RubyGems has 1.0.0, so an unwired leg installs that copy) and the
  Gemfile takes the gemspec (`gemspec`). The wiring rewrites the requirement to `= 1.0.0` and
  appends `gem "hypercast", path: "<the pin>"`.
- `ruby/ruby/wasm-smoke/Gemfile`: the ruby.wasm smoke's, which names no gem literally and takes
  the consumer's own wasm gem from `WASM_GEM_DIR`. Every leg but build-ruby-wasm leaves it
  alone; there, with `ruby-wasm-<abi>` staged, the wiring appends
  `gem "hypercast-wasm", path: "<root>/.companions/hypercast-wasm/hypercast-wasm-<version>"`,
  where suite-ruby-wasm.sh unpacks the gem the pin packs.
- `php/composer.json`, `php/php/composer.json`: the root manifest, requiring `ext-hypercast`
  `^1.1`, and the suite's, which requires no extension. The PHP wiring changes neither: it names
  the pinned extension in `HYPERCAST_PHP_EXTENSION` and tells Composer to ignore `ext-hypercast`.
- `java/java/build.gradle.kts`, `java/java/settings.gradle.kts`: the Java build. It takes
  `io.github.skunkwerkx:hypercast:1.1.0` from Maven Central, which has only 1.0.0. The wiring
  changes neither: its init script serves the pin from the job-local repository and forces its
  version.
- `csharp/csharp/Directory.Build.props`, `csharp/csharp/HyperTabular/HyperTabular.csproj`: the C#
  build. The csproj takes `HyperCast` 1.1.0 by a literal PackageReference (nuget.org has 1.0.0);
  the wiring rewrites that one attribute to `[1.0.0]`.
- `swift/Package.swift`, `swift/swift/Package.swift`, `swift/swift/StaticSmokeTest/Package.swift`:
  the root manifest and the binding's, each requiring
  `.package(url: "https://github.com/SkunkWerkx/HyperCast", from: "1.1.0")` (no release has a
  1.1.0, so an unwired build cannot resolve), and the static smoke test's, which reaches HyperCast
  through the root manifest and names it only in a comment. The wiring rewrites each requirement
  to `.package(path: "<relative path to .companions/hypercast>")`.
- `c/rust/Cargo.toml`, `c/c/smoke/hypercast.sh`: the version c/smoke/hypercast.sh asks for comes
  from the `hypercast` requirement in rust/Cargo.toml (`1.0.0`); without `HYPERCAST_PACKAGES` the
  script downloads that version's GitHub Release archive. The wiring pins the requirement to
  `=<pin version>` and points `HYPERCAST_PACKAGES` at the unpacked pin archives.

- `r/r/DESCRIPTION`, `r/rust/Cargo.toml`: the R package and the core's manifest. The DESCRIPTION
  imports `hypercast (>= 1.1.0)`, which no repository has (CRAN has no hypercast at all); the
  R package's `tools/vendor.sh` vendors the `hypercast` crate rust/Cargo.toml requires, from
  crates.io unless `HYPERCAST_PATH` names a source. The wiring relaxes the import to
  `(>= <pin version>)`, pins the crate to `=<pin version>`, installs the pin's R package into
  the job's library and exports `HYPERCAST_PATH=<the pin>/rust`.

`npm-pinned/` is HyperCast's side at 14df722, which the npm wiring packs:
`typescript/package.json` (its `files` whitelist, which holds no addon, and `napi.targets`) and
`.github/hyper.json`. `r-pinned/` holds its `r/DESCRIPTION`, which the wiring stamps with
`Hyper-Pin: <commit>` before installing. `java-pinned/` holds its Gradle build's `build.gradle.kts` (the `group`)
and `settings.gradle.kts` (the root project's name, the artifact id), and `csharp-pinned/` its
package project, `csharp/HyperCast/HyperCast.csproj` (the `PackageId`), and `.github/hyper.json`
(the project name the static-libs tree is laid out by).

The tests copy a fixture into a temporary checkout and wire it there. Refresh a file by copying
it again from HyperTabular when its shape changes.
