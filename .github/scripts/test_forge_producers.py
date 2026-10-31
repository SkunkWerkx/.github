"""Regression contracts for isolated Forge producers and immutable action sources."""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import unittest

WORKFLOWS = Path(__file__).resolve().parents[1] / 'workflows'


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f'{name}.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PieAcceptanceContracts(unittest.TestCase):
    """hyper-pie-acceptance.yml: PIE installs the released zips on every platform and minor, from
    a leg holding PHP and the pinned PIE only, then a fresh PHP checks the extension."""
    PATH = WORKFLOWS / 'hyper-pie-acceptance.yml'

    def setUp(self):
        import yaml_lite
        self.text = self.PATH.read_text()
        self.workflow = yaml_lite.load(self.text)
        self.steps = self.workflow['jobs']['install']['steps']

    def step(self, name):
        return next(step for step in self.steps if step.get('name') == name)

    def test_called_and_dispatched_with_the_same_inputs(self):
        on = self.workflow['on']
        self.assertEqual(set(on), {'workflow_call', 'workflow_dispatch'})
        called, dispatched = on['workflow_call']['inputs'], on['workflow_dispatch']['inputs']
        self.assertEqual(list(called), ['package', 'extension', 'package_version', 'repository', 'minors'])
        self.assertEqual(list(dispatched), list(called))
        for name in called:
            with self.subTest(input=name):
                self.assertEqual(dispatched[name], called[name])
        self.assertEqual(called['repository']['default'], '')
        self.assertIs(called['extension']['required'], True)
        self.assertNotIn('default', called['extension'])

    def test_minors_move_with_the_build_and_the_inventory(self):
        php_ext_package = load_script('php_ext_package')
        minors = ' '.join(php_ext_package.MINORS)
        import yaml_lite
        native = yaml_lite.load((WORKFLOWS / 'hyper-build-native.yml').read_text())['on']['workflow_call']['inputs']
        extension = yaml_lite.load((WORKFLOWS / 'hyper-build-php-extension.yml').read_text())['on']['workflow_call']['inputs']
        self.assertEqual(self.workflow['on']['workflow_call']['inputs']['minors']['default'], minors)
        self.assertEqual(native['php_versions']['default'], minors)
        self.assertEqual(extension['php_versions']['default'], minors)

    def test_every_platform_on_every_minor(self):
        run = next(s['run'] for s in self.workflow['jobs']['legs']['steps'] if s.get('id') == 'matrix')
        self.assertIn('php_ext_package.py matrix --mode release --versions "$MINORS" --floor \'\'', run)
        self.assertIn('^[0-9]+\\.[0-9]+\\.[0-9]+$', run)
        install = self.workflow['jobs']['install']
        self.assertEqual(install['strategy']['matrix'], '${{ fromJSON(needs.legs.outputs.matrix) }}')
        self.assertIs(install['strategy']['fail-fast'], False)

    def test_the_leg_holds_php_and_the_pinned_pie_only(self):
        fetch = self.step('Fetch PIE, and verify its checksum and attestation')
        self.assertIn('php_ext_package.py" pie-pin', fetch['run'])
        self.assertIn('gh attestation verify "$phar" --owner php', fetch['run'])
        self.assertNotRegex(self.text, r'[0-9a-f]{64}')
        # The only checkout is the Forge's own: nothing of the consumer's but its Release.
        checkouts = [s for job in self.workflow['jobs'].values() for s in job['steps']
                     if s.get('uses', '').startswith('actions/checkout@')]
        self.assertEqual(len(checkouts), 2)
        for checkout in checkouts:
            self.assertEqual(checkout['with']['path'], '.hyperforge')
        self.assertNotIn('composer install', self.text)
        self.assertNotIn('cargo', self.text)
        self.assertIn('tools: none', self.text)

    def test_both_install_steps_run_the_leg_script_with_the_bare_version(self):
        for name in ('Install with PIE, then check from a fresh PHP',
                     'Install with PIE, then check from a fresh PHP (Alpine)'):
            with self.subTest(step=name):
                run = self.step(name)['run']
                self.assertRegex(run, r'pie_acceptance\.sh"? "\$[A-Za-z_]+" "\$[A-Za-z_]+" "?\$?/?[A-Za-z_.]+"? '
                                      r'"\$PACKAGE" "\$EXTENSION" "\$PACKAGE_VERSION" "\$REPOSITORY" "\$ENTRY_SHA256"')
        self.assertIn('alpine:3.24', self.step('Install with PIE, then check from a fresh PHP (Alpine)')['run'])
        script = (WORKFLOWS.parent / 'scripts/pie_acceptance.sh').read_text()
        self.assertIn('release_tag="v$package_version"', script)
        self.assertIn('install --no-interaction "$@" "$package:$release_tag"', script)
        self.assertIn('repository:add "$@" vcs "$repository"', script)
        self.assertIn('"$target_php" "$here/pie_acceptance.php" "$extension" "$package_version" "$entry_sha256"', script)
        self.assertIn('-e EXTENSION ', self.step('Install with PIE, then check from a fresh PHP (Alpine)')['run'])
        self.assertIn('-e ENTRY_SHA256 ', self.step('Install with PIE, then check from a fresh PHP (Alpine)')['run'])
        self.assertEqual(self.workflow['jobs']['install']['env']['EXTENSION'], '${{ inputs.extension }}')

    def test_the_installed_file_is_the_release_zips_entry(self):
        # Before PIE runs, each leg downloads its own zip from the Release, verifies the release
        # attestation over it against a real release, and hands its entry's sha256 to the checks.
        fetch = self.step("Fetch this leg's zip from the Release, and verify it")
        run = fetch['run']
        self.assertIn('php_ext_package.py" asset --rid "$RID" --minor "$MINOR" --version "$tag" --extension "$EXTENSION"', run)
        self.assertIn('tag="v$PACKAGE_VERSION"', run)
        self.assertIn('gh release download "$tag" --repo "$release_repo" --pattern "$name"', run)
        self.assertIn('gh attestation verify "$zip" --repo "$release_repo" --source-ref "refs/tags/$tag"', run)
        self.assertIn('--predicate-type https://github.com/SkunkWerkx/.github/predicates/release/v1', run)
        self.assertIn('if [ -z "$REPOSITORY" ]; then', run)
        self.assertIn('release_repo=$GITHUB_REPOSITORY', run)
        self.assertIn('echo "ENTRY_SHA256=$sha256" >> "$GITHUB_ENV"', run)
        self.assertEqual(fetch['env']['GH_TOKEN'], '${{ github.token }}')
        names = [step.get('name') for step in self.steps]
        for install in ('Install with PIE, then check from a fresh PHP',
                        'Install with PIE, then check from a fresh PHP (Alpine)'):
            self.assertLess(names.index("Fetch this leg's zip from the Release, and verify it"), names.index(install))
        self.assertEqual(self.workflow['jobs']['install']['permissions']['attestations'], 'read')
        php = (WORKFLOWS.parent / 'scripts/pie_acceptance.php').read_text()
        self.assertIn("hash_file('sha256', $file) === $entrySha256", php)
        self.assertIn("ini_get('extension_dir')", php)

    def test_both_install_steps_authenticate_composer_to_github_without_logging_it(self):
        auth = '\'{"github-oauth":{"github.com":"${{ github.token }}"}}\''
        for name in ('Install with PIE, then check from a fresh PHP',
                     'Install with PIE, then check from a fresh PHP (Alpine)'):
            with self.subTest(step=name):
                self.assertEqual(self.step(name)['env']['COMPOSER_AUTH'], auth.strip("'"))
        self.assertIn('-e COMPOSER_AUTH ', self.step('Install with PIE, then check from a fresh PHP (Alpine)')['run'])
        self.assertEqual(self.text.count('COMPOSER_AUTH'), 3)
        self.assertNotRegex(self.text, r'(echo|printf|cat)[^\n]*COMPOSER_AUTH')
        script = (WORKFLOWS.parent / 'scripts/pie_acceptance.sh').read_text()
        self.assertNotIn('COMPOSER_AUTH', script)
        self.assertNotIn('set -x', script)

    def test_the_checks_are_the_corpus_vectors(self):
        php = (WORKFLOWS.parent / 'scripts/pie_acceptance.php').read_text()
        self.assertIn("const V5_EXPECT = '2ed6657d-e927-568b-95e1-2665a8aea6a2';", php)
        self.assertIn("const SQL_ORDER = '07060504-0908-0080-8000-017f22e279b0';", php)
        self.assertIn('phpversion($extension) === $version', php)
        self.assertNotIn('ini_set', php)
        # The extension is an argument; only the API checks are HyperUuid's, and another
        # extension fails rather than passing on checks that aren't its own.
        self.assertNotIn("const EXTENSION", php)
        self.assertIn("if ($extension !== 'hyperuuid') {", php)


