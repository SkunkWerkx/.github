"""Validate package identities from their contents before preserving or publishing.

This checks metadata and archive structure, not registry signatures. Provenance
and registry-specific signing verification remain the release inventory's job.
RubyGem YAML is inspected as a narrow scalar subset and never deserialized; an npm
tarball's package.json is parsed as JSON and nothing in it is run.

The counts (wheels, Ruby platform gems, npm platform packages) are the consumer's
`.github/hyper.json` values when a config is passed, the Forge defaults otherwise. The npm
and CRAN groups are optional: a consumer without those bindings ships neither.
"""
from email import policy
from email.parser import BytesParser
import gzip
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tomllib
from xml.etree import ElementTree as ET
import zipfile

from types import SimpleNamespace

import hyper_config
from package_preparation import crate_payload

NPM_SCOPE = '@skunkwerkx'
# The corpus members every R source tarball must carry (tests/testthat/corpus/), so a
# tarball missing one fails the tag run, not the CRAN check weeks later.
R_CORPUS_FILES = ('v5.json', 'sql_order.json', 'sql_order_refused.json', 'timestamp.json', 'inspect.json')
DEFAULTS = SimpleNamespace(wheels=hyper_config.WHEELS, ruby_platforms=hyper_config.RUBY_PLATFORMS,
                           node_platforms=hyper_config.NODE_PLATFORMS)


def npm_names(crate, platforms):
    """The root npm package's name, and its platform packages' names in napi-rs's form."""
    return crate, [f'{NPM_SCOPE}/{crate}-{suffix}' for suffix in platforms]


def npm_filename(name, version):
    """`npm pack`'s file name: a scoped name loses its `@` and its `/` becomes `-`."""
    return name.removeprefix('@').replace('/', '-') + f'-{version}.tgz'


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate metadata key: {key}')
        result[key] = value
    return result


def checked_paths(names):
    if len(names) != len(set(names)):
        raise ValueError('duplicate archive entries')
    for name in names:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '\\' in name or any(part in ('.', '..', '') for part in name.rstrip('/').split('/')):
            raise ValueError(f'unsafe archive entry: {name}')


def zip_entries(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        checked_paths([member.filename for member in members])
        if any(stat.S_ISLNK(member.external_attr >> 16) for member in members):
            raise ValueError('archive links are not package metadata')
        return {member.filename: archive.read(member) for member in members if not member.is_dir()}


def tar_entries(path):
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        checked_paths([member.name for member in members])
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError('archive links are not package metadata')
        return {member.name: archive.extractfile(member).read() for member in members if member.isfile()}


def one(values, description):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f'expected exactly one {description}')
    return values[0]


