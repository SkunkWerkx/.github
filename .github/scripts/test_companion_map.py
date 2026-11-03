"""companion_map.py: which of a companion's CI artifacts each consumer leg takes, and which
ecosystems that leg overrides (the spec's "The mapping")."""
from pathlib import Path
import re
import shutil
import tempfile
import unittest

import companion_map
from companion_map import Leg
import hyper_config
import yaml_lite

GITHUB = Path(__file__).resolve().parents[1]
CFG = hyper_config.HyperConfig({'crate': 'hypertabular', 'project': 'HyperTabular',
                                'exports': {'sources': ['rust/src/ffi.rs'], 'patterns': ['x'], 'prefixes': ['x']}})
HYPERCAST_CI = GITHUB.parents[1] / 'HyperCast' / '.github' / 'workflows' / 'ci.yml'


def leg(workflow, job, rid=None, **extra):
    return Leg(workflow, job, rid, extra)


def arts(workflow, job, rid=None, **extra):
    return companion_map.artifacts_for(leg(workflow, job, rid, **extra), 'hypercast', CFG)


def ecos(workflow, job, rid=None, **extra):
    return companion_map.ecosystems_for(leg(workflow, job, rid, **extra))


class LegTests(unittest.TestCase):
    def test_every_matrix_leg_maps(self):
        legs = companion_map.legs()
        seen = {(l.workflow, l.job) for l in legs}
        self.assertTrue(any(l.workflow == 'hyper-core.yml' for l in legs))
        self.assertIn(('hyper-build-php-extension.yml', 'build'), seen)
        for l in legs:
            with self.subTest(leg=l):
                if (l.workflow, l.job) in companion_map.NO_COMPANION:
                    self.assertEqual(companion_map.ecosystems_for(l), [])
                    self.assertEqual(companion_map.artifacts_for(l, 'hypercast', CFG), [])
                    continue
                ecosystems = companion_map.ecosystems_for(l)
                self.assertTrue(ecosystems)
                self.assertTrue(set(ecosystems) <= set(companion_map.ECOSYSTEMS))
                companion_map.artifacts_for(l, 'hypercast', CFG)  # expands without error
        # Nothing mapped or excused that no workflow has, and nothing both.
        consumer = {(l.workflow, l.job) for l in companion_map.CONSUMER_LEGS}
        self.assertEqual(set(companion_map.JOBS) - seen - consumer, set())
        self.assertEqual(set(companion_map.NO_COMPANION) - seen, set())
        self.assertEqual(set(companion_map.JOBS) & set(companion_map.NO_COMPANION), set())
        for key, reason in companion_map.NO_COMPANION.items():
            with self.subTest(excused=key):
                self.assertTrue(reason.strip())
                self.assertNotIn('\n', reason)

    def test_legs_cover_every_workflow_glob(self):
        names = {l.workflow for l in companion_map.legs()}
        for pattern in ('hyper-core*.yml', 'hyper-build-*.yml', 'hyper-lint-*.yml', 'hyper-test-*.yml'):
            for path in (GITHUB / 'workflows').glob(pattern):
                self.assertIn(path.name, names)

    def test_computed_php_matrix_expands_to_the_release_legs(self):
        php = [l for l in companion_map.legs() if l.workflow == 'hyper-build-php-extension.yml' and l.job == 'build']
        import php_ext_package
        self.assertEqual(len(php), len(php_ext_package.PLATFORMS) * len(php_ext_package.MINORS))
        self.assertTrue(all(l.rid and l.extra.get('minor') for l in php))

    def test_unknown_leg_is_refused(self):
        with self.assertRaises(ValueError):
            companion_map.ecosystems_for(leg('hyper-test-nope.yml', 'test', 'linux-x64'))
        with self.assertRaises(ValueError):
            arts('hyper-test-python.yml', 'test')  # a rid template with no rid
        with self.assertRaises(ValueError):
            companion_map.artifacts_for(leg('hyper-test-go.yml', 'test', 'linux-x64'), 'hypertabular', CFG)


