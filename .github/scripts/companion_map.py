"""Which of a companion's CI artifacts each consumer leg takes, and which ecosystems that leg
overrides: the one place that knows (the companion-pins spec, "The mapping").

A leg is one job, or one matrix entry of it, of a Forge workflow a consumer's board calls
(`hyper-core*`, `hyper-build-*`, `hyper-lint-*`, `hyper-test-*`, and the legacy
`hyper-check-core`), read from the workflows with yaml_lite rather than kept by hand, plus the
few board jobs every Hyper* ci.yml keeps as its own (CONSUMER_LEGS). The artifact names are the
Forge's upload names, which carry no crate: `test_companion_map` holds every name this module
returns against the literal names a full board's upload steps produce (`producers`), so a
renamed artifact fails a Forge test, not a consumer's run.
"""
from itertools import product
import json
from pathlib import Path
import re
from typing import NamedTuple

import php_ext_package
import yaml_lite

GITHUB = Path(__file__).resolve().parents[1]
WORKFLOW_GLOBS = ('hyper-core*.yml', 'hyper-check-core.yml', 'hyper-build-*.yml', 'hyper-lint-*.yml', 'hyper-test-*.yml')
# Workflows no companion's ci.yml calls since the producer extraction (docs/ci-timing-baseline.md;
# HyperTabular, a consumer, still calls them until it moves): their legs are still mapped, in case
# one is called again, but an artifact one of them uploads never reaches a companion's run, so it
# proves nothing about a name.
LEGACY_WORKFLOWS = ('hyper-build-native.yml', 'hyper-check-core.yml', 'hyper-test-swift-static.yml')
# The workflows a full board's ci.yml calls that can upload what a leg takes: these globs minus
# LEGACY_WORKFLOWS, with the composite actions they use. No pack, publish or release workflow.
PRODUCER_GLOBS = ('hyper-core*.yml', 'hyper-build-*.yml', 'hyper-lint-*.yml', 'hyper-test-*.yml')
# The inputs the companion's (HyperCast's) ci.yml passes that reach an upload name, where they
# differ from the Forge default: its test-ruby job adds the compat ABI, so the board uploads
# ruby-native-<rid>-3.4 beside the default ruby_version's -4.0. Every other name-bearing input is
# left at its default (hyper-build-wasm.yml's artifact_name included). A consumer's input the Forge
# does not know surfaces as a missing artifact on that consumer's board. test_companion_map holds
# this against hyper_config.RUBY_ABIS.
COMPANION_INPUTS = {'hyper-test-ruby.yml': {'ruby_compat_version': '3.4'}}
ECOSYSTEMS = ('rust', 'go', 'python', 'pyodide', 'npm', 'ruby', 'java', 'csharp', 'php', 'swift', 'r', 'c')
# The rid a leg with no `rid` column runs on, from its runner label.
RUNNER_RIDS = {'ubuntu-26.04': 'linux-x64', 'ubuntu-26.04-arm': 'linux-arm64',
               'macos-latest': 'osx-arm64', 'xcode-27': 'osx-arm64'}
# What the wasm build uploads its static archive as: hyper-build-wasm.yml's `artifact_name`
# default, which HyperCast's ci.yml does not override.
WASM_ARCHIVE = 'wasm-emscripten-staticlib'
# What hyper-test-c.yml's pack job uploads on every board, PR and full: the per-RID C archives.
# (The release tail's pack-c action uploads `c-packages`, which a board never makes.)
C_PACKAGES = 'c-packages-ci'


class Leg(NamedTuple):
    workflow: str
    job: str
    rid: str | None
    extra: dict


# The matrix values that choose a leg's artifacts beyond its rid: the Ruby ABI (one of the
# config's ruby_abis; absent, every one), the PHP minor and thread safety (`nts` or `zts`; absent,
# NTS), and hyper-build-native.yml's shard.
LEG_KEYS = ('abi', 'minor', 'ts', 'shard')
_LEG_PART = re.compile(r'[A-Za-z0-9_.-]+')


