# SkunkWerkx

**Native-core libraries, written once in Rust, called directly — not wrapped, not shimmed — from C#, Java, Go, Swift, Ruby, PHP, and Python. Every performance claim is a measured receipt, and the losses print next to the wins.**

## The Hyper* series

| Project | What it is | The one-line receipt |
| --- | --- | --- |
| [**HyperUuid**](https://github.com/SkunkWerkx/HyperUuid) | One RFC 9562 UUID engine (v4/v5/v6/v7, batch generation, SQL Server byte ordering) reached from inside every host language's own process | Faster than the platform's own UUID call in every roster language except Go — 5.7x `Guid.NewGuid()`, 2.8x `SecureRandom.uuid`, 4.2x CPython 3.14's own `uuid.uuid7()` |
| [**HyperCast**](https://github.com/SkunkWerkx/HyperCast) | Allocation-free parsers for scalars from untrusted text — booleans, numerics, UUIDs, temporals. Every parse returns a Verdict (the value, or a closed reason code plus the offending byte span); never throws, never allocates | Beats its own platform's culture-machinery parser in every roster language except Go — RFC 3339 timestamps 4.0x faster than `DateTimeOffset.TryParse`, 4.3x `Time.iso8601`, 2.7x `DateTimeImmutable` |
| [**HyperForge**](https://github.com/SkunkWerkx/.github) | The shared foundry behind both: the reusable 6-platform CI pipeline, the layout conventions a new Hyper* repo adopts wholesale, and the build archaeology — learned once, banked, never re-learned | Every binding's real test suite, run on every leg, against that leg's freshly built native library |

Both libraries share one architecture: a single Rust `cdylib` with zero runtime dependencies and a plain C ABI, compiled fresh on six real-hardware legs (`linux`/`osx`/`windows` × `x64`/`arm64`), and reached from each language over its own direct door — `P/Invoke`, FFM, `cgo`/`purego`, `Fiddle`, PHP's `FFI`, `ctypes` — or linked straight into the VM as a native extension (PyO3 for CPython, Magnus for CRuby). No runtime bridge, no serialization layer, no embedded interpreter. One implementation, one set of test vectors, on every platform.

## How the numbers are earned

The finding both projects share isn't a trick; it's a measurement. Each language's boundary cost was priced, then either dieted or replaced:

- **Direct FFI where the crossing floor is already nanoseconds** — C# and Java cross in single digits; PHP's `ext-ffi` in ~105 ns. At those floors any remaining slowness is *wrapper*, which gets dieted (static scratch, zero-copy `const char *`, flat doors), not excused.
- **A native extension where the mechanism itself was the bill** — CPython's `ctypes` prices every call at ~1 µs, Ruby's `Fiddle` at ~1.6 µs. No diet fixes that, so those bindings link the Rust core directly into the VM (PyO3, Magnus), auto-selected when loadable. The zero-compile `ctypes`/`Fiddle` fallbacks stay fully supported — they're the compile-free install and the Pyodide/WASM path — with the same suite green on both backends and cross-backend agreement pinned by tests.
- **Go is the control group.** `runtime.cgocall` costs ~100 ns of structural, un-dietable scheduler bookkeeping, and Go's stdlib on the far side is genuinely excellent. Go loses per call in both projects, and the loss is printed as one — it's what makes the rest of the scoreboard credible. Batch APIs divide the toll by N and put Go's numbers right next to everyone else's.

**Non-negotiables, every round:** allocation-free cores asserted by a counting `#[global_allocator]`, not a doc comment. Full AOT in .NET (`PublishAot`) and Java (GraalVM Native Image), proven by smoke tests that produce real native binaries. WASM ride-along for the core (`wasm32-wasip1` under `wasmtime`). Every binding's actual test suite run on all six platforms in CI. No number enters a README from a rushed run.

## Why "Hyper"

The series owes its founding attitude to Casey Muratori's talks on what "premature optimization" actually meant. Knuth's line gets quoted as a license to never care; Muratori's point is that most slow software was never *optimized badly* — it was **pessimized by default**: allocations nobody needed, layers nobody asked for, work done and thrown away on every call. These libraries are that argument, practiced — and the other half of taking performance seriously is refusing to assert it without a receipt.

## Lineage

The Hyper* cores descend from [SequentialGuid](https://github.com/buvinghausen/SequentialGuid) and [Svartalfheim](https://github.com/NorseArchitecture/Svartalfheim) — the C# libraries whose SQL Server byte-order permutation and `Norse.Primitives` conformance suites seeded HyperUuid's ordering transforms and HyperCast's ~250-vector corpus, respectively.

Everything here is [MIT](https://github.com/SkunkWerkx/.github/blob/master/LICENSE). Pull requests and issues are welcome; a PR should stay green on the 6-leg matrix before merging.
