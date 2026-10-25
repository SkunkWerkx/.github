"""The generic A -> B release guard, driven by a consumer's hyper.json."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from test_hyper_config import MINIMAL, write_config

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_guard', SCRIPTS / 'release_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
REPO = 'SkunkWerkx/HyperUuid'
CHANGELOG = '# Changelog\n\n## [Unreleased]\n\n## [0.9.0] - 2026-10-20\n\n### Changed\n\n- A thing.\n\n## [0.8.0] - 2026-09-01\n\n- Older.\n'


def step(path, name):
    """A workflow step's script, by its `- name:`, with no expressions substituted."""
    tail = path.read_text().split(f'      - name: {name}\n')[1]
    body = tail.split('        run: |\n')[1]
    lines = []
    for line in body.splitlines():
        if line and not line.startswith('          '):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines))


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        write_config(self.root)
        self.cfg = guard.config(self.root)
        self.write('rust/Cargo.toml', 'version = "0.9.0"\n')
        self.write('CHANGELOG.md', CHANGELOG)
        self.write('source.txt', 'source\n')
        self.commit('preparation')
        self.a = self.git('rev-parse', 'HEAD')
        self.payload = 'go/staticlib/linux_amd64/libhyperuuid.a'
        for path in guard.payload_paths(self.cfg, Path('artifacts')):
            self.write(path, 'verified bytes')
            if path.startswith('php/'):
                (self.root / path).chmod(0o755)
        self.commit(f'stage\n\nRelease-Source-SHA: {self.a}\nRelease-CI-Run-ID: 42\nRelease-Forge-SHA: {"f" * 40}')
        self.b = self.git('rev-parse', 'HEAD')
        self.run = dict(id=42, head_sha=self.a, head_branch='master',
                        path='.github/workflows/ci.yml', event='workflow_dispatch',
                        status='completed', conclusion='success',
                        repository={'full_name': REPO})

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

    def write(self, path, value):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(value)

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def amend_to(self, path, value):
        self.git('reset', '--hard', self.b)
        self.write(path, value)
        self.git('add', '.')
        self.git('commit', '--amend', '--no-edit', '-q')
        return self.git('rev-parse', 'HEAD')

    def gate(self, override='', tag='v0.9.0', commit=None):
        with patch.object(guard, 'api', return_value=self.run):
            return guard.release_record(self.root, self.cfg, commit or self.b, tag, override, REPO)

    def test_positive_parent_and_run(self):
        self.assertEqual(self.gate(), (self.a, '42'))
        self.assertEqual(self.gate('42'), (self.a, '42'))

    def test_reject_incorrect_run(self):
        for key, value in [('id', 43), ('head_sha', 'f' * 40), ('path', '.github/workflows/other.yml'),
                           ('event', 'pull_request'), ('status', 'in_progress'),
                           ('conclusion', 'failure'), ('head_branch', 'other')]:
            with self.subTest(key=key), patch.dict(self.run, {key: value}):
                with self.assertRaises(ValueError):
                    self.gate()
        with self.assertRaises(ValueError):
            self.gate('43')

    def test_reject_wrong_tag(self):
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.gate(tag='v0.8.0')

    def prepared_with(self, changelog):
        """A fresh A (with this CHANGELOG, or none) and its staging child B."""
        self.git('reset', '--hard', self.a)
        if changelog is None:
            self.git('rm', '-q', 'CHANGELOG.md')
        else:
            self.write('CHANGELOG.md', changelog)
        self.git('add', '-A')
        self.git('commit', '--amend', '--no-edit', '-q')
        a = self.git('rev-parse', 'HEAD')
        for path in guard.payload_paths(self.cfg, Path('artifacts')):
            self.write(path, 'verified bytes')
            if path.startswith('php/'):
                (self.root / path).chmod(0o755)
        self.commit(f'stage\n\nRelease-Source-SHA: {a}\nRelease-CI-Run-ID: 42')
        self.run['head_sha'] = a
        return a, self.git('rev-parse', 'HEAD')

    def test_tag_requires_its_changelog_section_but_a_branch_dispatch_does_not(self):
        a, b = self.prepared_with(CHANGELOG.replace('## [0.9.0] - 2026-10-20', '## [Unreleased]'))
        with self.assertRaisesRegex(ValueError, r'no "## \[0.9.0\]" section'):
            self.gate(commit=b)
        self.assertEqual(self.gate(commit=b, tag=''), (a, '42'))
        a, b = self.prepared_with('## [0.9.0]\n\n## [0.8.0]\n- old\n')
        with self.assertRaisesRegex(ValueError, 'no "## '):
            self.gate(commit=b)
        a, b = self.prepared_with(None)
        with self.assertRaisesRegex(ValueError, 'CHANGELOG.md is missing'):
            self.gate(commit=b)

    def test_tag_refuses_a_swift_dependency_on_a_revision_or_branch(self):
        pinned = ('.package(url: "https://github.com/SkunkWerkx/HyperCast",\n'
                  '         revision: "525ef572843e7be955a94fd4b705382948adb2af")')
        for manifest in ('Package.swift', 'swift/Package.swift'):
            self.git('reset', '--hard', self.a)
            self.write(manifest, f'let package = Package(dependencies: [\n    {pinned}\n])\n')
            a, b = self.prepared_with(CHANGELOG)
            with self.assertRaisesRegex(ValueError, rf'{manifest}: \.package\(url'):
                self.gate(commit=b)
            self.assertEqual(self.gate(commit=b, tag=''), (a, '42'))
        self.assertEqual(guard.unversioned_swift_pins(
            '.package(url: "u", branch: "main")\n.package(url: "v", from: "1.0.0")\n'
            '.package(path: "../")\n.package(url: "w", exact: "1.0.0")'),
            ['.package(url: "u", branch: "main")'])

    def test_changelog_section_is_the_body_of_exactly_one_heading(self):
        self.assertEqual(guard.changelog_section(CHANGELOG, '0.9.0'), '### Changed\n\n- A thing.')
        self.assertEqual(guard.changelog_section(CHANGELOG, '0.8.0'), '- Older.')
        self.assertIsNone(guard.changelog_section(CHANGELOG, '0.9.1'))
        self.assertIsNone(guard.changelog_section(CHANGELOG, 'Unreleased'))

    def test_reject_unrelated_source_or_workflow_change(self):
        for path in ['source.txt', '.github/workflows/ci.yml',
                     'swift/HyperUuidCore.artifactbundle/include/hyperuuid.h',
                     'go/staticlib/linux_amd64/unexpected.a']:
            with self.subTest(path=path):
                bad = self.amend_to(path, 'unrelated')
                with self.assertRaises(ValueError):
                    self.gate(commit=bad)

    def test_reject_nonparent_source(self):
        self.write('another', 'new source')
        self.commit(f'stage\n\nRelease-Source-SHA: {self.a}\nRelease-CI-Run-ID: 42')
        self.b = self.git('rev-parse', 'HEAD')
        with self.assertRaises(ValueError):
            self.gate()

    def test_reject_branch_advancement(self):
        with patch.object(guard, 'git', return_value=self.b), self.assertRaises(ValueError):
            guard.require_master(self.root, self.a)
        with patch.object(guard, 'git', return_value=self.a):
            guard.require_master(self.root, self.a)

    def test_compare_all_artifact_bytes_and_missing_artifact(self):
        artifacts = self.root / 'artifacts'
        paths = {self.payload: artifacts / 'original.a'}
        paths[self.payload].parent.mkdir()
        paths[self.payload].write_text('verified bytes')
        guard.compare_payloads(self.root, paths)
        paths[self.payload].write_text('wrong run bytes')
        with self.assertRaises(ValueError):
            guard.compare_payloads(self.root, paths)
        paths[self.payload].unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            guard.compare_payloads(self.root, paths)

    def test_wrong_source_attestation_is_rejected(self):
        def gh(args, **kwargs):
            self.assertIn('--source-digest', args)
            self.assertEqual(args[args.index('--source-digest') + 1], self.a)
            self.assertIn('--repo', args)
            self.assertEqual(args[args.index('--signer-repo') + 1], 'SkunkWerkx/.github')
            raise subprocess.CalledProcessError(1, args, 'wrong source')
        with patch.object(guard.subprocess, 'run', side_effect=gh), self.assertRaises(subprocess.CalledProcessError):
            guard.verify_attestation(self.root / self.payload, self.a, REPO)

    def test_reject_missing_payload_even_when_it_did_not_change(self):
        # A staging record must describe a complete tree, including unchanged paths.
        self.git('rm', '-q', 'go/staticlib/windows_arm64/libhyperuuid.a')
        self.git('commit', '--amend', '--no-edit', '-q')
        self.b = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'missing staged payload'):
            self.gate()

    def test_reject_payload_with_wrong_git_mode(self):
        self.git('update-index', '--chmod=+x', self.payload)
        self.git('commit', '--amend', '--no-edit', '-q')
        self.b = self.git('rev-parse', 'HEAD')
        with self.assertRaises(ValueError):
            self.gate()

    def test_reject_duplicate_trailer_even_when_one_value_is_invalid(self):
        self.git('commit', '--amend', '-qm',
                 f'stage\n\nRelease-Source-SHA: {self.a}\nRelease-CI-Run-ID: 42\nRelease-CI-Run-ID: invalid')
        self.b = self.git('rev-parse', 'HEAD')
        with self.assertRaises(ValueError):
            self.gate()

    def test_record_cli_checks_full_tag_ref_and_supports_branch_dispatch(self):
        output = self.root / 'outputs'
        env = {'GITHUB_REPOSITORY': REPO, 'GITHUB_SHA': self.b,
               'GITHUB_OUTPUT': str(output), 'GITHUB_REF': 'refs/heads/master'}
        before = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, env, clear=True), patch.object(guard, 'api', return_value=self.run), \
                 patch.object(sys, 'argv', ['release_guard.py', 'record']):
                guard.main()
                self.assertEqual(output.read_text(), f'source_sha={self.a}\nrun_id=42\n')
                os.environ['GITHUB_REF'] = 'refs/tags/v0.8.0'
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    guard.main()
        finally:
            os.chdir(before)

    def test_paths_mode_prints_every_committed_payload_for_the_inventory(self):
        output = subprocess.check_output([sys.executable, str(SCRIPTS / 'release_guard.py'), 'paths'],
                                         cwd=self.root, text=True)
        paths = output.split()
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(set(paths), set(guard.payload_paths(self.cfg, Path('artifacts'))))
        self.assertIn('php/src/native/linux-x64/libhyperuuid.so', paths)
        self.assertIn('swift/HyperUuidCoreApple.xcframework/Info.plist', paths)
        # 7 PHP libraries, 13 Go and 11 Swift archives, two metadata files, three slices x 3.
        self.assertEqual(len(paths), 7 + 13 + 11 + 2 + 9)


