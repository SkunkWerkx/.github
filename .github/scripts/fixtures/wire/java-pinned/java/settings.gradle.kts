rootProject.name = "hypercast"

// A local-dev-only GraalVM Native Image smoke test, mirroring csharp/HyperCast.AotSmokeTest —
// proves Cast's FFM downcalls survive ahead-of-time compilation to a real native binary,
// no JVM required to run it. Full AOT is a HyperCast non-negotiable (docs/roadmap.md).
include(":aot-smoke-test")

// JMH benchmarks, mirroring rust/benches and csharp/HyperCast.Benchmarks — run by hand
// with `./gradlew :benchmarks:jmh`.
include(":benchmarks")
