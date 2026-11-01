"""release_headings.py: the CHANGELOG and r/NEWS.md headings settled at the version by the
prepare workflow, a no-op when the owner already renamed them, a refusal on a missing section
or a ⟨placeholder⟩ left in it."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from test_hyper_config import write_config

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_headings', SCRIPTS / 'release_headings.py')
headings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(headings)

CHANGELOG = '# Changelog\n\n## [Unreleased]\n\n### Added\n\n- A thing.\n\n## [1.0.0] — 2026-10-20\n\n- Older.\n'


class HeadingTests(unittest.TestCase):
    def test_unreleased_becomes_the_dated_version_heading_once(self):
        once = headings.rename_unreleased(CHANGELOG, '1.1.0', '2026-11-01')
        self.assertIn('\n## [1.1.0] — 2026-11-01\n', once)
        self.assertNotIn('Unreleased', once)
        self.assertEqual(headings.rename_unreleased(once, '1.1.0', '2026-11-02'), once)
        with self.assertRaisesRegex(ValueError, r'no \[Unreleased\] or \[1.2.0\] section'):
            headings.rename_unreleased(once, '1.2.0', '2026-11-02')

    def test_news_heading_takes_the_version(self):
        self.assertEqual(headings.rename_news('# hyperuuid (development version)\n\n* A thing.\n', 'hyperuuid', '1.1.0'),
                         '# hyperuuid 1.1.0\n\n* A thing.\n')

    def test_news_heading_of_another_release_is_refused_not_relabelled(self):
        # r/NEWS.md's top heading is the last release's until the owner adds a new one: renaming
        # it would relabel that release's entries as this one's, and the CRAN tarball ships that.
        previous = '# hyperuuid 1.1.0\n\n* The 1.1.0 entries.\n'
        self.assertEqual(headings.rename_news(previous, 'hyperuuid', '1.1.0'), previous)
        with self.assertRaisesRegex(ValueError, r'names 1\.1\.0, not 1\.2\.0'):
            headings.rename_news(previous, 'hyperuuid', '1.2.0')
        with self.assertRaisesRegex(ValueError, 'development version'):
            headings.rename_news('# Something else\n', 'hyperuuid', '1.2.0')
        self.assertEqual(headings.rename_news('# hyperuuid 1.1.0\n', 'hyperuuid', '1.1.0'), '# hyperuuid 1.1.0\n')
        with self.assertRaises(ValueError):
            headings.rename_news('# Something else\n', 'hyperuuid', '1.1.0')

    def test_settle_rewrites_both_files_and_refuses_a_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            (root / 'CHANGELOG.md').write_text(CHANGELOG.replace('- A thing.', '- A thing of ⟨n⟩ KB.'))
            (root / 'r').mkdir()
            (root / 'r/NEWS.md').write_text('# hyperuuid (development version)\n\n* A thing.\n')
            with self.assertRaisesRegex(ValueError, 'placeholder'):
                headings.settle(root, 'hyperuuid', '1.1.0', '2026-11-01')
            self.assertIn('[Unreleased]', (root / 'CHANGELOG.md').read_text(), 'nothing is written on a refusal')
            (root / 'CHANGELOG.md').write_text(CHANGELOG)
            self.assertEqual(headings.settle(root, 'hyperuuid', '1.1.0', '2026-11-01'), ['CHANGELOG.md', 'r/NEWS.md'])
            self.assertIn('## [1.1.0] — 2026-11-01', (root / 'CHANGELOG.md').read_text())
            self.assertEqual((root / 'r/NEWS.md').read_text().splitlines()[0], '# hyperuuid 1.1.0')
            self.assertEqual(headings.settle(root, 'hyperuuid', '1.1.0', '2026-11-02'), [])
            (root / 'r/NEWS.md').unlink()
            self.assertEqual(headings.settle(root, 'hyperuuid', '1.1.0', '2026-11-02'), [], 'no NEWS.md is fine')


if __name__ == '__main__':
    unittest.main()