class AbiProbeTests(unittest.TestCase):
    """The consumer's C probe is compiled against the bundle header and linked with a payload."""
    def setUp(self):
        if not shutil.which('cc'):
            self.skipTest('needs a C compiler')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        write_config(self.root)
        self.cfg = guard.config(self.root)
        include = self.root / self.cfg.bundle / 'include'
        include.mkdir(parents=True)
        (include / 'hyperuuid.h').write_text('#include <stdint.h>\nuint32_t hyperuuid_version(void);\nint32_t uuid_probe(uint8_t *p);\n')
        probe = self.root / self.cfg.abi_probe
        probe.parent.mkdir(parents=True)
        probe.write_text('#include "hyperuuid.h"\nint main(void) {\n'
                         '  if (hyperuuid_version() != EXPECTED_VERSION) return 1;\n'
                         '  uint8_t b[1] = {7}; if (uuid_probe(b) != 5) return 2; return 0; }\n')

    def fixture(self, version, rejected):
        c = self.root / 'fixture.c'
        c.write_text(f'#include <stdint.h>\nuint32_t hyperuuid_version(void) {{return {version};}}\n'
                     f'int32_t uuid_probe(uint8_t *p) {{return {rejected};}}\n')
        obj, shared, archive = self.root / 'f.o', self.root / 'f.so', self.root / 'f.a'
        subprocess.run(['cc', '-fPIC', '-c', str(c), '-o', str(obj)], check=True)
        subprocess.run(['cc', '-shared', str(obj), '-o', str(shared)], check=True)
        archive.unlink(missing_ok=True)
        subprocess.run(['ar', 'rcs', str(archive), str(obj)], check=True)
        return shared, archive

    def test_version_and_return_contract_through_shared_and_static_payloads(self):
        for version, rejected, accepted in [('0x000900', 5, True), ('0x000800', 5, False), ('0x000900', 0, False)]:
            for payload in self.fixture(version, rejected):
                with self.subTest(version=version, rejected=rejected, payload=payload.suffix):
                    if accepted:
                        guard.check_runtime(self.root, self.cfg, payload, '0.9.0')
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            guard.check_runtime(self.root, self.cfg, payload, '0.9.0')

    def test_missing_probe_is_an_error_not_a_pass(self):
        (self.root / self.cfg.abi_probe).unlink()
        shared, _ = self.fixture('0x000900', 5)
        with self.assertRaisesRegex(ValueError, 'probe'):
            guard.check_runtime(self.root, self.cfg, shared, '0.9.0')