def parse_leg(text):
    """A leg as a fetch-companions caller names it: `<workflow>:<job>[:<rid>][:key=value...]`,
    the keys from LEG_KEYS, each at most once. A ci.yml job with a fixed rid (CONSUMER_RIDS)
    takes it when none is given."""
    parts = str(text).split(':')
    if len(parts) < 2 or not all(_LEG_PART.fullmatch(p) for p in parts[:2]):
        raise ValueError(f'leg {text!r} is not <workflow>:<job>[:<rid>][:key=value...]')
    workflow, job, rest = parts[0], parts[1], parts[2:]
    rid = None
    if rest and '=' not in rest[0]:
        rid = rest.pop(0)
        if not _LEG_PART.fullmatch(rid):
            raise ValueError(f'leg {text!r}: {rid!r} is not a rid')
    extra = {}
    for part in rest:
        key, sep, value = part.partition('=')
        if not sep or key not in LEG_KEYS or not _LEG_PART.fullmatch(value) or key in extra:
            raise ValueError(f'leg {text!r}: {part!r} is not one of {", ".join(k + "=<value>" for k in LEG_KEYS)}, '
                             'each at most once, after the rid')
        extra[key] = value
    if rid is None and workflow == 'ci.yml':
        rid = CONSUMER_RIDS.get(job)
    return Leg(workflow, job, rid, extra)


def leg_string(leg):
    """The string parse_leg reads back as the same leg (its LEG_KEYS values only)."""
    parts = [leg.workflow, leg.job] + ([leg.rid] if leg.rid else [])
    return ':'.join(parts + [f'{k}={leg.extra[k]}' for k in LEG_KEYS if leg.extra.get(k) not in (None, '')])


# A role is one binding's part of a leg: the ecosystems it overrides and the artifact
# templates it takes. `{rid}` is the leg's rid; `{abi}` every Ruby ABI (the leg's, given) and `{wasi}` the WASI
# platform from the config; `{node_rid}` every napi RID; `{android}` every Android RID
# hyper-build-android.yml builds; `{minor}` the leg's PHP minor and `{ts}` its thread safety, as
# php_ext_package.artifact_name spells it in the upload name (nothing for NTS, `-zts` for ZTS).
ROLES = {
    'rust': (('rust',), ()),
    'go': (('go',), ()),
    # The Go lint regenerates the translated core (TestCoreIsCurrent), building the consumer's
    # crate and the companion's with it: the Cargo patch as well as the replace.
    'go-lint': (('rust', 'go'), ()),
    'python': (('rust', 'python'), ('pypi-wheels-{rid}',)),
    # build-wheels tests each wheel in fresh venvs, some with no wheel of the companion's.
    'python-wheels': (('rust', 'python'), ('pypi-wheels-{rid}', 'pypi-wheels-sdist')),
    # build-sdist installs the sdist on manylinux2014 (x64, older than the wheels' manylinux)
    # and on Alpine x64.
    'python-sdist': (('rust', 'python'), ('pypi-wheels-sdist', 'pypi-wheels-linux-x64', 'pypi-wheels-linux-musl-x64')),
    'python-fallthrough': (('rust', 'python'), ('pypi-wheels-sdist', 'pypi-wheels-{rid}')),
    # The Pyodide wheel's job runs no native interpreter that installs the companion: the
    # Pyodide wiring rewrites the requirement and hands pyodide-suite the pinned wheel.
    'pyodide': (('rust', 'pyodide'), ('pypi-wheels-pyodide',)),
    'npm': (('rust', 'npm'), ('node-{rid}',)),
    'npm-wasi': (('rust', 'npm'), ('node-{wasi}',)),
    'npm-package': (('npm',), ('node-{node_rid}', 'node-{wasi}')),
    'ruby': (('rust', 'ruby'), ('ruby-native-{rid}-{abi}', 'native-{rid}')),
    # The ruby.wasm build links the companion's <name>-wasm gem, which the wiring packs from the
    # pin around the companion's own wasm32-wasip1 archive for the leg's minor (abi=).
    'ruby-wasm': (('rust', 'ruby'), ('ruby-wasm-{abi}',)),
    'ruby-lint': (('ruby',), ()),
    'java': (('rust', 'java'), ('jni-{rid}', 'native-{rid}')),
    'csharp': (('csharp',), ('native-{rid}', 'static-libs')),
    'csharp-blazor': (('csharp',), (WASM_ARCHIVE,)),
    'csharp-android': (('csharp',), ('native-{android}', 'static-libs')),
    'csharp-apple': (('csharp',), ('static-libs',)),
    'php': (('rust', 'php'), ('php-ext-{rid}-{minor}{ts}',)),
    'swift': (('swift',), ('swift-release-assets',)),
    'r': (('r',), ()),
    'c': (('c',), (C_PACKAGES,)),
}