class PhpExtensionContracts(unittest.TestCase):
    """hyper-build-php-extension.yml builds, tests and packs the PIE zips; nothing else builds
    PHP, and the ext-ffi suites are gone."""
    PATH = WORKFLOWS / 'hyper-build-php-extension.yml'
    MUSL = WORKFLOWS.parent / 'scripts/php_ext_musl.sh'

    def setUp(self):
        import yaml_lite
        self.text = self.PATH.read_text()
        self.workflow = yaml_lite.load(self.text)
        self.jobs = self.workflow['jobs']
        self.steps = self.jobs['build']['steps']

    def step(self, name):
        return next(step for step in self.steps if step.get('name') == name)

    def test_inputs(self):
        inputs = self.workflow['on']['workflow_call']['inputs']
        self.assertEqual(set(inputs), {'crate', 'mode', 'php_versions', 'php_floor_version', 'attest_native'})
        self.assertEqual(inputs['php_versions']['default'], '8.3 8.4 8.5')
        self.assertEqual(inputs['mode']['default'], 'release')

    def test_version_job_reads_the_crate_version_at_the_commit_and_derives_the_tag(self):
        version = self.jobs['version']
        self.assertEqual(set(version['outputs']), {'package_version', 'release_tag', 'matrix', 'env_prefix'})
        run = next(step['run'] for step in version['steps'] if step.get('id') == 'version')
        self.assertIn('cargo metadata --no-deps --format-version 1', run)
        self.assertIn('.packages[] | select(.name == $crate) | .version', run)
        self.assertIn("^[0-9]+\\.[0-9]+\\.[0-9]+$", run)
        self.assertIn('release_tag=v$package_version', run)
        matrix = next(step['run'] for step in version['steps'] if step.get('id') == 'matrix')
        self.assertIn('php_ext_package.py" matrix --mode "$MODE" --versions "$PHP_VERSIONS" --floor "$PHP_FLOOR_VERSION"', matrix)
        build = self.jobs['build']
        self.assertEqual(build['needs'], 'version')
        self.assertEqual(build['strategy']['matrix'], '${{ fromJSON(needs.version.outputs.matrix) }}')
        self.assertIs(build['strategy']['fail-fast'], False)

    def test_the_tag_reaches_only_the_packer_and_the_bare_version_only_the_load_check(self):
        texts = (self.text, self.MUSL.read_text())
        for text in texts:
            text = text.replace('\\\n', ' ')  # shell line continuations
            packs = re.findall(r'php_ext_package\.py"? pack[^\n]*', text)
            self.assertTrue(packs)
            for call in packs:
                self.assertIn('--version "$RELEASE_TAG"', call)
            for call in re.findall(r'php_ext_load_check\.php"? [^\n]*', text):
                self.assertTrue(call.rstrip().endswith('"$CRATE" "$PACKAGE_VERSION"'), call)
        self.assertEqual(set(re.findall(r'RELEASE_TAG: (.*)', self.text)), {'${{ needs.version.outputs.release_tag }}'})
        self.assertEqual(set(re.findall(r'PACKAGE_VERSION: (.*)', self.text)),
                         {'${{ needs.version.outputs.package_version }}'})
        # release_tag is written once, as "v" + package_version, and read only as the job output.
        rest = self.text
        for known in ('needs.version.outputs.release_tag', 'steps.version.outputs.release_tag',
                      'release_tag=v$package_version', '      release_tag:', '`release_tag`'):
            rest = rest.replace(known, '')
        self.assertNotIn('release_tag', rest)
        check = (WORKFLOWS.parent / 'scripts/php_ext_load_check.php').read_text()
        self.assertIn('phpversion($crate)', check)
        self.assertIn('!== $want', check)

    def test_rustc_bootstrap_is_set_once_on_the_windows_build_step(self):
        code = '\n'.join(line for line in self.text.splitlines() if not line.lstrip().startswith('#'))
        self.assertEqual(code.count('RUSTC_BOOTSTRAP'), 1)
        steps = [step for step in self.steps if 'RUSTC_BOOTSTRAP' in (step.get('env') or {})]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['env']['RUSTC_BOOTSTRAP'], '1')
        self.assertEqual(steps[0]['if'], "runner.os == 'Windows'")
        self.assertIn('abi_vectorcall', self.text)
        self.assertIn('rust-lang/rust#124485', self.text)
        for other in ('hyper-build-native.yml', 'hyper-build-wheels.yml', 'hyper-build-static.yml'):
            self.assertNotIn('RUSTC_BOOTSTRAP', (WORKFLOWS / other).read_text(), other)
        self.assertNotIn('RUSTC_BOOTSTRAP', self.MUSL.read_text())

    def test_phpunit_runs_on_every_leg(self):
        test = self.step('Test the extension (composer test)')
        self.assertEqual(test['if'], "matrix.kind != 'musl'")
        self.assertIn('tools/run-tests.php', test['run'])
        self.assertIn('export "${ENV_PREFIX}_PHP_EXTENSION=$EXTENSION"', test['run'])
        self.assertIn('if [ "$STUBS" = true ]; then export "${ENV_PREFIX}_CHECK_STUBS=1"; fi', test['run'])
        self.assertEqual(test['env']['STUBS'], '${{ matrix.stubs }}')
        self.assertEqual(self.jobs['build']['env']['ENV_PREFIX'], '${{ needs.version.outputs.env_prefix }}')
        musl = self.MUSL.read_text()
        self.assertIn('php tools/run-tests.php', musl)
        self.assertEqual(self.step('Build, load-check, test and pack on Alpine')['if'], "matrix.kind == 'musl'")

    DERIVE = "printf '%s' \"$CRATE\" | tr '[:lower:]-' '[:upper:]_'"

    def test_no_crate_name_is_hard_coded_in_the_suite_variables(self):
        for path in (self.PATH, self.MUSL):
            with self.subTest(path=path.name):
                self.assertNotIn('HYPERUUID_', path.read_text())

    def test_the_suite_variable_prefix_is_derived_from_the_crate_the_same_way_in_both(self):
        version = next(s for s in self.jobs['version']['steps'] if s.get('id') == 'version')
        self.assertIn(f'env_prefix=$({self.DERIVE})', version['run'])
        self.assertEqual(self.jobs['version']['outputs']['env_prefix'],
                         '${{ steps.version.outputs.env_prefix }}')
        musl = self.MUSL.read_text()
        self.assertIn('env_prefix=$(' + self.DERIVE.replace('$CRATE', '$crate') + ')', musl)
        self.assertIn('-e "${env_prefix}_PHP_EXTENSION=/ext/$crate.so"', musl)
        for crate, prefix in (('hyperuuid', 'HYPERUUID'), ('hypercast', 'HYPERCAST'),
                              ('hyper-tabular', 'HYPER_TABULAR')):
            with self.subTest(crate=crate):
                out = subprocess.run(['bash', '-c', self.DERIVE], env={'CRATE': crate, 'PATH': os.environ['PATH']},
                                     capture_output=True, text=True, check=True).stdout
                self.assertEqual(out, prefix)

    def test_each_leg_loads_packs_and_uploads_its_zip_and_release_attests_it(self):
        load = self.step('Load-check the extension, and record the ABI it is pinned to')
        self.assertEqual(load['if'], "matrix.kind != 'musl'")
        self.assertIn('-d "extension=$EXTENSION"', load['run'])
        upload = next(step for step in self.steps if str(step.get('uses', '')).startswith('actions/upload-artifact@'))
        self.assertEqual(upload['with']['name'], 'php-ext-${{ matrix.rid }}-${{ matrix.minor }}')
        self.assertEqual(upload['with']['path'], 'php-dist/*.zip')
        attest = next(step for step in self.steps if str(step.get('uses', '')).startswith('actions/attest-build-provenance@'))
        self.assertIn("inputs.attest_native && inputs.mode == 'release'", attest['if'])
        self.assertEqual(attest['with']['subject-path'], 'php-dist/*.zip')
        glibc = self.step('glibc floor — nothing newer than GLIBC_2.34 is referenced')
        self.assertEqual(glibc['if'], "matrix.kind == 'glibc'")

    def test_pie_comes_from_the_packer_pin_checked_by_hash_and_attestation_before_any_pack(self):
        fetch = self.step('Fetch PIE, and verify its checksum and attestation')
        self.assertIn('php_ext_package.py" pie-pin', fetch['run'])
        self.assertIn('gh attestation verify "$phar" --owner php', fetch['run'])
        self.assertEqual(fetch['env']['GH_TOKEN'], '${{ github.token }}')
        self.assertNotRegex(self.text, r'[0-9a-f]{64}')
        names = [step.get('name') for step in self.steps]
        self.assertLess(names.index('Fetch PIE, and verify its checksum and attestation'),
                        names.index('Build, load-check, test and pack on Alpine'))
        self.assertLess(names.index('Fetch PIE, and verify its checksum and attestation'),
                        names.index('Pack the PIE zip'))

    def test_the_build_is_the_crate_php_ext_alias(self):
        for name in ('Build the extension', 'Build the extension (Windows)'):
            self.assertIn('cargo php-ext', self.step(name)['run'], name)
        self.assertIn('cargo php-ext', self.MUSL.read_text())
        # `--features php` is clippy's only (below); every build is the alias.
        self.assertEqual(self.text.count('--features php'), 1)

    def test_clippy_runs_over_the_php_feature_on_the_stub_leg(self):
        # check-core lints every other feature set but can't build this one (PHP headers and
        # libclang); the stub leg has both.
        clippy = self.step('Clippy over the extension (the php feature)')
        self.assertEqual(clippy['if'], 'matrix.stubs')
        self.assertEqual(clippy['working-directory'], 'rust')
        self.assertEqual(clippy['run'], 'cargo clippy --features php -- -D warnings')
        toolchain = next(step for step in self.steps
                         if str(step.get('uses', '')).startswith('dtolnay/rust-toolchain@'))
        self.assertEqual(toolchain['with']['components'], "${{ matrix.stubs && 'clippy' || '' }}")
        names = [step.get('name') for step in self.steps]
        self.assertLess(names.index('Stub generator prerequisites (cargo-php, libclang)'),
                        names.index('Clippy over the extension (the php feature)'))
        self.assertLess(names.index('Point bindgen at one system LLVM, not at Swift\'s clang'),
                        names.index('Clippy over the extension (the php feature)'))

    def test_the_packer_is_told_the_crate_as_the_extension(self):
        for text in (self.text, self.MUSL.read_text()):
            text = text.replace('\\\n', ' ')
            packs = re.findall(r'php_ext_package\.py"? pack[^\n]*', text)
            self.assertTrue(packs)
            for call in packs:
                self.assertIn('--extension "$CRATE"', call)
        for name in ('php_ext_package.py', 'pie_asset_names.php', 'pie_acceptance.sh'):
            self.assertNotIn("'hyperuuid'", (WORKFLOWS.parent / 'scripts' / name).read_text(), name)
        self.assertNotIn('--exclude=php_ext.rs;', (WORKFLOWS / 'hyper-check-core.yml').read_text())
        self.assertIn('--exclude=php_ext.rs --exclude-dir=php_ext', (WORKFLOWS / 'hyper-check-core.yml').read_text())

    def test_native_calls_it_in_both_modes_and_runs_no_php_suite(self):
        native = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        self.assertIn('  build-php-extension:\n    if: inputs.php\n', native)
        job = native.split('  build-php-extension:\n', 1)[1]
        for line in ('mode: ${{ inputs.mode }}', 'php_versions: ${{ inputs.php_versions }}',
                     'php_floor_version: ${{ inputs.php_floor_version }}'):
            self.assertIn(line, job)
        import yaml_lite
        inputs = yaml_lite.load(native)['on']['workflow_call']['inputs']
        self.assertNotIn('php_version', inputs)
        self.assertEqual(inputs['php_versions']['default'], '8.3 8.4 8.5')
        suite = (WORKFLOWS.parent / 'actions/musl-suite/suite-musl.sh').read_text()
        for text in (native, suite):
            for gone in ('ext-ffi', 'ffi.enable', 'phpunit', 'setup-php', 'php/src/native', '--features php'):
                self.assertNotIn(gone, text)
        self.assertNotIn('\n  php)', suite)


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
                     'hyper-test-swift-static.yml', 'hyper-test-c.yml'):
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


