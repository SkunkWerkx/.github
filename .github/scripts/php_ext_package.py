"""The PHP extension's release zips, for PIE's `pre-packaged-binary` download method.

One zip per platform and PHP minor (NTS only), attached to the GitHub Release, named the way PIE
looks a binary up and holding one file:

    Unix     php_{ext}-{tag}_php{X.Y}-{x86_64|arm64}-{linux|darwin}-{glibc|musl|bsdlibc}-nts.zip
             holding {ext}.so (a macOS build too: PIE copies `{ext}.so` and nothing else)
    Windows  php_{ext}-{tag}-{X.Y}-nts-{vs16|vs17}-x86_64.zip
             holding the zip's stem plus .dll, the name PIE's Windows installer looks for

`{ext}` is the extension's name, the consumer's crate (`--extension`, which the workflows pass as
`$CRATE`). `{tag}` is the release tag (`v1.1.0`): PIE puts Composer's pretty version in the name, which for
a tag is the tag itself. Every other segment comes from the PHP the extension was built against,
asked directly (`php -r`, and `php -i`'s "PHP Extension Build" line for the Windows compiler), on
the leg that built it.

The name is then checked against PIE's own code, never against this file's: pie_asset_names.php,
run by the same PHP, loads the pinned PIE phar and prints the names PIE would accept on this
platform (PrePackagedBinaryAssetName on Unix, WindowsExtensionAssetName on Windows). A zip whose
name is not on that list fails, so a naming bug here cannot vouch for itself. Those PIE classes
are @internal: PIE_VERSION moves by hand, and every move reruns the probe and its negative
controls. The phar's attestation (`gh attestation verify --owner php`) and its sha256 are checked
by the workflow that downloads it.

`verify` fails unless
  1. the zip has exactly one entry;
  2. the entry is {ext}.so on Unix, or the zip's stem plus .dll on Windows (and a DLL name PIE
     looks for);
  3. the zip's name is one PIE lists for this PHP;
  4. the name's version segment is --version.

Zips are deterministic (fixed time and mode), so a pack from the same library is byte-identical
with the same Python and zlib. (The zip is packed once and preserved, so a different zlib's
deflate bytes never meet the attested ones.)

  pack    --library F --php PHP --pie PHAR --version TAG --extension EXT --out DIR
          names and writes the zip, verifies it, and prints its path
  verify  ZIP --php PHP --pie PHAR --version TAG --extension EXT
  asset   --rid RID --minor X.Y --version TAG --extension EXT
          prints the name of the zip one release leg ships, from the platform table
  matrix  --mode pr|release --versions "8.3 8.4 8.5" --floor 8.3
          prints hyper-build-php-extension.yml's build matrix as JSON
  pie-pin prints PIE_VERSION and PIE_SHA256, for the workflow that fetches the phar

A failure is one line on stderr, `php_ext_package: <reason>`, and exit status 1.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import zipfile

PIE_VERSION = '1.5.5'
PIE_SHA256 = '2ca0dff6868f1b53c095625674deef38ac698588e0a25e4321cd834a5b7553bc'
PROBE = Path(__file__).resolve().with_name('pie_asset_names.php')
EPOCH = (1980, 1, 1, 0, 0, 0)
ARCHITECTURES = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'x64': 'x86_64', 'arm64': 'arm64', 'aarch64': 'arm64'}
FAMILIES = {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'windows'}
# Lowercase, and no `+` build metadata: PIE lowercases the names it looks up, and the version job
# only ever yields a bare vX.Y.Z (a pre-release suffix is allowed for a hand run).
TAG = re.compile(r'^v\d+\.\d+\.\d+(?:-[0-9a-z.]+)?$')
# A PHP extension name, which is also the crate's: PIE puts it in every name.
EXTENSION_NAME = re.compile(r'^[a-z][a-z0-9_]*$')
FACTS = '''echo json_encode([
    'os_family' => PHP_OS_FAMILY,
    'machine' => php_uname('m'),
    'php' => PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION,
    'zts' => (bool) PHP_ZTS,
    'debug' => (bool) PHP_DEBUG,
    'libc' => PHP_OS_FAMILY === 'Darwin' ? 'bsdlibc'
        : (PHP_OS_FAMILY === 'Linux' ? (glob('/lib/ld-musl-*.so.1') ? 'musl' : 'glibc') : null),
]);'''


# The platforms a zip is built for, each on its own runner: (rid, runner label, how the leg gets
# its PHP). Windows ARM64 has no native PHP build (PHP runs there as an x64 process), so win-x64
# serves it. osx-x64 builds on the Apple silicon runner against an x86_64 PHP under Rosetta 2:
# Homebrew bottles nothing for Intel macOS any more, and the Intel runner is in its sunset.
PLATFORMS = (
    ('linux-x64', 'ubuntu-26.04', 'glibc'),
    ('linux-arm64', 'ubuntu-26.04-arm', 'glibc'),
    ('linux-musl-x64', 'ubuntu-26.04', 'musl'),
    ('linux-musl-arm64', 'ubuntu-26.04-arm', 'musl'),
    ('osx-x64', 'macos-latest', 'macos'),
    ('osx-arm64', 'macos-latest', 'macos'),
    ('win-x64', 'windows-latest', 'windows'),
)
MINOR = re.compile(r'^\d+\.\d+$')
# The minors a release ships: hyper-build-native.yml's php_versions default and
# hyper-pie-acceptance.yml's minors default (test_forge_producers holds all three equal), and what
# the release inventory expects one zip per platform of.
MINORS = ('8.3', '8.4', '8.5')
# The Visual Studio each minor's php.net Windows build is compiled with, the `vsNN` PIE reads from
# `php -i` and puts in the name: VS16 for 8.3, VS17 from 8.4 on.
WINDOWS_COMPILERS = {'8.3': 'vs16'}
# The MSVC toolset a Windows leg links with, and the installer component that carries it, per
# compiler. PHP refuses an extension linked with a newer toolset than its own core: it compares
# the PE linker versions' minor tens digit (win32/winutil.c, is_compatible), so the 8.3 core
# (14.29) refuses a 14.3x or newer DLL, and the 8.4 and 8.5 cores (14.44) refuse 14.5x, which is
# what the runner's newest Visual Studio links with. Each is its compiler's last toolset, which
# php.net's builds use, so neither pin moves.
WINDOWS_TOOLSETS = {
    'vs16': ('14.29', 'Microsoft.VisualStudio.Component.VC.14.29.16.11.x86.x64'),
    'vs17': ('14.44', 'Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64'),
}
LIBCS = {'glibc': 'glibc', 'musl': 'musl', 'macos': 'bsdlibc'}


def minor_key(minor):
    return tuple(int(part) for part in minor.split('.'))


def matrix(mode, versions, floor):
    """The build legs. `release`: every platform on every minor, the zips a release ships.
    `pr`: every platform on the newest minor, plus linux-x64 and win-x64 on the floor (win-x64
    because the floor's Windows build can take another toolset). The stub diff runs on one leg,
    linux-x64 on the newest minor. A Windows leg names its MSVC toolset and component."""
    minors = versions.split()
    if not minors or len(set(minors)) != len(minors) or not all(MINOR.match(m) for m in minors):
        raise ValueError(f'php_versions must be distinct X.Y minors, space-separated, not {versions!r}')
    if floor and not MINOR.match(floor):
        raise ValueError(f'php_floor_version must be an X.Y minor or empty, not {floor!r}')
    newest = max(minors, key=minor_key)
    if mode == 'release':
        pairs = [(platform, minor) for minor in sorted(minors, key=minor_key) for platform in PLATFORMS]
    elif mode == 'pr':
        pairs = [(platform, newest) for platform in PLATFORMS]
        if floor and floor != newest:
            pairs += [(platform, floor) for platform in PLATFORMS if platform[0] in ('linux-x64', 'win-x64')]
    else:
        raise ValueError(f'mode is pr or release, not {mode!r}')
    return {'include': [leg(rid, runner, kind, minor, rid == 'linux-x64' and minor == newest)
                        for (rid, runner, kind), minor in pairs]}


def leg(rid, runner, kind, minor, stubs):
    entry = {'rid': rid, 'os': runner, 'kind': kind, 'minor': minor, 'stubs': stubs}
    if kind == 'windows':
        entry['msvc_toolset'], entry['msvc_component'] = WINDOWS_TOOLSETS[windows_compiler(minor)]
    return entry


def windows_compiler(minor):
    return WINDOWS_COMPILERS.get(minor, 'vs17')


def leg_asset_name(rid, minor, tag, extension):
    """The zip one release leg (platform `rid`, PHP `minor`) ships, from the platform table."""
    require_tag(tag)
    require_extension(extension)
    if not MINOR.match(minor):
        raise ValueError(f'--minor is an X.Y minor, not {minor!r}')
    kind = next((kind for name, _runner, kind in PLATFORMS if name == rid), None)
    if kind is None:
        raise ValueError(f'no PHP leg for {rid!r}: one of {", ".join(name for name, _, _ in PLATFORMS)}')
    family, arch = rid.split('-')[0], ARCHITECTURES[rid.rsplit('-', 1)[1]]
    if kind == 'windows':
        return f'php_{extension}-{tag}-{minor}-nts-{windows_compiler(minor)}-{arch}.zip'
    system = {'linux': 'linux', 'osx': 'darwin'}[family]
    return f'php_{extension}-{tag}_php{minor}-{arch}-{system}-{LIBCS[kind]}-nts.zip'


def release_asset_names(tag, extension, minors=MINORS):
    """Every zip a release attaches, PLATFORMS x minors, from the platform table: the names
    asset_name() gives each leg's PHP (a test holds the two equal), without a PHP to ask."""
    return [leg_asset_name(rid, minor, tag, extension)
            for minor in sorted(minors, key=minor_key) for rid, _runner, _kind in PLATFORMS]


def php_facts(php):
    """The target PHP's own answers: what a name is made of."""
    facts = json.loads(subprocess.run([php, '-r', FACTS], check=True, capture_output=True, text=True).stdout)
    info = subprocess.run([php, '-i'], check=True, capture_output=True, text=True).stdout
    build = re.search(r'PHP Extension Build[ =>\t]+(API\S*)', info)
    facts['extension_build'] = build.group(1) if build else None
    return facts


def require_tag(version):
    if not TAG.match(version):
        raise ValueError(f'--version is the release tag (vX.Y.Z, lowercase), not {version!r}')


def require_extension(extension):
    if not EXTENSION_NAME.match(extension):
        raise ValueError(f'--extension is a lowercase PHP extension name, not {extension!r}')


def is_windows(facts):
    return facts['os_family'] == 'Windows'


def asset_name(facts, version, extension):
    """(zip name, its one entry) for the PHP these facts describe."""
    require_extension(extension)
    family = FAMILIES.get(facts['os_family'])
    if family is None:
        raise ValueError(f'no PIE binary for {facts["os_family"]}: built for Linux, Darwin and Windows only')
    if facts['zts']:
        raise ValueError('a thread-safe (ZTS) PHP: only NTS builds are shipped')
    if facts['debug']:
        raise ValueError('a debug PHP: only release builds are shipped')
    arch = ARCHITECTURES.get(facts['machine'].lower())
    if arch is None:
        raise ValueError(f'unsupported architecture {facts["machine"]}: x86_64 or arm64 only')
    if family == 'windows':
        parts = (facts['extension_build'] or '').split(',')
        compilers = [part.lower() for part in parts if re.fullmatch(r'V[CS]\d+', part)]
        if len(compilers) != 1:
            raise ValueError(f'no compiler in "PHP Extension Build" ({facts["extension_build"]})')
        stem = f'php_{extension}-{version}-{facts["php"]}-nts-{compilers[0]}-{arch}'
        return f'{stem}.zip', f'{stem}.dll'
    return f'php_{extension}-{version}_php{facts["php"]}-{arch}-{family}-{facts["libc"]}-nts.zip', f'{extension}.so'


def pie_names(php, pie, version, extension):
    """The names PIE itself would accept for this PHP, from the phar's own code."""
    result = subprocess.run([php, str(PROBE), str(pie), version, extension],
                            check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f'pie_asset_names.php exited {result.returncode}: {result.stderr.strip()}')
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not names:
        raise ValueError('pie_asset_names.php listed no names')
    return names


def version_segment(name, windows, extension):
    ext = re.escape(extension)
    pattern = (rf'^php_{ext}-(?P<version>.+)-\d+\.\d+-[a-z0-9]+-[a-z0-9]+-[a-z0-9_]+\.zip$' if windows
               else rf'^php_{ext}-(?P<version>.+?)_php\d+\.\d+-.+\.zip$')
    match = re.match(pattern, name)
    return match.group('version') if match else None


def verify(path, php, pie, version, extension):
    """Raise unless the zip at path is one PIE would download and install for this PHP."""
    path = Path(path)
    require_tag(version)
    require_extension(extension)
    windows = is_windows(php_facts(php))
    names = pie_names(php, pie, version, extension)
    with zipfile.ZipFile(path) as archive:
        entries = archive.namelist()
    failures = []
    if len(entries) != 1:
        failures.append(f'check 1: {len(entries)} entries {entries}; a PIE zip holds exactly one')
    else:
        expected = f'{path.stem}.dll' if windows else f'{extension}.so'
        if entries[0] != expected:
            failures.append(f'check 2: the entry is {entries[0]!r}; PIE installs {expected!r}')
        elif windows and expected not in names:
            failures.append(f'check 2: PIE looks for none of {names}, not {expected!r}')
    if path.name not in names:
        failures.append(f'check 3: {path.name} is not a name PIE {PIE_VERSION} lists for this PHP: {names}')
    segment = version_segment(path.name, windows, extension)
    if segment != version:
        failures.append(f'check 4: the version segment is {segment!r}, not the release tag {version!r}')
    if failures:
        raise ValueError(f'{path.name}:\n  ' + '\n  '.join(failures))


def pack(library, php, pie, version, extension, out):
    """Write the one zip for library and this PHP into out, verify it, and return its path."""
    require_tag(version)
    require_extension(extension)
    library = Path(library)
    if not library.is_file():
        raise ValueError(f'missing library: {library}')
    name, entry = asset_name(php_facts(php), version, extension)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    info = zipfile.ZipInfo(entry, date_time=EPOCH)
    info.create_system = 3
    info.external_attr = 0o100755 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(info, library.read_bytes())
    try:
        verify(path, php, pie, version, extension)
    except BaseException:
        path.unlink()
        raise
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)
    packing = sub.add_parser('pack', help='name, write and verify one zip')
    packing.add_argument('--library', type=Path, required=True, help='the built extension (.so, .dylib or .dll)')
    checking = sub.add_parser('verify', help='check one zip against PIE')
    checking.add_argument('zip', type=Path)
    for command in (packing, checking):
        command.add_argument('--php', required=True, help='the PHP binary the extension was built against')
        command.add_argument('--pie', type=Path, required=True, help=f'the PIE {PIE_VERSION} phar')
        command.add_argument('--version', required=True, help='the release tag, vX.Y.Z')
        command.add_argument('--extension', required=True, help="the extension's name (the crate's)")
    packing.add_argument('--out', type=Path, required=True)
    leg = sub.add_parser('asset', help="print the name of one release leg's zip")
    leg.add_argument('--rid', required=True, help='the platform, as in the build matrix')
    leg.add_argument('--minor', required=True, help='the PHP minor, X.Y')
    leg.add_argument('--version', required=True, help='the release tag, vX.Y.Z')
    leg.add_argument('--extension', required=True, help="the extension's name (the crate's)")
    legs = sub.add_parser('matrix', help="print the build workflow's matrix as JSON")
    legs.add_argument('--mode', dest='leg_mode', required=True, help='pr or release')
    legs.add_argument('--versions', required=True, help='the PHP minors, space-separated')
    legs.add_argument('--floor', default='', help='the floor minor (pr mode adds it on linux-x64)')
    sub.add_parser('pie-pin', help='print PIE_VERSION and PIE_SHA256')
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        # One line, not a traceback: a missing PHP, a bad zip or a refused name.
        detail = str(error).replace('\n', ' ')
        raise SystemExit(f'php_ext_package: {detail}') from None


def run(args):
    if args.mode == 'matrix':
        print(json.dumps(matrix(args.leg_mode, args.versions, args.floor)))
    elif args.mode == 'pie-pin':
        print(PIE_VERSION, PIE_SHA256)
    elif args.mode == 'asset':
        print(leg_asset_name(args.rid, args.minor, args.version, args.extension))
    elif args.mode == 'pack':
        print(pack(args.library, args.php, args.pie, args.version, args.extension, args.out))
    else:
        verify(args.zip, args.php, args.pie, args.version, args.extension)
        print(f'{args.zip.name}: PIE {PIE_VERSION} lists it, one entry, version {args.version}')


if __name__ == '__main__':
    main()