def caller_inputs(text, workflow):
    """The `with:` keys and values each HyperCast ci.yml job calling the Forge's `workflow` passes
    (ci.yml is beyond yaml_lite, so read by indentation: jobs at 2, keys at 4, inputs at 6)."""
    calls, job, in_with = [], None, False
    for line in text.splitlines():
        if re.match(r'^  [\w-]+:\s*$', line):
            job, in_with = None, False
        elif re.match(r'^    uses: .*/\.github/workflows/' + re.escape(workflow) + '@', line):
            job = {}
            calls.append(job)
        elif job is not None and re.match(r'^    with:\s*$', line):
            in_with = True
        elif job is not None and in_with and re.match(r'^    \S', line):
            in_with = False
        elif job is not None and in_with:
            m = re.match(r'^      ([\w-]+):\s*(.*?)\s*$', line)
            if m:
                job[m.group(1)] = m.group(2).strip('"\'')
    return calls


class ProducerTests(unittest.TestCase):
    def test_artifact_names_match_producers(self):
        produced = companion_map.producers()
        self.assertTrue(produced)
        self.assertFalse([n for n in produced if '${{' in n])
        self.assertTrue(companion_map.all_artifacts(CFG))
        self.assertEqual(companion_map.unmatched(CFG), [], 'no upload step on a board workflow produces these')

    def renamed_copy(self, tmp, relative, old, new):
        """A copy of the Forge's workflows and actions with one upload name renamed."""
        root = Path(tmp) / '.github'
        for part in ('workflows', 'actions'):
            shutil.copytree(GITHUB / part, root / part)
        path = root / relative
        text = path.read_text()
        self.assertEqual(text.count(old), 1, f'{relative}: {old!r} is not one upload name')
        path.write_text(text.replace(old, new))
        return root

    def assert_rename_is_caught(self, relative, old, new, touched):
        before = companion_map.producers()
        self.assertTrue(set(touched) <= before)
        with tempfile.TemporaryDirectory() as tmp:
            root = self.renamed_copy(tmp, relative, old, new)
            produced = companion_map.producers(root)
            self.assertEqual(before - produced, set(touched))
            self.assertEqual(companion_map.unmatched(CFG, root), sorted(touched))

    def test_renaming_the_core_native_upload_is_caught(self):
        # Negative control: the non-musl native-<rid> comes only from hyper-core.yml's core job.
        self.assert_rename_is_caught('workflows/hyper-core.yml', 'name: native-${{ matrix.rid }}',
                                     'name: corelib-${{ matrix.rid }}',
                                     [f'native-{rid}' for rid in ('linux-arm64', 'linux-x64', 'osx-arm64', 'win-arm64', 'win-x64')])

    def test_renaming_the_magnus_upload_is_caught(self):
        # Negative control: the glibc, macOS and Windows platform gems' natives come only from
        # the ruby-magnus action (musl's from ruby-magnus-musl).
        self.assert_rename_is_caught('actions/ruby-magnus/action.yml',
                                     'name: ruby-native-${{ inputs.rid }}-${{ inputs.ruby-version }}',
                                     'name: magnus-${{ inputs.rid }}-${{ inputs.ruby-version }}',
                                     [f'ruby-native-{rid}-{abi}' for rid in ('linux-arm64', 'linux-x64', 'osx-arm64', 'win-arm64', 'win-x64')
                                      for abi in CFG.ruby_abis])

    def test_renaming_the_ruby_wasm_upload_is_caught(self):
        # Negative control: the ruby.wasm archives come only from build-ruby-wasm, one per
        # ruby_wasm_abis default (a fromJSON matrix, resolved from the input's default).
        self.assert_rename_is_caught('workflows/hyper-build-wasm.yml', 'name: ruby-wasm-${{ matrix.abi }}',
                                     'name: rbwasm-${{ matrix.abi }}', [f'ruby-wasm-{abi}' for abi in CFG.ruby_abis])

    def test_bogus_names_are_not_produced(self):
        produced = companion_map.producers()
        for bogus in ('native-bogus', 'jni-bogus', 'ruby-native-osx-x64-9.9', 'php-ext-osx-x64-7.0',
                      'ruby-native-osx-x64-4.0', 'php-ext-osx-x64-8.5', 'c-packages', 'nuget-org-package'):
            self.assertNotIn(bogus, produced)

    def test_producers_are_board_workflows_only(self):
        names = {p.name for p in companion_map.producer_workflows()}
        self.assertTrue(names)
        self.assertFalse(names & set(companion_map.LEGACY_WORKFLOWS))
        self.assertFalse([n for n in names if n.startswith(('hyper-pack-', 'hyper-publish-', 'hyper-prepare-'))])
        self.assertIn('hyper-build-php-extension.yml', names)

    def test_all_artifacts_is_the_union(self):
        union = set()
        for l in companion_map.legs() + list(companion_map.CONSUMER_LEGS):
            if l.workflow not in companion_map.LEGACY_WORKFLOWS:
                union |= set(companion_map.artifacts_for(l, 'hypercast', CFG))
        self.assertEqual(companion_map.all_artifacts(CFG), union)
        for expected in ('pypi-wheels-sdist', 'pypi-wheels-pyodide', 'node-wasm32-wasip1', 'node-osx-x64',
                         'wasm-emscripten-staticlib', 'swift-release-assets', 'c-packages-ci', 'static-libs',
                         'native-android-arm64', 'native-android-x64', 'ruby-native-win-arm64-3.4',
                         'php-ext-win-x64-8.3', 'jni-osx-x64'):
            self.assertIn(expected, union)

    def test_consumer_legs_are_the_ci_yml_jobs(self):
        self.assertEqual({(l.workflow, l.job) for l in companion_map.CONSUMER_LEGS},
                         {k for k in companion_map.JOBS if k[0] == 'ci.yml'})

    def test_ruby_abis_are_what_the_companion_uploads(self):
        ruby = yaml_lite.load((GITHUB / 'workflows' / 'hyper-test-ruby.yml').read_text())
        inputs = ruby['on']['workflow_call']['inputs']
        uploaded = {inputs['ruby_version']['default'], companion_map.COMPANION_INPUTS['hyper-test-ruby.yml']['ruby_compat_version']}
        self.assertEqual(set(hyper_config.RUBY_ABIS), uploaded)
        self.assertEqual(set(CFG.ruby_abis), uploaded)

    def test_companion_inputs_are_hypercasts(self):
        """Every input HyperCast's ci.yml passes that reaches an upload name is in
        COMPANION_INPUTS with that value, and every COMPANION_INPUTS entry is one it passes."""
        if not HYPERCAST_CI.is_file():
            self.skipTest(f'no sibling HyperCast checkout at {HYPERCAST_CI}')
        text = HYPERCAST_CI.read_text()
        for path in companion_map.producer_workflows():
            relevant = companion_map.name_inputs(path)
            for call in caller_inputs(text, path.name):
                passed = {k: v for k, v in call.items() if k in relevant}
                with self.subTest(workflow=path.name):
                    self.assertEqual(passed, companion_map.COMPANION_INPUTS.get(path.name, {}))
        self.assertEqual(companion_map.name_inputs(GITHUB / 'workflows' / 'hyper-test-ruby.yml'),
                         {'ruby_version', 'ruby_compat_version'})
        self.assertEqual(caller_inputs(text, 'hyper-test-ruby.yml')[0].get('ruby_compat_version'), '3.4')

    def test_platform_lists_come_from_the_config(self):
        cfg = hyper_config.HyperConfig({**CFG.data, 'ruby': {'abis': ['4.0']}, 'node': {'wasi': ''}})
        self.assertEqual(companion_map.artifacts_for(leg('hyper-test-ruby.yml', 'test', 'linux-x64'), 'hypercast', cfg),
                         ['native-linux-x64', 'ruby-native-linux-x64-4.0'])
        self.assertEqual(companion_map.artifacts_for(leg('hyper-build-wasm.yml', 'build-node-wasi'), 'hypercast', cfg), [])