# hyper-build-native.yml's Windows shards: `ffi` and `swift-ruby`; every other leg is `all`.
_FFI = ('rust', 'go', 'java', 'npm', 'r', 'python', 'csharp')
_SHARDS = {'all': _FFI + ('swift', 'ruby'), 'ffi': _FFI, 'swift-ruby': ('rust', 'swift', 'ruby')}

# (workflow, job) -> its roles, or ('shard', {value: roles}) where a matrix column decides.
JOBS = {
    # Each core leg smoke-tests its own C archive, whose C++ programs build against the
    # companion's (c/smoke/<name>.sh): on a PR board, the only C smoke there is.
    ('hyper-core.yml', 'core'): ('rust', 'c'),
    ('hyper-core.yml', 'core-musl'): ('rust', 'c'),
    ('hyper-core.yml', 'core-osx-x64'): ('rust', 'c'),
    ('hyper-core-checks.yml', 'checks'): ('rust',),
    ('hyper-core-proofs.yml', 'proofs'): ('rust',),
    ('hyper-check-core.yml', 'check-no-std'): ('rust',),
    ('hyper-check-core.yml', 'check-no-panic'): ('rust',),
    ('hyper-check-core.yml', 'check-core'): ('rust',),
    ('hyper-check-core.yml', 'check-cdylib'): ('rust',),
    ('hyper-check-core.yml', 'test-wasi'): ('rust',),
    ('hyper-check-core.yml', 'lint-rust'): ('rust',),
    ('hyper-check-core.yml', 'check-msrv'): ('rust',),
    ('hyper-check-core.yml', 'check-semver'): ('rust',),
    ('hyper-check-core.yml', 'test-big-endian'): ('rust',),
    ('hyper-check-core.yml', 'lint-go'): ('go-lint',),
    ('hyper-check-core.yml', 'lint-node'): ('npm',),
    ('hyper-check-core.yml', 'lint-java-jni'): ('rust',),
    ('hyper-check-core.yml', 'lint-r'): ('r',),
    ('hyper-check-core.yml', 'lint-ruby'): ('ruby-lint',),
    ('hyper-build-android.yml', 'build-native-android'): ('rust',),
    ('hyper-build-static.yml', 'build-static-libs'): ('rust',),
    ('hyper-build-native.yml', 'build-native'): ('shard', _SHARDS),
    ('hyper-build-native.yml', 'build-native-osx-x64'): ('rust', 'java', 'npm'),
    ('hyper-build-native.yml', 'build-native-musl'): ('rust', 'java', 'go', 'npm', 'csharp', 'ruby', 'python'),
    ('hyper-build-php-extension.yml', 'build'): ('php',),
    ('hyper-build-wasm.yml', 'build-node-wasi'): ('npm-wasi',),
    ('hyper-build-wasm.yml', 'build-wasm'): ('rust',),
    ('hyper-build-wasm.yml', 'test-blazor'): ('csharp-blazor',),
    ('hyper-build-wasm.yml', 'test-rust-browser'): ('rust',),
    ('hyper-build-wasm.yml', 'test-go-wasm'): ('go',),
    ('hyper-build-wasm.yml', 'build-ruby-wasm'): ('ruby-wasm',),
    ('hyper-build-wheels.yml', 'build-sdist'): ('python-sdist',),
    ('hyper-build-wheels.yml', 'sdist-fallthrough'): ('python-fallthrough',),
    ('hyper-build-wheels.yml', 'build-wheels'): ('python-wheels',),
    ('hyper-build-wheels.yml', 'build-pyodide-wheel'): ('pyodide',),
    ('hyper-test-c.yml', 'smoke'): ('c',),
    ('hyper-test-c.yml', 'smoke-apple-mobile'): ('c',),
    ('hyper-test-csharp.yml', 'test'): ('csharp',),
    ('hyper-test-csharp.yml', 'test-musl'): ('csharp',),
    ('hyper-test-go.yml', 'test'): ('go',),
    ('hyper-test-go.yml', 'test-musl'): ('go',),
    ('hyper-test-go.yml', 'test-big-endian'): ('go',),
    ('hyper-test-java.yml', 'test'): ('java',),
    ('hyper-test-java.yml', 'test-musl'): ('java',),
    ('hyper-test-java.yml', 'test-osx-x64'): ('java',),
    ('hyper-test-python.yml', 'test'): ('python',),
    ('hyper-test-python.yml', 'test-musl'): ('python',),
    ('hyper-test-r.yml', 'test'): ('r',),
    ('hyper-test-ruby.yml', 'test'): ('ruby',),
    ('hyper-test-ruby.yml', 'test-musl'): ('ruby',),
    ('hyper-test-swift.yml', 'test'): ('swift',),
    ('hyper-test-swift-static.yml', 'test-swift-static'): ('swift',),
    ('hyper-test-typescript.yml', 'test'): ('npm',),
    ('hyper-test-typescript.yml', 'test-musl'): ('npm',),
    ('hyper-test-typescript.yml', 'test-osx-x64'): ('npm',),
    # The board jobs each Hyper* ci.yml keeps as its own: the C# Apple and Android legs, the
    # NuGet consumer test and the npm consumer tests (Verdaccio), which the spec's C# and
    # TypeScript rows and its isolated-environments table name.
    # Each artifact list mirrors that job's download-artifact steps in HyperTabular's ci.yml:
    # test-apple-mobile, static-libs (before "What is installed, and what was staged") and
    # c-packages-ci (before "Unpack the four Apple mobile archives"), and the Swift suites on the
    # iOS simulator and Mac Catalyst (xcodebuild), which link the XCFramework, and the Go suite
    # (the translated core, from the pin's source; no artifact);
    ('ci.yml', 'test-apple-mobile'): ('csharp-apple', 'c', 'swift', 'go'),
    # test-android, the native-android-* pattern and static-libs (before "Stage
    # runtimes/android-{rid}/native/"), and the Go and Swift suites cross-built for the emulator:
    # Go from the pin's source, Swift linking the bundle's *-unknown-linux-android variants;
    ('ci.yml', 'test-android'): ('csharp-android', 'go', 'swift'),
    # test-nuget-package, native-linux-x64 and static-libs (before "Stage
    # runtimes/linux-x64/native/");
    ('ci.yml', 'test-nuget-package'): ('csharp',),
    # test-npm-package, the node-* pattern (before "The conformance suite against the WASI
    # module, native refused"): every napi addon and the WASI package.
    ('ci.yml', 'test-npm-package'): ('npm-package',),
}
# The rid a consumer's own job runs on, where its artifacts need one.
CONSUMER_RIDS = {'test-nuget-package': 'linux-x64'}
CONSUMER_LEGS = tuple(Leg(workflow, job, CONSUMER_RIDS.get(job), {}) for workflow, job in JOBS if workflow == 'ci.yml')

