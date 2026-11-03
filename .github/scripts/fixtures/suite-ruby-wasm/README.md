# suite-ruby-wasm.sh stubs

`test_companion_workflows.SuiteRubyWasmTests` runs `actions/ruby-wasm-suite/suite-ruby-wasm.sh`'s
gem stage over a wired consumer with these first on `PATH`, so the pinned hypercast-wasm's
packing, unpacking and proof run for real while nothing builds an interpreter:

- `bundle`: `install` does nothing; `exec rake wasm:gem[<abi>]` packs `lib/` and the ABI's
  archive, gzipped with mtime 0 as the Hyper* Rakefiles do, into `pkg/<name>-wasm-1.0.0.gem` (a
  tar); `exec rbwasm build ... -o <file>` writes the file; `info <gem> --path` answers the path
  the `BUNDLE_GEMFILE` gives the gem, else RubyGems' copy under `$HOME`, which is what Bundler
  does with and without the wiring's line. Every call is logged to `$STUB_LOG`.
- `gem`: `unpack` extracts that tar into `<target>/<gem's name>/`; `spec --ruby` prints a
  gemspec.
- `brew`: `--prefix openssl@3` for the script's macOS branch, answering `$STUB_OPENSSL`.

The real build, rbwasm included, runs in hyper-build-wasm.yml's build-ruby-wasm on every
release-mode board of a consumer with a `companions` block.
