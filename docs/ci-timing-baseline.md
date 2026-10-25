# CI producer scheduling baseline

These are successful **release-mode CI** runs from October 21, 2026, before the
producer extraction. They are not measurements of the redesigned workflows.

The change exposes `hyper-build-static.yml` and `hyper-build-android.yml` as
independent producer jobs in the caller. Apple tests need only the static producer;
Android tests need both producers. Swift static checks run separately through
`hyper-test-swift-static.yml`. The native entry point no longer calls any of the three:
every caller ran them as its own jobs, so the nested copies (and the native inputs that
fed only them: `apple_mobile`, `android`, `swift_floor_version` and the three switches
that turned them off) were removed. Native's `static_libs` still controls each leg's
fresh per-leg static build.

The ext-php-rs extension build, staging, PHP ABI load probe, upload and attestation
run on the original three unix RIDs (`linux-x64`, `linux-arm64`, `osx-arm64`) in
`hyper-build-php-extension.yml`'s jobs, no longer inside the native matrix. The Intel macOS
job was already core-only. The PHP FFI suites and musl floor tests keep their original
conditions. The separate extension jobs add a macOS runner and repeat PHP setup; their
effect is not included in the timing model below, which predates them.

A reusable workflow that calls a Forge composite action checks out the consumer at the
workspace root and the Forge under `.hyperforge` at `job.workflow_sha`, then calls the
action from that checkout; a consumer's `release.yml` checks the Forge out at the commit
`find-ci-run` resolved (`forge_sha`) instead. An absent or mutable
revision fails before checkout. GitHub documents `job.workflow_repository` and
`job.workflow_sha` specifically for reusable workflows checking out their own
source; they are unavailable on GitHub Enterprise Server. See the
[job context documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).

## Recorded runs

[HyperTabular run 39546209581](https://github.com/SkunkWerkx/HyperTabular/actions/runs/39546209581)
used source `eaad9ed0904397375f8f84a03bae8aeaf1943d2f`, starting at
04:50:16 UTC. Times below are elapsed minutes and seconds from run start.

| Job | Artifacts ready | Observed start | Observed wait | Runtime | Observed finish | Modeled finish |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test-apple-mobile | 2:22 | 21:09 | 18:47 | 19:33 | 40:42 | 21:55 |
| test-android | 3:38 | 21:09 | 17:31 | 6:34 | 27:43 | 10:12 |

[HyperCast run 39534507493](https://github.com/SkunkWerkx/HyperCast/actions/runs/39534507493)
used source `17781f491f60c5eedd40ceeb92033545f9af3e73`, starting at
02:42:31 UTC.

| Job | Artifacts ready | Observed start | Observed wait | Runtime | Observed finish | Modeled finish |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test-apple-mobile | 1:58 | 15:38 | 13:40 | 14:47 | 30:25 | 16:45 |
| test-android | 2:32 | 15:37 | 13:05 | 6:37 | 22:14 | 9:09 |

Observed wait includes both dependency waiting and runner scheduling. Modeled
finish is artifact readiness plus the measured test runtime, assuming immediate
runner availability. It does not predict whole-board completion or a live speedup.
An actual after-run must compare queue delay, total runner time, and wall clock as
well as artifact readiness, particularly under the macOS concurrency limit.

## Reproduce

```bash
gh run view 39546209581 --repo SkunkWerkx/HyperTabular \
  --json jobs,startedAt,conclusion > /tmp/tabular-ci.json
python3 .github/scripts/ci_timing.py /tmp/tabular-ci.json
gh run view 39534507493 --repo SkunkWerkx/HyperCast \
  --json jobs,startedAt,conclusion > /tmp/cast-ci.json
python3 .github/scripts/ci_timing.py /tmp/cast-ci.json
python3 .github/scripts/test_forge_producers.py -v
```

actionlint 1.7.12, the version `validate.yml` pins, predates the documented
`job.workflow_*` properties; the runner labels it does not know are declared in
`.github/actionlint.yaml`. This is `validate.yml`'s invocation, which ignores only those
two property diagnostics:

```bash
actionlint -shellcheck= \
  -ignore 'property "workflow_(sha|repository)" is not defined' \
  .github/workflows/hyper-build-native.yml \
  .github/workflows/hyper-build-static.yml \
  .github/workflows/hyper-build-android.yml \
  .github/workflows/hyper-test-swift-static.yml \
  .github/workflows/hyper-build-php-extension.yml \
  .github/workflows/hyper-build-wasm.yml \
  .github/workflows/hyper-build-wheels.yml
```

This is local syntax and contract validation. Hosted compiler, PHP ABI,
attestation, simulator, and emulator checks still require the actual CI run.