# Jobs that never touch the companion, each with why.
NO_COMPANION = {
    ('hyper-build-php-extension.yml', 'version'): 'reads the crate version (cargo metadata --no-deps) and computes the matrix; builds nothing',
    ('hyper-build-native.yml', 'build-php-extension'): "a call of hyper-build-php-extension.yml, whose own legs are mapped there",
    ('hyper-build-wheels.yml', 'sdist-config'): "reads the consumer's hyper.json for the sdist switch; builds nothing",
    ('hyper-check-core.yml', 'lint-python'): 'ruff over python/, which reads the sources alone and installs no dependency',
    ('hyper-check-core.yml', 'lint-php'): "phpcs over php/, after a composer install of php/'s dev tools, which "
                                         'require no ext-<companion> and load no extension',
    ('hyper-check-core.yml', 'check-exports'): "a static scan of the consumer's export sites and header; builds nothing",
    ('hyper-check-core.yml', 'check-corpus'): "re-derives the consumer's corpus from its own oracle script; builds nothing",
    ('hyper-check-core.yml', 'lint-workflows'): "actionlint over the consumer's workflows; builds nothing",
    ('hyper-check-core.yml', 'format-csharp'): '`dotnet format whitespace` over the solution, which restores nothing '
                                               '(no project.assets.json, no package folder: measured)',
    ('hyper-check-core.yml', 'format-java'): "spotlessCheck, which resolves no dependency (measured: Gradle's module "
                                             'cache holds no companion after it)',
    ('hyper-check-core.yml', 'lint-swift'): 'SwiftLint over swift/Sources, which reads files and resolves no package',
    ('hyper-check-core.yml', 'format-swift'): '`swift format lint`, which reads files and resolves no package '
                                              '(measured: no .build, no Package.resolved)',
    ('hyper-test-c.yml', 'pack'): "packs the consumer's own libraries (c_package.py pack) and lints its smoke "
                                  "sources; it never reads the companion's archive",
}


