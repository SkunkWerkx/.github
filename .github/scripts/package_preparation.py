"""Prepare immutable registry requests; publish them without invoking a packer.

Protocols: doc.rust-lang.org/cargo/reference/registry-web-api.html#publish and
central.sonatype.org/publish/publish-portal-api/. HTTP mutations are never retried
blindly. Receipts are written before requests, including requests whose response
may be lost. The caller must preserve receipts even when this process fails.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import struct
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def request(method, url, data=None, headers=None):
    headers = {'User-Agent': 'SkunkWerkx-HyperForge-release/1', **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def not_served(error):
    """Whether an HTTP error means the registry does not hold the file: a 404, or the 403
    AccessDenied an S3 bucket that refuses listing answers for a key it does not hold. Both
    static.crates.io and rubygems.org/downloads are such buckets (measured 2026-10-25: a
    version never published answers 403 with an S3 request ID and that body). Any other
    error, a bare 403 included, is not an absence."""
    if error.code == 404:
        return True
    if error.code != 403 or not (error.headers or {}).get('x-amz-request-id'):
        return False
    return b'<Code>AccessDenied</Code>' in (error.read(4096) or b'')


def github_json(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint], text=True))


def require_unattempted_publication(repo, run_id, sha, workflow_path, registry,
                                   current_run_id=None, current_attempt=None, api=github_json):
    """Permit a fresh upload only when complete attempt history proves none ran.

    A missing registry receipt says nothing about another registry in the same
    release. Inspect the exact publisher job in every previous attempt instead.
    Unknown, incomplete, deleted or attempted history is deliberately ambiguous.
    """
    if registry not in ('crate', 'maven') or not str(run_id).isdigit() or int(run_id) < 1:
        raise ValueError('Invalid publication history request')
    prefix = f'repos/{repo}/actions/runs/{run_id}'
    run = api(prefix)
    same_run = str(run_id) == str(current_run_id)
    def identity(record, attempt=None, completed=True):
        if (record.get('id') != int(run_id) or record.get('head_sha') != sha
                or record.get('repository', {}).get('full_name') != repo
                or record.get('head_repository', {}).get('full_name') != repo
                or record.get('path', '').split('@', 1)[0] != workflow_path
                or record.get('event') not in ('push', 'workflow_dispatch')
                or completed and record.get('status') != 'completed'
                or attempt is not None and record.get('run_attempt') != attempt):
            raise ValueError('Prior publication history has wrong source/repository/workflow or is incomplete')
    identity(run, completed=not same_run)
    last_attempt = run.get('run_attempt')
    if not isinstance(last_attempt, int) or not 1 <= last_attempt <= 50:
        raise ValueError('Invalid or incomplete publication attempt history')
    if same_run:
        if not str(current_attempt).isdigit() or int(current_attempt) != last_attempt:
            raise ValueError('Current publication attempt does not match GitHub history')
        last_attempt -= 1
    job_name = {'maven': 'maven-publish / publish', 'crate': 'pack-crates / publish'}[registry]
    upload_names = {'maven': {'Publish the preserved Central bundle', 'Publish to Maven Central'},
                    'crate': {'Publish the preserved crate request', 'cargo publish'}}[registry]
    for attempt in range(1, last_attempt + 1):
        attempt_prefix = f'{prefix}/attempts/{attempt}'
        identity(api(attempt_prefix), attempt=attempt)
        jobs = []
        expected = None
        page = 1
        while True:
            batch = api(f'{attempt_prefix}/jobs?per_page=100&page={page}')
            count = batch.get('total_count')
            current = batch.get('jobs')
            if (not isinstance(count, int) or count < 0 or not isinstance(current, list)
                    or expected is not None and count != expected or not current and len(jobs) < count):
                raise ValueError('Publication jobs list is not complete')
            expected = count
            jobs.extend(current)
            if len(jobs) >= count:
                break
            if len(current) != 100:
                raise ValueError('Publication jobs list is not complete')
            page += 1
        if len(jobs) != expected or len({job.get('id') for job in jobs}) != expected:
            raise ValueError('Publication jobs list is not complete')
        publishers = [job for job in jobs if job.get('name') == job_name]
        if len(publishers) != 1:
            raise ValueError('Known publisher job is missing or ambiguous in complete history')
        job = publishers[0]
        if (job.get('run_id') != int(run_id) or job.get('run_attempt', attempt) != attempt
                or job.get('head_sha') != sha or job.get('status') != 'completed'):
            raise ValueError('Publisher job history is incomplete or belongs to another source')
        steps = job.get('steps', [] if job.get('conclusion') == 'skipped' else None)
        if not isinstance(steps, list):
            raise ValueError('Publisher step history is incomplete and ambiguous')
        for step in steps:
            if step.get('name') in upload_names and step.get('conclusion') != 'skipped':
                raise ValueError('Prior registry upload is ambiguous without its receipt; reconcile published bytes or recover the exact Central deployment ID')


def prior_upload_check(registry):
    repo = os.environ['GITHUB_REPOSITORY']
    reference = os.environ.get('CALLER_WORKFLOW_REF') or os.environ['GITHUB_WORKFLOW_REF']
    if not reference.startswith(repo + '/') or '@' not in reference:
        raise ValueError('Cannot identify the caller publication workflow')
    workflow_path = reference[len(repo) + 1:].split('@', 1)[0]
    # A recovery run can itself be rerun: an earlier attempt of this run may have
    # uploaded and lost its receipt, so its history is checked alongside the
    # recovered run's, never instead of it.
    current = os.environ['GITHUB_RUN_ID']
    runs = [current]
    recovery = os.environ.get('RECOVERY_RUN_ID', '')
    if recovery and recovery != current:
        runs.append(recovery)
    for run_id in runs:
        require_unattempted_publication(repo, run_id, os.environ['GITHUB_SHA'], workflow_path, registry,
                                       current_run_id=current,
                                       current_attempt=os.environ['GITHUB_RUN_ATTEMPT'])


def crate_metadata(crate):
    with tarfile.open(crate) as archive:
        manifests = [m for m in archive.getmembers() if m.name.count('/') == 1 and m.name.endswith('/Cargo.toml')]
        if len(manifests) != 1:
            raise ValueError('Expected exactly one normalized Cargo.toml')
        manifest = manifests[0]
        root = PurePosixPath(manifest.name).parent
        contents = tomllib.loads(archive.extractfile(manifest).read().decode())
        package = contents['package']
        if package.get('publish') is False or isinstance(package.get('publish'), list) and 'crates-io' not in package['publish']:
            raise ValueError('Manifest forbids crates.io publication')
        metadata = {'name': package['name'], 'vers': package['version'], 'deps': [],
                    'features': contents.get('features', {}), 'authors': package.get('authors', []),
                    'keywords': package.get('keywords', []), 'categories': package.get('categories', []),
                    'badges': contents.get('badges', {})}
        for key in ('description', 'documentation', 'homepage', 'license', 'repository', 'links'):
            metadata[key] = package.get(key)
        metadata['rust_version'] = package.get('rust-version')
        metadata['license_file'] = package.get('license-file')
        readme = package.get('readme')
        metadata['readme_file'] = readme if isinstance(readme, str) else None
        metadata['readme'] = None
        if isinstance(readme, str):
            path = root / readme
            if '..' in path.parts or path.is_absolute():
                raise ValueError('README must be inside the packaged crate')
            metadata['readme'] = archive.extractfile(str(path)).read().decode()
        def dependencies(table, target=None):
            for section, kind in (('dependencies', 'normal'), ('dev-dependencies', 'dev'), ('build-dependencies', 'build')):
                for alias, value in table.get(section, {}).items():
                    value = {'version': value} if isinstance(value, str) else value
                    if 'version' not in value or any(key in value for key in ('git', 'path', 'registry', 'registry-index')):
                        raise ValueError('Only normalized crates.io dependencies are supported')
                    metadata['deps'].append({'name': value.get('package', alias), 'version_req': value['version'],
                                             'features': value.get('features', []), 'optional': value.get('optional', False),
                                             'default_features': value.get('default-features', True), 'target': target,
                                             'kind': kind, 'registry': None,
                                             'explicit_name_in_toml': alias if 'package' in value else None})
        dependencies(contents)
        for target, table in contents.get('target', {}).items():
            dependencies(table, target)
        return metadata


def frame(metadata, crate):
    return struct.pack('<I', len(metadata)) + metadata + struct.pack('<I', len(crate)) + crate


def prepare_crate(crate, out):
    metadata = json.dumps(crate_metadata(crate), sort_keys=True, separators=(',', ':')).encode()
    payload = crate.read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(crate, out / crate.name)
    (out / 'metadata.json').write_bytes(metadata)
    (out / 'upload.bin').write_bytes(frame(metadata, payload))


def crate_payload(directory):
    crates = list(directory.glob('*.crate'))
    if len(crates) != 1:
        raise ValueError('Expected one preserved .crate')
    metadata = (directory / 'metadata.json').read_bytes()
    payload = crates[0].read_bytes()
    if json.loads(metadata) != crate_metadata(crates[0]):
        raise ValueError('Saved upload metadata does not describe the saved crate')
    body = (directory / 'upload.bin').read_bytes()
    if body != frame(metadata, payload):
        raise ValueError('Saved upload request differs from attested metadata/crate')
    return json.loads(metadata), payload, body


def publish_crate(directory, receipt, token, sleep=time.sleep, prior_check=None):
    metadata, payload, body = crate_payload(directory)
    name, version = metadata['name'], metadata['vers']
    url = f'https://static.crates.io/crates/{name}/{name}-{version}.crate'
    expected = digest(payload)
    state = json.loads(receipt.read_text()) if receipt.exists() else {}
    if state and state.get('crate_sha256') != expected:
        raise ValueError('Receipt belongs to another crate payload')
    try:
        served = request('GET', url)
    except urllib.error.HTTPError as error:
        if not not_served(error):
            raise
    else:
        if digest(served) != expected:
            raise ValueError('Published crate version has a different digest')
        save(receipt, {'state': 'published', 'crate_sha256': expected, 'name': name, 'version': version})
        return
    if state.get('state') in ('upload-in-flight', 'uploaded', 'published'):
        raise ValueError('Previous crate upload is ambiguous or not yet visible; wait and reconcile before retrying')
    if prior_check is not None:
        prior_check()
    state = {'state': 'upload-in-flight', 'crate_sha256': expected, 'name': name, 'version': version}
    save(receipt, state)
    try:
        response = json.loads(request('PUT', 'https://crates.io/api/v1/crates/new', data=body,
                                      headers={'Authorization': token, 'Content-Type': 'application/octet-stream',
                                               'Accept': 'application/json'}))
    except urllib.error.HTTPError as error:
        # crates.io answers a refusal with an error status and its reason in the body, which
        # urllib would otherwise drop. The receipt stays upload-in-flight: reconcile first.
        detail = error.read(4096).decode(errors='replace')
        raise ValueError(f'crates.io refused the upload (HTTP {error.code}): {detail}') from None
    if response.get('errors'):
        raise ValueError(f'crates.io rejected upload: {response["errors"]}')
    state['state'] = 'uploaded'
    save(receipt, state)
    for _ in range(60):
        try:
            served = request('GET', url)
        except urllib.error.HTTPError as error:
            if not not_served(error):
                raise
        else:
            if digest(served) != expected:
                raise ValueError('Published crate differs from the saved package')
            state['state'] = 'published'
            save(receipt, state)
            return
        sleep(10)
    raise TimeoutError('Crate upload accepted but not visible; resume with this receipt')


def maven_bundle(repository, out):
    # Match the pinned 0.37.0 Central defaults: md5/sha1 checksums only,
    # no checksums of detached signatures and no repository metadata.
    files = sorted(p for p in repository.rglob('*')
                   if p.is_file() and 'maven-metadata' not in p.name
                   and p.suffix not in ('.sha256', '.sha512')
                   and not p.name.endswith(('.asc.md5', '.asc.sha1')))
    poms = [p for p in files if p.suffix == '.pom']
    if len(poms) != 1:
        raise ValueError('Expected exactly one Maven coordinate')
    pom = poms[0]
    stem = pom.stem
    required = [pom, pom.with_suffix('.jar'), pom.with_name(stem + '-sources.jar'), pom.with_name(stem + '-javadoc.jar')]
    for path in required:
        if path not in files or path.with_name(path.name + '.asc') not in files:
            raise ValueError(f'Missing required signed Maven artifact: {path.name}')
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(repository.resolve()):
            raise ValueError('Maven repository contains a link outside its tree')
        if path.suffix in ('.jar', '.pom', '.module') and path.with_name(path.name + '.asc') not in files:
            raise ValueError(f'Missing signature: {path.name}')
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out / 'central-bundle.zip', 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            relative = path.relative_to(repository)
            bundle.write(path, relative.as_posix())
            dest = out / 'repository' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)


def maven_registry_matches(package):
    found = missing = 0
    with zipfile.ZipFile(package / 'central-bundle.zip') as bundle:
        for name in bundle.namelist():
            try:
                served = request('GET', 'https://repo.maven.apache.org/maven2/' + name)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                missing += 1
            else:
                if served != bundle.read(name):
                    raise ValueError('Published Maven coordinate differs from preserved bundle')
                found += 1
    if found and missing:
        raise ValueError('Maven coordinate is partially visible; wait before reconciling')
    return bool(found)


def confirm_maven(package, attempts=120, sleep=time.sleep):
    # Central reports PUBLISHED before repo1 and its CDN serve every file, and
    # maven-check has just cached 404s for them; allow up to an hour.
    for attempt in range(attempts):
        try:
            if maven_registry_matches(package):
                return
        except ValueError as error:
            if 'partially visible' not in str(error):
                raise
        if attempt + 1 < attempts:
            sleep(30)
    raise TimeoutError('Maven publication is not fully visible; recover using the saved bundle and receipt')


def verify_deployment(package, deployment_id, auth):
    # A recovered Portal ID is accepted only after its deployed files match our
    # saved bundle. Human labels alone cannot bind an ambiguous upload to bytes.
    with zipfile.ZipFile(package / 'central-bundle.zip') as bundle:
        for name in bundle.namelist():
            url = f'https://central.sonatype.com/api/v1/publisher/deployment/{urllib.parse.quote(deployment_id, safe="")}/download/{name}'
            if request('GET', url, headers=auth) != bundle.read(name):
                raise ValueError('Recovered Central deployment differs from saved bundle')


def publish_maven(package, receipt, token, deployment_id=None, sleep=time.sleep, prior_check=None):
    payload = (package / 'central-bundle.zip').read_bytes()
    expected = digest(payload)
    auth = {'Authorization': f'Bearer {token}'}
    state = json.loads(receipt.read_text()) if receipt.exists() else {}
    if state and state.get('bundle_sha256') != expected:
        raise ValueError('Receipt belongs to another Maven bundle')
    if deployment_id:
        if state.get('deployment_id') not in (None, deployment_id):
            raise ValueError('Conflicting deployment IDs')
        verify_deployment(package, deployment_id, auth)
        state.update({'bundle_sha256': expected, 'deployment_id': deployment_id, 'state': 'uploaded'})
        save(receipt, state)
    if not state.get('deployment_id'):
        if state:
            raise ValueError('Central upload is ambiguous: recover the exact Portal deployment ID before retrying')
        if prior_check is not None:
            prior_check()
        state = {'state': 'upload-in-flight', 'bundle_sha256': expected}
        save(receipt, state)
        boundary = 'hyperforge-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="bundle"; filename="central-bundle.zip"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + payload + f'\r\n--{boundary}--\r\n'.encode()
        deployment_id = request('POST', 'https://central.sonatype.com/api/v1/publisher/upload?publishingType=USER_MANAGED&name=hyperforge-' + expected,
                                data=body, headers={**auth, 'Content-Type': f'multipart/form-data; boundary={boundary}'}).decode().strip()
        if not deployment_id or '/' in deployment_id or len(deployment_id) > 100:
            raise ValueError('Invalid Central deployment ID; preserve the ambiguous-upload receipt')
        state.update({'deployment_id': deployment_id, 'state': 'uploaded'})
        save(receipt, state)
    deployment_id = state['deployment_id']
    endpoint = 'https://central.sonatype.com/api/v1/publisher/'
    publish_requested = False
    for _ in range(180):
        status = json.loads(request('POST', endpoint + 'status?id=' + urllib.parse.quote(deployment_id, safe=''), headers=auth))
        phase = status['deploymentState']
        if phase == 'FAILED':
            raise ValueError(f'Central validation failed: {status.get("errors")}')
        if phase == 'PUBLISHED':
            state['state'] = 'published'
            save(receipt, state)
            return
        # A status response still naming VALIDATED permits the same transition
        # on the same deployment ID; this never creates a second upload. Request
        # it once per invocation: Central can keep reporting VALIDATED after
        # accepting it and may reject a repeated request for the deployment.
        if phase == 'VALIDATED' and not publish_requested:
            state['state'] = 'publish-in-flight'
            save(receipt, state)
            publish_requested = True
            request('POST', endpoint + 'deployment/' + urllib.parse.quote(deployment_id, safe=''), headers=auth)
        sleep(10)
    raise TimeoutError('Central deployment is pending; resume using its preserved ID and bundle')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('crate-prepare', 'crate-publish', 'maven-bundle', 'maven-publish', 'maven-check', 'maven-confirm'))
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.command == 'crate-prepare':
        prepare_crate(args.source, args.destination)
    elif args.command == 'crate-publish':
        publish_crate(args.source, args.destination, os.environ['CARGO_REGISTRY_TOKEN'],
                      prior_check=lambda: prior_upload_check('crate'))
    elif args.command == 'maven-bundle':
        maven_bundle(args.source, args.destination)
    elif args.command == 'maven-check':
        print('published=' + str(maven_registry_matches(args.source)).lower())
    elif args.command == 'maven-confirm':
        confirm_maven(args.source)
    else:
        token = base64.b64encode((os.environ['MAVEN_CENTRAL_USERNAME'] + ':' + os.environ['MAVEN_CENTRAL_PASSWORD']).encode()).decode()
        publish_maven(args.source, args.destination, token, deployment_id=os.environ.get('CENTRAL_DEPLOYMENT_ID') or None,
                      prior_check=lambda: prior_upload_check('maven'))


if __name__ == '__main__':
    main()