class MappingRowTests(unittest.TestCase):
    """One per row of the spec's mapping table."""

    def test_rust_core(self):
        for workflow, job, rid in (('hyper-core-checks.yml', 'checks', None), ('hyper-build-wasm.yml', 'build-wasm', None),
                                   ('hyper-build-android.yml', 'build-native-android', 'android-x64')):
            with self.subTest(job=job):
                self.assertEqual(ecos(workflow, job, rid), ['rust'])
                self.assertEqual(arts(workflow, job, rid), [])
        # The core legs also smoke-test this leg's C archive, whose C++ programs build against the
        # companion's (c/smoke/<name>.sh): the PR board's only C smoke.
        for job, rid in (('core', 'win-x64'), ('core-musl', 'linux-musl-x64'), ('core-osx-x64', 'osx-x64')):
            with self.subTest(job=job):
                self.assertEqual(ecos('hyper-core.yml', job, rid), ['rust', 'c'])
                self.assertEqual(arts('hyper-core.yml', job, rid), ['c-packages-ci'])

    def test_go(self):
        for workflow, job, rid in (('hyper-test-go.yml', 'test', 'win-x64'), ('hyper-test-go.yml', 'test-musl', 'linux-musl-arm64'),
                                   ('hyper-build-wasm.yml', 'test-go-wasm', None)):
            with self.subTest(job=job):
                self.assertEqual(ecos(workflow, job, rid), ['go'])
                self.assertEqual(arts(workflow, job, rid), [])
        # The Go lint regenerates the translated core (TestCoreIsCurrent), which builds the
        # consumer's crate, and with it the companion's: the Cargo patch as well as the replace.
        for workflow, job, rid in (('hyper-lint-go.yml', 'lint', 'linux-arm64'), ('hyper-check-core.yml', 'lint-go', None)):
            with self.subTest(job=job):
                self.assertEqual(ecos(workflow, job, rid), ['rust', 'go'])
                self.assertEqual(arts(workflow, job, rid), [])

    def test_python(self):
        self.assertEqual(ecos('hyper-test-python.yml', 'test', 'osx-arm64'), ['rust', 'python'])
        self.assertEqual(arts('hyper-test-python.yml', 'test', 'osx-arm64'), ['pypi-wheels-osx-arm64'])
        self.assertEqual(arts('hyper-test-python.yml', 'test-musl', 'linux-musl-x64'), ['pypi-wheels-linux-musl-x64'])
        self.assertEqual(arts('hyper-build-wheels.yml', 'build-wheels', 'osx-x64'), ['pypi-wheels-osx-x64', 'pypi-wheels-sdist'])
        self.assertEqual(arts('hyper-build-wheels.yml', 'build-sdist', 'linux-x64'),
                         ['pypi-wheels-linux-musl-x64', 'pypi-wheels-linux-x64', 'pypi-wheels-sdist'])
        self.assertEqual(arts('hyper-build-wheels.yml', 'sdist-fallthrough', 'linux-x64', python='pypy3.11'),
                         ['pypi-wheels-linux-x64', 'pypi-wheels-sdist'])
        self.assertEqual(ecos('hyper-build-wheels.yml', 'build-pyodide-wheel', 'linux-x64'), ['rust', 'pyodide'])
        self.assertEqual(arts('hyper-build-wheels.yml', 'build-pyodide-wheel', 'linux-x64'), ['pypi-wheels-pyodide'])
        # ruff installs nothing: the Python lint never meets the companion.
        self.assertIn(('hyper-lint-python.yml', 'lint'), companion_map.NO_COMPANION)
        self.assertEqual(ecos('hyper-lint-python.yml', 'lint', 'linux-arm64'), [])

    def test_typescript(self):
        self.assertEqual(ecos('hyper-test-typescript.yml', 'test', 'win-arm64'), ['rust', 'npm'])
        self.assertEqual(arts('hyper-test-typescript.yml', 'test', 'win-arm64'), ['node-win-arm64'])
        self.assertEqual(arts('hyper-test-typescript.yml', 'test-osx-x64', 'osx-x64'), ['node-osx-x64'])
        self.assertEqual(arts('hyper-build-wasm.yml', 'build-node-wasi', 'linux-x64'), ['node-wasm32-wasip1'])
        self.assertEqual(arts('hyper-lint-typescript.yml', 'lint', 'linux-arm64'), ['node-linux-arm64'])
        self.assertEqual(ecos('ci.yml', 'test-npm-package'), ['npm'])
        self.assertEqual(arts('ci.yml', 'test-npm-package'),
                         sorted([f'node-{rid}' for rid in CFG.node_platforms] + ['node-wasm32-wasip1']))

    def test_ruby(self):
        self.assertEqual(ecos('hyper-test-ruby.yml', 'test', 'win-arm64'), ['rust', 'ruby'])
        self.assertEqual(arts('hyper-test-ruby.yml', 'test', 'win-arm64'),
                         ['native-win-arm64', 'ruby-native-win-arm64-3.4', 'ruby-native-win-arm64-4.0'])
        self.assertEqual(arts('hyper-test-ruby.yml', 'test-musl', 'linux-musl-arm64'),
                         ['native-linux-musl-arm64', 'ruby-native-linux-musl-arm64-3.4', 'ruby-native-linux-musl-arm64-4.0'])
        # osx-x64 has no platform gem: Fiddle on the leg's native library alone.
        self.assertEqual(arts('hyper-build-native.yml', 'build-native-osx-x64', 'osx-x64'),
                         ['jni-osx-x64', 'native-osx-x64', 'node-osx-x64'])
        self.assertEqual(ecos('hyper-lint-ruby.yml', 'lint', 'linux-arm64'), ['ruby'])
        self.assertEqual(arts('hyper-lint-ruby.yml', 'lint', 'linux-arm64'), [])
        # The ruby.wasm build takes HyperCast's own wasm32-wasip1 archive for its minor (from
        # build-ruby-wasm on the companion's board), on any runner: no rid in the name.
        self.assertEqual(ecos('hyper-build-wasm.yml', 'build-ruby-wasm', abi='4.0'), ['rust', 'ruby'])
        for rid in (None, 'linux-x64', 'osx-x64'):
            self.assertEqual(arts('hyper-build-wasm.yml', 'build-ruby-wasm', rid, abi='3.4'), ['ruby-wasm-3.4'])
        self.assertEqual(arts('hyper-build-wasm.yml', 'build-ruby-wasm'), ['ruby-wasm-3.4', 'ruby-wasm-4.0'])
        with self.assertRaises(ValueError):
            arts('hyper-build-wasm.yml', 'build-ruby-wasm', abi='9.9')

    def test_java(self):
        self.assertEqual(ecos('hyper-test-java.yml', 'test', 'linux-arm64'), ['rust', 'java'])
        self.assertEqual(arts('hyper-test-java.yml', 'test', 'linux-arm64'), ['jni-linux-arm64', 'native-linux-arm64'])
        self.assertEqual(arts('hyper-test-java.yml', 'test-musl', 'linux-musl-x64'), ['jni-linux-musl-x64', 'native-linux-musl-x64'])
        self.assertEqual(arts('hyper-test-java.yml', 'test-osx-x64', 'osx-x64'), ['jni-osx-x64', 'native-osx-x64'])
        # spotlessCheck resolves no dependency (measured: Gradle's module cache holds no HyperCast
        # after it), and runs before the fetch; the JNI crate's clippy and tests need the Rust core.
        self.assertEqual(ecos('hyper-lint-java.yml', 'lint', 'linux-arm64'), ['rust'])
        self.assertEqual(arts('hyper-lint-java.yml', 'lint', 'linux-arm64'), [])
        self.assertEqual(ecos('hyper-check-core.yml', 'lint-java-jni'), ['rust'])
        self.assertIn(('hyper-check-core.yml', 'format-java'), companion_map.NO_COMPANION)

    def test_csharp(self):
        self.assertEqual(ecos('hyper-test-csharp.yml', 'test', 'win-x64'), ['csharp'])
        self.assertEqual(arts('hyper-test-csharp.yml', 'test', 'win-x64'), ['native-win-x64', 'static-libs'])
        self.assertEqual(arts('hyper-test-csharp.yml', 'test-musl', 'linux-musl-x64'), ['native-linux-musl-x64', 'static-libs'])
        self.assertEqual(arts('hyper-build-wasm.yml', 'test-blazor', 'linux-x64'), ['wasm-emscripten-staticlib'])
        # Each job also runs a Go suite and a Swift one, which resolve the companion too.
        self.assertEqual(ecos('ci.yml', 'test-android'), ['go', 'csharp', 'swift'])
        self.assertEqual(arts('ci.yml', 'test-android'),
                         ['native-android-arm64', 'native-android-x64', 'static-libs', 'swift-release-assets'])
        self.assertEqual(ecos('ci.yml', 'test-apple-mobile'), ['go', 'csharp', 'swift', 'c'])
        self.assertEqual(arts('ci.yml', 'test-apple-mobile'), ['c-packages-ci', 'static-libs', 'swift-release-assets'])
        self.assertEqual(arts('ci.yml', 'test-nuget-package', 'linux-x64'), ['native-linux-x64', 'static-libs'])
        # `dotnet format whitespace` restores nothing (measured: no project.assets.json, no package
        # folder), so the C# lint never meets the companion.
        for key in (('hyper-lint-csharp.yml', 'lint'), ('hyper-check-core.yml', 'format-csharp')):
            with self.subTest(key=key):
                self.assertIn(key, companion_map.NO_COMPANION)
        self.assertEqual(ecos('hyper-lint-csharp.yml', 'lint', 'linux-arm64'), [])

    def test_php(self):
        self.assertEqual(ecos('hyper-build-php-extension.yml', 'build', 'win-x64', minor='8.3'), ['rust', 'php'])
        self.assertEqual(arts('hyper-build-php-extension.yml', 'build', 'win-x64', minor='8.3'), ['php-ext-win-x64-8.3'])
        # phpcs, and php/'s dev tools, which load no extension: the PHP lint never meets the companion.
        self.assertIn(('hyper-lint-php.yml', 'lint'), companion_map.NO_COMPANION)
        self.assertEqual(ecos('hyper-lint-php.yml', 'lint', 'linux-arm64'), [])

    def test_swift(self):
        for workflow, job, rid in (('hyper-test-swift.yml', 'test', 'win-arm64'),
                                   ('hyper-test-swift-static.yml', 'test-swift-static', 'linux-arm64')):
            with self.subTest(workflow=workflow):
                self.assertEqual(ecos(workflow, job, rid), ['swift'])
                self.assertEqual(arts(workflow, job, rid), ['swift-release-assets'])
        # SwiftLint (the static build, syntax rules only) and `swift format lint` read files and
        # resolve no package (measured: no .build, no Package.resolved, nothing in SwiftPM's caches).
        for key in (('hyper-lint-swift.yml', 'lint'), ('hyper-check-core.yml', 'lint-swift'),
                    ('hyper-check-core.yml', 'format-swift')):
            with self.subTest(key=key):
                self.assertIn(key, companion_map.NO_COMPANION)
        self.assertEqual(ecos('hyper-lint-swift.yml', 'lint', 'linux-arm64'), [])
        self.assertNotIn('swift-lint', companion_map.ROLES)

    def test_r(self):
        for workflow, job, rid in (('hyper-test-r.yml', 'test', 'win-x64'), ('hyper-lint-r.yml', 'lint', 'linux-arm64')):
            with self.subTest(workflow=workflow):
                self.assertEqual(ecos(workflow, job, rid), ['r'])
                self.assertEqual(arts(workflow, job, rid), [])

    def test_c(self):
        for job, rid in (('smoke', 'linux-musl-x64'), ('smoke-apple-mobile', 'osx-arm64')):
            with self.subTest(job=job):
                self.assertEqual(ecos('hyper-test-c.yml', job, rid), ['c'])
                self.assertEqual(arts('hyper-test-c.yml', job, rid), ['c-packages-ci'])
        # The pack job packs the consumer's own libraries and lints its smoke sources: it never
        # reads the companion's archive.
        self.assertIn(('hyper-test-c.yml', 'pack'), companion_map.NO_COMPANION)
        self.assertEqual(ecos('hyper-test-c.yml', 'pack', 'linux-x64'), [])

    def test_build_native_shards(self):
        self.assertEqual(ecos('hyper-build-native.yml', 'build-native', 'win-x64', shard='swift-ruby'), ['rust', 'ruby', 'swift'])
        self.assertEqual(arts('hyper-build-native.yml', 'build-native', 'win-x64', shard='swift-ruby'),
                         ['native-win-x64', 'ruby-native-win-x64-3.4', 'ruby-native-win-x64-4.0', 'swift-release-assets'])
        self.assertEqual(ecos('hyper-build-native.yml', 'build-native', 'win-x64', shard='ffi'),
                         ['rust', 'go', 'python', 'npm', 'java', 'csharp', 'r'])
        self.assertEqual(ecos('hyper-build-native.yml', 'build-native', 'linux-x64', shard='all'),
                         ['rust', 'go', 'python', 'npm', 'ruby', 'java', 'csharp', 'swift', 'r'])


