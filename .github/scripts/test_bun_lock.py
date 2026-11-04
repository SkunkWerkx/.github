"""bun_lock.py: Bun's text lockfile, read the way the wiring and the proofs need it."""
from pathlib import Path
import tempfile
import unittest

import bun_lock

LOCK = '''{
  "lockfileVersion": 1,
  "workspaces": {
    "": {
      "name": "a,}",
      "devDependencies": { "left-pad": "1.3.0", },
    },
  },
  "packages": {
    "left-pad": ["left-pad@1.3.0", "", {}, "sha512-regi+stry=="],

    "@skunkwerkx/hypercast": ["@skunkwerkx/hypercast@/work/.companions/hypercast-npm/x.tgz", {}, "sha512-tar+ball=="],

    "@skunkwerkx/linked": ["@skunkwerkx/linked@link:@skunkwerkx/linked", {}],

    "@scope/other": ["@scope/other@2.0.0", "https://mirror.example/", { "os": "linux" }, "sha512-mir+ror=="],

    "@scope/other/left-pad": ["left-pad@1.0.0", "", {}, "sha512-nes+ted=="],

    "windows": ["windows@C:\\\\work\\\\x.tgz", {}, "sha512-win=="],
  },
}
'''


class BunLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / bun_lock.LOCK).write_text(LOCK)

    def test_trailing_commas_go_and_strings_keep_theirs(self):
        lock = bun_lock.parse(LOCK)
        self.assertEqual(lock['workspaces']['']['name'], 'a,}')
        self.assertEqual(bun_lock.parse('{"a\\",]": [1,2,],}'), {'a",]': [1, 2]})
        with self.assertRaises(ValueError):
            bun_lock.parse('{"a": ]')

    def test_entry_has_the_shape_an_npm_lock_has(self):
        self.assertEqual(bun_lock.entry(self.dir, 'left-pad'),
                         {'version': '1.3.0', 'resolved': '', 'integrity': 'sha512-regi+stry=='})
        self.assertEqual(bun_lock.entry(self.dir, '@skunkwerkx/hypercast'),
                         {'version': None, 'resolved': 'file:/work/.companions/hypercast-npm/x.tgz',
                          'integrity': 'sha512-tar+ball=='})
        self.assertEqual(bun_lock.entry(self.dir, '@scope/other')['resolved'], 'https://mirror.example/')
        self.assertEqual(bun_lock.entry(self.dir, 'windows')['resolved'], 'file:C:\\work\\x.tgz')
        self.assertIsNone(bun_lock.entry(self.dir, 'absent'))

    def test_a_link_is_a_path_with_no_integrity(self):
        self.assertEqual(bun_lock.entry(self.dir, '@skunkwerkx/linked'),
                         {'version': None, 'resolved': 'link:@skunkwerkx/linked', 'integrity': None})

    def test_version_is_the_registry_copys(self):
        self.assertEqual(bun_lock.version(self.dir, '@scope/other'), '2.0.0')
        self.assertIsNone(bun_lock.version(self.dir, '@skunkwerkx/hypercast'))
        self.assertIsNone(bun_lock.version(self.dir, 'absent'))

    def test_entries_name_a_nested_copy_by_its_package(self):
        nested = next(e for e in bun_lock.entries(bun_lock.read(self.dir)) if e[0] == '@scope/other/left-pad')
        self.assertEqual(nested[1:3], ('left-pad', '1.0.0'))


if __name__ == '__main__':
    unittest.main()