class GoTranslatedCoreContracts(unittest.TestCase):
    """The Go binding carries the core as committed Go source translated by wasm2go: no cgo,
    no committed archive, one test step per leg right after the Rust steps, and its wasm
    suites and core-freshness check in the jobs that own those toolchains."""
    @classmethod
    def setUpClass(cls):
        import yaml_lite
        cls.native_text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        cls.native = yaml_lite.load(cls.native_text)
        cls.wasm_text = (WORKFLOWS / 'hyper-build-wasm.yml').read_text()
        cls.wasm = yaml_lite.load(cls.wasm_text)
        cls.check_text = (WORKFLOWS / 'hyper-check-core.yml').read_text()
        cls.check = yaml_lite.load(cls.check_text)
        cls.prepare_text = (WORKFLOWS / 'hyper-prepare-release.yml').read_text()
        cls.prepare = yaml_lite.load(cls.prepare_text)

    @staticmethod
    def describe(step):
        return ' '.join(str(step.get(k, '')) for k in ('name', 'uses', 'run', 'working-directory', 'with', 'env'))

    def test_go_runs_without_cgo_right_after_the_rust_compile_steps(self):
        steps = self.native['jobs']['build-native']['steps']
        go = [i for i, s in enumerate(steps) if s.get('working-directory') == 'go' and 'go test' in s.get('run', '')]
        self.assertEqual(len(go), 1, 'one Go test step on the desktop legs')
        step = steps[go[0]]
        self.assertEqual(step['env']['CGO_ENABLED'], '0')
        self.assertIn('inputs.go', step['if'])
        collect = next(i for i, s in enumerate(steps) if s.get('id') == 'native_lib')
        first_java = next(i for i, s in enumerate(steps) if 'setup-java' in s.get('uses', ''))
        self.assertLess(collect, go[0])
        self.assertLess(go[0], first_java, 'Go runs ahead of every binding that has already shipped')
        setup = steps[go[0] - 1]
        self.assertIn('actions/setup-go@', setup['uses'])
        self.assertEqual(setup['with']['go-version-file'], 'go/go.mod')
        self.assertNotIn('llvm-mingw', self.native_text)
        # On Alpine the suite runs once, on the floor image go.mod names, with CGO_ENABLED=0 and
        # no C toolchain: nothing links, so the run proves the OS source and the CPU-feature read
        # on musl. It leads the musl suites as it leads the desktop job's.
        musl = self.native['jobs']['build-native-musl']['steps']
        go_musl = [i for i, s in enumerate(musl) if str(s.get('with', {}).get('binding')) == 'go']
        self.assertEqual(len(go_musl), 1)
        self.assertEqual(musl[go_musl[0]]['with']['image'], '${{ steps.images.outputs.go }}')
        self.assertIn('inputs.go', musl[go_musl[0]]['if'])
        images = next(i for i, s in enumerate(musl) if s.get('id') == 'images')
        first_other = next(i for i, s in enumerate(musl) if 'on Alpine' in s.get('name', '') and i != go_musl[0] and i > images)
        self.assertLess(images, go_musl[0])
        self.assertLess(go_musl[0], first_other, 'Go leads the musl suites')
        self.assertIn('go=golang:$(sed -n \'s/^go \\([0-9]*\\.[0-9]*\\).*/\\1/p\' go/go.mod)-alpine', self.native_text)
        self.assertNotIn('build-base', self.native_text)
        self.assertNotIn('go/staticlib', self.native_text)
        self.assertNotIn('CGO_ENABLED: "1"', self.native_text)

    def test_go_wasm_suite_runs_three_runtimes_and_the_browser_page(self):
        inputs = self.wasm['on']['workflow_call']['inputs']
        self.assertIs(inputs['go_wasm_suite']['type'] == 'boolean' and inputs['go_wasm_suite']['default'], False)
        self.assertEqual(inputs['go_tinygo_smoke']['default'], '')
        self.assertNotIn('test-go-tinygo', self.wasm['jobs'])
        job = self.wasm['jobs']['test-go-wasm']
        self.assertEqual(job['if'], "inputs.go_wasm_suite || inputs.go_tinygo_smoke != ''")
        text = ' '.join(self.describe(s) for s in job['steps'])
        for needle in ('tinygo test -target=wasip1 -c', 'wasmtime run --dir', '_CORPUS=', '_CRATE_VERSION=',
                       '"$GITHUB_WORKSPACE::/repo"', 'GOOS=js GOARCH=wasm go test', 'GOOS=wasip1 GOARCH=wasm go test',
                       'internal/exhaustiontest', 'sha256sum -c -', 'go_js_wasm_exec', 'env -i PATH='):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertNotIn('static-libs', text)
        self.assertNotIn('go/staticlib', self.wasm_text)
        names = [s.get('name', '') for s in job['steps']]
        control = next(s for s in job['steps'] if 'without --dir' in s.get('name', ''))
        self.assertIn('not found', control['run'])
        suite = [s for s in job['steps'] if 'if: ' not in s and s.get('run') and 'GOOS=' in s['run']]
        self.assertTrue(all('if' not in s or 'go_wasm_suite' in s['if'] for s in suite))
        browser = next(s for s in job['steps'] if 'headless Chrome' in s.get('name', ''))
        self.assertEqual(browser['if'], "inputs.go_tinygo_smoke != ''")
        self.assertIn('tinygo build -target=wasm', browser['run'])
        self.assertEqual(names.index(browser['name']), len(names) - 1)
        setup_go = next(s for s in job['steps'] if 'actions/setup-go@' in s.get('uses', ''))
        self.assertEqual(setup_go['with']['go-version-file'], 'go/go.mod')
        wasmtime = next(s for s in job['steps'] if s.get('name', '').startswith('Install wasmtime '))
        self.assertRegex(wasmtime['env']['WASMTIME_SHA256'], r'^[0-9a-f]{64}$')

    def test_lint_go_sweeps_checks_the_core_and_races(self):
        steps = self.check['jobs']['lint-go']['steps']
        text = ' '.join(self.describe(s) for s in steps)
        self.assertNotIn('check_go_archives', text)
        sweep = next(s for s in steps if s.get('name') == 'Cross-compile sweep')
        self.assertIn('go vet ./...', sweep['run'])
        for pair in ('js wasm', 'wasip1 wasm', 'linux amd64', 'windows arm64', 'darwin arm64', 'android arm64',
                     'linux 386', 'freebsd amd64'):
            with self.subTest(pair=pair):
                self.assertIn(pair, sweep['run'])
        for absent in ('ios arm64', 'android amd64'):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, sweep['run'])
        self.assertIn('CGO_ENABLED=0', sweep['run'])
        self.assertIn('go vet -tags purego ./...', sweep['run'])
        core = next(s for s in steps if s.get('name') == 'The committed core is current')
        self.assertIn('rust-toolchain.toml', core['run'])
        self.assertIn('rustup toolchain install', core['run'])
        self.assertIn('--target wasm32-wasip1', core['run'])
        self.assertIn('_CHECK_CORE=1', core['run'])
        self.assertIn("-run '^(TestCoreIsCurrent|TestRegenerationCarriesTheCrateVersion)$'", core['run'])
        race = next(s for s in steps if s.get('name') == 'Race')
        self.assertIn('go test -race', race['run'])
        self.assertIn('internal/wasm2go', text)
        self.assertIn('revive', text)
        self.assertIn('gofmt', text)

    def test_prepare_release_regenerates_the_go_core(self):
        steps = self.prepare['jobs']['prepare-release']['steps']
        names = [s.get('name', '') for s in steps]
        napi = names.index('Regenerate the napi-rs wrappers at the version')
        regen = names.index('Regenerate the Go core at the version')
        commit = names.index('Commit and push if anything actually changed')
        self.assertLess(napi, regen)
        self.assertLess(regen, commit)
        step = steps[regen]
        self.assertEqual(step['if'], "hashFiles('go/internal/wasm2go/go.mod') != ''")
        self.assertIn('rustup toolchain install', step['run'])
        self.assertIn('go/internal/wasm2go/rust-toolchain.toml', step['run'])
        self.assertIn('(cd go && go generate)', step['run'])
        setup = steps[regen - 1]
        self.assertIn('actions/setup-go@', setup['uses'])
        self.assertEqual(setup['if'], step['if'])
        self.assertEqual(setup['with']['go-version-file'], 'go/go.mod')
        add = 'if [ -f go/internal/wasm2go/go.mod ]; then git add go/internal/core/core.go; fi'
        self.assertIn(add, steps[commit]['run'])
        self.assertLess(steps[commit]['run'].index(add), steps[commit]['run'].index('git diff --cached --quiet'))


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

    def test_big_endian_leg_is_off_by_default_and_guards_itself(self):
        import yaml_lite
        workflow = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())
        inputs = workflow['on']['workflow_call']['inputs']
        self.assertEqual(inputs['big_endian']['default'], '')
        self.assertEqual(inputs['big_endian']['type'], 'string')
        jobs = workflow['jobs']
        job = jobs['test-big-endian']
        self.assertEqual(job['if'], "inputs.big_endian != ''")
        self.assertEqual(list(jobs)[-1], 'test-big-endian')
        steps = job['steps']
        blob = ' '.join(str(s) for s in steps)
        for value in ('--locked', 'grep -qx', 'sql_order', 'FAILED', 'qemu-user', 'libc6-dev-', '-cross', '-L /usr/',
                      'cargo test --locked --release --target', 'sql_order.json'):
            self.assertIn(value, blob)
        for step in (s for s in steps if 'run' in s):
            with self.subTest(run=step['run'][:40]):
                self.assertIn('shell', step)
        guard = next(s['run'] for s in steps if 'arch=' in s.get('run', ''))
        for target, code in (('s390x-unknown-linux-gnu', 0),
                             ('s390x-unknown-linux-musl', 1),
                             ('x86_64-pc-windows-msvc', 1)):
            with self.subTest(target=target):
                done = subprocess.run(
                    ['bash', '-c', guard], capture_output=True, text=True,
                    env={'PATH': os.environ['PATH'], 'TARGET': target,
                         'GITHUB_ENV': os.devnull})
                self.assertEqual(done.returncode, code, done.stdout + done.stderr)


