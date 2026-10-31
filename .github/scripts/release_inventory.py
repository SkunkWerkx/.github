"""Bind preserved registry inputs to the exact A/B release, and reconcile retries.

The inventory is itself attested by the consumer. File attestations independently
bind reused wheels to A and assembled packages to B. No native compilation occurs.

The inventory (schema 2, written by `create --natives`) also lists the native payloads
committed at B and names the release tag, and is the predicate of the release attestation: one `actions/attest`
statement, signed by the consumer's release.yml at the tag, whose subjects are every file in
the inventory (`checksums` prints them in shasum form) and whose predicate type is
PREDICATE_TYPE (documented in docs/predicates/release-v1.md). `verify` checks it for every
file in scope with the bundle downloaded once, so a consumer's one-line command,

    gh attestation verify <file> --repo <owner>/<repo> --source-ref refs/tags/vX.Y.Z \
        --predicate-type <PREDICATE_TYPE>

is exactly what the release ran before publishing.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import c_package
import hyper_config
import php_ext_package
from package_preparation import not_served
from release_guard import LIBRARY_SUFFIXES
from release_package_metadata import DEFAULTS, npm_all_names, npm_filename, npm_package, validate_package_metadata

FORGE = 'SkunkWerkx/.github'
PREDICATE_TYPE = 'https://github.com/SkunkWerkx/.github/predicates/release/v1'
SCHEMA = 2
# The registries a release publishes to. The CRAN source tarball is inventoried and attested
# like a package but no job uploads it: the maintainer submits the preserved bytes by hand.
REGISTRIES = ('nuget', 'ruby', 'pypi', 'crate', 'maven', 'npm')
# `github`, `c` and `php` hold the files the finalizer attaches to the GitHub Release beside the
# inventory: with swift_remote, the two Swift zips the root Package.swift names (built at A);
# with c_assets, one C archive per platform (packed at B); and the PHP extension's PIE zips, one
# per platform and PHP minor (built and attested at A, in the ci.yml run), which PIE downloads
# from the Release.
RELEASE_ASSET_GROUPS = ('github', 'c', 'php')
INVENTORY_GROUPS = REGISTRIES + ('cran',) + RELEASE_ASSET_GROUPS
# Built and attested by the Forge at A, in the ci.yml run, rather than packed at B.
BUILT_AT_SOURCE = ('pypi/', 'github/', 'php/')
# Signed in the consumer's own release.yml at B rather than by the Forge.
CONSUMER_SIGNED = ('ruby/', 'npm/', 'cran/', 'c/')
IDENTITY = ('repository', 'crate', 'version', 'source_sha', 'staging_sha',
            'ci_run_id', 'package_run_id', 'forge_sha')
NPM_REGISTRY = 'https://registry.npmjs.org/'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def php_asset_names(version, extension):
    """The PIE zips a release of bare `version` attaches: php_ext_package.py's platforms on each of
    its minors, named with the release tag (`v` + version), the version PIE puts in the name, for
    the extension `extension` (the consumer's crate)."""
    return php_ext_package.release_asset_names(f'v{version}', extension)


def validate_identity(identity):
    if set(identity) != set(IDENTITY):
        raise ValueError('incomplete release identity')
    for key in ('source_sha', 'staging_sha', 'forge_sha'):
        if not re.fullmatch('[0-9a-f]{40}', str(identity[key])):
            raise ValueError(f'invalid {key}')
    for key in ('ci_run_id', 'package_run_id'):
        if not re.fullmatch('[1-9][0-9]*', str(identity[key])):
            raise ValueError(f'invalid {key}')
    if not re.fullmatch('[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', identity['repository']):
        raise ValueError('invalid repository')
    if not re.fullmatch('[a-z][a-z0-9_]*', identity['crate']):
        raise ValueError('invalid crate')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', identity['version']):
        raise ValueError('release requires a stable semantic version')


def payload_files(root):
    files = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f'payload must be a regular file: {relative}')
        if path.is_file() and relative != 'inventory.json':
            if relative.split('/')[0] not in INVENTORY_GROUPS:
                raise ValueError(f'unexpected payload: {relative}')
            files.append(path)
    return files


