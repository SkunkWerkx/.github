"""Bind preserved registry inputs to the exact A/B release, and reconcile retries.

The inventory is itself attested by the consumer. File attestations independently
bind reused wheels to A and assembled packages to B. No native compilation occurs.
"""
import argparse
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
import urllib.request
import zipfile

from release_package_metadata import validate_package_metadata

FORGE = 'SkunkWerkx/.github'
IDENTITY = ('repository', 'crate', 'version', 'source_sha', 'staging_sha',
            'ci_run_id', 'package_run_id', 'forge_sha')
PLATFORMS = ('x86_64-linux-gnu', 'aarch64-linux-gnu', 'x86_64-linux-musl',
             'aarch64-linux-musl', 'arm64-darwin', 'x64-mingw-ucrt', 'aarch64-mingw-ucrt')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
            if relative.split('/')[0] not in ('nuget','ruby','pypi','crate','maven'):
                raise ValueError(f'unexpected payload: {relative}')
            files.append(path)
    return files


def validate_layout(paths, crate, version):
    groups = {group: [p for p in paths if p.split('/')[0] == group]
              for group in ('nuget','ruby','pypi','crate','maven')}
    nuget = groups['nuget']
    if len(nuget) != 2 or sorted(Path(p).suffix for p in nuget) != ['.nupkg','.snupkg']:
        raise ValueError('NuGet package or symbols missing')
    for p in nuget:
        if Path(p).name.lower() != f'{crate}.{version}{Path(p).suffix}':
            raise ValueError(f'NuGet version/name mismatch: {p}')
    gems = {f'ruby/{crate}-{version}.gem', f'ruby/{crate}-wasm-{version}.gem'}
    gems.update(f'ruby/{crate}-{version}-{platform}.gem' for platform in PLATFORMS)
    if set(groups['ruby']) != gems:
        raise ValueError('Ruby platform/version inventory is incomplete')
    wheels = groups['pypi']
    if len(wheels) != 25:
        raise ValueError(f'expected 25 wheels, got {len(wheels)}')
    for p in wheels:
        if not Path(p).name.startswith(f'{crate}-{version}-') or not p.endswith('.whl'):
            raise ValueError(f'wheel version/name mismatch: {p}')
    crates = groups['crate']
    if len([p for p in crates if p.endswith('.crate')]) != 1 or not any(p.endswith(f'{crate}-{version}.crate') for p in crates):
        raise ValueError('crate version/package missing')
    if not any(p.endswith('.json') for p in crates) or not any(p.endswith('.bin') for p in crates):
        raise ValueError('saved crate request/metadata missing')
    if len(groups['maven']) < 1 or len([p for p in groups['maven'] if p.endswith('.zip')]) != 1:
        raise ValueError('Maven signed bundle missing')


def build_inventory(root, identity):
    validate_identity(identity)
    paths = payload_files(root)
    validate_layout([p.relative_to(root).as_posix() for p in paths], identity['crate'], identity['version'])
    validate_package_metadata(root, identity['crate'], identity['version'])
    files = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        files.append(dict(path=name, size=path.stat().st_size, sha256=sha256(path),
                          source_sha=identity['source_sha' if name.startswith('pypi/') else 'staging_sha'],
                          signer_repo=identity['repository'] if name.startswith('ruby/') else FORGE))
    return dict(schema=1, **identity, files=files)


def validate_inventory(root, saved, identity):
    current = build_inventory(root, identity)
    if saved != current:
        raise ValueError('preserved inventory identity, file set, provenance or bytes differ')


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


def attest_files(root, inventory, registry=None):
    for entry in inventory['files']:
        if registry and not entry['path'].startswith(registry+'/'):
            continue
        subprocess.run(['gh','attestation','verify',str(root/entry['path']),
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
        if error.code == 404:
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


def finalize(repo, version, subject, inventory):
    """Create only missing Go tag/Release; reject an existing different subject."""
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
        subprocess.run(['gh','release','create',f'v{version}','--repo',repo,'--verify-tag',
                        '--title',f'v{version}','--generate-notes',str(inventory)],check=True)
    else:
        release = json.loads(result.stdout)
        if release['isDraft']:
            raise ValueError('existing draft release requires reconciliation')
        if any(asset['name'] == inventory.name for asset in release['assets']):
            with tempfile.TemporaryDirectory() as directory:
                subprocess.run(['gh','release','download',f'v{version}','--repo',repo,
                                '--pattern',inventory.name,'--dir',directory],check=True)
                if (Path(directory)/inventory.name).read_bytes() != inventory.read_bytes():
                    raise ValueError('existing GitHub Release inventory differs from verified bytes')
        else:
            subprocess.run(['gh','release','upload',f'v{version}',str(inventory),'--repo',repo],check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['create','verify','run','registry-plan','registry-confirm','nuget-finalized','nuget-receipt','finalize'])
    parser.add_argument('--root',type=Path,default=Path('release-packages'))
    parser.add_argument('--registry',choices=['nuget','ruby','pypi','crate','maven'])
    parser.add_argument('--inventory-only',action='store_true',
                        help='Verify the signed inventory and every hash after individual package verification')
    parser.add_argument('--output',type=Path,default=Path('pending'))
    args = parser.parse_args()
    identity = {key:os.environ.get(key.upper(),'') for key in IDENTITY}
    if args.command == 'run':
        selected = os.environ.get('PREPARED_RUN_ID','')
        validate_identity({**identity,'package_run_id':selected})
        run = api(f"repos/{identity['repository']}/actions/runs/{selected}")
        validate_package_run(run,{**identity,'package_run_id':selected})
    elif args.command in ('create','verify'):
        if args.command == 'create':
            inventory = build_inventory(args.root,identity)
            (args.root/'inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
        else:
            inventory = json.loads((args.root/'inventory.json').read_text())
            # Recovery may originate in an earlier package run but must have the same
            # repository, A, B, source run and pinned Forge implementation.
            identity['package_run_id'] = str(inventory['package_run_id'])
            validate_inventory(args.root,inventory,identity)
            subprocess.run(['gh','attestation','verify',str(args.root/'inventory.json'),
                            '--repo',identity['repository'],'--source-digest',identity['staging_sha']],check=True)
        run = api(f"repos/{identity['repository']}/actions/runs/{identity['package_run_id']}")
        validate_package_run(run,identity,os.environ.get('GITHUB_RUN_ID',''))
        if not args.inventory_only:
            attest_files(args.root,inventory,args.registry)
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
        finalize(identity['repository'],identity['version'],identity['staging_sha'],args.root/'inventory.json')


if __name__ == '__main__':
    main()
