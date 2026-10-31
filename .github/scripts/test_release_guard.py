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
        self.payload = 'swift/HyperUuidCore.artifactbundle/x86_64-unknown-linux-gnu/libhyperuuid.a'
        for path in guard.payload_paths(self.cfg, Path('artifacts')):
            self.write(path, 'verified bytes')
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
        a, b = self.prepared_with(CHANGELOG.replace('- A thing.', '- Grows by \u27e8n\u27e9 KB.'))
        with self.assertRaisesRegex(ValueError, 'still holds a .placeholder'):
            self.gate(commit=b)
        self.assertEqual(self.gate(commit=b, tag=''), (a, '42'))
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
                     'swift/HyperUuidCore.artifactbundle/x86_64-unknown-linux-gnu/unexpected.a']:
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
        self.git('rm', '-q', 'swift/HyperUuidCore.artifactbundle/aarch64-unknown-windows-msvc/hyperuuid.lib')
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
        self.assertIn('swift/HyperUuidCore.artifactbundle/x86_64-unknown-linux-gnu/libhyperuuid.a', paths)
        self.assertFalse([path for path in paths if path.startswith('php/') or path.startswith('go/')])
        self.assertIn('swift/HyperUuidCoreApple.xcframework/Info.plist', paths)
        # 11 Swift archives, two metadata files, three slices x 3; Go commits no archive.
        self.assertEqual(len(paths), 11 + 2 + 9)


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


SWIFT_MANIFEST = 'let coreRelease = "0.8.0"\nlet coreChecksum = ""\nlet appleCoreChecksum = ""\n'


def staging_fixture(root, data):
    """A consumer checkout at A beside fake CI artifacts for every payload, a stub gh that
    accepts provenance at `sha`, and the staging step's script with its environment."""
    write_config(root, data)
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
    (root / 'Package.swift').write_text(SWIFT_MANIFEST)
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
    for name in cfg.swift_assets:
        zip_path = guard.swift_assets_dir(root) / name
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(f'zip of {name}'.encode())
    # Every RID's shared library: staging verifies each one's provenance, and the guard probes
    # and smokes the linux-x64 one, though none is committed.
    for rid, name in cfg.lib_names.items():
        native = root / 'artifacts/native' / f'native-{rid}' / name
        native.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dll if name.endswith('.dll') else shared, native)
    sha = 'a' * 40
    script = step(SCRIPTS.parent / 'workflows/hyper-stage-natives.yml',
                  'Verify attestations, then stage into swift/')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    for key, value in (('commit.gpgsign', 'false'), ('user.name', 'Test'), ('user.email', 'test@example.com')):
        subprocess.run(['git', '-C', str(root), 'config', key, value], check=True)
    subprocess.run(['git', '-C', str(root), 'add', 'rust', '.github', 'Package.swift', cfg.bundle + '/include'], check=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'source A'], check=True)
    source_sha = guard.git(root, 'rev-parse', 'HEAD')
    gh = root / 'bin/gh'
    gh.parent.mkdir()
    gh.write_text('#!/bin/sh\ncase "$*" in *"--source-digest ' + sha + '"*) test -f "$3";; *) exit 1;; esac\n')
    gh.chmod(0o755)
    env = {**os.environ, 'PATH': f'{gh.parent}:{os.environ["PATH"]}', 'SOURCE_SHA': sha,
           'GITHUB_REPOSITORY': REPO, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
           'FORGE_SCRIPTS': str(SCRIPTS)}
    return cfg, paths, sha, script, env, source_sha


class SharedPayloadTests(unittest.TestCase):
    def test_the_linux_x64_shared_library_is_required_though_nothing_commits_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            (root / 'rust/src').mkdir(parents=True)
            (root / 'rust/Cargo.toml').write_text('version = "0.9.0"\n')
            (root / 'rust/src/ffi.rs').write_text('pub extern "C" fn hyperuuid_version() {}\npub extern "C" fn uuid_new_v7() {}\n')
            cfg = guard.config(root)
            paths = guard.payload_paths(cfg, root / 'artifacts')
            for src in paths.values():
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text('archive')
            with self.assertRaisesRegex(ValueError, 'missing artifact payload: .*native-linux-x64/libhyperuuid.so'):
                guard.validate_payloads(root, cfg, paths, 'a' * 40, REPO, attest=False)


class StagingIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('lld-link') or not shutil.which('llvm-nm') or not shutil.which('sha256sum'):
            self.skipTest('DLL fixture requires LLVM lld-link and llvm-nm, the script sha256sum')
        if sys.platform == 'darwin':
            self.skipTest('cc -shared builds Mach-O here, which the ELF-oriented fixture cannot use; validate.yml runs this on Ubuntu')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_positive_stages_exact_downloaded_bytes_and_rejects_incomplete_set(self):
        root = self.root
        cfg, paths, sha, script, env, source_sha = staging_fixture(root, MINIMAL)
        with patch.object(guard, 'verify_attestation') as verify:
            guard.validate_payloads(root, cfg, paths, sha, REPO)
            # 14 static archives (the bundle's 11 and the XCFramework's three), and the
            # linux-x64 shared library the probe links.
            self.assertEqual(verify.call_count, 14 + 1)
        result = subprocess.run(['bash', '-c', script], cwd=root, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        guard.compare_payloads(root, paths)
        subprocess.run(['git', '-C', str(root), 'add', cfg.bundle, cfg.xcframework], check=True)
        guard.validate_staging_index(root, cfg, source_sha)
        shipped = root / 'swift/HyperUuidCore.artifactbundle/x86_64-unknown-linux-gnu/libhyperuuid.a'
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
        # Eight shared libraries and the 14 archives, each verified and listed.
        self.assertEqual(len((root / 'digests.txt').read_text().splitlines()), 8 + 14)
        missing = paths['swift/HyperUuidCore.artifactbundle/x86_64-unknown-linux-gnu/libhyperuuid.a']
        missing.unlink()
        with patch.object(guard, 'verify_attestation'), self.assertRaisesRegex(ValueError, 'missing artifact'):
            guard.validate_payloads(root, cfg, paths, sha, REPO)

    def test_swift_remote_stages_the_zips_checksums_and_no_swift_tree(self):
        root = self.root
        cfg, paths, sha, script, env, source_sha = staging_fixture(root, {**MINIMAL, 'swift_remote': True})
        with patch.object(guard, 'verify_attestation') as verify:
            guard.validate_payloads(root, cfg, paths, sha, REPO)
            # Nothing is committed; the linux-x64 shared library is still probed.
            self.assertEqual(paths, {})
            self.assertEqual(verify.call_count, 1)
        result = subprocess.run(['bash', '-c', script], cwd=root, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / cfg.bundle / 'info.json').exists())
        self.assertFalse((root / cfg.xcframework).exists())
        named = guard.swift_release.read((root / 'Package.swift').read_text())
        assets = guard.swift_assets_dir(root)
        self.assertEqual(named, {'version': '0.9.0', 'core': guard.swift_release.sha256(assets / cfg.swift_assets[0]),
                                 'apple': guard.swift_release.sha256(assets / cfg.swift_assets[1])})
        # Eight libraries and the two zips, each verified and listed.
        self.assertEqual(len((root / 'digests.txt').read_text().splitlines()), 8 + 2)
        self.assertFalse((root / 'go').exists())
        subprocess.run(['git', '-C', str(root), 'add', 'Package.swift'], check=True)
        guard.validate_staging_index(root, cfg, source_sha)
        (assets / cfg.swift_assets[0]).write_bytes(b'a different zip')
        with self.assertRaisesRegex(ValueError, 'Core.artifactbundle.zip'):
            guard.validate_staging_index(root, cfg, source_sha)