def layout_config(workspace, crate):
    """The consumer's hyper.json in the checkout at B, which fixes the wheel, gem and npm
    package counts; the Forge defaults when the tree has none (the fixtures)."""
    if workspace is None or not (Path(workspace) / hyper_config.CONFIG_PATH).is_file():
        return DEFAULTS
    cfg = hyper_config.load(workspace)
    if cfg.crate != crate:
        raise ValueError(f'hyper.json names crate {cfg.crate}, the release {crate}')
    return cfg


def validate_layout(paths, crate, version, cfg=None):
    cfg = cfg or DEFAULTS
    groups = {group: [p for p in paths if p.split('/')[0] == group] for group in INVENTORY_GROUPS}
    nuget = groups['nuget']
    if len(nuget) != 2 or sorted(Path(p).suffix for p in nuget) != ['.nupkg','.snupkg']:
        raise ValueError('NuGet package or symbols missing')
    for p in nuget:
        if Path(p).name.lower() != f'{crate}.{version}{Path(p).suffix}':
            raise ValueError(f'NuGet version/name mismatch: {p}')
    gems = {f'ruby/{crate}-{version}.gem', f'ruby/{crate}-wasm-{version}.gem'}
    gems.update(f'ruby/{crate}-{version}-{platform}.gem' for platform in cfg.ruby_platforms.values())
    if set(groups['ruby']) != gems:
        raise ValueError('Ruby platform/version inventory is incomplete')
    wheels = [p for p in groups['pypi'] if p.endswith('.whl')]
    sdists = [p for p in groups['pypi'] if p.endswith('.tar.gz')]
    if len(wheels) + len(sdists) != len(groups['pypi']):
        raise ValueError(f'pypi holds a file that is neither a wheel nor an sdist: {groups["pypi"]}')
    if len(wheels) != cfg.wheels:
        raise ValueError(f'expected {cfg.wheels} wheels, got {len(wheels)}')
    for p in wheels:
        if not Path(p).name.startswith(f'{crate}-{version}-'):
            raise ValueError(f'wheel version/name mismatch: {p}')
    if sdists != ([f'pypi/{crate}-{version}.tar.gz'] if cfg.sdist else []):
        raise ValueError(f'sdist inventory is wrong: expected '
                         f'{"only " + crate + "-" + version + ".tar.gz" if cfg.sdist else "none"}, got {sdists}')
    crates = groups['crate']
    if len([p for p in crates if p.endswith('.crate')]) != 1 or not any(p.endswith(f'{crate}-{version}.crate') for p in crates):
        raise ValueError('crate version/package missing')
    if not any(p.endswith('.json') for p in crates) or not any(p.endswith('.bin') for p in crates):
        raise ValueError('saved crate request/metadata missing')
    if len(groups['maven']) < 1 or len([p for p in groups['maven'] if p.endswith('.zip')]) != 1:
        raise ValueError('Maven signed bundle missing')
    # Optional groups: a consumer without the TypeScript or R binding ships neither.
    if groups['npm']:
        names = npm_all_names(crate, cfg.node_platforms.values(), getattr(cfg, 'node_wasi', ''))
        if set(groups['npm']) != {f'npm/{npm_filename(name, version)}' for name in names}:
            raise ValueError('npm root, platform and wasi package inventory is incomplete or stale')
    if groups['cran'] and groups['cran'] != [f'cran/{crate}_{version}.tar.gz']:
        raise ValueError('CRAN source tarball name/version mismatch')
    assets = sorted(f'github/{name}' for name in getattr(cfg, 'swift_assets', ()))
    if sorted(groups['github']) != assets:
        raise ValueError(f'GitHub Release assets are wrong: expected {assets}, got {sorted(groups["github"])}')
    archives = sorted(f'c/{name}' for name in c_package.asset_names(cfg, version)) if getattr(cfg, 'c_assets', False) else []
    if sorted(groups['c']) != archives:
        raise ValueError(f'C archives are wrong: expected {archives}, got {sorted(groups["c"])}')
    zips = sorted(f'php/{name}' for name in php_asset_names(version, crate))
    if sorted(groups['php']) != zips:
        missing, extra = sorted(set(zips) - set(groups['php'])), sorted(set(groups['php']) - set(zips))
        raise ValueError(f'PHP extension zips are wrong: missing {missing}, unexpected {extra}')