class LegStringTests(unittest.TestCase):
    """The leg a fetch-companions caller names: `<workflow>:<job>[:<rid>][:key=value...]`."""

    def parse(self, text):
        return companion_map.parse_leg(text)

    def test_the_short_forms_are_unchanged(self):
        self.assertEqual(self.parse('hyper-test-go.yml:test:linux-x64'), leg('hyper-test-go.yml', 'test', 'linux-x64'))
        self.assertEqual(self.parse('hyper-test-r.yml:test'), leg('hyper-test-r.yml', 'test'))
        # A ci.yml job with a fixed rid takes it without being told.
        self.assertEqual(self.parse('ci.yml:test-nuget-package'), leg('ci.yml', 'test-nuget-package', 'linux-x64'))
        self.assertEqual(self.parse('ci.yml:test-android'), leg('ci.yml', 'test-android'))

    def test_extra_keys_parse_with_or_without_a_rid(self):
        self.assertEqual(self.parse('hyper-build-php-extension.yml:build:linux-x64:minor=8.4'),
                         leg('hyper-build-php-extension.yml', 'build', 'linux-x64', minor='8.4'))
        self.assertEqual(self.parse('hyper-test-ruby.yml:test:win-x64:abi=3.4'),
                         leg('hyper-test-ruby.yml', 'test', 'win-x64', abi='3.4'))
        self.assertEqual(self.parse('hyper-build-native.yml:build-native:win-x64:shard=ffi'),
                         leg('hyper-build-native.yml', 'build-native', 'win-x64', shard='ffi'))
        # Syntax only: whether the job reads the keys is artifacts_for's question.
        self.assertEqual(self.parse('hyper-test-r.yml:test:abi=3.4:minor=8.4'),
                         leg('hyper-test-r.yml', 'test', None, abi='3.4', minor='8.4'))

    def test_malformed_leg_strings_are_refused(self):
        for text in ('', 'hyper-test-go.yml', 'hyper-test-go.yml:', ':test', 'hyper-test-go.yml:test:',
                     'hyper-test-go.yml:test:linux-x64:abi', 'hyper-test-go.yml:test:linux-x64:abi=',
                     'hyper-test-go.yml:test:linux-x64:abl=3.4', 'hyper-test-go.yml:test:linux-x64:abi=3.4:abi=4.0',
                     'hyper-test-go.yml:test:linux-x64:linux-arm64', 'hyper-test-go.yml:test:linux x64'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.parse(text)

    def text_arts(self, text):
        return companion_map.artifacts_for(self.parse(text), 'hypercast', CFG)

    def test_extra_keys_select_exactly_their_artifacts(self):
        # Ruby: the leg's ABI alone, beside the native library; without one, every ABI.
        self.assertEqual(self.text_arts('hyper-test-ruby.yml:test:linux-x64:abi=3.4'),
                         ['native-linux-x64', 'ruby-native-linux-x64-3.4'])
        self.assertEqual(self.text_arts('hyper-test-ruby.yml:test:linux-x64:abi=4.0'),
                         ['native-linux-x64', 'ruby-native-linux-x64-4.0'])
        self.assertEqual(self.text_arts('hyper-test-ruby.yml:test:linux-x64'),
                         ['native-linux-x64', 'ruby-native-linux-x64-3.4', 'ruby-native-linux-x64-4.0'])
        # osx-x64 still has no platform gem, whatever the ABI.
        self.assertEqual(self.text_arts('hyper-test-ruby.yml:test:osx-x64:abi=3.4'), ['native-osx-x64'])
        with self.assertRaisesRegex(ValueError, 'ABI'):
            self.text_arts('hyper-test-ruby.yml:test:linux-x64:abi=2.7')
        # PHP: the leg's minor; none is an error, as before.
        self.assertEqual(self.text_arts('hyper-build-php-extension.yml:build:linux-x64:minor=8.4'), ['php-ext-linux-x64-8.4'])
        with self.assertRaisesRegex(ValueError, 'minor'):
            self.text_arts('hyper-build-php-extension.yml:build:linux-x64')
        # The build-native shard: its ecosystems and its artifacts.
        ffi = self.parse('hyper-build-native.yml:build-native:win-x64:shard=ffi')
        self.assertEqual(companion_map.ecosystems_for(ffi), ['rust', 'go', 'python', 'npm', 'java', 'csharp', 'r'])
        self.assertEqual(companion_map.artifacts_for(ffi, 'hypercast', CFG),
                         ['jni-win-x64', 'native-win-x64', 'node-win-x64', 'pypi-wheels-win-x64', 'static-libs'])
        self.assertEqual(self.text_arts('hyper-build-native.yml:build-native:win-x64:shard=swift-ruby:abi=4.0'),
                         ['native-win-x64', 'ruby-native-win-x64-4.0', 'swift-release-assets'])
        with self.assertRaisesRegex(ValueError, 'shard'):
            self.text_arts('hyper-build-native.yml:build-native:win-x64:shard=nope')

    def test_keys_the_legs_job_never_reads_are_refused(self):
        for text, unread in (('hyper-test-go.yml:test:linux-x64:abi=3.4', 'abi'),
                             ('hyper-test-r.yml:test:abi=3.4:minor=8.4', 'abi, minor'),
                             ('hyper-test-ruby.yml:test:linux-x64:minor=8.4', 'minor'),
                             ('hyper-build-php-extension.yml:build:linux-x64:minor=8.4:abi=3.4', 'abi'),
                             ('hyper-test-ruby.yml:test:linux-x64:shard=ffi', 'shard')):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, f'reads no {unread}'):
                    self.text_arts(text)
        # A shard job reads shard=, and abi= where its shard takes a Ruby template.
        self.text_arts('hyper-build-native.yml:build-native:win-x64:shard=swift-ruby:abi=4.0')
        with self.assertRaisesRegex(ValueError, 'reads no abi'):
            self.text_arts('hyper-build-native.yml:build-native:win-x64:shard=ffi:abi=4.0')

    def test_every_matrix_leg_round_trips_through_its_string(self):
        for l in companion_map.legs():
            if (l.workflow, l.job) in companion_map.NO_COMPANION:
                continue
            text = companion_map.leg_string(l)
            with self.subTest(leg=text):
                parsed = self.parse(text)
                self.assertEqual(companion_map.artifacts_for(parsed, 'hypercast', CFG),
                                 companion_map.artifacts_for(l, 'hypercast', CFG))
                self.assertEqual(companion_map.ecosystems_for(parsed), companion_map.ecosystems_for(l))


if __name__ == '__main__':
    unittest.main()