def _workflows(github):
    seen = set()
    for pattern in WORKFLOW_GLOBS:
        for path in sorted((github / 'workflows').glob(pattern)):
            if path.name not in seen:
                seen.add(path.name)
                yield path


_FROM_JSON = re.compile(r'\$\{\{\s*fromJSON\(\s*(inputs\.[\w-]+)\s*\)\s*\}\}')
_MODE_ROWS = re.compile(r"\$\{\{\s*fromJSON\(\s*(?P<format>format\(\s*)?inputs\.mode == 'pr'\s*&&\s*'(?P<pr>[^']*)'"
                        r"\s*\|\|\s*'(?P<release>[^']*)'\s*(?:,\s*inputs\.crate\s*\)\s*)?\)\s*\}\}")


def matrix_rows(job, mode='release'):
    """A job's `include` rows, literal or selected by `inputs.mode`: the `pr` or the `release` list
    of a `fromJSON(inputs.mode == 'pr' && '[...]' || '[...]')`, with `format`'s escapes undone and
    its `{0}` back to `${{ inputs.crate }}`. None when the job has neither."""
    include = ((job.get('strategy') or {}).get('matrix') or {}).get('include')
    if isinstance(include, list):
        return include
    found = _MODE_ROWS.fullmatch(include.strip()) if isinstance(include, str) else None
    if not found:
        return None
    text = found[mode]
    if found['format']:
        text = text.replace('{{', '\0').replace('}}', '\1').replace('{0}', '${{ inputs.crate }}')
        text = text.replace('\0', '{').replace('\1', '}')
    return json.loads(text)


def _entries(workflow, job_name, job, scope=None):
    """The job's matrix entries: literal ones, the PHP build's computed release matrix, or one
    empty entry (no matrix, or one computed from inputs). With `scope` (the workflow's input
    defaults), an axis that is `${{ fromJSON(inputs.x) }}` takes the default's list."""
    if (workflow, job_name) == ('hyper-build-php-extension.yml', 'build'):
        minors = php_ext_package.MINORS
        return php_ext_package.matrix('release', ' '.join(minors), minors[0])['include']
    matrix = (job.get('strategy') or {}).get('matrix')
    if not isinstance(matrix, dict):
        return [{}]
    rows = matrix_rows(job)
    if rows is not None:
        return rows
    if scope:
        matrix = dict(matrix)
        for key, value in matrix.items():
            found = _FROM_JSON.fullmatch(str(value)) if isinstance(value, str) else None
            if found and isinstance(scope.get(found[1]), str):
                listed = json.loads(scope[found[1]])
                if isinstance(listed, list):
                    matrix[key] = listed
    axes = {k: v for k, v in matrix.items() if isinstance(v, list)}
    if len(axes) != len(matrix):
        return [{}]
    return [dict(zip(axes, combo)) for combo in product(*axes.values())]


def _rid(job_name, job, entry):
    if entry.get('rid'):
        return entry['rid']
    if job_name.endswith('-osx-x64'):
        return 'osx-x64'
    runner = entry.get('os') or job.get('runs-on')
    return RUNNER_RIDS.get(runner) if isinstance(runner, str) else None


def legs(github=GITHUB):
    """Every leg of the Forge workflows a consumer's board calls."""
    out = []
    for path in _workflows(Path(github)):
        jobs = yaml_lite.load(path.read_text(encoding='utf-8')).get('jobs') or {}
        for name, job in jobs.items():
            for entry in _entries(path.name, name, job):
                extra = {k: v for k, v in entry.items() if k != 'rid' and not isinstance(v, (dict, list))}
                out.append(Leg(path.name, name, _rid(name, job, entry), extra))
    return out


def _roles(leg):
    key = (leg.workflow, leg.job)
    if key in NO_COMPANION:
        return ()
    if key not in JOBS:
        raise ValueError(f'{leg.workflow}:{leg.job} is not mapped: add it to companion_map.JOBS or NO_COMPANION')
    roles = JOBS[key]
    if roles and roles[0] == 'shard':
        shard = leg.extra.get('shard', 'all')
        if shard not in roles[1]:
            raise ValueError(f'{leg.workflow}:{leg.job}: no shard {shard!r}')
        roles = roles[1][shard]
    return roles


