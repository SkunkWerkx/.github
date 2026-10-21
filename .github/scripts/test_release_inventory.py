"""Functional tests for the immutable package hand-off and registry recovery."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from test_package_inventory_metadata import create_packages

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

    def test_inventory_binds_all_bytes_and_provenance_to_a_b(self):
        result = self.m.build_inventory(self.root, self.identity)
        self.assertEqual(len(result['files']), len(self.files))
        self.m.validate_inventory(self.root, result, self.identity)
        for entry in result['files']:
            self.assertEqual(entry['source_sha'], self.identity['source_sha' if entry['path'].startswith('pypi/') else 'staging_sha'])

    def test_recovery_rejects_altered_missing_and_extra_bytes(self):
        saved = self.m.build_inventory(self.root, self.identity)
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
                    self.m.validate_inventory(self.root, saved, self.identity)

    def test_recovery_rejects_wrong_subject_run_source_and_signer(self):
        saved = self.m.build_inventory(self.root, self.identity)
        for field, value in [('staging_sha', 'c'*40), ('source_sha', 'd'*40),
                             ('ci_run_id', '99'), ('repository', 'other/repo'),
                             ('forge_sha', 'e'*40)]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.m.validate_inventory(self.root, saved, {**self.identity, field:value})
        saved['files'][0]['source_sha'] = 'c'*40
        with self.assertRaises(ValueError): self.m.validate_inventory(self.root, saved, self.identity)

    def test_missing_registry_platform_and_stale_version_rejected(self):
        (self.root/'ruby/hypercast-0.10.0-arm64-darwin.gem').unlink()
        with self.assertRaisesRegex(ValueError, 'Ruby'):
            self.m.build_inventory(self.root, self.identity)
        (self.root/'ruby/hypercast-0.10.0-arm64-darwin.gem').write_bytes(self.files['ruby/hypercast-0.10.0-arm64-darwin.gem'])
        (self.root/'pypi/hypercast-0.10.0-abi0-none-platform.whl').rename(self.root/'pypi/hypercast-0.8.0-abi0-none-platform.whl')
        with self.assertRaisesRegex(ValueError, 'version'):
            self.m.build_inventory(self.root, self.identity)

    def test_symlinks_rejected(self):
        path = self.root/'crate/upload.bin'
        path.unlink()
        path.symlink_to(self.root/'crate/metadata.json')
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.m.build_inventory(self.root, self.identity)

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

    def test_cli_recovers_verified_bytes_without_native_artifacts_and_rejects_wrong_provenance(self):
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
path=pathlib.Path(args[2])
source='a'*40 if path.parent.name=='pypi' else 'b'*40
if os.environ.get('WRONG_SOURCE') and path.suffix=='.crate': source='c'*40
sys.exit(0 if args[args.index('--source-digest')+1]==source else 1)
''')
        gh.chmod(0o755)
        env={**os.environ,**{key.upper():value for key,value in self.identity.items()},
             'PATH':str(directory)+os.pathsep+os.environ['PATH'],'GITHUB_RUN_ID':'34'}
        command=['python3',str(Path(__file__).with_name('release_inventory.py'))]
        subprocess.run([*command,'create','--root',str(self.root)],env=env,check=True)
        env.update(GITHUB_RUN_ID='56',PACKAGE_RUN_ID='56')
        subprocess.run([*command,'verify','--root',str(self.root)],env=env,check=True)
        result=subprocess.run([*command,'verify','--root',str(self.root)],
                              env={**env,'WRONG_SOURCE':'1'},capture_output=True)
        self.assertNotEqual(result.returncode,0,'Wrong-source crate provenance must reject recovery')


if __name__ == '__main__': unittest.main()
