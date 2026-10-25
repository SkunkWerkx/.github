"""The GitHub Release body: the CHANGELOG section plus the generated Verifying section."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_notes', SCRIPTS / 'release_notes.py')
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)
CHANGELOG = '# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-11-01\n\n### Changed\n\n- **SemVer.** The API is a promise now.\n\n## [0.9.0] - 2026-10-20\n\n- Old.\n'
INVENTORY = dict(schema=2, repository='SkunkWerkx/HyperUuid', crate='hyperuuid', version='1.0.0',
                 source_sha='a' * 40, staging_sha='b' * 40, ci_run_id='42', package_run_id='43',
                 forge_sha='f' * 40, tag='v1.0.0',
                 files=[dict(path='ruby/hyperuuid-1.0.0.gem', location='packages'),
                        dict(path='pypi/x.whl', location='packages'),
                        dict(path='go/staticlib/linux_amd64/libhyperuuid.a', location='source',
                             source_sha='a' * 40, signer_repo='SkunkWerkx/.github'),
                        dict(path='swift/HyperUuidCore.artifactbundle/info.json', location='source')])


class ReleaseNotesTests(unittest.TestCase):
    def test_body_is_the_section_then_the_verifying_block(self):
        body = notes.notes(CHANGELOG, INVENTORY)
        self.assertTrue(body.startswith('### Changed\n\n- **SemVer.**'))
        self.assertNotIn('## [1.0.0]', body)
        self.assertNotIn('- Old.', body)
        self.assertIn('[CHANGELOG.md](https://github.com/SkunkWerkx/HyperUuid/blob/v1.0.0/CHANGELOG.md)', body)
        self.assertIn('## Verifying', body)
        self.assertIn('--source-ref refs/tags/v1.0.0 --predicate-type ' + notes.PREDICATE_TYPE, body)
        self.assertIn('--signer-repo SkunkWerkx/.github --source-digest ' + 'a' * 40, body)
        self.assertIn('[42](https://github.com/SkunkWerkx/HyperUuid/actions/runs/42)', body)
        self.assertIn('all 4 of them', body)
        self.assertIn('the 1 native libraries committed for Go, Swift and PHP, and the 1 metadata',
                      ' '.join(body.split()))
        self.assertIn('PEP 740', body)

    def test_missing_section_and_schema_one_are_errors(self):
        with self.assertRaisesRegex(ValueError, 'no "## \\[1.0.1\\]"'):
            notes.notes(CHANGELOG, {**INVENTORY, 'version': '1.0.1'})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'CHANGELOG.md').write_text(CHANGELOG)
            (root / 'inventory.json').write_text(json.dumps({**INVENTORY, 'schema': 1}))
            result = subprocess.run([sys.executable, str(SCRIPTS / 'release_notes.py'), '--changelog', str(root / 'CHANGELOG.md'),
                                     '--inventory', str(root / 'inventory.json'), '--output', str(root / 'notes.md')],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('schema 2', result.stderr)
            (root / 'inventory.json').write_text(json.dumps(INVENTORY))
            subprocess.run([sys.executable, str(SCRIPTS / 'release_notes.py'), '--changelog', str(root / 'CHANGELOG.md'),
                            '--inventory', str(root / 'inventory.json'), '--output', str(root / 'notes.md')], check=True)
            self.assertIn('## Verifying', (root / 'notes.md').read_text())


if __name__ == '__main__':
    unittest.main()