def ecosystems_for(leg):
    """The ecosystems a leg overrides, in ECOSYSTEMS order; [] for NO_COMPANION."""
    wanted = {eco for role in _roles(leg) for eco in ROLES[role][0]}
    return [eco for eco in ECOSYSTEMS if eco in wanted]


def _android_rids(github):
    job = yaml_lite.load((Path(github) / 'workflows' / 'hyper-build-android.yml').read_text(encoding='utf-8'))['jobs']['build-native-android']
    return tuple(entry['rid'] for entry in job['strategy']['matrix']['include'])


def _expand(template, leg, cfg, github):
    fields = set(re.findall(r'\{(\w+)\}', template))
    if not fields:
        return [template]
    if 'rid' in fields and not leg.rid:
        raise ValueError(f'{leg.workflow}:{leg.job}: {template} needs the leg\'s rid')
    if 'abi' in fields and 'rid' in fields and leg.rid not in cfg.ruby_platforms:
        return []  # no platform gem for this rid (osx-x64): Fiddle on the native library alone
    if 'minor' in fields and not leg.extra.get('minor'):
        raise ValueError(f'{leg.workflow}:{leg.job}: {template} needs the leg\'s PHP minor')
    ts = leg.extra.get('ts', 'nts')
    if 'ts' in fields and ts not in php_ext_package.THREAD_SAFETY:
        raise ValueError(f'{leg.workflow}:{leg.job}: thread safety {ts!r} is nts or zts')
    if 'wasi' in fields and not cfg.node_wasi:
        return []
    abis = list(cfg.ruby_abis)
    if 'abi' in fields and leg.extra.get('abi'):
        if str(leg.extra['abi']) not in abis:
            raise ValueError(f'{leg.workflow}:{leg.job}: Ruby ABI {leg.extra["abi"]} is not one of the config\'s '
                             f'({", ".join(abis)})')
        abis = [str(leg.extra['abi'])]
    values = {'rid': [leg.rid], 'abi': abis, 'wasi': [cfg.node_wasi],
              'node_rid': list(cfg.node_platforms), 'android': list(_android_rids(github)),
              'minor': [leg.extra.get('minor')], 'ts': ['-zts' if ts == 'zts' else '']}
    names = [template]
    for field in fields:
        names = [n.replace('{' + field + '}', str(v)) for n in names for v in values[field]]
    return names


def _artifacts(leg, cfg, github):
    names = set()
    for role in _roles(leg):
        for template in ROLES[role][1]:
            names.update(_expand(template, leg, cfg, github))
    return sorted(names)


def _unread_keys(leg):
    """The leg's LEG_KEYS values nothing of its job reads: `abi`, `minor` and `ts` where no
    template of its roles has the field, `shard` off a shard job."""
    key = (leg.workflow, leg.job)
    sharded = key in JOBS and JOBS[key] and JOBS[key][0] == 'shard'
    fields = {f for role in _roles(leg) for t in ROLES[role][1] for f in re.findall(r'\{(\w+)\}', t)}
    return [k for k in LEG_KEYS if k in leg.extra and not (k in fields or (k == 'shard' and sharded))]


def artifacts_for(leg, crate, cfg, github=GITHUB):
    """The companion `crate`'s CI artifacts the leg takes, rid-expanded over the config's
    platform lists. The names are the Forge's and carry no crate; the crate is checked only
    to be a companion's, not the consumer's own. A LEG_KEYS value the leg's job never reads
    (`abi=` on a Go leg) is refused, as a mistyped key is."""
    if not isinstance(crate, str) or not re.fullmatch('[a-z][a-z0-9_]*', crate):
        raise ValueError(f'{crate!r} is not a crate name')
    if crate == cfg.crate:
        raise ValueError(f'{crate} is the consumer itself, never its own companion')
    unread = _unread_keys(leg)
    if unread:
        raise ValueError(f'{leg.workflow}:{leg.job} reads no {", ".join(unread)}: leave '
                         f'{", ".join(k + "=" for k in unread)} off this leg')
    return _artifacts(leg, cfg, Path(github))