def native_files(workspace, natives):
    """The committed native payloads, as repository-relative paths under the checkout at B;
    empty for a consumer that commits none (swift_remote, the Go core as Go source)."""
    if workspace is None:
        raise ValueError('the inventory needs --workspace, the checkout at B')
    if len(natives) != len(set(natives)):
        raise ValueError('native payload list must be unique')
    for relative in natives:
        if relative.split('/')[0] in INVENTORY_GROUPS or relative == 'inventory.json':
            raise ValueError(f'native payload path collides with a package path: {relative}')
        path = Path(workspace) / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'native payload must be a committed regular file: {relative}')
    return list(natives)


def entry_path(root, workspace, entry):
    if entry.get('location') == 'source':
        if workspace is None:
            raise ValueError('the inventory needs --workspace, the checkout at B')
        return Path(workspace) / entry['path']
    return root / entry['path']


def build_inventory(root, identity, natives, workspace):
    validate_identity(identity)
    paths = payload_files(root)
    cfg = layout_config(workspace, identity['crate'])
    validate_layout([p.relative_to(root).as_posix() for p in paths], identity['crate'], identity['version'], cfg)
    validate_package_metadata(root, identity['crate'], identity['version'], cfg)
    files = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        files.append(dict(path=name, size=path.stat().st_size, sha256=sha256(path),
                          source_sha=identity['source_sha' if name.startswith(BUILT_AT_SOURCE) else 'staging_sha'],
                          signer_repo=identity['repository'] if name.startswith(CONSUMER_SIGNED) else FORGE))
    for entry in files:
        entry['location'] = 'packages'
    for relative in native_files(workspace, natives):
        path = Path(workspace) / relative
        entry = dict(path=relative, size=path.stat().st_size, sha256=sha256(path), location='source')
        # The libraries are committed at B, built and attested by the Forge at A, like the
        # wheels. The metadata staged beside them (the Swift bundle's info.json, the
        # XCFramework's Info.plist, headers and module maps) carries no build provenance;
        # the release attestation is what binds it.
        if path.suffix in LIBRARY_SUFFIXES:
            entry.update(source_sha=identity['source_sha'], signer_repo=FORGE)
        files.append(entry)
    return dict(schema=SCHEMA, **identity, tag=f"v{identity['version']}", files=files)


def validate_inventory(root, saved, identity, workspace):
    if saved.get('schema') != SCHEMA:
        raise ValueError(f'only a schema {SCHEMA} inventory verifies')
    natives = [e['path'] for e in saved['files'] if e.get('location') == 'source']
    current = build_inventory(root, identity, natives, workspace)
    if saved != current:
        raise ValueError('preserved inventory identity, file set, provenance or bytes differ')


def checksums(inventory):
    """Every inventory file in shasum form: the subjects of the release attestation."""
    return ''.join(f"{entry['sha256']}  {entry['path']}\n" for entry in inventory['files'])


def in_scope(entry, registry):
    return not registry or entry['path'].startswith(registry + '/')


