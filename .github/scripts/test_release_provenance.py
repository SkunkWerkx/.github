"""Execute the reusable packers' verification steps with a local gh double.
Run: python3 .github/scripts/test_release_provenance.py -v
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parent
WORKFLOWS = Path(os.environ['FORGE_WORKFLOWS']) if 'FORGE_WORKFLOWS' in os.environ else Path(__file__).resolve().parents[1] / 'workflows'
SHA = 'a' * 40


def step(workflow, name, values):
    """Extract a step's script from a workflow, or from a composite action under actions/."""
    path = WORKFLOWS / workflow if '/' not in workflow else WORKFLOWS.parent / workflow
    text = path.read_text()
    tail = re.split(rf'^ *- name: {re.escape(name)}\n', text, maxsplit=1, flags=re.M)[1]
    tail = re.split(r'^ *run: \|\n', tail, maxsplit=1, flags=re.M)[1]
    indent = len(tail) - len(tail.lstrip(' '))
    lines = []
    for line in tail.splitlines():
        if line and not line.startswith(' ' * indent):
            break
        lines.append(line)
    script = textwrap.dedent('\n'.join(lines))
    return re.sub(r'\$\{\{ ([^}]+) \}\}', lambda m: values[m[1]], script)


MAVEN_ACTION = 'actions/prepare-maven-package/action.yml'
RIDS = ('linux-x64', 'linux-arm64', 'linux-musl-x64', 'linux-musl-arm64',
        'osx-x64', 'osx-arm64', 'win-x64', 'win-arm64')


def lib(rid, crate='hyperuuid'):
    return f'{crate}.dll' if rid.startswith('win') else f'lib{crate}.dylib' if rid.startswith('osx') else f'lib{crate}.so'


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        gh = self.root / 'bin/gh'
        gh.parent.mkdir()
        gh.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
args=sys.argv[1:]
if args[0]=='api':
    print(os.environ.get('RUN_SHA', 'a'*40)); sys.exit(0)
