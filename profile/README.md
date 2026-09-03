# SkunkWerkx

**Native-core libraries, written once in Rust, called directly — not wrapped, not shimmed — from C#, Java, Go, Swift, Ruby, PHP, and Python. Every performance claim is a measured receipt, and the losses print next to the wins.**

## The Hyper* series

Three rounds, each built strictly on the one below it. The first two are shipped; the third is where the work is now.

| Round | Project | What it is | The receipt |
| --- | --- | --- | --- |
| Identity | [**HyperUuid**](https://github.com/SkunkWerkx/HyperUuid) | One RFC 9562 UUID engine (v4/v5/v6/v7, batch generation, SQL Server byte ordering) reached from inside every host language's own process | Faster than the platform's own UUID call in every roster language except Go — 5.7x `Guid.NewGuid()`, 2.8x `SecureRandom.uuid`, 4.2x CPython 3.14's own `uuid.uuid7()` |
| Trust | [**HyperCast**](https://github.com/SkunkWerkx/HyperCast) | Allocation-free parsers that turn untrusted text into strongly typed values — booleans, numerics, UUIDs, temporals, Excel serials. Every parse returns a Verdict: the value, or a closed reason code plus the offending byte span. Never throws, never allocates, never guesses a culture | Beats its own platform's culture-machinery parser in every roster language except Go — RFC 3339 timestamps 4.0x faster than `DateTimeOffset.TryParse`, 11.4x `Instant.parse`, 14.9x Swift's `ISO8601FormatStyle`, 4.3x `Time.iso8601` |
| Ingestion | [**HyperTabular**](https://github.com/SkunkWerkx/HyperTabular) · [**HyperDelimited**](https://github.com/SkunkWerkx/HyperDelimited) · [**HyperWorkbook**](https://github.com/SkunkWerkx/HyperWorkbook) | The payoff: CSV/TSV/delimited text and XLSX/ODS workbooks read forward-only, every cell cast through a HyperCast door, the FFI boundary crossed once per chunk instead of once per cell | In progress — the Rust crates exist and their suites are green against HyperCast's master; the real-writer corpus, the bindings, and the numbers are what remain |
| Foundry | [**HyperForge**](https://github.com/SkunkWerkx/.github) | The shared pipeline behind all of them: six real-hardware CI legs, reusable pack/publish/attest workflows, the layout conventions a new Hyper* repo adopts wholesale, and the build archaeology — learned once, banked, never re-learned | Every binding's real test suite, run on every leg, against that leg's freshly built native library |

Every library shares one architecture: a single Rust `cdylib` with zero runtime dependencies and a plain C ABI, compiled fresh on six legs (`linux`/`osx`/`windows` × `x64`/`arm64`), and reached from each language over its own direct door — `P/Invoke`, FFM, `cgo`/`purego`, `Fiddle`, PHP's `FFI` — or linked straight into the VM as a native extension (PyO3 for CPython, Magnus for CRuby). No runtime bridge, no serialization layer, no embedded interpreter. One implementation, one set of test vectors, on every platform.

## Where it stands

**Shipped.** HyperUuid and HyperCast are published to every registry their languages have — crates.io, NuGet, Maven Central, PyPI, RubyGems, Packagist — and Go and Swift resolve from the git tag, which is their real publish story. Each was then installed from its real registry into a clean project and run, because "the publish succeeded" and "a consumer can use it" are different claims, and the gap between them is where most of the shipped bugs were found. Every artifact — the package where a registry has one, the native binary underneath it either way — carries a GitHub build-provenance attestation, and the release pipelines publish tokenless wherever a registry offers Trusted Publishing.

**Proven, not configured.** C# publishes under `PublishAot` on all six platforms and the produced binary runs every door; Java survives a real GraalVM Native Image build with the FFM and resource metadata shipped inside the jar, so a consumer inherits it with no configuration. Both cores pass their full suites under `wasm32-wasip1`, and a Blazor WebAssembly app that only adds a `PackageReference` links HyperUuid or HyperCast straight into the browser build.

**In progress: the ingestion round.** The thesis is amortization. A million-row, 20-column file is 20 million scalar casts; driven cell by cell from the host that is real crossing overhead on the cheapest FFI mechanism and catastrophic from Python or Ruby. Driven as "here is a buffer and a column schema, fill these typed column buffers and parallel verdict arrays," the crossing tax rounds to zero while the doors run in a tight native loop. HyperUuid already measured this exact effect on its batch API: 19.6x, from collapsing thousands of crossings into one. The three ingestion repos are that thesis built out:

- **HyperTabular** is the contract every format provider speaks — a format-neutral cell, a caller-declared plan of doors, the cast engine, the column-major batch, and the `#[repr(C)]` shapes every binding will share. Culture stays out of the core exactly as in HyperCast: nothing is sniffed, no type inference, no separator detection.
- **HyperDelimited** is CSV/TSV/any single-byte separator, with a SIMD structural scanner (carry-less multiply for quote parity on both aarch64 and x86-64), delivering cells zero-copy.
- **HyperWorkbook** is XLSX and ODS: a hand-rolled zip reader, streaming inflate, one pull tokenizer for every XML part, styles and shared strings, and Excel's serial dates under the caller-declared epoch — the `1900-02-29` that never existed reported as the fault it is.

What comes next, in order: a conformance corpus of files real applications wrote (Excel, LibreOffice, Google Sheets, Numbers), the seven bindings on the batch surface, and then the numbers. The tabular layer is server domain — AOT-clean like everything else, wasm deliberately out of scope there.

## How the numbers are earned

The finding every project shares isn't a trick; it's a measurement. Each language's boundary cost was priced, then either dieted or replaced:

- **Direct FFI where the crossing floor is already nanoseconds** — C# and Java cross in single digits; PHP's `ext-ffi` in ~105 ns. At those floors any remaining slowness is *wrapper*, and it gets dieted — static scratch, zero-copy inputs, flat doors, nothing built to be thrown away — not excused. The second pass of that diet is what took HyperCast's Java UUID door from a loss against `UUID.fromString` to a win, and Swift from three mallocs per call to none.
- **A native extension where the mechanism itself was the bill** — CPython's `ctypes` priced every call at ~1 µs, Ruby's `Fiddle` at ~1.6 µs. No diet fixes that, so those bindings link the Rust core directly into the VM. Python is PyO3 only: one `abi3` wheel per platform covers every supported CPython, so the `ctypes` fallback was retired in both projects. Ruby ships precompiled Magnus gems for every leg, Windows on ARM included, and keeps `Fiddle` as the zero-compile fallback for anywhere else — both backends green on the same suite, pinned to agree by a cross-backend test.
- **Go is the control group.** `runtime.cgocall` costs ~100 ns of structural scheduler bookkeeping, and Go's stdlib on the far side is genuinely excellent. Go loses per call, and the loss is printed as one — it's what makes the rest of the scoreboard credible. The by-value shims removed every allocation from its doors; the crossing remains, and the batch layer is what divides it by N.

**Non-negotiables, every round:** allocation-free cores asserted by a counting `#[global_allocator]`, not a doc comment. A shared conformance corpus every binding replays byte for byte. Full AOT in .NET and Java, proven by smoke tests that produce real native binaries. Every binding's actual test suite on all six platforms in CI. Provenance on every artifact. No number enters a README from a rushed run, and re-runs get published even when they go the wrong way.

## Why "Hyper"

The series owes its founding attitude to Casey Muratori's talks on what "premature optimization" actually meant. Knuth's line gets quoted as a license to never care; Muratori's point is that most slow software was never *optimized badly* — it was **pessimized by default**: allocations nobody needed, layers nobody asked for, work done and thrown away on every call. These libraries are that argument, practiced — and the other half of taking performance seriously is refusing to assert it without a receipt.

## Lineage

The Hyper* cores descend from [SequentialGuid](https://github.com/buvinghausen/SequentialGuid) and [Svartalfheim](https://github.com/NorseArchitecture/Svartalfheim) — the C# libraries whose SQL Server byte-order permutation seeded HyperUuid's ordering transforms, whose `Norse.Primitives` conformance suites seeded HyperCast's corpus (380 vectors across twelve files today), and whose `Primitives.Ingestion` readers are the origin blueprint for the tabular layer's surface.

Everything here is [MIT](https://github.com/SkunkWerkx/.github/blob/master/LICENSE). Pull requests and issues are welcome; a PR should stay green on the six-leg matrix before merging.
