# Fixtures for test_companion_gate.py

Recorded live on 2026-11-01 with the gate's own probes, real HTTP against each registry:

```sh
python3 companion_gate.py --record --crate hyperuuid --version 1.1.0 --repo SkunkWerkx/HyperUuid > fixtures/gate/hyperuuid-1.1.0.json
python3 companion_gate.py --record --crate hypercast --version 1.1.0 > fixtures/gate/hypercast-1.1.0.json
```

`--record` keeps, for each probe URL, the HTTP status and a 200's body trimmed to the fields the
probe reads (`Probe.trim` in `companion_gate.py`); a 404 keeps an empty body. The HyperCast
fixture was recorded without `--repo`, so it also holds the repository lookup
(`api.github.com/repos/SkunkWerkx/hypercast`, which gives the casing the Go proxy needs).

- `hyperuuid-1.1.0.json`: HyperUuid 1.1.0, live on every registry. Its GitHub Release lacks
  only the R tarball, `hyperuuid_1.1.0.tar.gz`: 1.1.0 was released before `finalize-release`
  attached `cran/<name>_X.Y.Z.tar.gz`. The test asserts exactly that one item, then adds the
  asset to the parsed response in code and asserts the gate passes.
- `hypercast-1.1.0.json`: HyperCast 1.1.0, released nowhere yet. crates.io, NuGet, Maven
  Central, RubyGems, npm (the 0.0.1 placeholders) and Packagist answer with older versions;
  PyPI, the Go proxy and the GitHub Release answer 404.

No CRAN URL is recorded: the gate never queries CRAN, and the tests' fetcher fails on one.
Re-record with the same commands when a probe's URL or the fields it reads change.