def all_artifacts(cfg, github=GITHUB):
    """Every artifact any leg of a board takes (LEGACY_WORKFLOWS' legs left out): what the
    companions job downloads from a pinned run."""
    names = set()
    for leg in legs(github) + list(CONSUMER_LEGS):
        if leg.workflow not in LEGACY_WORKFLOWS:
            names.update(_artifacts(leg, cfg, Path(github)))
    return names


_EXPR = re.compile(r'\$\{\{(.*?)\}\}')


def _resolve(template, scope):
    """The template with every `${{ x }}` replaced from scope, or None if any expression has
    no non-empty value there: a name that is not fully literal proves nothing."""
    unresolved = []

    def value(m):
        v = scope.get(m.group(1).strip())
        if isinstance(v, bool) or not isinstance(v, (str, int)) or v == '':
            unresolved.append(m.group(1))
            return ''
        return str(v)
    out = _EXPR.sub(value, str(template))
    return None if unresolved else out


def _defaults(inputs):
    return {f'inputs.{k}': v['default'] for k, v in (inputs or {}).items() if isinstance(v, dict) and 'default' in v}


def _uploads(steps):
    for step in steps or []:
        if isinstance(step, dict) and 'actions/upload-artifact' in str(step.get('uses', '')):
            yield (step.get('with') or {}).get('name', '')


def _action(step, github):
    m = re.search(r'\.github/actions/([\w-]+)$', str(step.get('uses', '')))
    return (Path(github) / 'actions' / m.group(1) / 'action.yml') if m else None


def producer_workflows(github=GITHUB):
    seen = []
    for pattern in PRODUCER_GLOBS:
        for path in sorted((Path(github) / 'workflows').glob(pattern)):
            if path.name not in LEGACY_WORKFLOWS and path not in seen:
                seen.append(path)
    return seen


def producers(github=GITHUB):
    """Every literal artifact name a full board's upload steps produce. A job's names are
    resolved per matrix entry (the PHP build's computed matrix through php_ext_package), with
    the workflow's input defaults under COMPANION_INPUTS; a composite action's from the calling
    step's `with:`, itself resolved in the caller's scope, then the action's own defaults."""
    names = set()
    for path in producer_workflows(github):
        data = yaml_lite.load(path.read_text(encoding='utf-8'))
        on = data.get('on') or {}
        inputs = ((on.get('workflow_call') or {}) if isinstance(on, dict) else {}).get('inputs')
        base = {**_defaults(inputs), **{f'inputs.{k}': v for k, v in COMPANION_INPUTS.get(path.name, {}).items()}}
        for job_name, job in (data.get('jobs') or {}).items():
            for entry in _entries(path.name, job_name, job, base):
                scope = {**base, **{f'matrix.{k}': v for k, v in entry.items()}}
                for template in _uploads(job.get('steps')):
                    names.add(_resolve(template, scope))
                for step in job.get('steps') or []:
                    action = _action(step, github)
                    if not action:
                        continue
                    spec = yaml_lite.load(action.read_text(encoding='utf-8'))
                    inner = _defaults(spec.get('inputs'))
                    for key, value in (step.get('with') or {}).items():
                        inner[f'inputs.{key}'] = _resolve(value, scope) if isinstance(value, str) else value
                    for template in _uploads((spec.get('runs') or {}).get('steps')):
                        names.add(_resolve(template, inner))
    names.discard(None)
    return names


def name_inputs(path, github=GITHUB):
    """The workflow's inputs that reach an upload name, directly or through a composite
    action's `with:`."""
    data = yaml_lite.load(Path(path).read_text(encoding='utf-8'))
    found = set()
    for job in (data.get('jobs') or {}).values():
        texts = [str(t) for t in _uploads(job.get('steps'))]
        for step in job.get('steps') or []:
            action = _action(step, github)
            if action:
                spec = yaml_lite.load(action.read_text(encoding='utf-8'))
                used = {m.strip()[7:] for t in _uploads((spec.get('runs') or {}).get('steps'))
                        for m in _EXPR.findall(str(t)) if m.strip().startswith('inputs.')}
                texts += [str(v) for k, v in (step.get('with') or {}).items() if k in used]
        for text in texts:
            found |= {m.strip()[7:] for m in _EXPR.findall(text) if m.strip().startswith('inputs.')}
    return found


def unmatched(cfg, github=GITHUB):
    """The names some leg takes that no full board's upload step produces, sorted: empty when
    every name is paired with a producer."""
    return sorted(all_artifacts(cfg, github) - producers(github))