class StagingRequestTests(unittest.TestCase):
    """ci.yml's request-staging job dispatches staging from inside the run it names, and a
    human-dispatched run raises workflow_run too: staging waits for the run, then holds it to
    the full check, and the second request for one A is a notice, not a second B."""
    A, B = 'a' * 40, 'b' * 40
    WORKFLOW = SCRIPTS.parent / 'workflows/hyper-stage-natives.yml'

    def run_record(self, status, conclusion=None, **changes):
        return {'id': 42, 'head_sha': self.A, 'head_branch': 'master', 'path': '.github/workflows/ci.yml',
                'event': 'workflow_dispatch', 'repository': {'full_name': REPO},
                'status': status, 'conclusion': conclusion, **changes}

    def await_run(self, records, timeout=600):
        reads, sleeps, now = iter(records), [], [0]

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        with patch.object(guard, 'api', side_effect=lambda repo, endpoint: next(reads)):
            run = guard.await_run(REPO, self.A, '42', timeout, sleep=sleep, clock=lambda: now[0])
        return run, sleeps

    def test_waits_for_the_run_then_requires_success(self):
        run, sleeps = self.await_run([self.run_record('in_progress'), self.run_record('in_progress'),
                                      self.run_record('completed', 'success')])
        self.assertEqual((run['status'], len(sleeps)), ('completed', 2))
        for conclusion in ('failure', 'cancelled'):
            with self.subTest(conclusion=conclusion), self.assertRaisesRegex(ValueError, 'conclusion=success'):
                self.await_run([self.run_record('in_progress'), self.run_record('completed', conclusion)])
        # The bound runs out on an unfinished run: refused, never staged.
        with self.assertRaisesRegex(ValueError, 'status=completed'):
            self.await_run([self.run_record('in_progress')] * 10, timeout=60)
        # Without a wait (workflow_run, a lookup), an unfinished run is refused at once.
        with self.assertRaisesRegex(ValueError, 'status=completed'):
            self.await_run([self.run_record('in_progress')], timeout=0)

    def test_a_wrong_run_fails_before_the_wait(self):
        for changes in ({'head_sha': self.B}, {'head_branch': 'feature'}, {'event': 'pull_request'},
                        {'path': '.github/workflows/other.yml'}, {'repository': {'full_name': 'someone/else'}}):
            sleep = unittest.mock.Mock()
            with self.subTest(changes=changes), \
                 patch.object(guard, 'api', return_value=self.run_record('in_progress', **changes)), \
                 self.assertRaises(ValueError):
                guard.await_run(REPO, self.A, '42', 600, sleep=sleep)
            sleep.assert_not_called()

    def master(self, sha, parents, message):
        return {'sha': sha, 'parents': [{'sha': p} for p in parents], 'commit': {'message': message}}

    def test_master_at_a_stages_and_a_staged_a_is_skipped(self):
        trailers = f'chore: stage\n\nRelease-Source-SHA: {self.A}\nRelease-CI-Run-ID: 42\n'
        cases = {'at A': (self.master(self.A, ['c' * 40], 'chore: prepare v1.0.0'), None),
                 'staged': (self.master(self.B, [self.A], trailers), self.B)}
        for name, (head, expected) in cases.items():
            with self.subTest(name), patch.object(guard, 'api', return_value=head) as api:
                self.assertEqual(guard.staged_as(REPO, self.A), expected)
                api.assert_called_once_with(REPO, 'commits/master')
        refused = {'an unrelated commit': self.master(self.B, [self.A], 'docs: a fix'),
                   'staged from another source': self.master(self.B, [self.A], trailers.replace(self.A, 'c' * 40)),
                   'not A\'s child': self.master(self.B, ['c' * 40], trailers),
                   'beyond B': self.master('d' * 40, [self.B], 'docs: after the release')}
        for name, head in refused.items():
            with self.subTest(name), patch.object(guard, 'api', return_value=head), \
                 self.assertRaisesRegex(ValueError, 'master advanced beyond preparation source'):
                guard.staged_as(REPO, self.A)

    def test_skip_step_skips_the_staging_job_with_a_notice(self):
        script = step(self.WORKFLOW, 'Skip a preparation source master already carries as B')
        script = script.replace('.hyperforge/.github/scripts/', f'{SCRIPTS}/')
        trailers = f'stage\n\nRelease-Source-SHA: {self.A}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / 'bin/gh'
            gh.parent.mkdir()
            gh.write_text('#!/bin/sh\ntest "$*" = "api repos/' + REPO + '/commits/master" && cat "$MASTER_JSON"\n')
            gh.chmod(0o755)
            for name, head, staged in (('at A', self.master(self.A, [], ''), ''),
                                       ('staged', self.master(self.B, [self.A], trailers), self.B),
                                       ('advanced', self.master(self.B, [self.A], 'docs'), None)):
                with self.subTest(name):
                    (root / 'master.json').write_text(json.dumps(head))
                    (root / 'outputs').write_text('')
                    env = {**os.environ, 'PATH': f'{gh.parent}:{os.environ["PATH"]}', 'SOURCE_SHA': self.A,
                           'GITHUB_REPOSITORY': REPO, 'GITHUB_OUTPUT': str(root / 'outputs'),
                           'MASTER_JSON': str(root / 'master.json')}
                    result = subprocess.run(['bash', '-c', script], cwd=root, env=env, text=True, capture_output=True)
                    if staged is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('master advanced beyond preparation source', result.stderr)
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((root / 'outputs').read_text(), f'staged={staged}\n')
                    self.assertEqual('::notice::' in result.stdout, bool(staged))

    def test_staging_job_is_gated_on_the_skip_and_waits_for_the_run(self):
        text = self.WORKFLOW.read_text()
        body = text.split('\njobs:\n')[1]
        source, stage = body.split('\n  stage-native-binaries:\n')
        self.assertIn('  source:\n', source)
        self.assertIn('staged: ${{ steps.staged.outputs.staged }}', source)
        self.assertNotIn(': write', source)
        header_lines = stage.split('    steps:')[0]
        self.assertIn('needs: source', header_lines)
        self.assertIn("if: needs.source.outputs.staged == ''", header_lines)
        find = stage.split('      - name: Resolve and validate the exact preparation run\n')[1].split('\n\n')[0]
        self.assertRegex(find, r'RUN_WAIT_SECONDS: "[1-9][0-9]*"')
        self.assertIn('release_guard.py" run', find)


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