class SdistContracts(unittest.TestCase):
    """The optional Python sdist: built and proven at its floor, then the interpreters with
    no wheel are tried against it without blocking the run."""

    @classmethod
    def setUpClass(cls):
        import yaml_lite
        cls.text = (WORKFLOWS / 'hyper-build-wheels.yml').read_text()
        cls.jobs = yaml_lite.load(cls.text)['jobs']

    @staticmethod
    def blob(job):
        return '\n'.join(
            str(s.get('run', '')) + ' ' + ' '.join(f'{k}: {v}' for k, v in (s.get('with') or {}).items())
            for s in job['steps'])

    def test_sdist_jobs_shape(self):
        jobs = self.jobs
        pin = jobs['build-wheels']['steps'][0]['uses']
        self.assertTrue(pin.startswith('actions/checkout@'))
        for name in ('build-sdist', 'sdist-fallthrough'):
            with self.subTest(job=name):
                first = jobs[name]['steps'][0]
                self.assertEqual(first['uses'], pin)
                self.assertNotIn('with', first)
        config = jobs['sdist-config']
        self.assertIn('sdist', config['outputs'])
        runs = [str(s.get('run', '')) for s in config['steps']]
        validate = next(i for i, s in enumerate(config['steps']) if s.get('name') == 'Validate Forge workflow revision')
        forge = next(i for i, s in enumerate(config['steps']) if s.get('with', {}).get('path') == '.hyperforge')
        self.assertLess(validate, forge)
        self.assertEqual(config['steps'][forge]['with']['ref'], '${{ job.workflow_sha }}')
        self.assertTrue(any('hyper_config.py shell' in r and 'sdist=$SDIST' in r for r in runs))

        build = jobs['build-sdist']
        self.assertEqual(build['needs'], 'sdist-config')
        self.assertEqual(build['if'], "needs.sdist-config.outputs.sdist == 'true'")
        self.assertNotIn('inputs.mode', build['if'])
        for value in ('command: sdist', 'twine check --strict', 'manylinux2014_x86_64',
                      'cp311-cp311', 'python:3.11-alpine', 'requires rustc', '/v/bin/python -I -c'):
            self.assertIn(value, self.blob(build), value)
        self.assertIsNone(re.search(r'(?<![/\w])python -I', self.blob(build)))
        attest = next(s for s in build['steps'] if 'attest-build-provenance' in str(s.get('uses', '')))
        wheels_attest = next(s for s in jobs['build-wheels']['steps']
                             if 'attest-build-provenance' in str(s.get('uses', '')))
        self.assertEqual(attest['if'], wheels_attest['if'])
        self.assertEqual(attest['with']['subject-path'], 'python/dist/*.tar.gz')
        upload = next(s for s in build['steps'] if 'upload-artifact' in str(s.get('uses', '')))
        self.assertEqual(upload['with']['name'], 'pypi-wheels-sdist')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        self.assertNotIn('if', upload)

        fall = jobs['sdist-fallthrough']
        self.assertIs(fall['continue-on-error'], True)
        self.assertEqual(fall['needs'], 'build-sdist')
        self.assertEqual(fall['strategy']['matrix']['python'], ['3.14t', 'pypy3.11'])
        for value in ('GITHUB_STEP_SUMMARY', 'cp -rL python/tests "$RUNNER_TEMP/suite/tests"',
                      'mkdir -p "$RUNNER_TEMP/suite"', '"$RUNNER_TEMP/v/bin/pip" install pytest mypy',
                      '"$RUNNER_TEMP/v/bin/python" -m pytest -rs', '_is_gil_enabled', '__file__'):
            self.assertIn(value, self.blob(fall), value)
        self.assertIsNone(re.search(r'(?<![/\w])python -I', self.blob(fall)))

        for name in ('sdist-config', 'build-sdist', 'sdist-fallthrough'):
            for step in jobs[name]['steps']:
                if 'run' in step:
                    with self.subTest(job=name, step=step.get('name')):
                        self.assertIn('shell', step)

        order = [m.start() for m in (re.search(rf'^  {j}:', self.text, re.M)
                                      for j in ('sdist-config', 'build-sdist', 'sdist-fallthrough', 'build-wheels'))]
        self.assertEqual(order, sorted(order))


