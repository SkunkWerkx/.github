"""Regression contracts for isolated Forge producers and immutable action sources."""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import unittest

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'


class ProducerContracts(unittest.TestCase):
    def test_timing_model_waits_for_both_android_architectures(self):
        path = Path(__file__).with_name('ci_timing.py')
        self.assertTrue(path.exists())
        spec = importlib.util.spec_from_file_location('ci_timing', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def entry(name, start, end):
            return {'name': name, 'startedAt': f'2026-10-21T00:{start}:00Z',
                    'completedAt': f'2026-10-21T00:{end}:00Z', 'conclusion': 'success'}
        run = {'conclusion': 'success', 'startedAt': '2026-10-21T00:00:00Z', 'jobs': [
            entry('build-native / build-static-libs', '00', '02'),
            entry('build-native / build-native-android (android-arm64, aarch64)', '00', '01'),
            entry('build-native / build-native-android (android-x64, x86_64)', '03', '04'),
            entry('test-android', '10', '16'),
            entry('test-apple-mobile', '10', '30')]}
        result = {row['job']: row for row in module.summarize(run)}
        self.assertEqual(result['test-android']['artifact_ready_seconds'], 240)
        self.assertEqual(result['test-android']['observed_wait_seconds'], 360)
        self.assertEqual(result['test-android']['modeled_finish_seconds'], 600)
        self.assertEqual(result['test-apple-mobile']['artifact_ready_seconds'], 120)
        self.assertEqual(result['test-apple-mobile']['modeled_finish_seconds'], 1320)

    def test_parallel_php_keeps_existing_platforms_load_probe_and_artifacts(self):
        path = WORKFLOWS / 'hyper-build-php-extension.yml'
        self.assertTrue(path.exists())
        text = path.read_text()
        self.assertEqual(re.findall(r'            rid: ([\w-]+)', text),
                         ['linux-x64', 'linux-arm64', 'osx-arm64'])
        for value in ('cargo rustc --release --crate-type cdylib --features php',
                      'php -d extension=', 'extension_loaded(',
                      'name: php-native-${{ matrix.rid }}',
                      'actions/attest-build-provenance@'):
            self.assertIn(value, text)
        native = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        # The extension is built only by its own job: the matrix builds none, so it can never
        # be built twice or by two recipes that drift apart, and there is no schedule to pick.
        self.assertNotIn('php_extension_schedule', native)
        self.assertNotIn('- name: Validate PHP extension schedule', native)
        self.assertIn("  build-php-extension:\n    if: inputs.php && inputs.mode != 'pr'\n", native)
        for gone in ('--features php', 'php-native-${{ matrix.rid }}'):
            self.assertNotIn(gone, native)

    def test_wasi_suite_runs_under_a_pinned_checksummed_wasmtime_with_the_repository_mounted(self):
        import yaml_lite
        workflow = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())
        self.assertIs(workflow['on']['workflow_call']['inputs']['wasi_tests']['default'], True)
        job = workflow['jobs']['test-wasi']
        self.assertEqual(job['if'], 'inputs.wasi_tests')
        steps = {step.get('name'): step for step in job['steps']}
        install = next(step for name, step in steps.items() if name and name.startswith('Install wasmtime '))
        version = install['env']['WASMTIME_VERSION']
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        self.assertEqual(install['name'], f'Install wasmtime {version}')
        self.assertRegex(install['env']['WASMTIME_SHA256'], r'^[0-9a-f]{64}$')
        self.assertIn('sha256sum -c -', install['run'])
        self.assertIn('releases/download/v$WASMTIME_VERSION/', install['run'])
        run = steps['cargo test for wasm32-wasip1 under wasmtime']['run']
        self.assertIn('--dir $GITHUB_WORKSPACE --dir $target', run)
        self.assertIn('cargo test --target wasm32-wasip1', run)

    def test_every_composite_job_checks_out_the_executing_forge_revision(self):
        for name in ('hyper-build-native.yml', 'hyper-build-wasm.yml',
                     'hyper-build-wheels.yml', 'hyper-build-static.yml',
                     'hyper-test-swift-static.yml'):
            path = WORKFLOWS / name
            self.assertTrue(path.exists(), name)
            text = path.read_text()
            self.assertNotRegex(text, r'uses: SkunkWerkx/\.github/\.github/actions/')
            jobs = re.split(r'\n  [\w-]+:\n', text.split('\njobs:\n', 1)[1])
            for body in jobs:
                if 'uses: ./.hyperforge/.github/actions/' not in body:
                    continue
                self.assertIn('ref: ${{ job.workflow_sha }}', body)
                self.assertIn('path: .hyperforge', body)
                self.assertLess(body.index('ref: ${{ job.workflow_sha }}'),
                                body.index('uses: ./.hyperforge/.github/actions/'))

    def test_every_third_party_action_is_pinned_to_a_commit(self):
        # A tag can be moved after the fact; a commit cannot. The trailing comment is the
        # version Dependabot reads and rewrites when it bumps the pin.
        uses = re.compile(r'^\s*(?:- )?uses:\s*(\S+)(.*)$', re.M)
        for path in sorted(WORKFLOWS.parent.glob('**/*.yml')):
            for ref, comment in uses.findall(path.read_text()):
                if ref.startswith('./'):
                    continue
                with self.subTest(path=path.name, ref=ref):
                    self.assertRegex(ref, r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$')
                    self.assertRegex(comment, r'^ # \S+$')

    def test_missing_or_mutable_forge_revision_fails_before_checkout(self):
        text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        self.assertIn('- name: Validate Forge workflow revision', text)
        tail = text.split('- name: Validate Forge workflow revision\n', 1)[1]
        block = tail.split('        run: |\n', 1)[1]
        lines = []
        for line in block.splitlines():
            if line and not line.startswith('          '):
                break
            lines.append(line[10:])
        script = '\n'.join(lines)
        for sha, wanted in (('a' * 40, 0), ('', 1), ('master', 1), ('a' * 39, 1)):
            result = subprocess.run(['bash', '-c', script],
                                    env={**os.environ, 'FORGE_WORKFLOW_SHA': sha},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, wanted, (sha, result.stderr))

    def test_packers_and_publishers_require_the_forge_revision_before_checking_it_out(self):
        # The reusable packers and publishers check the Forge out by job.workflow_sha; an
        # empty or moved value there would run whatever master holds, so each refuses first.
        guard = '      - name: Require the exact executing Forge revision\n'
        checkout = '      - name: Check out the exact executing Forge revision\n'
        for name in ('hyper-pack-crate.yml', 'hyper-pack-maven.yml', 'hyper-pack-nuget.yml',
                     'hyper-publish-crate.yml', 'hyper-publish-maven.yml'):
            text = (WORKFLOWS / name).read_text()
            with self.subTest(workflow=name):
                self.assertEqual(text.count(checkout), 1)
                self.assertEqual(text.count(guard), 1)
                self.assertLess(text.index(guard), text.index(checkout))
                block = text.split(guard, 1)[1].split('\n\n', 1)[0]
                self.assertIn('FORGE_SHA: ${{ job.workflow_sha }}', block)
                self.assertIn('FORGE_REPOSITORY: ${{ job.workflow_repository }}', block)
                self.assertIn('[[ "$FORGE_SHA" =~ ^[0-9a-f]{40}$ ]]', block)
                self.assertIn('[ "$FORGE_REPOSITORY" = SkunkWerkx/.github ]', block)

    def test_native_leaves_the_static_android_and_swift_static_producers_to_the_caller(self):
        import yaml_lite
        text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        workflow = yaml_lite.load(text)
        inputs = workflow['on']['workflow_call']['inputs']
        # Every caller runs these producers as top-level jobs of its own, so that Apple and
        # Android tests start when their artifacts exist; a nested copy here has no user.
        for gone in ('static_libs_artifact', 'swift_static_tests', 'android_shared_artifact',
                     'apple_mobile', 'android', 'swift_floor_version'):
            self.assertNotIn(gone, inputs)
            self.assertNotIn(f'inputs.{gone}', text)
        self.assertEqual(set(workflow['jobs']),
                         {'build-native', 'build-native-osx-x64', 'build-native-musl',
                          'build-php-extension'})
        for producer in ('hyper-build-static.yml', 'hyper-build-android.yml',
                         'hyper-test-swift-static.yml'):
            self.assertNotIn(f'uses: ./.github/workflows/{producer}', text)
        # Local suites still build their own fresh archives.
        local = text.split('- name: Build this leg\'s static libraries', 1)[1].split('\n      #', 1)[0]
        self.assertIn('inputs.static_libs', local)

    def test_extracted_producers_keep_packaging_artifact_contracts(self):
        static = WORKFLOWS / 'hyper-build-static.yml'
        android = WORKFLOWS / 'hyper-build-android.yml'
        self.assertTrue(static.exists())
        self.assertTrue(android.exists())
        self.assertIn('upload: "true"', static.read_text())
        text = android.read_text()
        for value in ('android-arm64', 'android-x64', 'max-page-size=16384',
                      'name: native-${{ matrix.rid }}', 'actions/attest-build-provenance@'):
            self.assertIn(value, text)


class PrepareReleaseContracts(unittest.TestCase):
    def setUp(self):
        self.text = (WORKFLOWS / 'hyper-prepare-release.yml').read_text()

    def test_node_is_set_up_only_when_a_typescript_package_exists(self):
        self.assertRegex(self.text, r"(?s)- uses: actions/setup-node@[0-9a-f]{40} # v[\d.]+\n"
                         r"\s+if: hashFiles\('typescript/package.json'\) != ''\n"
                         r"\s+with:\n\s+node-version: '24'")
        self.assertLess(self.text.index('actions/setup-node@'),
                        self.text.index('Bump every committed manifest'))

    def test_npm_lock_is_regenerated_only_when_package_json_exists(self):
        self.assertIn('if [ -f typescript/package.json ]; then (cd typescript && '
                      'npm install --package-lock-only --ignore-scripts); fi', self.text)

    def test_r_wrapper_lock_is_neither_staged_nor_regenerated_nor_committed(self):
        # r/tools/vendor.sh --stage derives r/src/rust/Cargo.lock from rust/Cargo.lock
        # wherever the R package is built; it is gitignored, so prepare-release has nothing
        # to regenerate there and nothing to commit. The header names why; no step touches it.
        import yaml_lite
        steps = yaml_lite.load(self.text)['jobs']['prepare-release']['steps']
        runs = '\n'.join(s.get('run', '') for s in steps)
        self.assertNotIn('vendor.sh', runs)
        self.assertNotIn('r/src/rust', runs)
        self.assertIn('never committed', self.text.split('\non:', 1)[0])

    def test_companion_loop_names_the_three_crate_dirs(self):
        self.assertIn('for companion in rust/fuzz rust/browser-test typescript; do',
                      self.text)

    def test_commit_step_adds_the_new_locks_under_the_existence_guard(self):
        self.assertIn('for lock in rust/fuzz/Cargo.lock rust/browser-test/Cargo.lock '
                      'typescript/package-lock.json typescript/Cargo.lock; do\n'
                      '            if [ -f "$lock" ]; then git add "$lock"; fi', self.text)

    def test_napi_wrappers_are_rebuilt_after_the_bump_and_committed(self):
        # napi bakes package.json's version into binding.js's version-mismatch check, so a
        # bump without a rebuild fails every Node leg's wrapper guard on the release-mode run.
        import yaml_lite
        steps = yaml_lite.load(self.text)['jobs']['prepare-release']['steps']
        names = [s.get('name', '') for s in steps]
        bump = names.index('Bump every committed manifest to the version')
        commit = names.index('Commit and push if anything actually changed')
        build = [i for i, s in enumerate(steps)
                 if '(cd typescript && npm ci --ignore-scripts && npm run build)' in s.get('run', '')]
        self.assertEqual(len(build), 1)
        step = steps[build[0]]
        self.assertEqual(step['if'], "hashFiles('typescript/package.json') != ''")
        self.assertLess(bump, build[0])
        self.assertLess(build[0], commit)
        add = 'if [ -f typescript/package.json ]; then git add typescript/binding.js typescript/binding.d.ts; fi'
        self.assertIn(add, steps[commit]['run'])
        self.assertLess(steps[commit]['run'].index(add),
                        steps[commit]['run'].index('git diff --cached --quiet'))
        self.assertNotIn('.node', steps[commit]['run'])


class BuildNativeContracts(unittest.TestCase):
    """The TypeScript (napi-rs) and R legs: off by default, so a caller without either
    binding runs exactly the steps it ran before, and guarded like every other binding."""
    BASE = "matrix.shard != 'swift-ruby' && !cancelled() && steps.native_lib.conclusion == 'success'"
    WRAPPERS = ('git diff --exit-code -- typescript/index.js typescript/index.d.ts '
                'typescript/binding.js typescript/binding.d.ts')

    @classmethod
    def setUpClass(cls):
        import yaml_lite
        cls.text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        cls.workflow = yaml_lite.load(cls.text)
        cls.inputs = cls.workflow['on']['workflow_call']['inputs']
        cls.jobs = cls.workflow['jobs']
        cls.suite = (WORKFLOWS.parent / 'actions' / 'musl-suite' / 'suite-musl.sh').read_text()

    def steps(self, job):
        return self.jobs[job]['steps']

    @staticmethod
    def describe(step):
        return ' '.join(str(step.get(k, '')) for k in ('name', 'uses', 'run', 'working-directory', 'with'))

    def test_new_inputs_exist_with_their_defaults(self):
        wanted = {'node': False, 'r': False, 'node_version': '26',
                  'node_floor_versions': '22 24', 'bun_version': 'latest',
                  'r_version': 'release', 'r_floor_version': 'oldrel'}
        for name, default in wanted.items():
            with self.subTest(input=name):
                self.assertIn(name, self.inputs)
                self.assertIs(self.inputs[name]['required'], False)
                self.assertEqual(self.inputs[name]['default'], default)
                self.assertIn('description', self.inputs[name])
        self.assertEqual(self.inputs['node']['type'], 'boolean')
        self.assertEqual(self.inputs['r']['type'], 'boolean')
        self.assertIn('every language the consumer enables', self.inputs['mode']['description'])
        self.assertNotIn('eight languages', self.inputs['mode']['description'])

    def test_every_node_step_in_the_matrix_carries_the_binding_guard(self):
        node = [s for s in self.steps('build-native')
                if re.search(r'setup-node|setup-bun|typescript|node-\$\{\{', self.describe(s))]
        self.assertGreaterEqual(len(node), 5)
        for step in node:
            with self.subTest(step=step.get('name') or step.get('uses')):
                self.assertIn(self.BASE, step['if'])
                self.assertIn('inputs.node', step['if'])
        for step in node:
            if step.get('id') != 'node_addon' and 'setup-node' not in step.get('uses', ''):
                with self.subTest(after_build=step.get('name') or step.get('uses')):
                    self.assertIn("steps.node_addon.conclusion == 'success'", step['if'])
        bun = [s for s in node if 'setup-bun' in self.describe(s) or 'test:bun' in self.describe(s)]
        self.assertEqual(len(bun), 2)
        for step in bun:
            self.assertIn("matrix.rid != 'win-arm64'", step['if'])
        upload = next(s for s in node if s.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['with']['name'], 'node-${{ matrix.rid }}')
        self.assertEqual(upload['with']['path'], 'typescript/*.node')
        attest = next(s for s in node if s.get('uses', '').startswith('actions/attest-build-provenance@'))
        self.assertIn("inputs.mode != 'pr'", attest['if'])

    def test_every_r_step_carries_the_binding_guard_and_skips_win_arm64(self):
        r = [s for s in self.steps('build-native')
             if re.search(r'r-lib/actions|vendor\.sh|\bR\b', self.describe(s))]
        self.assertGreaterEqual(len(r), 4)
        for step in r:
            with self.subTest(step=step.get('name') or step.get('uses')):
                self.assertIn(self.BASE, step['if'])
                self.assertIn('inputs.r', step['if'])
                self.assertIn("matrix.rid != 'win-arm64'", step['if'])
        check = [s for s in r if s.get('uses', '').startswith('r-lib/actions/check-r-package@')]
        self.assertEqual(len(check), 2)
        for step in check:
            self.assertEqual(step['with']['working-directory'], 'r')
            self.assertEqual(step['with']['args'], 'c("--no-manual", "--as-cran")')
            self.assertEqual(step['with']['error-on'], '"note"')
        floor = [s for s in r if 'r_floor_version' in s['if']]
        self.assertGreaterEqual(len(floor), 2)
        for step in floor:
            self.assertIn("matrix.rid == 'linux-x64'", step['if'])
            self.assertIn('inputs.r_floor_version != inputs.r_version', step['if'])
        self.assertIn('bash r/tools/vendor.sh\n', self.text)
        self.assertIn('R on Windows ARM64 is experimental', self.text)

    def test_the_wrapper_guard_follows_every_napi_build(self):
        builds = 0
        for job in self.jobs.values():
            for step in job.get('steps', []):
                run = step.get('run', '')
                for match in re.finditer(r'npm run build|napi build', run):
                    builds += 1
                    self.assertIn(self.WRAPPERS, run[match.end():], run)
        self.assertEqual(builds, 2)
        for napi in re.findall(r'napi build[^\n]*', self.text):
            self.assertIn('--js binding.js --dts binding.d.ts', napi)

    def test_osx_x64_cross_builds_the_addon_and_tests_it_under_rosetta(self):
        steps = self.steps('build-native-osx-x64')
        text = '\n'.join(self.describe(s) for s in steps)
        self.assertIn('npx napi build --release --platform --target x86_64-apple-darwin '
                      '--js binding.js --dts binding.d.ts', text)
        self.assertIn('lipo -archs', text.split('napi build', 1)[1])
        x64 = [s for s in steps if s.get('uses', '').startswith('actions/setup-node@')
               and s.get('with', {}).get('architecture') == 'x64']
        self.assertEqual(len(x64), 1)
        names = [s.get('with', {}).get('name') for s in steps]
        self.assertIn('node-osx-x64', names)
        for step in steps:
            if re.search(r'setup-node|typescript|node-osx-x64', self.describe(step) + str(step.get('with', ''))):
                self.assertIn('inputs.node', step.get('if', ''), step)

    def test_musl_job_runs_the_node_suite_and_its_floors(self):
        steps = self.steps('build-native-musl')
        suite = [s for s in steps if s.get('with', {}).get('binding') == 'node']
        self.assertEqual(len(suite), 1)
        self.assertEqual(suite[0]['with']['image'], 'node:${{ inputs.node_version }}-alpine')
        self.assertIn('inputs.node', suite[0]['if'])
        upload = next(s for s in steps if s.get('with', {}).get('name') == 'node-${{ matrix.rid }}')
        self.assertEqual(upload['with']['path'], 'out-musl/*.node')
        self.assertIn('inputs.node', upload['if'])
        floors = next(s for s in steps if 'NODE_FLOOR_VERSIONS' in s.get('env', {}))
        self.assertIn('inputs.node', floors['if'])
        self.assertIn('suite-musl.sh', floors['run'])
        self.assertLess(steps.index(upload), steps.index(floors))

    def test_suite_musl_has_a_node_arm_that_guards_the_wrappers(self):
        self.assertRegex(self.suite, r'(?m)^# <binding> is one of: .*\bnode\b')
        arm = self.suite.split('\n  node)\n', 1)[1].split('\n    ;;\n', 1)[0]
        self.assertIn('node:${NODE_VERSION:-26}-alpine', arm)
        self.assertIn('-C target-feature=-crt-static -L native=/unwind', arm)
        self.assertIn('npm ci && npm run build && npm test', arm)
        after = arm.split('npm run build', 1)[1]
        self.assertIn('for wrapper in index.js index.d.ts binding.js binding.d.ts; do\n'
                      '        cmp "$wrapper" "/src/typescript/$wrapper" || '
                      '{ echo "::error::napi build rewrote typescript/$wrapper"; exit 1; }\n'
                      '      done', after)
        self.assertIn('readelf -d', arm)
        self.assertNeededGateIsExact(arm)
        self.assertIn('oven/bun:alpine', arm)
        self.assertIn('bun test tests/', arm)

    def assertNeededGateIsExact(self, script):
        # The count as well as the name: "libc.musl-"*".so.1 " alone also matches
        # "libc.musl-aarch64.so.1 libgcc_s.so.1 ".
        self.assertIn('set -- $needed\n', script)
        self.assertRegex(script, r'case "\$#:\$\{1:-\}" in\n\s+"1:libc\.musl-"\*"\.so\.1"\) ;;\n'
                                 r'\s+\*\) echo "::error::')
        self.assertNotIn('"libc.musl-"*".so.1 ")', script)

    def test_build_musl_needed_gate_counts_as_well_as_names(self):
        self.assertNeededGateIsExact(
            (WORKFLOWS.parent / 'actions' / 'musl-native' / 'build-musl.sh').read_text())

    def test_linux_gnu_addon_glibc_floor_is_printed_and_never_fails(self):
        step = next(s for s in self.steps('build-native')
                    if 'GLIBC_' in str(s.get('run', '')) and '.node' in str(s.get('run', '')))
        self.assertIn("runner.os == 'Linux'", step['if'])
        self.assertIn('steps.node_addon.conclusion == \'success\'', step['if'])
        self.assertIn('::group::', step['run'])
        self.assertNotIn('exit 1', step['run'])
        self.assertIs(step.get('continue-on-error'), True)


class CheckCoreLintContracts(unittest.TestCase):
    def test_every_new_run_step_names_its_shell(self):
        import yaml_lite
        core = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())
        native = yaml_lite.load((WORKFLOWS / 'hyper-build-native.yml').read_text())
        steps = core['jobs']['lint-node']['steps'] + core['jobs']['lint-r']['steps'] + [
            s for s in native['jobs']['build-native-osx-x64']['steps']
            if 'inputs.node' in str(s.get('if', ''))]
        runs = [s for s in steps if 'run' in s]
        self.assertGreaterEqual(len(runs), 8)
        for step in runs:
            with self.subTest(run=step['run'][:40]):
                self.assertIn('shell', step)

    def test_node_and_r_lints_are_off_by_default_and_guarded(self):
        import yaml_lite
        workflow = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())
        inputs = workflow['on']['workflow_call']['inputs']
        for name, job in (('lint_node', 'lint-node'), ('lint_r', 'lint-r')):
            with self.subTest(input=name):
                self.assertIs(inputs[name]['default'], False)
                self.assertEqual(inputs[name]['type'], 'boolean')
                self.assertEqual(workflow['jobs'][job]['if'], f'inputs.{name}')
        node = ' '.join(str(s) for s in workflow['jobs']['lint-node']['steps'])
        for value in ('npm ci', 'npm run lint', 'cargo fmt --check',
                      'cargo clippy --release -- -D warnings'):
            self.assertIn(value, node)
        r_steps = workflow['jobs']['lint-r']['steps']
        r = ' '.join(str(s) for s in r_steps)
        for value in ('r-lib/actions/setup-r@', 'lintr::lint_package("r")',
                      'lintr::lint_dir("r/bench")', 'lintr::lint_dir("r/tools")',
                      'cargo fmt --check --manifest-path r/src/rust/Cargo.toml',
                      'clang-format --dry-run -Werror r/src/*.c r/src/*.h'):
            self.assertIn(value, r)
        # lintr's object_usage linter needs the package installed, or every exported name
        # lints as unknown: the install step precedes the lint step and the lint step reads
        # the scratch library.
        runs = [s.get('run', '') for s in r_steps]
        install = next(i for i, run in enumerate(runs) if 'R CMD INSTALL --library=' in run)
        lint = next(i for i, run in enumerate(runs) if 'lintr::lint_package' in run)
        self.assertLess(install, lint)
        self.assertIn('vendor.sh --stage', runs[install])
        self.assertEqual(r_steps[lint]['env']['R_LIBS'], '${{ runner.temp }}/rlib')


if __name__ == '__main__':
    unittest.main()