class PlatformSymbolsTests(unittest.TestCase):
    def test_missing_and_removed_exports_across_object_formats(self):
        if not shutil.which('clang') or not shutil.which('llvm-nm'):
            self.skipTest('cross-format fixtures require clang and llvm-nm')
        cfg = guard.hyper_config.HyperConfig(MINIMAL)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / 'fixture.c'
            for target in ('x86_64-unknown-linux-gnu', 'aarch64-apple-darwin',
                           'x86_64-pc-windows-msvc', 'wasm32-wasi'):
                for names in ({'hyperuuid_version', 'uuid_new_v7'},
                              {'hyperuuid_version', 'uuid_new_v7', 'uuid_new_v6'},
                              {'hyperuuid_version'}):
                    source.write_text('\n'.join(f'int {name}(void) {{ return 0; }}' for name in names))
                    obj, archive = tmp / 'fixture.o', tmp / 'fixture.a'
                    archive.unlink(missing_ok=True)
                    subprocess.run(['clang', '-target', target, '-c', str(source), '-o', str(obj)], check=True)
                    subprocess.run(['llvm-ar', 'rcs', str(archive), str(obj)], check=True)
                    expected = {'hyperuuid_version', 'uuid_new_v7'}
                    with self.subTest(target=target, names=names):
                        if names == expected:
                            guard.check_symbols(archive, expected, cfg)
                        else:
                            with self.assertRaises(ValueError):
                                guard.check_symbols(archive, expected, cfg)