p=pathlib.Path(args[2])
if '--repo' not in args or '--signer-repo' not in args or '--source-digest' not in args: sys.exit(2)
if args[args.index('--source-digest')+1] != 'a'*40: sys.exit(3)
if not p.is_file() or p.read_text() != 'verified': sys.exit(4)
with open('verified.log','a') as log: log.write(str(p)+'\\n')
''')
        gh.chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{gh.parent}:{os.environ["PATH"]}',
                    'GITHUB_REPOSITORY': 'SkunkWerkx/HyperUuid', 'SOURCE_SHA': SHA,
                    'SOURCE_RUN_ID': '42', 'GITHUB_OUTPUT': str(self.root / 'outputs')}

    def write(self, path, value='verified'):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def execute(self, script):
        return subprocess.run(['bash', '-c', script], cwd=self.root, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def consumer(self, crate='hyperuuid'):
        """The consumer checkout's hyper.json, which names the libraries (hyper_config.py's lib_names)."""
        self.write('.github/hyper.json', json.dumps(dict(
            crate=crate, project='HyperUuid',
            exports=dict(sources=['rust/src/ffi.rs'], patterns=['x'], prefixes=['uuid_']))))
        self.env['FORGE_SCRIPTS'] = str(SCRIPTS)

    def maven(self):
        self.consumer()
        self.env.update(CRATE='hyperuuid', MUSL='true', RESOURCES_DIR='resources/native')
        return step(MAVEN_ACTION, 'Stage native/{rid}/{filename} resources', {})

    def test_maven_verifies_every_native_before_publication(self):
        paths = [self.write(f'artifacts/native-{rid}/{lib(rid)}') for rid in RIDS]
        script = self.maven() + '\ntouch published'
        self.assertEqual(self.execute(script).returncode, 0)
        self.assertEqual(len((self.root / 'verified.log').read_text().splitlines()), 8)
        for src in paths:
            rid = src.parent.name.removeprefix('native-')
            self.assertEqual((self.root / f'resources/native/{rid}/{src.name}').read_bytes(), src.read_bytes())
        (self.root / 'published').unlink(missing_ok=True)
        paths[0].write_text('wrong source')
        self.assertNotEqual(self.execute(script).returncode, 0)
        self.assertFalse((self.root / 'published').exists())
        paths[0].write_text('verified')
        paths[0].unlink()
        self.assertNotEqual(self.execute(script).returncode, 0)
        self.assertFalse((self.root / 'published').exists())

    def test_nuget_rejects_wrong_source_native_static_and_browser_archives(self):
        files = [self.write('csharp/runtimes/linux-x64/native/libhyperuuid.so'),
                 self.write('csharp/runtimes/browser-wasm/nativeassets/net11.0/libhyperuuid.a'),
                 self.write('csharp/staticlibs/linux-x64/libhyperuuid.a')]
        script = step('hyper-pack-nuget.yml', 'Verify build provenance of every native about to be packed', {
            'inputs.runtimes_dir': 'csharp/runtimes', 'steps.source.outputs.sha': SHA}) + '\ntouch packed'
        self.assertEqual(self.execute(script).returncode, 0)
        self.assertEqual(len((self.root / 'verified.log').read_text().splitlines()), 3)
        for src in files:
            (self.root / 'packed').unlink(missing_ok=True)
            src.write_text('wrong source')
            self.assertNotEqual(self.execute(script).returncode, 0)
            self.assertFalse((self.root / 'packed').exists())
            src.write_text('verified')

    def test_packers_derive_source_from_existing_run_input(self):
        for workflow in ('hyper-pack-nuget.yml', MAVEN_ACTION):
            script = step(workflow, "Resolve the reused artifacts' source SHA", {})
            self.assertEqual(self.execute(script).returncode, 0)
            self.assertIn(f'sha={SHA}', (self.root / 'outputs').read_text())
            self.env['RUN_SHA'] = 'null'
            self.assertNotEqual(self.execute(script).returncode, 0)
            self.env.pop('RUN_SHA')

    def test_maven_stages_hyper_json_library_names_without_musl_and_refuses_another_crate(self):
        for rid in RIDS:
            self.write(f'artifacts/native-{rid}/{lib(rid)}')
        script = self.maven()
        self.env['MUSL'] = 'false'
        self.assertEqual(self.execute(script).returncode, 0)
        staged = sorted(p.relative_to(self.root / 'resources/native').as_posix()
                        for p in (self.root / 'resources/native').rglob('*') if p.is_file())
        self.assertEqual(staged, sorted(f'{rid}/{lib(rid)}' for rid in RIDS if 'musl' not in rid))
        self.env['CRATE'] = 'hypercast'
        result = self.execute(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hyper.json', result.stdout)

    def test_nuget_arranges_hyper_json_library_names_per_rid(self):
        self.consumer()
        for rid in (*RIDS, 'android-arm64', 'android-x64'):
            self.write(f'artifacts/native-{rid}/{lib(rid)}')
        script = step('hyper-pack-nuget.yml', 'Arrange runtimes/{rid}/native/ layout', {})
        for musl, android, rids in (('true', 'false', RIDS),
                                    ('false', 'false', [r for r in RIDS if 'musl' not in r]),
                                    ('true', 'true', [*RIDS, 'android-arm64', 'android-x64'])):
            with self.subTest(musl=musl, android=android):
                shutil.rmtree(self.root / 'csharp', ignore_errors=True)
                self.env.update(INPUT_CRATE='hyperuuid', MUSL=musl, ANDROID=android, RUNTIMES_DIR='csharp/runtimes')
                result = self.execute(script)
                self.assertEqual(result.returncode, 0, result.stderr)
                staged = sorted(p.relative_to(self.root / 'csharp/runtimes').as_posix()
                                for p in (self.root / 'csharp/runtimes').rglob('*') if p.is_file())
                self.assertEqual(staged, sorted(f'{rid}/native/{lib(rid)}' for rid in rids))
        self.env['INPUT_CRATE'] = 'hypercast'
        result = self.execute(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('hyper.json', result.stdout)


if __name__ == '__main__':
    unittest.main()
