"""The PHP extension's PIE zips: the name from the target PHP's facts, the one entry, and the check
against the names PIE itself lists (stubbed here; the real phar is CI's, through
pie_asset_names.php)."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


php_ext_package = load('php_ext_package')
TAG = 'v1.1.0'
EXT = 'hyperuuid'
MUSL = {'os_family': 'Linux', 'machine': 'x86_64', 'php': '8.4', 'zts': False, 'debug': False, 'libc': 'musl',
        'extension_build': 'API20240924,NTS'}
DARWIN = {'os_family': 'Darwin', 'machine': 'arm64', 'php': '8.5', 'zts': False, 'debug': False,
          'libc': 'bsdlibc', 'extension_build': 'API20250925,NTS'}
WINDOWS = {'os_family': 'Windows', 'machine': 'AMD64', 'php': '8.4', 'zts': False, 'debug': False, 'libc': None,
           'extension_build': 'API20240924,NTS,VS17'}


def pie_unix(version, arch, os_family, libc, php):
    """What PrePackagedBinaryAssetName::packageNames() prints for an NTS, non-debug PHP."""
    stem = f'php_hyperuuid-{version}_php{php}-{arch}-{os_family}'
    libcs = (libc, 'anylibc') if os_family == 'linux' else (libc,)
    return [f'{stem}-{flavour}{ts}.{ext}' for flavour in libcs for ts in ('', '-nts') for ext in ('zip', 'tgz')]


def pie_windows(version, php, compiler):
    """What WindowsExtensionAssetName::zipNames() and dllNames() print for an NTS x86_64 PHP."""
    return [f'php_hyperuuid-{version}-{php}-{order}-x86_64.{ext}'
            for ext in ('zip', 'dll') for order in (f'nts-{compiler}', f'{compiler}-nts')]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def zip(self, name, entries):
        path = self.dir / name
        with zipfile.ZipFile(path, 'w') as archive:
            for entry in entries:
                archive.writestr(entry, b'\x7fELF extension')
        return path

    def verify(self, path, facts, names, version=TAG):
        with patch.object(php_ext_package, 'php_facts', return_value=facts), \
                patch.object(php_ext_package, 'pie_names', return_value=names) as pie:
            php_ext_package.verify(path, 'php', 'pie.phar', version, EXT)
        pie.assert_called_once_with('php', 'pie.phar', version, EXT)

    def assert_fails(self, path, facts, names, check, version=TAG):
        with self.assertRaises(ValueError) as caught:
            self.verify(path, facts, names, version)
        message = str(caught.exception)
        self.assertIn(f'check {check}', message)
        return message


class Verify(Fixture):
    def test_a_correct_unix_zip_passes(self):
        path = self.zip('php_hyperuuid-v1.1.0_php8.4-x86_64-linux-musl-nts.zip', ['hyperuuid.so'])
        self.verify(path, MUSL, pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4'))

    def test_a_correct_windows_zip_passes(self):
        name = 'php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64'
        path = self.zip(f'{name}.zip', [f'{name}.dll'])
        self.verify(path, WINDOWS, pie_windows(TAG, '8.4', 'vs17'))

    def test_a_musl_build_named_glibc_fails_check_3(self):
        path = self.zip('php_hyperuuid-v1.1.0_php8.4-x86_64-linux-glibc-nts.zip', ['hyperuuid.so'])
        message = self.assert_fails(path, MUSL, pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4'), 3)
        for check in (1, 2, 4):
            self.assertNotIn(f'check {check}', message)

    def test_a_windows_dll_not_named_for_the_zip_fails_check_2(self):
        path = self.zip('php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64.zip', ['php_hyperuuid.dll'])
        message = self.assert_fails(path, WINDOWS, pie_windows(TAG, '8.4', 'vs17'), 2)
        for check in (1, 3, 4):
            self.assertNotIn(f'check {check}', message)

    def test_a_unix_entry_not_named_for_the_extension_fails_check_2(self):
        path = self.zip('php_hyperuuid-v1.1.0_php8.5-arm64-darwin-bsdlibc-nts.zip', ['libhyperuuid.dylib'])
        self.assert_fails(path, DARWIN, pie_unix(TAG, 'arm64', 'darwin', 'bsdlibc', '8.5'), 2)

    def test_a_two_entry_zip_fails_check_1(self):
        path = self.zip('php_hyperuuid-v1.1.0_php8.4-x86_64-linux-musl-nts.zip', ['hyperuuid.so', 'README.md'])
        message = self.assert_fails(path, MUSL, pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4'), 1)
        for check in (3, 4):
            self.assertNotIn(f'check {check}', message)

    def test_a_bare_version_against_a_v_tag_fails_check_4(self):
        path = self.zip('php_hyperuuid-1.1.0_php8.4-x86_64-linux-musl-nts.zip', ['hyperuuid.so'])
        message = self.assert_fails(path, MUSL, pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4'), 4)
        self.assertIn("'1.1.0'", message)
        self.assertIn("'v1.1.0'", message)

    def test_a_bare_windows_version_against_a_v_tag_fails_check_4(self):
        name = 'php_hyperuuid-1.1.0-8.4-nts-vs17-x86_64'
        path = self.zip(f'{name}.zip', [f'{name}.dll'])
        self.assert_fails(path, WINDOWS, pie_windows(TAG, '8.4', 'vs17'), 4)

    def test_a_windows_dll_pie_would_not_look_for_fails_check_2(self):
        name = 'php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64'
        path = self.zip(f'{name}.zip', [f'{name}.dll'])
        zips_only = [n for n in pie_windows(TAG, '8.4', 'vs17') if n.endswith('.zip')]
        self.assert_fails(path, WINDOWS, zips_only, 2)

    def test_an_empty_pie_listing_never_passes(self):
        path = self.zip('php_hyperuuid-v1.1.0_php8.4-x86_64-linux-musl-nts.zip', ['hyperuuid.so'])
        self.assert_fails(path, MUSL, [], 3)


class Name(unittest.TestCase):
    def test_unix_names(self):
        self.assertEqual(php_ext_package.asset_name(MUSL, TAG, EXT),
                         ('php_hyperuuid-v1.1.0_php8.4-x86_64-linux-musl-nts.zip', 'hyperuuid.so'))
        self.assertEqual(php_ext_package.asset_name(DARWIN, TAG, EXT),
                         ('php_hyperuuid-v1.1.0_php8.5-arm64-darwin-bsdlibc-nts.zip', 'hyperuuid.so'))
        glibc_arm = {**MUSL, 'machine': 'aarch64', 'libc': 'glibc', 'php': '8.3'}
        self.assertEqual(php_ext_package.asset_name(glibc_arm, TAG, EXT)[0],
                         'php_hyperuuid-v1.1.0_php8.3-arm64-linux-glibc-nts.zip')

    def test_windows_name_takes_the_compiler_from_the_extension_build(self):
        stem = 'php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64'
        self.assertEqual(php_ext_package.asset_name(WINDOWS, TAG, EXT), (f'{stem}.zip', f'{stem}.dll'))
        vs16 = {**WINDOWS, 'php': '8.3', 'extension_build': 'API20230831,NTS,VS16'}
        self.assertEqual(php_ext_package.asset_name(vs16, TAG, EXT)[0], 'php_hyperuuid-v1.1.0-8.3-nts-vs16-x86_64.zip')

    def test_refuses_what_is_not_shipped(self):
        for facts, why in (({**MUSL, 'zts': True}, 'thread-safe'), ({**MUSL, 'debug': True}, 'debug'),
                           ({**WINDOWS, 'extension_build': 'API20240924,NTS'}, 'compiler'),
                           ({**WINDOWS, 'extension_build': 'API20240924,NTS,VS17', 'machine': 'x86'}, 'x86'),
                           ({**MUSL, 'os_family': 'BSD'}, 'BSD')):
            with self.subTest(why=why), self.assertRaises(ValueError) as caught:
                php_ext_package.asset_name(facts, TAG, EXT)
            self.assertIn(why, str(caught.exception))


class Pack(Fixture):
    def pack(self, facts, names):
        library = self.dir / 'libhyperuuid.so'
        library.write_bytes(b'\x7fELF extension')
        out = self.dir / 'out'
        with patch.object(php_ext_package, 'php_facts', return_value=facts), \
                patch.object(php_ext_package, 'pie_names', return_value=names):
            return php_ext_package.pack(library, 'php', 'pie.phar', TAG, EXT, out)

    def test_packs_one_entry_and_verifies_it(self):
        path = self.pack(MUSL, pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4'))
        self.assertEqual(path.name, 'php_hyperuuid-v1.1.0_php8.4-x86_64-linux-musl-nts.zip')
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ['hyperuuid.so'])
            self.assertEqual(archive.read('hyperuuid.so'), b'\x7fELF extension')
            info = archive.getinfo('hyperuuid.so')
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(info.external_attr >> 16, 0o100755)

    def test_packs_windows_with_the_dll_named_for_the_zip(self):
        path = self.pack(WINDOWS, pie_windows(TAG, '8.4', 'vs17'))
        self.assertEqual(path.name, 'php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64.zip')
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ['php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64.dll'])

    def test_is_deterministic(self):
        names = pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4')
        first = self.pack(MUSL, names).read_bytes()
        self.assertEqual(self.pack(MUSL, names).read_bytes(), first)

    def test_a_name_pie_does_not_list_fails_the_pack(self):
        with self.assertRaises(ValueError) as caught:
            self.pack(MUSL, pie_unix(TAG, 'x86_64', 'linux', 'glibc', '8.4'))
        self.assertIn('check 3', str(caught.exception))

    def test_refuses_a_bare_version(self):
        with self.assertRaises(ValueError) as caught:
            php_ext_package.pack(self.dir / 'x.so', 'php', 'pie.phar', '1.1.0', EXT, self.dir)
        self.assertIn('release tag', str(caught.exception))

    def test_refuses_an_uppercase_or_build_metadata_tag(self):
        # PIE lowercases the names it looks up, and the version job yields a bare vX.Y.Z.
        for tag in ('v1.1.0-RC1', 'v1.1.0+b', 'V1.1.0'):
            with self.subTest(tag=tag), self.assertRaises(ValueError) as caught:
                php_ext_package.pack(self.dir / 'x.so', 'php', 'pie.phar', tag, EXT, self.dir)
            self.assertIn('release tag', str(caught.exception))
        php_ext_package.require_tag('v1.1.0-rc.1')

    def test_names_the_extension_it_is_given(self):
        library = self.dir / 'libhypercast.so'
        library.write_bytes(b'\x7fELF extension')
        names = [n.replace('hyperuuid', 'hypercast') for n in pie_unix(TAG, 'x86_64', 'linux', 'musl', '8.4')]
        with patch.object(php_ext_package, 'php_facts', return_value=MUSL), \
                patch.object(php_ext_package, 'pie_names', return_value=names) as pie:
            path = php_ext_package.pack(library, 'php', 'pie.phar', TAG, 'hypercast', self.dir / 'out')
        pie.assert_called_once_with('php', 'pie.phar', TAG, 'hypercast')
        self.assertEqual(path.name, 'php_hypercast-v1.1.0_php8.4-x86_64-linux-musl-nts.zip')
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ['hypercast.so'])
        with self.assertRaisesRegex(ValueError, 'extension name'):
            php_ext_package.pack(library, 'php', 'pie.phar', TAG, 'Hyper-Cast', self.dir / 'out')


class Probe(unittest.TestCase):
    def run_probe(self, returncode, stdout='', stderr=''):
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with patch.object(php_ext_package.subprocess, 'run', return_value=result) as run:
            names = php_ext_package.pie_names('php', 'pie.phar', TAG, EXT)
        command = run.call_args.args[0]
        self.assertEqual(command, ['php', str(php_ext_package.PROBE), 'pie.phar', TAG, EXT])
        return names

    def test_reads_one_name_per_line(self):
        self.assertEqual(self.run_probe(0, 'a.zip\nb.tgz\n'), ['a.zip', 'b.tgz'])

    def test_a_probe_that_cannot_load_pie_fails_loudly(self):
        with self.assertRaises(ValueError) as caught:
            self.run_probe(2, stderr='pie_asset_names: PIE internals changed: Php\\Pie\\Platform\\TargetPlatform\n')
        self.assertIn('PIE internals changed', str(caught.exception))

    def test_a_probe_that_lists_nothing_fails(self):
        with self.assertRaises(ValueError):
            self.run_probe(0, '\n')

    def test_the_probe_is_beside_the_packer(self):
        self.assertTrue(php_ext_package.PROBE.is_file())
        self.assertRegex(php_ext_package.PIE_VERSION, r'^\d+\.\d+\.\d+$')
        self.assertRegex(php_ext_package.PIE_SHA256, r'^[0-9a-f]{64}$')


class Matrix(unittest.TestCase):
    """The build legs hyper-build-php-extension.yml runs, from its mode and minors."""
    RIDS = ['linux-x64', 'linux-arm64', 'linux-musl-x64', 'linux-musl-arm64', 'osx-arm64', 'win-x64']

    def legs(self, mode, versions='8.3 8.4 8.5', floor='8.3'):
        return php_ext_package.matrix(mode, versions, floor)['include']

    def test_release_is_every_platform_on_every_minor(self):
        legs = self.legs('release')
        pairs = [(leg['rid'], leg['minor']) for leg in legs]
        self.assertEqual(len(pairs), 18)
        self.assertEqual(set(pairs), {(rid, minor) for rid in self.RIDS for minor in ('8.3', '8.4', '8.5')})

    def test_pr_is_every_platform_on_the_newest_plus_the_floor_on_linux_x64_and_win_x64(self):
        pairs = [(leg['rid'], leg['minor']) for leg in self.legs('pr')]
        self.assertEqual(pairs, [(rid, '8.5') for rid in self.RIDS] + [('linux-x64', '8.3'), ('win-x64', '8.3')])
        # A floor that is the newest minor adds no second leg.
        self.assertEqual(len(self.legs('pr', floor='8.5')), 6)
        self.assertEqual(len(self.legs('pr', floor='')), 6)

    def test_the_stub_diff_runs_on_linux_x64_newest_only(self):
        for mode in ('pr', 'release'):
            stubs = [(leg['rid'], leg['minor']) for leg in self.legs(mode) if leg['stubs']]
            self.assertEqual(stubs, [('linux-x64', '8.5')], mode)

    def test_each_leg_names_its_runner_and_kind(self):
        kinds = {leg['rid']: (leg['os'], leg['kind']) for leg in self.legs('pr')}
        self.assertEqual(kinds, {
            'linux-x64': ('ubuntu-26.04', 'glibc'), 'linux-arm64': ('ubuntu-26.04-arm', 'glibc'),
            'linux-musl-x64': ('ubuntu-26.04', 'musl'), 'linux-musl-arm64': ('ubuntu-26.04-arm', 'musl'),
            'osx-arm64': ('macos-latest', 'macos'),
            'win-x64': ('windows-latest', 'windows')})

    def test_windows_legs_link_with_their_php_builds_toolset(self):
        # php.net's 8.3 is linked with 14.29, 8.4 and 8.5 with 14.44; PHP refuses a DLL whose
        # linker minor has a higher tens digit than its core's.
        toolsets = {leg['minor']: (leg['msvc_toolset'], leg['msvc_component'])
                    for leg in self.legs('release') if leg['kind'] == 'windows'}
        self.assertEqual(toolsets, {
            '8.3': ('14.29', 'Microsoft.VisualStudio.Component.VC.14.29.16.11.x86.x64'),
            '8.4': ('14.44', 'Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64'),
            '8.5': ('14.44', 'Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64')})
        for leg in self.legs('release'):
            if leg['kind'] != 'windows':
                self.assertNotIn('msvc_toolset', leg, leg['rid'])

    def test_newest_is_by_number_not_by_text(self):
        pairs = {(leg['rid'], leg['minor']) for leg in self.legs('pr', versions='8.9 8.10', floor='')}
        self.assertIn(('linux-x64', '8.10'), pairs)

    def test_refuses_what_it_cannot_build(self):
        for mode, versions, floor in (('nightly', '8.5', ''), ('pr', '', ''), ('pr', '8.5 8.5', ''),
                                      ('pr', '8', ''), ('pr', '8.5', '8.x'), ('release', '8.4 v8.5', '')):
            with self.subTest(mode=mode, versions=versions, floor=floor), self.assertRaises(ValueError):
                php_ext_package.matrix(mode, versions, floor)

    def test_cli_prints_the_matrix_and_the_pie_pin(self):
        run = lambda *args: subprocess.run(['python3', str(SCRIPTS / 'php_ext_package.py'), *args],
                                           check=True, capture_output=True, text=True).stdout
        import json
        self.assertEqual(json.loads(run('matrix', '--mode', 'release', '--versions', '8.3 8.4 8.5',
                                        '--floor', '8.3')), php_ext_package.matrix('release', '8.3 8.4 8.5', '8.3'))
        self.assertEqual(run('pie-pin').split(), [php_ext_package.PIE_VERSION, php_ext_package.PIE_SHA256])
        self.assertEqual(run('asset', '--rid', 'win-x64', '--minor', '8.3', '--version', TAG, '--extension', EXT),
                         'php_hyperuuid-v1.1.0-8.3-nts-vs16-x86_64.zip\n')

    def test_cli_failures_are_one_line_not_a_traceback(self):
        for args in (('pack', '--library', __file__, '--php', '/nonexistent/php', '--pie', 'pie.phar',
                      '--version', TAG, '--extension', EXT, '--out', tempfile.gettempdir()),
                     ('asset', '--rid', 'win-arm64', '--minor', '8.5', '--version', TAG, '--extension', EXT)):
            with self.subTest(mode=args[0]):
                result = subprocess.run(['python3', str(SCRIPTS / 'php_ext_package.py'), *args],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                lines = result.stderr.splitlines()
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertTrue(lines[0].startswith('php_ext_package: '), lines[0])
                self.assertNotIn('Traceback', result.stderr)



class ReleaseAssetNames(unittest.TestCase):
    """The names the release inventory expects, from the platform table, without a PHP to ask."""
    # What each leg's PHP reports (php_facts): the packer's own input, per kind of leg.
    FACTS = {'glibc': ('Linux', 'glibc'), 'musl': ('Linux', 'musl'), 'macos': ('Darwin', 'bsdlibc'),
             'windows': ('Windows', None)}
    MACHINES = {'x64': 'x86_64', 'arm64': 'aarch64'}
    BUILD = {'8.3': 'API20230831,NTS,VS16', '8.4': 'API20240924,NTS,VS17', '8.5': 'API20250925,NTS,VS17'}

    def test_eighteen_names_each_what_the_packer_names_that_leg(self):
        names = php_ext_package.release_asset_names(TAG, EXT)
        self.assertEqual(len(names), 18)
        self.assertEqual(names, [php_ext_package.leg_asset_name(leg['rid'], leg['minor'], TAG, EXT)
                                 for leg in php_ext_package.matrix('release', ' '.join(php_ext_package.MINORS),
                                                                   '')['include']])
        self.assertEqual(len(set(names)), 18)
        packed = []
        for leg in php_ext_package.matrix('release', ' '.join(php_ext_package.MINORS), '')['include']:
            family, libc = self.FACTS[leg['kind']]
            machine = 'AMD64' if family == 'Windows' else self.MACHINES[leg['rid'].rsplit('-', 1)[1]]
            facts = {'os_family': family, 'machine': machine, 'php': leg['minor'], 'zts': False, 'debug': False,
                     'libc': libc, 'extension_build': self.BUILD[leg['minor']]}
            packed.append(php_ext_package.asset_name(facts, TAG, EXT)[0])
        self.assertEqual(sorted(names), sorted(packed))

    def test_windows_is_vs16_on_8_3_and_vs17_after(self):
        windows = [n for n in php_ext_package.release_asset_names(TAG, EXT) if '-nts-vs' in n]
        self.assertEqual(windows, ['php_hyperuuid-v1.1.0-8.3-nts-vs16-x86_64.zip',
                                   'php_hyperuuid-v1.1.0-8.4-nts-vs17-x86_64.zip',
                                   'php_hyperuuid-v1.1.0-8.5-nts-vs17-x86_64.zip'])
        self.assertTrue(all(n.endswith('-nts.zip') for n in php_ext_package.release_asset_names(TAG, EXT)
                            if n not in windows))

    def test_the_version_segment_is_the_tag(self):
        with self.assertRaises(ValueError):
            php_ext_package.release_asset_names('1.1.0', EXT)
        self.assertTrue(all(n.startswith('php_hyperuuid-v1.1.0') for n in php_ext_package.release_asset_names(TAG, EXT)))


if __name__ == '__main__':
    unittest.main()
