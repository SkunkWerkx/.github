"""Functional tests for the immutable package hand-off and registry recovery."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
import urllib.error
import zipfile

from test_package_inventory_metadata import NODE_SUFFIXES, create_packages, write_cran, write_npm

SPEC = importlib.util.spec_from_file_location('inventory', Path(__file__).with_name('release_inventory.py'))


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(cls.m)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = dict(repository='SkunkWerkx/HyperCast', crate='hypercast',
                             version='0.10.0', source_sha='a'*40, staging_sha='b'*40,
                             ci_run_id='12', package_run_id='34', forge_sha='f'*40)
        create_packages(self.root)
        self.files = {p.relative_to(self.root).as_posix():p.read_bytes()
                      for p in self.root.rglob('*') if p.is_file()}

    def build(self):
        """The inventory of this package root and a checkout at B holding the natives."""
        self.workspace, natives = self.natives()
        return self.m.build_inventory(self.root, self.identity, natives, self.workspace)

    def test_inventory_binds_all_bytes_and_provenance_to_a_b(self):
        result = self.build()
        packages = [e for e in result['files'] if e['location'] == 'packages']
        self.assertEqual(len(packages), len(self.files))
        self.m.validate_inventory(self.root, result, self.identity, self.workspace)
        for entry in packages:
            self.assertEqual(entry['source_sha'], self.identity['source_sha' if entry['path'].startswith('pypi/') else 'staging_sha'])

    def test_recovery_rejects_altered_missing_and_extra_bytes(self):
        saved = self.build()
        path = self.root/'crate/upload.bin'
        for operation in ['altered', 'missing', 'extra']:
            with self.subTest(operation=operation):
                path.write_bytes(self.files['crate/upload.bin'])
                extra = self.root/'crate/extra.bin'
                extra.unlink(missing_ok=True)
                if operation == 'altered': path.write_bytes(b'wrong')
                if operation == 'missing': path.unlink()
                if operation == 'extra': extra.write_bytes(b'extra')
                with self.assertRaises(ValueError):
                    self.m.validate_inventory(self.root, saved, self.identity, self.workspace)

    def test_recovery_rejects_wrong_subject_run_source_and_signer(self):
        saved = self.build()
        for field, value in [('staging_sha', 'c'*40), ('source_sha', 'd'*40),
                             ('ci_run_id', '99'), ('repository', 'other/repo'),
                             ('forge_sha', 'e'*40)]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.m.validate_inventory(self.root, saved, {**self.identity, field:value}, self.workspace)
        saved['files'][0]['source_sha'] = 'c'*40
        with self.assertRaises(ValueError): self.m.validate_inventory(self.root, saved, self.identity, self.workspace)
        # A schema 1 inventory (no natives, no tag) no longer verifies at all.
        legacy = dict(schema=1, **self.identity, files=[e for e in self.build()['files'] if e['location'] == 'packages'])
        with self.assertRaisesRegex(ValueError, 'schema 2'):
            self.m.validate_inventory(self.root, legacy, self.identity, self.workspace)

    def test_missing_registry_platform_and_stale_version_rejected(self):
        (self.root/'ruby/hypercast-0.10.0-arm64-darwin.gem').unlink()
        with self.assertRaisesRegex(ValueError, 'Ruby'):
            self.build()
        (self.root/'ruby/hypercast-0.10.0-arm64-darwin.gem').write_bytes(self.files['ruby/hypercast-0.10.0-arm64-darwin.gem'])
        (self.root/'pypi/hypercast-0.10.0-abi0-none-platform.whl').rename(self.root/'pypi/hypercast-0.8.0-abi0-none-platform.whl')
        with self.assertRaisesRegex(ValueError, 'version'):
            self.build()

    def test_symlinks_rejected(self):
        path = self.root/'crate/upload.bin'
        path.unlink()
        path.symlink_to(self.root/'crate/metadata.json')
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.build()

    def test_exact_run_identity_and_failed_publication_can_recover(self):
        run = dict(id=34, head_sha='b'*40, path='.github/workflows/release.yml',
                   event='push', status='completed', conclusion='failure',
                   repository=dict(full_name='SkunkWerkx/HyperCast'))
        self.m.validate_package_run(run, self.identity)
        for field,value in [('id',35), ('head_sha','c'*40), ('path','.github/workflows/ci.yml'),
                            ('event','pull_request'), ('status','in_progress')]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.m.validate_package_run({**run,field:value}, self.identity)

    def test_partial_publication_only_selects_missing_exact_bytes(self):
        files = [self.root/'ruby/hypercast-0.10.0.gem', self.root/'ruby/hypercast-wasm-0.10.0.gem']
        remote = {files[0].name: files[0].read_bytes()}
        pending = self.m.select_missing(files, lambda path: remote.get(path.name))
        self.assertEqual(pending, [files[1]])
        remote[files[0].name] = b'conflict'
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.m.select_missing(files, lambda path: remote.get(path.name))

    def test_fetch_reads_the_s3_denial_for_a_missing_gem_as_absent(self):
        # rubygems.org/downloads is served from S3, which answers 403 AccessDenied for a gem
        # it does not hold (measured 2026-10-25); any other 403 is an error.
        def denied(headers, body=b'<Error><Code>AccessDenied</Code></Error>'):
            error = urllib.error.HTTPError('https://rubygems.org/downloads/x.gem', 403, 'Forbidden', headers, io.BytesIO(body))
            self.addCleanup(error.close)
            return error
        with unittest.mock.patch.object(self.m.urllib.request, 'urlopen', side_effect=denied({'x-amz-request-id': 'R'})):
            self.assertIsNone(self.m.fetch('https://rubygems.org/downloads/x.gem'))
        for headers, body in (({}, b'<Error><Code>AccessDenied</Code></Error>'), ({'x-amz-request-id': 'R'}, b'rate limited')):
            with self.subTest(headers=headers, body=body), \
                 unittest.mock.patch.object(self.m.urllib.request, 'urlopen', side_effect=denied(headers, body)), \
                 self.assertRaises(urllib.error.HTTPError):
                self.m.fetch('https://rubygems.org/downloads/x.gem')

    def test_nuget_rewriting_can_only_add_repository_signature(self):
        packed, served = self.root/'packed.zip', self.root/'served.zip'
        for path in [packed, served]:
            with zipfile.ZipFile(path,'w') as z: z.writestr('lib/core.dll', b'bytes')
        with zipfile.ZipFile(served,'a') as z: z.writestr('.signature.p7s', b'signature')
        self.m.compare_nuget(packed, served)
        with zipfile.ZipFile(served,'w') as z:
            z.writestr('lib/core.dll', b'changed')
            z.writestr('.signature.p7s', b'signature')
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.m.compare_nuget(packed, served)

    def test_registry_confirmation_waits_for_visibility_and_fails_if_incomplete(self):
        attempts = iter([['missing'], ['missing'], []])
        self.m.confirm_registry(lambda:next(attempts), attempts=3, pause=lambda:None)
        with self.assertRaisesRegex(ValueError, 'visible'):
            self.m.confirm_registry(lambda:['missing'], attempts=2, pause=lambda:None)

    def natives(self):
        """A checkout at B with three committed native libraries and the Swift bundle's
        info.json, which is staged beside them without build provenance, and their path list."""
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        workspace = Path(outside.name) / 'checkout'
        self.natives_list = Path(outside.name) / 'natives.txt'
        natives = ['php/src/native/linux-x64/libhypercast.so', 'go/staticlib/linux_amd64/libhypercast.a',
                   'swift/HyperCastCore.artifactbundle/x86_64-unknown-linux-gnu/libhypercast.a',
                   'swift/HyperCastCore.artifactbundle/info.json']
        for index, relative in enumerate(natives):
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'native bytes' if index else b'first native')
        self.natives_list.write_text('\n'.join(natives) + '\n')
        return workspace, natives

    def test_schema_two_lists_committed_natives_at_a_and_names_the_tag(self):
        workspace, natives = self.natives()
        result = self.m.build_inventory(self.root, self.identity, natives, workspace)
        self.assertEqual((result['schema'], result['tag']), (2, 'v0.10.0'))
        self.assertEqual(len(result['files']), len(self.files) + 4)
        source = [e for e in result['files'] if e['location'] == 'source']
        self.assertEqual([e['path'] for e in source], natives)
        for entry in source[:3]:
            self.assertEqual((entry['source_sha'], entry['signer_repo']), ('a' * 40, self.m.FORGE))
        # Staged metadata has no build provenance to name; the release attestation binds it.
        self.assertNotIn('source_sha', source[3])
        self.assertNotIn('signer_repo', source[3])
        self.assertTrue(all(e['location'] == 'packages' for e in result['files'] if e['path'].split('/')[0] in self.m.REGISTRIES))
        self.m.validate_inventory(self.root, result, self.identity, workspace)
        # Equal bytes under two paths stay two subjects; `gh attestation verify` matches by digest.
        digests = [e['sha256'] for e in source[1:3]]
        self.assertEqual(digests[0], digests[1])
        lines = self.m.checksums(result).splitlines()
        self.assertEqual(len(lines), len(result['files']))
        self.assertEqual(lines[-1], f"{source[-1]['sha256']}  {natives[-1]}")
        self.assertTrue(all(len(line.split('  ')[0]) == 64 for line in lines))

    def test_schema_two_rejects_tampered_missing_or_colliding_natives(self):
        workspace, natives = self.natives()
        saved = self.m.build_inventory(self.root, self.identity, natives, workspace)
        (workspace / natives[1]).write_bytes(b'rebuilt elsewhere')
        with self.assertRaises(ValueError):
            self.m.validate_inventory(self.root, saved, self.identity, workspace)
        (workspace / natives[1]).unlink()
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.m.validate_inventory(self.root, saved, self.identity, workspace)
        with self.assertRaisesRegex(ValueError, 'workspace'):
            self.m.validate_inventory(self.root, saved, self.identity, None)
        for bad in (['nuget/x.nupkg'], ['inventory.json'], natives[:1] * 2, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.m.build_inventory(self.root, self.identity, bad, workspace)
        (self.root / 'inventory.json').write_text(json.dumps({**saved, 'schema': 1}))
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('release_inventory.py')), 'checksums',
                                 '--root', str(self.root), '--output', str(workspace / 'sums.txt')],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('schema 2', result.stderr)

    def test_release_attestation_is_checked_per_file_against_one_downloaded_bundle(self):
        workspace, natives = self.natives()
        inventory = self.m.build_inventory(self.root, self.identity, natives, workspace)
        calls = []
        def fake_run(args, check=True, cwd=None, **kwargs):
            calls.append(args)
            self.assertEqual(args[:3], ['gh', 'attestation', 'download'])
            self.assertEqual(args[args.index('--predicate-type') + 1], self.m.PREDICATE_TYPE)
            (Path(cwd) / 'sha256:abc.jsonl').write_text('{}')
        def fake_output(args, text=True, **kwargs):
            calls.append(args)
            self.assertEqual(args[:3], ['gh', 'attestation', 'verify'])
            self.assertTrue(Path(args[3]).is_file(), args[3])
            self.assertIn('--bundle', args)
            self.assertEqual(args[args.index('--source-digest') + 1], 'b' * 40)
            predicate = dict(inventory) if not os.environ.get('WRONG_PREDICATE') else {**inventory, 'version': '0.9.0'}
            return json.dumps([{'verificationResult': {'statement': {
                'predicateType': self.m.PREDICATE_TYPE, 'predicate': predicate}}}])
        with unittest.mock.patch.object(self.m.subprocess, 'run', fake_run), \
             unittest.mock.patch.object(self.m.subprocess, 'check_output', fake_output):
            self.m.verify_release_attestation(self.root, inventory, None, workspace)
            self.assertEqual(sum(c[2] == 'download' for c in calls), 1)
            self.assertEqual(sum(c[2] == 'verify' for c in calls), len(inventory['files']))
            calls.clear()
            self.m.verify_release_attestation(self.root, inventory, 'ruby', workspace)
            self.assertEqual(sum(c[2] == 'verify' for c in calls), 9)
            with unittest.mock.patch.dict(os.environ, {'WRONG_PREDICATE': '1'}), \
                 self.assertRaisesRegex(ValueError, 'predicate is not this inventory'):
                self.m.verify_release_attestation(self.root, inventory, 'crate', workspace)

    def test_cli_creates_schema_two_writes_checksums_outside_the_root_and_recovers(self):
        workspace, natives = self.natives()
        helper = tempfile.TemporaryDirectory()
        self.addCleanup(helper.cleanup)
        gh = Path(helper.name) / 'gh'
        gh.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
if args[0]=='api':
 print(json.dumps(dict(id=34,head_sha='b'*40,path='.github/workflows/release.yml',event='push',status='completed',conclusion='failure',repository=dict(full_name='SkunkWerkx/HyperCast'))))
 sys.exit(0)
if args[1]=='download':
 pathlib.Path('sha256:deadbeef.jsonl').write_text('{}')
 sys.exit(0)
path=pathlib.Path(args[2])
if '--predicate-type' in args:
 inventory=json.loads(pathlib.Path(os.environ['INVENTORY']).read_text())
 if os.environ.get('WRONG_PREDICATE'): inventory['version']='0.9.0'
 print(json.dumps([{'verificationResult':{'statement':{'predicateType':args[args.index('--predicate-type')+1],'predicate':inventory}}}]))
 sys.exit(0)
if path.name=='info.json':
 sys.exit('HTTP 404: Not Found')
source='a'*40 if path.parent.name=='pypi' or 'release-packages' not in path.parts else 'b'*40
sys.exit(0 if args[args.index('--source-digest')+1]==source else 1)
''')
        gh.chmod(0o755)
        root = self.root / 'release-packages'
        shutil.move(str(self.root / 'nuget'), root / 'nuget')
        for group in ('ruby', 'pypi', 'crate', 'maven'):
            shutil.move(str(self.root / group), root / group)
        env = {**os.environ, **{key.upper(): value for key, value in self.identity.items()},
               'PATH': str(gh.parent) + os.pathsep + os.environ['PATH'], 'GITHUB_RUN_ID': '34',
               'INVENTORY': str(root / 'inventory.json')}
        command = [sys.executable, str(Path(__file__).with_name('release_inventory.py'))]
        subprocess.run([*command, 'create', '--root', str(root), '--natives', str(self.natives_list),
                        '--workspace', str(workspace)], env=env, check=True, cwd=self.root)
        inventory = json.loads((root / 'inventory.json').read_text())
        self.assertEqual(inventory['schema'], 2)
        sums = self.root / 'release-checksums.txt'
        subprocess.run([*command, 'checksums', '--root', str(root), '--output', str(sums)], env=env, check=True)
        self.assertEqual(len(sums.read_text().splitlines()), len(inventory['files']))
        result = subprocess.run([*command, 'checksums', '--root', str(root), '--output', str(root / 'sums.txt')],
                                env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0, 'checksums inside the package root would become an unexpected payload')
        env.update(GITHUB_RUN_ID='56', PACKAGE_RUN_ID='56')
        subprocess.run([*command, 'verify', '--root', str(root), '--workspace', str(workspace)], env=env, check=True, cwd=self.root)
        subprocess.run([*command, 'verify', '--inventory-only', '--root', str(root), '--workspace', str(workspace)], env=env, check=True, cwd=self.root)
        for broken in ({'WRONG_PREDICATE': '1'}, {}):
            if not broken:
                (workspace / natives[0]).write_bytes(b'rebuilt')
            result = subprocess.run([*command, 'verify', '--root', str(root), '--workspace', str(workspace)],
                                    env={**env, **broken}, capture_output=True, cwd=self.root)
            self.assertNotEqual(result.returncode, 0, broken)
        result = subprocess.run([*command, 'verify', '--root', str(root)], env=env, capture_output=True, cwd=self.root)
        self.assertNotEqual(result.returncode, 0, 'schema 2 needs the checkout at B')

    def test_cli_verifies_from_the_checkout_with_the_relative_paths_the_actions_pass(self):
        # The actions run every command from the checkout at B with the default --root
        # (release-packages) and --workspace ., so every path is relative. `gh attestation
        # download` runs in a temporary directory and opens the file it is given, which a
        # relative path no longer names there; this gh does exactly that.
        workspace, natives = self.natives()
        helper = tempfile.TemporaryDirectory()
        self.addCleanup(helper.cleanup)
        gh = Path(helper.name) / 'gh'
        gh.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
if args[0]=='api':
 print(json.dumps(dict(id=34,head_sha='b'*40,path='.github/workflows/release.yml',event='push',status='completed',conclusion='success',repository=dict(full_name='SkunkWerkx/HyperCast'))))
 sys.exit(0)
if not pathlib.Path(args[2]).is_file():
 sys.exit(f'failed to open local artifact: open {args[2]}: no such file or directory')
if args[1]=='download':
 pathlib.Path('sha256:deadbeef.jsonl').write_text('{}')
 sys.exit(0)
if '--predicate-type' in args:
 inventory=json.loads(pathlib.Path('release-packages/inventory.json' if pathlib.Path('release-packages').is_dir() else os.environ['INVENTORY']).read_text())
 print(json.dumps([{'verificationResult':{'statement':{'predicateType':args[args.index('--predicate-type')+1],'predicate':inventory}}}]))
 sys.exit(0)
source='a'*40 if pathlib.Path(args[2]).parent.name=='pypi' or 'release-packages' not in pathlib.Path(args[2]).parts else 'b'*40
sys.exit(0 if args[args.index('--source-digest')+1]==source else 1)
''')
        gh.chmod(0o755)
        root = workspace / 'release-packages'
        root.mkdir()
        for group in self.m.REGISTRIES:
            shutil.move(str(self.root / group), root / group)
        env = {**os.environ, **{key.upper(): value for key, value in self.identity.items()},
               'PATH': str(gh.parent) + os.pathsep + os.environ['PATH'], 'GITHUB_RUN_ID': '34',
               'INVENTORY': str(root / 'inventory.json')}
        command = [sys.executable, str(Path(__file__).with_name('release_inventory.py'))]
        subprocess.run([*command, 'create', '--natives', str(self.natives_list), '--workspace', '.'],
                       env=env, check=True, cwd=workspace)
        for extra in ([], ['--inventory-only'], ['--registry', 'ruby']):
            with self.subTest(extra=extra):
                result = subprocess.run([*command, 'verify', '--workspace', '.', *extra],
                                        env=env, capture_output=True, text=True, cwd=workspace)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_recovers_verified_bytes_without_native_artifacts_and_rejects_wrong_provenance(self):
        workspace, natives = self.natives()
        helper=tempfile.TemporaryDirectory()
        self.addCleanup(helper.cleanup)
        directory=Path(helper.name)
        gh=directory/'gh'
        gh.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
if args[0]=='api':
 print(json.dumps(dict(id=34,head_sha='b'*40,path='.github/workflows/release.yml',event='push',status='completed',conclusion='failure',repository=dict(full_name='SkunkWerkx/HyperCast'))))
 sys.exit(0)
if args[1]=='download':
 pathlib.Path('sha256:deadbeef.jsonl').write_text('{}')
 sys.exit(0)
if '--predicate-type' in args:
 inventory=json.loads(pathlib.Path(os.environ['INVENTORY']).read_text())
 print(json.dumps([{'verificationResult':{'statement':{'predicateType':args[args.index('--predicate-type')+1],'predicate':inventory}}}]))
 sys.exit(0)
path=pathlib.Path(args[2])
source='a'*40 if path.parent.name=='pypi' or os.environ['ROOT'] not in str(path.resolve()) else 'b'*40
if os.environ.get('WRONG_SOURCE') and path.suffix=='.crate': source='c'*40
sys.exit(0 if args[args.index('--source-digest')+1]==source else 1)
''')
        gh.chmod(0o755)
        env={**os.environ,**{key.upper():value for key,value in self.identity.items()},
             'PATH':str(directory)+os.pathsep+os.environ['PATH'],'GITHUB_RUN_ID':'34',
             'INVENTORY':str(self.root/'inventory.json'),'ROOT':str(self.root.resolve())}
        command=['python3',str(Path(__file__).with_name('release_inventory.py'))]
        tree=['--root',str(self.root),'--workspace',str(workspace)]
        result=subprocess.run([*command,'create',*tree[:2]],env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0,'create without --natives must refuse')
        self.assertIn('--natives',result.stderr)
        subprocess.run([*command,'create',*tree,'--natives',str(self.natives_list)],env=env,check=True)
        env.update(GITHUB_RUN_ID='56',PACKAGE_RUN_ID='56')
        subprocess.run([*command,'verify',*tree],env=env,check=True)
        result=subprocess.run([*command,'verify',*tree],
                              env={**env,'WRONG_SOURCE':'1'},capture_output=True)
        self.assertNotEqual(result.returncode,0,'Wrong-source crate provenance must reject recovery')

    def layout(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file())

    def test_npm_and_cran_groups_are_optional_and_exact(self):
        paths = self.layout()
        self.assertEqual(sum(p.startswith('npm/') for p in paths), 9)
        self.assertIn('cran/hypercast_0.10.0.tar.gz', paths)
        self.m.validate_layout(paths, 'hypercast', '0.10.0')
        self.m.validate_layout([p for p in paths if p.split('/')[0] not in ('npm', 'cran')], 'hypercast', '0.10.0')
        platform = f'npm/skunkwerkx-hypercast-{NODE_SUFFIXES[0]}-0.10.0.tgz'
        broken = {
            'missing platform package': [p for p in paths if p != platform],
            'missing root package': [p for p in paths if p != 'npm/hypercast-0.10.0.tgz'],
            'stale platform version': [p.replace('-0.10.0.tgz', '-0.8.0.tgz') if p == platform else p for p in paths],
            'unscoped platform name': [p.replace('skunkwerkx-', '') if p == platform else p for p in paths],
            'extra npm file': paths + ['npm/hypercast-0.8.0.tgz'],
            'stale R version': [p.replace('0.10.0', '0.8.0') if p.startswith('cran/') else p for p in paths],
            'extra cran file': paths + ['cran/hypercast_0.8.0.tar.gz'],
            'R package under another name': [p.replace('hypercast_', 'wrongproject_') if p.startswith('cran/') else p for p in paths],
        }
        for label, layout in broken.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'npm|CRAN'):
                self.m.validate_layout(layout, 'hypercast', '0.10.0')
        with self.assertRaisesRegex(ValueError, 'unexpected payload'):
            (self.root / 'bun').mkdir()
            (self.root / 'bun/x.tgz').write_bytes(b'')
            self.m.payload_files(self.root)

    def test_npm_and_cran_are_signed_by_the_consumer_at_b(self):
        inventory = self.build()
        entries = [e for e in inventory['files'] if e['path'].split('/')[0] in ('npm', 'cran')]
        self.assertEqual(len(entries), 10)
        for entry in entries:
            self.assertEqual((entry['signer_repo'], entry['source_sha']), ('SkunkWerkx/HyperCast', 'b' * 40))
        self.assertIn('npm', self.m.REGISTRIES)
        self.assertNotIn('cran', self.m.REGISTRIES, 'no job ever uploads the CRAN tarball')
        self.assertEqual(self.m.INVENTORY_GROUPS, self.m.REGISTRIES + ('cran',))
        for bad in (['npm/x.tgz'], ['cran/x.tar.gz']):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'collides'):
                self.m.build_inventory(self.root, self.identity, bad, self.workspace)

    def test_layout_follows_the_consumer_config_at_b(self):
        inventory = self.build()
        config = self.workspace / '.github/hyper.json'
        config.parent.mkdir(parents=True)
        base = dict(crate='hypercast', project='HyperCast',
                    exports=dict(sources=['rust/src/ffi.rs'], patterns=['x'], prefixes=['hypercast_']))
        config.write_text(json.dumps(base))
        self.assertEqual(self.m.build_inventory(self.root, self.identity, [e['path'] for e in inventory['files'] if e['location'] == 'source'], self.workspace), inventory)
        natives = [e['path'] for e in inventory['files'] if e['location'] == 'source']
        for change, message in (({'wheels': 24}, '24 wheels'),
                                ({'node': {'platforms': {'linux-x64': 'linux-x64-gnu'}}}, 'npm'),
                                ({'crate': 'hypertabular'}, 'crate')):
            with self.subTest(change=change):
                config.write_text(json.dumps({**base, **change}))
                with self.assertRaisesRegex(ValueError, message):
                    self.m.build_inventory(self.root, self.identity, natives, self.workspace)

    def npm_registry(self, published):
        """A fake fetch over registry.npmjs.org holding `published` (local tarball -> served bytes)."""
        served, requests = {}, []
        for path, body in published.items():
            with tarfile.open(path) as archive:
                manifest = json.load(archive.extractfile('package/package.json'))
            quoted = manifest['name'].replace('/', '%2F')
            # The registry's own tarball URL: the full name, then the unscoped file name.
            tarball = f"https://registry.npmjs.org/{manifest['name']}/-/{manifest['name'].split('/')[-1]}-{manifest['version']}.tgz"
            integrity = 'sha512-' + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
            served[f"https://registry.npmjs.org/{quoted}/{manifest['version']}"] = json.dumps(
                dict(name=manifest['name'], version=manifest['version'],
                     dist=dict(integrity=integrity, tarball=tarball))).encode()
            served[tarball] = body
        def fetch(url):
            requests.append(url)
            return served.get(url)
        return fetch, requests, served

    def npm_plan(self, published, mutate=None):
        (self.root / 'inventory.json').write_text(json.dumps(self.build()))
        fetch, requests, served = self.npm_registry(published)
        if mutate:
            mutate(served)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        output = Path(outside.name) / 'pending'
        with unittest.mock.patch.object(self.m, 'fetch', fetch), \
             unittest.mock.patch.dict(os.environ, {'GITHUB_OUTPUT': ''}):
            pending = self.m.registry_plan(self.root, 'npm', output)
        return pending, requests, output

    def npm_paths(self):
        root_tgz = self.root / 'npm/hypercast-0.10.0.tgz'
        platforms = [self.root / f'npm/skunkwerkx-hypercast-{s}-0.10.0.tgz' for s in NODE_SUFFIXES]
        return root_tgz, platforms

    def test_npm_registry_plan_lists_exactly_the_missing_packages_platforms_first(self):
        root_tgz, platforms = self.npm_paths()
        everything = {p: p.read_bytes() for p in [root_tgz, *platforms]}
        # A half-published first release: the root and seven platforms are up, one is not.
        missing = platforms[3]
        pending, requests, output = self.npm_plan({p: b for p, b in everything.items() if p != missing})
        self.assertEqual(pending, [missing])
        self.assertEqual(sorted(p.name for p in output.iterdir()), [missing.name])
        self.assertIn(f'https://registry.npmjs.org/@skunkwerkx%2Fhypercast-{NODE_SUFFIXES[3]}/0.10.0', requests)
        self.assertIn('https://registry.npmjs.org/hypercast/0.10.0', requests)
        # Only the root is missing: it is pending, and last.
        pending, _, _ = self.npm_plan({p: b for p, b in everything.items() if p != root_tgz})
        self.assertEqual(pending, [root_tgz])
        # Nothing published: the eight platform packages, then the root.
        pending, _, _ = self.npm_plan({})
        self.assertEqual(pending, [*sorted(platforms), root_tgz])
        self.assertEqual(self.npm_plan(everything)[0], [])

    def test_npm_registry_plan_rejects_different_published_bytes(self):
        root_tgz, platforms = self.npm_paths()
        everything = {p: p.read_bytes() for p in [root_tgz, *platforms]}
        with self.assertRaisesRegex(ValueError, 'registry conflict'):
            self.npm_plan({**everything, platforms[0]: b'other bytes'})
        def integrity(served):
            url = 'https://registry.npmjs.org/hypercast/0.10.0'
            record = json.loads(served[url])
            record['dist']['integrity'] = 'sha512-' + base64.b64encode(b'x' * 64).decode()
            served[url] = json.dumps(record).encode()
        with self.assertRaisesRegex(ValueError, 'registry conflict'):
            self.npm_plan(everything, integrity)
        def host(served):
            url = 'https://registry.npmjs.org/hypercast/0.10.0'
            record = json.loads(served[url])
            record['dist']['tarball'] = 'https://example.com/hypercast-0.10.0.tgz'
            served[url] = json.dumps(record).encode()
        with self.assertRaisesRegex(ValueError, 'host'):
            self.npm_plan(everything, host)
        def vanished(served):
            del served['https://registry.npmjs.org/hypercast/-/hypercast-0.10.0.tgz']
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.npm_plan(everything, vanished)

    def test_cli_publishes_npm_and_never_cran(self):
        command = [sys.executable, str(Path(__file__).with_name('release_inventory.py')), 'registry-plan']
        for registry, accepted in (('npm', True), ('cran', False)):
            result = subprocess.run([*command, '--registry', registry, '--root', str(self.root / 'absent')],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual('invalid choice' in result.stderr, not accepted, result.stderr)


if __name__ == '__main__': unittest.main()