class StagingIntegrationTests(unittest.TestCase):
    def test_positive_stages_exact_downloaded_bytes_and_rejects_incomplete_set(self):
        if not shutil.which('lld-link') or not shutil.which('llvm-nm') or not shutil.which('sha256sum'):
            self.skipTest('DLL fixture requires LLVM lld-link and llvm-nm, the script sha256sum')
        if sys.platform == 'darwin':
            self.skipTest('cc -shared builds Mach-O here, which the ELF-oriented fixture cannot use; validate.yml runs this on Ubuntu')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            cfg = guard.config(root)
            (root / 'rust/src').mkdir(parents=True)
            (root / 'rust/Cargo.toml').write_text('version = "0.9.0"\n')
            exports = {'hyperuuid_version', 'uuid_v7_to_sql_order', 'uuid_new_v7'}
            (root / 'rust/src/ffi.rs').write_text(''.join(f'pub extern "C" fn {n}() {{}}\n' for n in exports))
            include = root / cfg.bundle / 'include'
            include.mkdir(parents=True)
            (include / 'hyperuuid.h').write_text('#include <stdint.h>\nuint32_t hyperuuid_version(void);\n'
                                                 'int32_t uuid_v7_to_sql_order(uint8_t *p);\n')
            (include / 'module.modulemap').write_text('module HyperUuidCore {}\n')
            probe = root / cfg.abi_probe
            probe.parent.mkdir(parents=True)
            probe.write_text('#include "hyperuuid.h"\nint main(void) { uint8_t p[8] = {0};\n'
                             '  return hyperuuid_version() == EXPECTED_VERSION && uuid_v7_to_sql_order(p) == 5 ? 0 : 1; }\n')
            source = root / 'fixture.c'
            source.write_text('''#include <stdint.h>
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
EXPORT uint32_t hyperuuid_version(void) {return 0x000900;}
EXPORT int32_t uuid_v7_to_sql_order(uint8_t *p) {return 5;}
EXPORT int uuid_new_v7(void) {return 0;}
''')
            obj, shared, archive, dll = [root / name for name in ('f.o', 'f.so', 'f.a', 'f.dll')]
            subprocess.run(['cc', '-fPIC', '-c', str(source), '-o', str(obj)], check=True)
            subprocess.run(['cc', '-shared', str(obj), '-o', str(shared)], check=True)
            subprocess.run(['ar', 'rcs', str(archive), str(obj)], check=True)
            subprocess.run(['clang', '-target', 'x86_64-pc-windows-msvc', '-ffreestanding',
                            '-fuse-ld=lld', '-nostdlib', '-shared', '-Wl,/noentry',
                            str(source), '-o', str(dll)], check=True)
            paths = guard.payload_paths(cfg, root / 'artifacts')
            for dest, src in paths.items():
                src.parent.mkdir(parents=True, exist_ok=True)
                if src.suffix in ('.a', '.lib'):
                    shutil.copyfile(archive, src)
                elif src.suffix == '.dll':
                    shutil.copyfile(dll, src)
                elif src.suffix in ('.so', '.dylib'):
                    shutil.copyfile(shared, src)
                else:
                    src.write_text('generated metadata')
            # Staging also verifies the Windows ARM library PHP never ships.
            extra = root / 'artifacts/native/native-win-arm64/hyperuuid.dll'
            extra.parent.mkdir(parents=True)
            shutil.copyfile(dll, extra)
            sha = 'a' * 40
            with patch.object(guard, 'verify_attestation') as verify:
                guard.validate_payloads(root, cfg, paths, sha, REPO)
                self.assertEqual(verify.call_count, 34)
            script = step(SCRIPTS.parent / 'workflows/hyper-stage-natives.yml',
                          'Verify attestations, then stage into php/, swift/, go/')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for key, value in (('commit.gpgsign', 'false'), ('user.name', 'Test'), ('user.email', 'test@example.com')):
                subprocess.run(['git', '-C', str(root), 'config', key, value], check=True)
            subprocess.run(['git', '-C', str(root), 'add', 'rust', '.github', cfg.bundle + '/include'], check=True)
            subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'source A'], check=True)
            source_sha = guard.git(root, 'rev-parse', 'HEAD')
            gh = root / 'bin/gh'
            gh.parent.mkdir()
            gh.write_text('#!/bin/sh\ncase "$*" in *"--source-digest ' + sha + '"*) test -f "$3";; *) exit 1;; esac\n')
            gh.chmod(0o755)
            env = {**os.environ, 'PATH': f'{gh.parent}:{os.environ["PATH"]}', 'SOURCE_SHA': sha,
                   'GITHUB_REPOSITORY': REPO, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                   'FORGE_SCRIPTS': str(SCRIPTS)}
            result = subprocess.run(['bash', '-c', script], cwd=root, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)
            guard.compare_payloads(root, paths)
            subprocess.run(['git', '-C', str(root), 'add', 'php/src/native', cfg.bundle, cfg.xcframework, 'go/staticlib'], check=True)
            guard.validate_staging_index(root, cfg, source_sha)
            shipped = root / 'go/staticlib/linux_amd64/libhyperuuid.a'
            original_bytes = shipped.read_bytes()
            shipped.write_bytes(b'copied wrong artifact')
            subprocess.run(['git', '-C', str(root), 'add', str(shipped)], check=True)
            with self.assertRaisesRegex(ValueError, 'do not match the recorded CI run'):
                guard.validate_staging_index(root, cfg, source_sha)
            shipped.write_bytes(original_bytes)
            subprocess.run(['git', '-C', str(root), 'add', str(shipped)], check=True)
            with self.assertRaisesRegex(ValueError, 'not based on preparation'):
                guard.validate_staging_index(root, cfg, 'f' * 40)
            manifest = root / 'rust/Cargo.toml'
            manifest.write_text(manifest.read_text() + '\n# unrelated change\n')
            subprocess.run(['git', '-C', str(root), 'add', 'rust/Cargo.toml'], check=True)
            with self.assertRaisesRegex(ValueError, 'unrelated A -> B change'):
                guard.validate_staging_index(root, cfg, source_sha)
            self.assertEqual(len((root / 'digests.txt').read_text().splitlines()), 35)
            missing = paths['go/staticlib/linux_amd64/libhyperuuid.a']
            missing.unlink()
            with patch.object(guard, 'verify_attestation'), self.assertRaisesRegex(ValueError, 'missing artifact'):
                guard.validate_payloads(root, cfg, paths, sha, REPO)