def xml_document(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('package XML must not declare entities')
    return ET.fromstring(data)


def xml_child(element, name):
    return one((child for child in element if child.tag.rsplit('}', 1)[-1] == name), name)


def xml_value(element, name):
    child = xml_child(element, name)
    if len(child) or child.text is None:
        raise ValueError(f'{name} must be a scalar')
    return child.text.strip()


def nuget(path, crate, version):
    entries = zip_entries(path)
    nuspec = one((data for name, data in entries.items() if name.endswith('.nuspec')), 'NuGet nuspec')
    document = xml_document(nuspec)
    metadata = xml_child(document, 'metadata')
    if xml_value(metadata, 'id').casefold() != crate.casefold() or xml_value(metadata, 'version') != version:
        raise ValueError(f'NuGet embedded package name/version differs: {path.name}')


def wheel(path, crate, version):
    entries = zip_entries(path)
    contents = one((data for name, data in entries.items() if name.endswith('.dist-info/METADATA')),
                   'wheel METADATA')
    metadata = BytesParser(policy=policy.default).parsebytes(contents)
    name = one(metadata.get_all('Name', []), 'wheel Name')
    actual_version = one(metadata.get_all('Version', []), 'wheel Version')
    normalize = lambda value: re.sub(r'[-_.]+', '-', str(value)).lower()
    if normalize(name) != normalize(crate) or str(actual_version) != version:
        raise ValueError(f'wheel embedded package name/version differs: {path.name}')


def yaml_scalar(value):
    value = value.strip()
    if value.startswith('"'):
        value = json.loads(value)
    elif value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', value):
        raise ValueError('unsupported RubyGem metadata scalar')
    return value


def gem(path, crate, version):
    entries = tar_entries(path)
    if 'metadata.gz' not in entries:
        raise ValueError('RubyGem metadata.gz missing')
    lines = gzip.decompress(entries['metadata.gz']).decode('utf-8').splitlines()
    if not lines or lines[0] != '--- !ruby/object:Gem::Specification':
        raise ValueError('unsupported RubyGem specification metadata')
    name_line = one((line for line in lines if line.startswith('name:')), 'RubyGem name')
    version_line = one((index for index, line in enumerate(lines) if line.startswith('version:')),
                       'RubyGem version')
    if lines[version_line] != 'version: !ruby/object:Gem::Version':
        raise ValueError('unsupported RubyGem version metadata')
    block = []
    for line in lines[version_line + 1:]:
        if line and not line.startswith(' '):
            break
        if line:
            block.append(line)
    nested = one(block, 'RubyGem version scalar')
    if not nested.startswith('  version:'):
        raise ValueError('unsupported RubyGem version scalar')
    expected = crate + ('-wasm' if path.name.startswith(f'{crate}-wasm-') else '')
    if yaml_scalar(name_line.split(':', 1)[1]) != expected or yaml_scalar(nested.split(':', 1)[1]) != version:
        raise ValueError(f'RubyGem embedded package name/version differs: {path.name}')


def rust_crate(path, crate, version):
    entries = tar_entries(path)
    normalized = one((name for name in entries if name.count('/') == 1 and name.endswith('/Cargo.toml')),
                     'normalized Cargo.toml')
    if normalized != f'{crate}-{version}/Cargo.toml':
        raise ValueError('crate archive root differs from release identity')
    package = tomllib.loads(entries[normalized].decode())['package']
    if package.get('name') != crate or package.get('version') != version:
        raise ValueError('crate embedded package name/version differs')
    metadata_bytes = (path.parent / 'metadata.json').read_bytes()
    metadata = json.loads(metadata_bytes, object_pairs_hook=unique_pairs)
    if metadata.get('name') != crate or metadata.get('vers') != version:
        raise ValueError('crate saved request metadata name/version differs')
    # Apply the publisher's own framing and complete dependency/features validation
    # at the global gate too, so malformed requests cannot fail only after other
    # registries ship.
    crate_payload(path.parent)


def maven(path, crate, version):
    entries = zip_entries(path)
    pom = one((name for name in entries if name.endswith('.pom')), 'Maven POM')
    prefix = f'io/github/skunkwerkx/{crate}/{version}/{crate}-{version}'
    if pom != prefix + '.pom' or any(not name.startswith(prefix) for name in entries):
        raise ValueError('Maven archive coordinate differs from release identity')
    document = xml_document(entries[pom])
    if (xml_value(document, 'groupId'), xml_value(document, 'artifactId'), xml_value(document, 'version')) != (
            'io.github.skunkwerkx', crate, version):
        raise ValueError('Maven embedded coordinate differs from release identity')
    required = [prefix + suffix for suffix in ('.pom', '.jar', '-sources.jar', '-javadoc.jar')]
    for name in required:
        if name not in entries:
            raise ValueError(f'Maven coordinate artifact missing: {name}')
    for name in entries:
        if name.endswith(('.pom', '.jar', '.module')) and not entries.get(name + '.asc'):
            raise ValueError(f'Maven artifact signature missing: {name}')
    # The publication helper also preserves the expanded repository. Bind it to the
    # signed ZIP so retry paths cannot accidentally use a different copy.
    repository = path.parent / 'repository'
    if repository.exists():
        actual = {file.relative_to(repository).as_posix(): file.read_bytes()
                  for file in repository.rglob('*') if file.is_file()}
        if actual != entries:
            raise ValueError('Maven expanded repository differs from preserved bundle')


def npm_package(path):
    """The package.json `npm pack` wrote into a tarball, parsed and never executed."""
    entries = tar_entries(path)
    if 'package/package.json' not in entries:
        raise ValueError(f'npm tarball has no package/package.json: {path.name}')
    manifest = json.loads(entries['package/package.json'], object_pairs_hook=unique_pairs)
    if not isinstance(manifest, dict) or not all(isinstance(manifest.get(key), str) for key in ('name', 'version')):
        raise ValueError(f'npm package.json needs a name and a version: {path.name}')
    return manifest


def npm_tarball(path, crate, version, platforms):
    """One of the npm packages: the root, whose optionalDependencies pin every platform
    package at this version, or a platform package. Its name must be the one its file says."""
    root, names = npm_names(crate, platforms)
    manifest = npm_package(path)
    name = manifest['name']
    if name not in (root, *names) or manifest['version'] != version or path.name != npm_filename(name, version):
        raise ValueError(f'npm embedded package name/version differs: {path.name}')
    if name == root and manifest.get('optionalDependencies') != {platform: version for platform in names}:
        raise ValueError(f'npm root optionalDependencies must pin every platform package at {version}')
    return name


def r_source_tarball(path, crate, version):
    """The R source package as `R CMD build` wrote it: DESCRIPTION, the vendored crates,
    the wrapper's lock (derived at staging, never committed, so only the tarball carries
    it), the core copy and the five corpus files under one top-level `{crate}/`."""
    entries = tar_entries(path)
    required = [f'{crate}/DESCRIPTION', f'{crate}/src/rust/vendor.tar.xz', f'{crate}/src/rust/Cargo.lock',
                f'{crate}/src/rust/core/Cargo.toml']
    required += [f'{crate}/tests/testthat/corpus/{name}' for name in R_CORPUS_FILES]
    for name in required:
        if name not in entries:
            raise ValueError(f'R source tarball member missing: {name}')
    if any(name.split('/')[0] != crate for name in entries):
        raise ValueError('R source tarball has entries outside its package directory')
    lines = entries[f'{crate}/DESCRIPTION'].decode('utf-8').splitlines()
    fields = {key: [line.split(':', 1)[1].strip() for line in lines if line.startswith(key + ':')]
              for key in ('Package', 'Version')}
    if fields != {'Package': [crate], 'Version': [version]}:
        raise ValueError('R source tarball DESCRIPTION package/version differs from release identity')


def validate_package_metadata(root: Path, crate: str, version: str, cfg=None) -> None:
    """Reject stale, renamed, ambiguous, or incomplete preserved registry inputs."""
    cfg = cfg or DEFAULTS
    gem_count = len(cfg.ruby_platforms) + 2
    try:
        packages = list((root / 'nuget').rglob('*.nupkg')) + list((root / 'nuget').rglob('*.snupkg'))
        if len(packages) != 2 or {path.suffix for path in packages} != {'.nupkg', '.snupkg'}:
            raise ValueError('expected NuGet package and symbols')
        for path in packages:
            nuget(path, crate, version)
        wheels = list((root / 'pypi').rglob('*.whl'))
        gems = list((root / 'ruby').rglob('*.gem'))
        if len(wheels) != cfg.wheels or len(gems) != gem_count:
            raise ValueError(f'expected {cfg.wheels} wheels and {gem_count} RubyGems')
        for path in wheels:
            wheel(path, crate, version)
        for path in gems:
            gem(path, crate, version)
        rust_crate(one((root / 'crate').rglob('*.crate'), 'crate package'), crate, version)
        maven(one((root / 'maven').rglob('*.zip'), 'Maven bundle'), crate, version)
        if (root / 'npm').exists():
            platforms = list(cfg.node_platforms.values())
            tarballs = list((root / 'npm').rglob('*'))
            names = [npm_tarball(path, crate, version, platforms) for path in tarballs]
            expected_root, expected = npm_names(crate, platforms)
            if sorted(names) != sorted([expected_root, *expected]):
                raise ValueError(f'expected the npm root package and {len(platforms)} platform packages, once each')
        if (root / 'cran').exists():
            r_source_tarball(one((root / 'cran').rglob('*'), 'R source tarball'), crate, version)
    except (OSError, UnicodeError, EOFError, KeyError, TypeError, zipfile.BadZipFile,
            tarfile.TarError, ET.ParseError) as error:
        raise ValueError(f'invalid package metadata: {error}') from error