class CsharpFrameworkContracts(unittest.TestCase):
    """csharp_aot_frameworks and csharp_extra_runtime: inert when empty, and the C# test step
    takes an absolute project path so a multi-targeted project loads."""

    @classmethod
    def setUpClass(cls):
        import yaml_lite
        cls.text = (WORKFLOWS / 'hyper-build-native.yml').read_text()
        cls.workflow = yaml_lite.load(cls.text)
        cls.inputs = cls.workflow['on']['workflow_call']['inputs']
        cls.steps = cls.workflow['jobs']['build-native']['steps']
        cls.musl_dir = WORKFLOWS.parent / 'actions' / 'musl-suite'
        cls.suite = (cls.musl_dir / 'suite-musl.sh').read_text()
        cls.action = (cls.musl_dir / 'action.yml').read_text()

    def step(self, needle):
        found = [s for jb in self.workflow['jobs'].values() for s in jb.get('steps', [])
                 if needle in str(s.get('name', ''))]
        self.assertEqual(len(found), 1, needle)
        return found[0]

    def test_framework_inputs_default_empty(self):
        for name in ('csharp_aot_frameworks', 'csharp_extra_runtime'):
            with self.subTest(input=name):
                self.assertIn(name, self.inputs)
                self.assertIs(self.inputs[name]['required'], False)
                self.assertEqual(self.inputs[name]['default'], '')
                self.assertEqual(self.inputs[name]['type'], 'string')
                self.assertIn('description', self.inputs[name])
        self.assertIn('NETSDK1129', self.inputs['csharp_aot_frameworks']['description'])
        self.assertIn('every target framework', self.inputs['csharp_extra_runtime']['description'])

    def test_setup_dotnet_adds_the_extra_runtime_only_when_asked(self):
        steps = [s for s in self.steps if 'actions/setup-dotnet' in str(s.get('uses', ''))]
        self.assertEqual(len(steps), 1)
        with_ = steps[0]['with']
        self.assertEqual(with_['global-json-file'], 'global.json')
        self.assertEqual(
            with_['dotnet-version'],
            "${{ inputs.csharp_extra_runtime != '' && format('{0}.x', inputs.csharp_extra_runtime) || '' }}")

    def test_aot_steps_loop_over_frameworks(self):
        publish = self.step('Native AOT publish')['run']
        self.assertIn('${{ inputs.csharp_aot_frameworks }}', publish)
        self.assertIn('-f "$tfm"', publish)
        self.assertIn('aot-out/$tfm', publish)
        self.assertIn('out=aot-out\n', publish)  # the empty list keeps today's single publish
        self.assertIn('-o "$out"', publish)
        self.assertIn('tee -a aot-publish.log', publish)
        self.assertIn('ls "$out" |', publish)
        run = self.step('Run the AOT-published binary')['run']
        self.assertIn('${{ inputs.csharp_aot_frameworks }}', run)
        self.assertIn('aot-out/$tfm/$name', run)
        self.assertIn('exe="aot-out/$name"', run)

    def test_csharp_test_step_uses_an_absolute_project_path(self):
        step = self.step('Test C# bindings')
        self.assertEqual(step['shell'], 'bash')
        self.assertEqual(step['env']['CSHARP_TEST_PROJECT'], '${{ inputs.csharp_test_project }}')
        self.assertIn('--project "$GITHUB_WORKSPACE/$CSHARP_TEST_PROJECT"', step['run'])
        self.assertNotIn('inputs.', step['run'])
        # the musl suite does the same, from /work
        self.assertIn('dotnet test --project "/work/$TEST_PROJECT"', self.suite)

    def test_musl_suite_passes_both_inputs(self):
        step = self.step('C# on Alpine')
        self.assertEqual(step['with']['csharp_aot_frameworks'], '${{ inputs.csharp_aot_frameworks }}')
        self.assertEqual(step['with']['csharp_extra_runtime'], '${{ inputs.csharp_extra_runtime }}')
        for name in ('csharp_aot_frameworks', 'csharp_extra_runtime'):
            self.assertIn(f'  {name}:', self.action)
        self.assertIn('CSHARP_AOT_FRAMEWORKS: ${{ inputs.csharp_aot_frameworks }}', self.action)
        self.assertIn('CSHARP_EXTRA_RUNTIME: ${{ inputs.csharp_extra_runtime }}', self.action)
        self.assertIn('extra_mounts=()', self.suite)
        self.assertIn('for tfm in ${AOT_FRAMEWORKS:--}', self.suite)
        self.assertIn('/work/aot-out/$tfm', self.suite)
        self.assertIn('/extra-runtime', self.suite)
        self.assertIn('mcr.microsoft.com/dotnet/runtime:${CSHARP_EXTRA_RUNTIME}-alpine', self.suite)