class DispatchTests(unittest.TestCase):
    def test_dispatch_uses_master_and_checks_this_dispatch_head(self):
        with patch.dict(sys.modules, {'release_guard': guard}):
            spec = importlib.util.spec_from_file_location('dispatch_preparation', SCRIPTS / 'dispatch_preparation.py')
            dispatch = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dispatch)
        sha = 'a' * 40
        run = dict(id=42, head_sha=sha, head_branch='master', path='.github/workflows/ci.yml',
                   event='workflow_dispatch', repository={'full_name': REPO})
        with patch.object(dispatch.subprocess, 'check_output', return_value='42\n') as post, \
             patch.object(dispatch, 'api', return_value=run), \
             patch.object(dispatch.subprocess, 'run') as cancel:
            self.assertEqual(dispatch.dispatch(REPO, sha), '42')
            self.assertIn('ref=master', post.call_args.args[0])
            run['head_sha'] = 'b' * 40
            with self.assertRaises(ValueError):
                dispatch.dispatch(REPO, sha)
            cancel.assert_called_once_with(['gh', 'run', 'cancel', '42', '--repo', REPO], check=True)

    def test_prepare_release_bumps_every_version_file_and_dispatches_without_a_gate(self):
        workflow = SCRIPTS.parent / 'workflows/hyper-prepare-release.yml'
        bump = step(workflow, 'Bump every committed manifest to the version')
        push = step(workflow, 'Commit and push if anything actually changed')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            files = {'rust/Cargo.toml': '[package]\nname = "hyperuuid"\nversion = "0.9.0"\nrust-version = "1.85"\n',
                     'csharp/HyperUuid/HyperUuid.csproj': '<Project><Version>0.9.0</Version></Project>\n',
                     'java/build.gradle.kts': 'version = System.getenv("HYPERUUID_VERSION") ?: "0.9.0"\n',
                     'ruby/hyperuuid.gemspec': 'spec.version = "0.9.0"\n',
                     'ruby/lib/hyperuuid.rb': 'module HyperUuid\n  VERSION = "0.9.0"\nend\n',
                     'rust/browser-test/Cargo.lock': 'lock\n', 'rust/Cargo.lock': 'lock\n'}
            for path, text in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text)
            cargo = root / 'bin/cargo'
            cargo.parent.mkdir()
            cargo.write_text('#!/bin/sh\necho "$@" >> cargo.log\ncase "$1" in update) echo bumped >> rust/browser-test/Cargo.lock;; check) echo bumped >> rust/Cargo.lock;; esac\n')
            cargo.chmod(0o755)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'commit.gpgsign=false', '-c', 'user.name=Test',
                            '-c', 'user.email=test@example.com', 'commit', '-qm', 'A'], check=True)
            env = {**os.environ, 'PATH': f'{cargo.parent}:{os.environ["PATH"]}', 'VERSION': '1.0.0',
                   'FORGE_SCRIPTS': str(SCRIPTS), 'GITHUB_OUTPUT': str(root / 'outputs'),
                   'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
            result = subprocess.run(['bash', '-c', bump], cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in list(files)[:5]:
                self.assertIn('1.0.0', (root / path).read_text(), path)
                self.assertNotIn('0.9.0', (root / path).read_text(), path)
            self.assertEqual((root / 'rust/Cargo.toml').read_text().count('1.0.0'), 1)  # not rust-version
            log = (root / 'cargo.log').read_text()
            self.assertIn('check --manifest-path rust/Cargo.toml', log)
            self.assertIn('update --manifest-path rust/browser-test/Cargo.toml --package hyperuuid', log)
            self.assertNotIn('rust/fuzz', log)
            # A push without a remote fails after the commit; the commit itself is what is checked.
            push = push.replace('git push origin HEAD:master', 'true')
            result = subprocess.run(['bash', '-c', push], cwd=root, env={**env, 'VERSION': '1.0.0'}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            self.assertEqual((root / 'outputs').read_text().strip(), f'source_sha={sha}')
            subject = subprocess.check_output(['git', '-C', str(root), 'log', '-1', '--format=%s'], text=True).strip()
            self.assertEqual(subject, 'chore: prepare v1.0.0')
            staged = subprocess.check_output(['git', '-C', str(root), 'show', '--stat', '--format=', 'HEAD'], text=True)
            self.assertIn('rust/browser-test/Cargo.lock', staged)
        dispatch_step = workflow.read_text().split('      - name: Run ci.yml against the preparation source\n')[1]
        self.assertNotIn('if:', dispatch_step.split('\n\n')[0])
        self.assertIn('dispatch_preparation.py', dispatch_step)


if __name__ == '__main__':
    unittest.main()