def verify_release_attestation(root, inventory, registry=None, workspace=None, cwd=None):
    """Every file in scope is a subject of a release attestation whose predicate is this
    inventory, signed in the consumer's own repository at B. One bundle download, then
    offline verification per file: the bundle names every subject."""
    entries = [e for e in inventory['files'] if in_scope(e, registry)]
    if not entries:
        raise ValueError('no inventory files in scope')
    repo, staging = inventory['repository'], inventory['staging_sha']
    # Absolute: the download runs in its own directory, where the relative --root and
    # --workspace the actions pass name nothing.
    def path(entry):
        return str(entry_path(root, workspace, entry).resolve())
    with tempfile.TemporaryDirectory(dir=cwd) as directory:
        subprocess.run(['gh', 'attestation', 'download', path(entries[0]),
                        '--repo', repo, '--predicate-type', PREDICATE_TYPE], check=True, cwd=directory)
        bundles = list(Path(directory).glob('*.jsonl'))
        if len(bundles) != 1:
            raise ValueError('expected one release attestation bundle')
        for entry in entries:
            output = subprocess.check_output(['gh', 'attestation', 'verify', path(entry),
                                              '--repo', repo, '--bundle', str(bundles[0]),
                                              '--source-digest', staging, '--predicate-type', PREDICATE_TYPE,
                                              '--format', 'json'], text=True)
            statements = [r['verificationResult']['statement'] for r in json.loads(output)]
            if not any(s.get('predicateType') == PREDICATE_TYPE and s.get('predicate') == inventory
                       for s in statements):
                raise ValueError(f"release attestation predicate is not this inventory: {entry['path']}")


def validate_package_run(run, identity, current_run_id=''):
    expected = dict(id=int(identity['package_run_id']), head_sha=identity['staging_sha'],
                    path='.github/workflows/release.yml')
    for key,value in expected.items():
        if run.get(key) != value:
            raise ValueError(f'prepared run {key} does not match release B')
    if run.get('repository',{}).get('full_name','').lower() != identity['repository'].lower():
        raise ValueError('prepared run belongs to another repository')
    if run.get('event') not in ('push','workflow_dispatch'):
        raise ValueError('prepared run is not a release entry')
    if str(run['id']) != str(current_run_id) and run.get('status') != 'completed':
        raise ValueError('prior package run must have finished before recovery')


def attest_files(root, inventory, registry=None, workspace=None):
    for entry in inventory['files']:
        if not in_scope(entry, registry) or 'signer_repo' not in entry:
            continue
        subprocess.run(['gh','attestation','verify',str(entry_path(root, workspace, entry)),
                        '--repo', inventory['repository'], '--signer-repo', entry['signer_repo'],
                        '--source-digest',entry['source_sha']], check=True)


def api(endpoint):
    return json.loads(subprocess.check_output(['gh','api',endpoint], text=True))


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent':'HyperForge-release/1', 'Accept':'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if not_served(error):
            return None
        raise


def select_missing(files, remote):
    missing = []
    for path in files:
        served = remote(path)
        if served is None:
            missing.append(path)
        elif served != path.read_bytes():
            raise ValueError(f'registry conflict: {path.name} already contains different bytes')
    return missing