class JniContracts(unittest.TestCase):
    """java_jni: off by default, ahead of TypeScript in every job, and the FFM Java steps
    step aside for it."""
    ATTEST = ("inputs.attest_native && inputs.mode != 'pr'", 'github.event.pull_request.head.repo.full_name == github.repository')

    @classmethod
    def setUpClass(cls):
        import yaml_lite
        cls.workflow = yaml_lite.load((WORKFLOWS / 'hyper-build-native.yml').read_text())
        cls.inputs = cls.workflow['on']['workflow_call']['inputs']
        cls.jobs = cls.workflow['jobs']
        cls.core = yaml_lite.load((WORKFLOWS / 'hyper-check-core.yml').read_text())
        cls.maven = [yaml_lite.load((WORKFLOWS / n).read_text())
                     for n in ('hyper-pack-maven.yml', 'hyper-publish-maven.yml')]

    def steps(self, job):
        return self.jobs[job]['steps']

    @staticmethod
    def index(steps, predicate):
        return next(i for i, s in enumerate(steps) if predicate(s))

    def test_jni_inputs_default_off(self):
        for spec, name, default, kind in ((self.inputs, 'java_jni', False, 'boolean'),
                                          (self.inputs, 'jdk_floor_version', '', 'string'),
                                          (self.core['on']['workflow_call']['inputs'], 'lint_java_jni', False, 'boolean')):
            with self.subTest(input=name):
                self.assertIn(name, spec)
                self.assertIs(spec[name]['required'], False)
                self.assertEqual(spec[name]['default'], default)
                self.assertEqual(spec[name]['type'], kind)
                self.assertTrue(spec[name]['description'])
        for workflow in self.maven:
            self.assertIs(workflow['on']['workflow_call']['inputs']['java_jni']['default'], False)

    def test_jni_steps_lead_typescript_in_every_job(self):
        def jni(s):
            return 'inputs.java_jni' in str(s.get('if', ''))
        def node(s):
            return s.get('uses', '').startswith('actions/setup-node@')
        steps = self.steps('build-native')
        first = self.index(steps, jni)
        self.assertGreater(first, self.index(steps, lambda s: s.get('name') == "Build this leg's static libraries"))
        self.assertLess(first, self.index(steps, node))
        steps = self.steps('build-native-osx-x64')
        first = self.index(steps, jni)
        self.assertGreater(first, self.index(steps, lambda s: s.get('name') == 'Attest native build provenance'))
        self.assertLess(first, self.index(steps, node))
        steps = self.steps('build-native-musl')
        suite = self.index(steps, lambda s: s.get('with', {}).get('binding') == 'java-jni')
        self.assertLess(suite, self.index(steps, lambda s: str(s.get('name', '')).startswith('Node on Alpine')))
        self.assertGreater(suite, self.index(steps, lambda s: s.get('name') == "Build this leg's static library"))
        self.assertEqual(steps[suite]['with']['image'], 'eclipse-temurin:${{ inputs.jdk_version }}-jdk-alpine')
        self.assertEqual(steps[suite]['with']['jdk_floor_version'], '${{ inputs.jdk_floor_version }}')
        self.assertIn('inputs.java && inputs.java_jni', steps[suite]['if'])
        native = next(s for s in steps if s.get('uses', '').endswith('actions/musl-native'))
        self.assertEqual(native['with']['jni'], '${{ inputs.java && inputs.java_jni }}')

    def test_ffm_java_steps_step_aside_for_jni(self):
        old = []
        for job in ('build-native', 'build-native-musl'):
            for s in self.steps(job):
                name = str(s.get('name', ''))
                if (s.get('uses', '').startswith('actions/setup-java@') or name.startswith('Place native lib for Java')
                        or name.startswith('Test Java bindings') or name.startswith('Java on Alpine')) \
                        and 'inputs.java_jni' not in str(s.get('if', '')).replace('!inputs.java_jni', ''):
                    old.append(s)
        self.assertEqual(len(old), 4, old)
        for s in old:
            with self.subTest(step=s.get('name') or s.get('uses')):
                self.assertIn('!inputs.java_jni', s['if'])
                self.assertNotIn('steps.jdk_floor', str(s))

    def test_jni_attestations_follow_the_cores_condition(self):
        found = 0
        for job in ('build-native', 'build-native-osx-x64'):
            for s in self.steps(job):
                if s.get('uses', '').startswith('actions/attest-build-provenance@') and 'jni' in str(s.get('with', '')):
                    found += 1
                    for part in self.ATTEST:
                        self.assertIn(part, s['if'], s)
                    self.assertIn('inputs.java_jni', s['if'])
        self.assertEqual(found, 2)
        action = (WORKFLOWS.parent / 'actions' / 'musl-native' / 'action.yml').read_text()
        import yaml_lite
        steps = yaml_lite.load(action)['runs']['steps']
        attest = [s for s in steps if s.get('uses', '').startswith('actions/attest-build-provenance@') and 'jni' in str(s.get('with', ''))]
        self.assertEqual(len(attest), 1)
        self.assertIn("inputs.attest == 'true'", attest[0]['if'])
        self.assertIn("inputs.jni == 'true'", attest[0]['if'])

    def test_jni_build_steps_carry_the_gradle_floor_and_the_export_check(self):
        text = '\n'.join(str(s) for s in self.steps('build-native'))
        for value in ('cargo build --release --locked --lib', 'GLIBC_2.34', 'nm -D --defined-only', 'JNI_OnLoad',
                      'core_exports()', './gradlew test testFloor checkSuiteParity javadoc --console=plain',
                      '-Porg.gradle.java.installations.auto-detect=false', 'jni-${{ matrix.rid }}'):
            self.assertIn(value, text)
        osx = '\n'.join(str(s) for s in self.steps('build-native-osx-x64'))
        for value in ('--target x86_64-apple-darwin', 'lipo -archs', 'os.arch = x86_64', 'jni-osx-x64',
                      './gradlew test testFloor checkSuiteParity javadoc'):
            self.assertIn(value, osx)

    def test_lint_java_jni_runs_the_std_tests_and_the_no_std_build(self):
        job = self.core['jobs']['lint-java-jni']
        self.assertEqual(job['if'], 'inputs.lint_java_jni')
        self.assertEqual(job['runs-on'], 'ubuntu-26.04')
        steps = job['steps']
        runs = [s for s in steps if 'run' in s]
        blob = '\n'.join(s['run'] for s in runs)
        for value in ('cargo fmt --check', 'cargo clippy --release --locked --all-targets -- -D warnings',
                      'cargo test --locked', 'cargo build --release --locked --lib', 'readelf -d', 'libc.so.6',
                      'CARGO_PROFILE_RELEASE_PANIC=unwind cargo build --release --locked --example no_panic --features no-panic',
                      'nm -D --defined-only', 'JNI_OnLoad', "hyper_config.load('../..').core_exports()"):
            self.assertIn(value, blob)
        for s in runs:
            with self.subTest(run=s['run'][:40]):
                self.assertEqual(s['shell'], 'bash')
                self.assertEqual(s['working-directory'], 'java/jni')
        toolchain = next(s for s in steps if s.get('uses', '').startswith('dtolnay/rust-toolchain@'))
        self.assertEqual(toolchain['with']['components'], 'rustfmt, clippy')


