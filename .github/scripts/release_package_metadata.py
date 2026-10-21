"""Validate package identities from their contents before preserving or publishing.

This checks metadata and archive structure, not registry signatures. Provenance
and registry-specific signing verification remain the release inventory's job.
RubyGem YAML is inspected as a narrow scalar subset and never deserialized.
"""
from email import policy
from email.parser import BytesParser
import gzip
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tarfile
import tomllib
from xml.etree import ElementTree as ET
import zipfile

from package_preparation import crate_payload


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
    payload = path.read_bytes()
    expected = struct.pack('<I', len(metadata_bytes)) + metadata_bytes + struct.pack('<I', len(payload)) + payload
    if (path.parent / 'upload.bin').read_bytes() != expected:
        raise ValueError('crate upload request does not contain the preserved metadata and archive')
    # Apply the publisher's complete dependency/features validation at the global
    # gate too, so malformed requests cannot fail only after other registries ship.
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


def validate_package_metadata(root: Path, crate: str, version: str) -> None:
    """Reject stale, renamed, ambiguous, or incomplete preserved registry inputs."""
    try:
        packages = list((root / 'nuget').rglob('*.nupkg')) + list((root / 'nuget').rglob('*.snupkg'))
        if len(packages) != 2 or {path.suffix for path in packages} != {'.nupkg', '.snupkg'}:
            raise ValueError('expected NuGet package and symbols')
        for path in packages:
            nuget(path, crate, version)
        wheels = list((root / 'pypi').rglob('*.whl'))
        gems = list((root / 'ruby').rglob('*.gem'))
        if len(wheels) != 25 or len(gems) != 9:
            raise ValueError('expected 25 wheels and nine RubyGems')
        for path in wheels:
            wheel(path, crate, version)
        for path in gems:
            gem(path, crate, version)
        rust_crate(one((root / 'crate').rglob('*.crate'), 'crate package'), crate, version)
        maven(one((root / 'maven').rglob('*.zip'), 'Maven bundle'), crate, version)
    except (OSError, UnicodeError, EOFError, KeyError, TypeError, zipfile.BadZipFile,
            tarfile.TarError, ET.ParseError) as error:
        raise ValueError(f'invalid package metadata: {error}') from error