def compare_nuget(packed, served):
    def entries(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('NuGet ZIP has duplicate paths')
            return {name:archive.read(name) for name in names if name != '.signature.p7s'}
    if entries(packed) != entries(served):
        raise ValueError('NuGet registry conflict: repository-signed payload differs')


def registry_plan(root, registry, output):
    inventory = json.loads((root/'inventory.json').read_text())
    crate,version = inventory['crate'],inventory['version']
    paths = [root/e['path'] for e in inventory['files'] if e['path'].startswith(registry+'/')]
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('pending publication directory must be empty')
    if registry == 'ruby':
        pending = select_missing(paths, lambda p: fetch(f'https://rubygems.org/downloads/{p.name}'))
    elif registry == 'pypi':
        body = fetch(f'https://pypi.org/pypi/{crate}/{version}/json')
        urls = json.loads(body)['urls'] if body else []
        known = {u['filename']:u for u in urls}
        def wheel_remote(path):
            record = known.get(path.name)
            if record is None: return None
            if record['digests']['sha256'] != sha256(path):
                raise ValueError(f'PyPI registry conflict: {path.name}')
            url = record['url']
            if not url.startswith('https://files.pythonhosted.org/'):
                raise ValueError('unexpected PyPI artifact host')
            served = fetch(url)
            if served is None: raise ValueError('PyPI lists an unavailable artifact')
            return served
        pending = select_missing(paths, wheel_remote)
    elif registry == 'npm':
        def npm_remote(path):
            manifest = npm_package(path)
            name = urllib.parse.quote(manifest['name'], safe='@')
            body = fetch(f"{NPM_REGISTRY}{name}/{urllib.parse.quote(manifest['version'])}")
            if body is None: return None
            dist = json.loads(body).get('dist', {})
            with path.open('rb') as stream:
                local = 'sha512-' + base64.b64encode(hashlib.file_digest(stream, 'sha512').digest()).decode()
            if dist.get('integrity') != local:
                raise ValueError(f'npm registry conflict: {path.name} already contains different bytes')
            url = dist.get('tarball', '')
            if not url.startswith(NPM_REGISTRY):
                raise ValueError('unexpected npm artifact host')
            served = fetch(url)
            if served is None: raise ValueError('npm lists an unavailable artifact')
            return served
        # The publish loop pushes in this order: every platform package and the WASI package
        # before the root, whose optionalDependencies and dependencies name them.
        root_name = npm_filename(crate, version)
        paths.sort(key=lambda p: (p.name == root_name, p.name))
        pending = select_missing(paths, npm_remote)
    elif registry == 'nuget':
        packed = next(p for p in paths if p.suffix == '.nupkg')
        url = f'https://api.nuget.org/v3-flatcontainer/{crate}/{version}/{crate}.{version}.nupkg'
        body = fetch(url)
        if body is None:
            pending = paths
        else:
            finalized = root.parent/'nuget-finalized'
            finalized.mkdir(exist_ok=True)
            served = finalized/packed.name
            served.write_bytes(body)
            compare_nuget(packed,served)
            subprocess.run(['dotnet','nuget','verify',str(served),'--all'],check=True)
            # NuGet's symbols service does not expose the uploaded snupkg bytes.
            # Do not claim it was accepted from main-package visibility alone.
            receipt_run = os.environ.get('PUBLICATION_RUN_ID','')
            if not receipt_run:
                raise ValueError('NuGet package exists with matching payload; symbol publication is not publicly verifiable. Supply publication_run_id with a successful nuget-publication receipt, or reconcile symbols before retrying.')
            receipt_path = root.parent/'nuget-receipt'
            subprocess.run(['gh','run','download',receipt_run,'--repo',inventory['repository'],
                            '--name','nuget-publication-receipt','--dir',str(receipt_path)], check=True)
            receipt = json.loads((receipt_path/'receipt.json').read_text())
            if receipt != dict(staging_sha=inventory['staging_sha'],
                               packages={p.name:sha256(p) for p in paths}, symbols_accepted=True):
                raise ValueError('NuGet receipt does not match prepared packages')
            subprocess.run(['gh','attestation','verify',str(receipt_path/'receipt.json'),
                            '--repo',inventory['repository'],'--source-digest',inventory['staging_sha']],check=True)
            pending = []
    else:
        raise ValueError('unsupported consumer registry')
    for path in pending:
        shutil.copyfile(path,output/path.name)
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with open(target,'a') as stream: stream.write(f'pending={str(bool(pending)).lower()}\n')
    return pending


def confirm_registry(check, attempts=20, pause=lambda:time.sleep(15)):
    for attempt in range(attempts):
        if not check():
            return
        if attempt + 1 < attempts:
            pause()
    raise ValueError('prepared registry files are not all visible after bounded confirmation')


def nuget_finalized(root):
    inventory = json.loads((root/'inventory.json').read_text())
    crate,version = inventory['crate'],inventory['version']
    packed = next(root/e['path'] for e in inventory['files'] if e['path'].startswith('nuget/') and e['path'].endswith('.nupkg'))
    directory = root.parent/'nuget-finalized'
    directory.mkdir(exist_ok=True)
    served = directory/packed.name
    def check():
        body = fetch(f'https://api.nuget.org/v3-flatcontainer/{crate}/{version}/{crate}.{version}.nupkg')
        if body is None:
            return [packed]
        served.write_bytes(body)
        compare_nuget(packed,served)
        subprocess.run(['dotnet','nuget','verify',str(served),'--all'],check=True)
        return []
    confirm_registry(check,attempts=60,pause=lambda:time.sleep(20))


def finalize(repo, version, subject, inventory, notes=None):
    """Create only missing Go tag/Release; reject an existing different subject.

    `notes` is the release body release_notes.py wrote from the CHANGELOG; without it the
    Release gets GitHub's generated notes. The Release carries the inventory and every file
    under the packages' `github/`, `c/` and `php/` groups, each byte-identical to a copy already there."""
    assets = [inventory, *(path for group in RELEASE_ASSET_GROUPS
                           for path in sorted((inventory.parent / group).glob('*')))]
    def remote_ref(tag):
        output = subprocess.check_output(['git','ls-remote','origin',f'refs/tags/{tag}',f'refs/tags/{tag}^{{}}'],text=True)
        records = dict(line.split()[::-1] for line in output.splitlines())
        return records.get(f'refs/tags/{tag}^{{}}',records.get(f'refs/tags/{tag}'))
    if remote_ref(f'v{version}') != subject:
        raise ValueError('root release tag does not point to B')
    go = f'go/v{version}'
    existing = remote_ref(go)
    if existing and existing != subject:
        raise ValueError('existing Go tag points to a different commit')
    if not existing:
        subprocess.run(['git','-c','tag.gpgSign=false','-c','user.name=github-actions[bot]',
                        '-c','user.email=41898282+github-actions[bot]@users.noreply.github.com',
                        'tag','-a',go,subject,'-m',f'Go module v{version}'],check=True)
        subprocess.run(['git','push','origin',f'refs/tags/{go}'],check=True)
    result = subprocess.run(['gh','release','view',f'v{version}','--repo',repo,'--json','tagName,isDraft,assets'],capture_output=True,text=True)
    if result.returncode:
        # A failed read could be a network/auth problem; confirm a real 404 via REST.
        check = subprocess.run(['gh','api','--include',f'repos/{repo}/releases/tags/v{version}'],capture_output=True,text=True)
        if 'HTTP 404' not in check.stdout and 'HTTP 404' not in check.stderr:
            raise ValueError('could not establish whether the GitHub Release exists')
        body = ['--notes-file', str(notes)] if notes else ['--generate-notes']
        subprocess.run(['gh','release','create',f'v{version}','--repo',repo,'--verify-tag',
                        '--title',f'v{version}',*body,*map(str,assets)],check=True)
    else:
        release = json.loads(result.stdout)
        if release['isDraft']:
            raise ValueError('existing draft release requires reconciliation')
        attached = {asset['name'] for asset in release['assets']}
        for path in assets:
            if path.name in attached:
                with tempfile.TemporaryDirectory() as directory:
                    subprocess.run(['gh','release','download',f'v{version}','--repo',repo,
                                    '--pattern',path.name,'--dir',directory],check=True)
                    if (Path(directory)/path.name).read_bytes() != path.read_bytes():
                        raise ValueError(f'existing GitHub Release asset {path.name} differs from verified bytes')
            else:
                subprocess.run(['gh','release','upload',f'v{version}',str(path),'--repo',repo],check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['create','verify','run','registry-plan','registry-confirm','nuget-finalized','nuget-receipt','finalize','checksums'])
    parser.add_argument('--root',type=Path,default=Path('release-packages'))
    parser.add_argument('--registry',choices=list(REGISTRIES))
    parser.add_argument('--inventory-only',action='store_true',
                        help='Verify the signed inventory and every hash after individual package verification')
    parser.add_argument('--output',type=Path,default=Path('pending'))
    parser.add_argument('--natives',type=Path,
                        help='create (required): file listing the committed native payloads (release_guard.py paths)')
    parser.add_argument('--workspace',type=Path,
                        help='create and verify: the checkout at B holding the committed natives')
    parser.add_argument('--notes',type=Path,help='finalize: the release body written by release_notes.py')
    args = parser.parse_args()
    identity = {key:os.environ.get(key.upper(),'') for key in IDENTITY}
    if args.command == 'run':
        selected = os.environ.get('PREPARED_RUN_ID','')
        validate_identity({**identity,'package_run_id':selected})
        run = api(f"repos/{identity['repository']}/actions/runs/{selected}")
        validate_package_run(run,{**identity,'package_run_id':selected})
    elif args.command in ('create','verify'):
        if args.command == 'create':
            if not args.natives:
                raise ValueError('create needs --natives, the committed payloads release_guard.py paths lists')
            natives = args.natives.read_text().split()
            inventory = build_inventory(args.root,identity,natives,args.workspace)
            (args.root/'inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
        else:
            inventory = json.loads((args.root/'inventory.json').read_text())
            # Recovery may originate in an earlier package run but must have the same
            # repository, A, B, source run and pinned Forge implementation.
            identity['package_run_id'] = str(inventory['package_run_id'])
            validate_inventory(args.root,inventory,identity,args.workspace)
            subprocess.run(['gh','attestation','verify',str(args.root/'inventory.json'),
                            '--repo',identity['repository'],'--source-digest',identity['staging_sha']],check=True)
        run = api(f"repos/{identity['repository']}/actions/runs/{identity['package_run_id']}")
        validate_package_run(run,identity,os.environ.get('GITHUB_RUN_ID',''))
        if not args.inventory_only:
            attest_files(args.root,inventory,args.registry,args.workspace)
        if args.command == 'verify':
            # The release attestation is new at the gate, so even --inventory-only checks it.
            verify_release_attestation(args.root,inventory,args.registry,args.workspace)
    elif args.command == 'checksums':
        inventory = json.loads((args.root/'inventory.json').read_text())
        if inventory.get('schema') != SCHEMA:
            raise ValueError(f'release attestation subjects need a schema {SCHEMA} inventory')
        if args.output.resolve().is_relative_to(args.root.resolve()):
            raise ValueError('the checksums file must not be written inside the package root')
        args.output.write_text(checksums(inventory))
    elif args.command == 'registry-plan':
        registry_plan(args.root,args.registry,args.output)
    elif args.command == 'registry-confirm':
        def check():
            with tempfile.TemporaryDirectory() as directory:
                return registry_plan(args.root,args.registry,Path(directory))
        confirm_registry(check)
    elif args.command == 'nuget-finalized':
        nuget_finalized(args.root)
    elif args.command == 'nuget-receipt':
        saved = json.loads((args.root/'inventory.json').read_text())
        paths = [args.root/e['path'] for e in saved['files'] if e['path'].startswith('nuget/')]
        args.output.mkdir(parents=True,exist_ok=True)
        receipt = dict(staging_sha=saved['staging_sha'], packages={p.name:sha256(p) for p in paths},symbols_accepted=True)
        (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    else:
        finalize(identity['repository'],identity['version'],identity['staging_sha'],args.root/'inventory.json',args.notes)


if __name__ == '__main__':
    main()