class WasmToolchainContracts(unittest.TestCase):
    """hyper-build-wasm.yml builds the archive with build-std on stable, guards it, and links it
    on .NET 10 and 11."""

    @classmethod
    def setUpClass(cls):
        import yaml_lite
        cls.workflow = yaml_lite.load((WORKFLOWS / 'hyper-build-wasm.yml').read_text())

    def steps(self, job):
        return {step.get('name'): step for step in self.workflow['jobs'][job]['steps']}

    def test_build_checks_out_the_executing_forge_for_the_guard(self):
        names = list(self.steps('build-wasm'))
        self.assertLess(names.index('Validate Forge workflow revision'), names.index('Check out the executing Forge revision'))
        self.assertNotIn('outputs', self.workflow['jobs']['build-wasm'])

    def test_build_runs_build_std_on_stable_for_one_step(self):
        steps = self.steps('build-wasm')
        install = next(step for step in self.workflow['jobs']['build-wasm']['steps']
                       if str(step.get('uses', '')).startswith('dtolnay/rust-toolchain@'))
        self.assertNotIn('toolchain', install['with'])
        self.assertEqual(install['with']['components'], 'rust-src')
        self.assertEqual(install['with']['targets'], 'wasm32-unknown-emscripten')
        build = steps['Build as a wasm32-unknown-emscripten static library']
        self.assertIn('cargo wasm-staticlib', build['run'])
        self.assertNotIn('cargo +', build['run'])
        self.assertEqual(build['env'], {'RUSTC_BOOTSTRAP': '1'})
        others = [step.get('name') for job in self.workflow['jobs'].values() for step in job['steps']
                  if 'RUSTC_BOOTSTRAP' in str(step) and step is not build]
        self.assertEqual(others, [])

    def test_guard_runs_on_every_toolchain(self):
        steps = self.steps('build-wasm')
        names = list(steps)
        guard = steps['The static library carries no exception handling and no feature .NET rejects']
        self.assertNotIn('if', guard)
        self.assertIn('python3 "$GITHUB_WORKSPACE/.hyperforge/.github/scripts/wasm_archive_guard.py"', guard['run'])
        self.assertIn('--llvm-bin "$EMSDK/upstream/bin"', guard['run'])
        self.assertLess(names.index('The static library carries no copy of Rust\'s standard library'),
                        names.index('The static library carries no exception handling and no feature .NET rejects'))
        self.assertLess(names.index('The static library carries no exception handling and no feature .NET rejects'),
                        names.index('Attest the static library'))

    def test_blazor_adds_dotnet_10_on_every_toolchain(self):
        steps = self.steps('test-blazor')
        sdks = steps['The .NET SDKs']
        self.assertEqual(sdks['with']['global-json-file'], 'global.json')
        self.assertEqual(sdks['with']['dotnet-version'], '10.0.x')
        self.assertNotIn('The .NET 10 runtime', steps)
        workload = steps['wasm-tools-net10 workload']
        self.assertNotIn('if', workload)
        self.assertEqual(workload['run'], 'dotnet workload install wasm-tools-net10')
        self.assertNotIn('if', steps['wasm-tools workload'])


if __name__ == '__main__':
    unittest.main()
