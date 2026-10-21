"""Offline tests for exact-byte package assembly and publication recovery."""
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

SCRIPT = Path(__file__).with_name('package_preparation.py')


class Packages(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'exact-byte preparation helper is missing')
        spec = importlib.util.spec_from_file_location('packages', SCRIPT)
        self.p = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.p)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def crate(self):
        crate = self.root / 'hyperuuid-1.2.3.crate'
        manifest = b'''[package]\nname="hyperuuid"\nversion="1.2.3"\nlicense="MIT"\nreadme="README.md"\n[features]\ndefault=["std"]\nstd=[]\n[dependencies.renamed]\npackage="real-name"\nversion="^2"\ndefault-features=false\noptional=true\n[target.'cfg(unix)'.dependencies.libc]\nversion="0.2"\n'''
        with tarfile.open(crate, 'w:gz') as archive:
            for name, data in [('Cargo.toml', manifest), ('README.md', b'crate readme')]:
                entry = tarfile.TarInfo('hyperuuid-1.2.3/' + name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return crate

    def test_publication_history_steps_have_github_credentials_and_caller_identity(self):
        workflows=SCRIPT.parents[1]/'workflows'
        for filename,step in [('hyper-publish-crate.yml','Publish the preserved crate request'),
                              ('hyper-publish-maven.yml','Publish the preserved Central bundle')]:
            with self.subTest(workflow=filename):
                block=(workflows/filename).read_text().split(f'      - name: {step}\n',1)[1].split('\n      - ',1)[0]
                self.assertIn('GH_TOKEN: ${{ github.token }}',block)
                self.assertIn('CALLER_WORKFLOW_REF: ${{ github.workflow_ref }}',block)

    def test_crate_upload_contains_saved_bytes_and_renamed_target_dependencies(self):
        crate = self.crate()
        original = crate.read_bytes()
        dest = self.root / 'prepared'
        self.p.prepare_crate(crate, dest)
        body = (dest / 'upload.bin').read_bytes()
        size = struct.unpack('<I', body[:4])[0]
        metadata = json.loads(body[4:4 + size])
        self.assertEqual(metadata['name'], 'hyperuuid')
        self.assertEqual(metadata['vers'], '1.2.3')
        self.assertEqual(metadata['readme'], 'crate readme')
        renamed = next(d for d in metadata['deps'] if d['name'] == 'real-name')
        self.assertEqual(renamed['explicit_name_in_toml'], 'renamed')
        self.assertEqual(renamed['default_features'], False)
        libc = next(d for d in metadata['deps'] if d['name'] == 'libc')
        self.assertEqual(libc['target'], 'cfg(unix)')
        self.assertEqual(struct.unpack('<I', body[4 + size:8 + size])[0], len(original))
        self.assertEqual(body[8 + size:], original)
        self.assertEqual((dest / crate.name).read_bytes(), original)
        self.assertEqual(json.loads((dest / 'metadata.json').read_text()), metadata)

    def test_crate_rejects_alternative_registry_dependencies(self):
        for dependency in ('registry = "internal"', 'registry-index = "sparse+https://example.com/index/"'):
            crate = self.root / 'other-1.0.0.crate'
            manifest = f'[package]\nname="other"\nversion="1.0.0"\n[dependencies.private]\nversion="1"\n{dependency}\n'.encode()
            with tarfile.open(crate, 'w:gz') as archive:
                entry = tarfile.TarInfo('other-1.0.0/Cargo.toml')
                entry.size = len(manifest)
                archive.addfile(entry, io.BytesIO(manifest))
            with self.subTest(dependency=dependency), self.assertRaisesRegex(ValueError, 'crates.io dependencies'):
                self.p.crate_metadata(crate)

    def test_crate_rejects_metadata_or_payload_changed_after_preparation(self):
        dest = self.root / 'prepared'
        self.p.prepare_crate(self.crate(), dest)
        (dest / 'metadata.json').write_text('{"name":"different"}')
        with self.assertRaises(ValueError):
            self.p.crate_payload(dest)

    def test_maven_bundle_preserves_complete_coordinate_but_excludes_repository_metadata(self):
        repo = self.root / 'repo/io/github/skunkwerkx/hyperuuid/1.2.3'
        repo.mkdir(parents=True)
        for name, content in [('hyperuuid-1.2.3.jar', b'jar'), ('hyperuuid-1.2.3-sources.jar', b'sources'),
                              ('hyperuuid-1.2.3-javadoc.jar', b'docs'), ('hyperuuid-1.2.3.pom', b'pom'),
                              ('hyperuuid-1.2.3.module', b'module')]:
            (repo / name).write_bytes(content)
            (repo / (name + '.asc')).write_bytes(b'signature')
            (repo / (name + '.sha1')).write_text(hashlib.sha1(content).hexdigest())
        (repo.parent / 'maven-metadata.xml').write_text('repository metadata')
        (repo / 'hyperuuid-1.2.3.jar.sha512').write_text('unused checksum')
        (repo / 'hyperuuid-1.2.3.jar.asc.md5').write_text('unused signature checksum')
        out = self.root / 'package'
        self.p.maven_bundle(self.root / 'repo', out)
        with zipfile.ZipFile(out / 'central-bundle.zip') as bundle:
            self.assertEqual(len(bundle.namelist()), 15)
            self.assertEqual(bundle.read('io/github/skunkwerkx/hyperuuid/1.2.3/hyperuuid-1.2.3.jar'), b'jar')
            self.assertFalse(any('maven-metadata' in name for name in bundle.namelist()))
        self.assertEqual((out / 'repository/io/github/skunkwerkx/hyperuuid/1.2.3/hyperuuid-1.2.3.jar').read_bytes(), b'jar')

    def test_maven_rejects_unsigned_or_incomplete_coordinate(self):
        repo = self.root / 'repo/io/github/skunkwerkx/hyperuuid/1.2.3'
        repo.mkdir(parents=True)
        (repo / 'hyperuuid-1.2.3.jar').write_bytes(b'jar')
        with self.assertRaises(ValueError):
            self.p.maven_bundle(self.root / 'repo', self.root / 'package')

    def test_crate_existing_matching_version_is_verified_without_reupload(self):
        dest = self.root / 'prepared'
        crate = self.crate()
        self.p.prepare_crate(crate, dest)
        receipt = self.root / 'receipt.json'
        calls = []
        def remote(method, url, **kwargs):
            calls.append(method)
            if method != 'GET':
                self.fail('Existing matching crate must never be uploaded again')
            return crate.read_bytes()
        with patch.object(self.p, 'request', side_effect=remote):
            self.p.publish_crate(dest, receipt, 'secret')
        self.assertEqual(json.loads(receipt.read_text())['state'], 'published')

    def test_crate_existing_different_version_bytes_fail_closed(self):
        dest = self.root / 'prepared'
        self.p.prepare_crate(self.crate(), dest)
        with patch.object(self.p, 'request', return_value=b'different package'):
            with self.assertRaisesRegex(ValueError, 'different digest'):
                self.p.publish_crate(dest, self.root / 'receipt.json', 'secret')

    def test_crate_ambiguous_upload_is_not_retried_while_registry_is_not_visible(self):
        dest = self.root / 'prepared'
        crate = self.crate()
        self.p.prepare_crate(crate, dest)
        receipt = self.root / 'receipt.json'
        receipt.write_text(json.dumps({'state': 'upload-in-flight', 'crate_sha256': hashlib.sha256(crate.read_bytes()).hexdigest()}))
        absent = urllib.error.HTTPError('url', 404, 'absent', {}, io.BytesIO())
        self.addCleanup(absent.close)
        with patch.object(self.p, 'request', side_effect=absent):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                self.p.publish_crate(dest, receipt, 'secret')

    def test_maven_registry_reconciliation_rejects_partial_or_different_coordinates(self):
        package = self.root / 'package'
        package.mkdir()
        with zipfile.ZipFile(package / 'central-bundle.zip', 'w') as bundle:
            bundle.writestr('io/github/skunkwerkx/lib/1/lib-1.jar', b'jar')
            bundle.writestr('io/github/skunkwerkx/lib/1/lib-1.pom', b'pom')
        absent = urllib.error.HTTPError('url', 404, 'absent', {}, io.BytesIO())
        self.addCleanup(absent.close)
        with patch.object(self.p, 'request', side_effect=[b'jar', b'pom']):
            self.assertTrue(self.p.maven_registry_matches(package))
        with patch.object(self.p, 'request', side_effect=[absent, absent]):
            self.assertFalse(self.p.maven_registry_matches(package))
        with patch.object(self.p, 'request', side_effect=[b'jar', absent]):
            with self.assertRaisesRegex(ValueError, 'partially'):
                self.p.maven_registry_matches(package)
        with patch.object(self.p, 'request', return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError, 'differs'):
                self.p.maven_registry_matches(package)

    def test_maven_confirmation_waits_for_full_publication_and_rejects_conflicts(self):
        package=self.root/'package'
        package.mkdir()
        with zipfile.ZipFile(package/'central-bundle.zip','w') as bundle:
            bundle.writestr('group/library/1/library-1.jar',b'jar')
            bundle.writestr('group/library/1/library-1.pom',b'pom')
        absent=urllib.error.HTTPError('url',404,'absent',{},io.BytesIO())
        self.addCleanup(absent.close)
        with patch.object(self.p,'request',side_effect=[b'jar',absent,b'jar',b'pom']):
            self.p.confirm_maven(package,attempts=2,sleep=lambda _:None)
        with patch.object(self.p,'request',return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError,'differs'):
                self.p.confirm_maven(package,attempts=2,sleep=lambda _:None)

    def history(self, registry='maven', attempts=1, upload='skipped'):
        repo = 'SkunkWerkx/HyperUuid'
        run = {'id': 66, 'head_sha': 'b' * 40, 'path': '.github/workflows/release.yml',
               'repository': {'full_name': repo}, 'head_repository': {'full_name': repo},
               'status': 'completed', 'run_attempt': attempts, 'event': 'push'}
        records = {f'repos/{repo}/actions/runs/66': dict(run)}
        job_name = {'maven': 'maven-publish / publish', 'crate': 'pack-crates / publish'}[registry]
        step_name = {'maven': 'Publish the preserved Central bundle', 'crate': 'Publish the preserved crate request'}[registry]
        for attempt in range(1, attempts + 1):
            prefix = f'repos/{repo}/actions/runs/66/attempts/{attempt}'
            records[prefix] = {**run, 'run_attempt': attempt}
            job = {'id': attempt, 'run_id': 66, 'run_attempt': attempt, 'head_sha': 'b' * 40,
                   'name': job_name, 'status': 'completed', 'conclusion': 'failure',
                   'steps': [{'name': 'Verify every prepared file against this release source B',
                              'status': 'completed', 'conclusion': 'failure'},
                             {'name': step_name, 'status': 'completed', 'conclusion': upload}]}
            records[prefix + '/jobs?per_page=100&page=1'] = {'total_count': 1, 'jobs': [job]}
        return repo, run, records

    def require_unused(self, registry='maven', records=None):
        repo, _, defaults = self.history(registry)
        return self.p.require_unattempted_publication(repo, '66', 'b' * 40,
                                                     '.github/workflows/release.yml', registry,
                                                     api=(records or defaults).__getitem__)

    def test_missing_receipt_allows_a_known_publisher_that_never_reached_upload(self):
        for registry in ('maven', 'crate'):
            self.require_unused(registry)

    def test_missing_receipt_rejects_attempted_upload_even_if_a_later_attempt_skipped_it(self):
        repo, _, records = self.history(attempts=2)
        first = records[f'repos/{repo}/actions/runs/66/attempts/1/jobs?per_page=100&page=1']['jobs'][0]
        for conclusion in ('success', 'failure', 'cancelled'):
            first['steps'][-1].update({'conclusion': conclusion, 'started_at': '2026-10-21T00:00:00Z'})
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                self.require_unused(records=records)

    def test_missing_receipt_requires_complete_job_pages_and_known_publisher_identity(self):
        repo, _, records = self.history()
        key = f'repos/{repo}/actions/runs/66/attempts/1/jobs?per_page=100&page=1'
        records[key]['jobs'][0]['name'] = 'unrelated job'
        with self.assertRaisesRegex(ValueError, 'publisher'):
            self.require_unused(records=records)
        records[key]['total_count'] = 2
        with self.assertRaisesRegex(ValueError, 'complete'):
            self.require_unused(records=records)

    def test_missing_receipt_rejects_incomplete_wrong_repository_workflow_and_sha_history(self):
        for key, value in (('status', 'in_progress'), ('head_sha', 'a' * 40),
                           ('path', '.github/workflows/ci.yml'), ('repository', {'full_name': 'attacker/repo'})):
            repo, _, records = self.history()
            records[f'repos/{repo}/actions/runs/66'][key] = value
            with self.assertRaises(ValueError):
                self.require_unused(records=records)

    def test_job_attempt_is_bound_by_endpoint_when_optional_job_field_is_absent(self):
        repo, _, records = self.history()
        job = records[f'repos/{repo}/actions/runs/66/attempts/1/jobs?per_page=100&page=1']['jobs'][0]
        job.pop('run_attempt')
        self.require_unused(records=records)

    def test_complete_job_pagination_finds_publisher_on_second_page(self):
        repo, _, records = self.history()
        prefix = f'repos/{repo}/actions/runs/66/attempts/1/jobs?per_page=100&page='
        publisher = records[prefix + '1']['jobs'][0]
        records[prefix + '1'] = {'total_count': 101, 'jobs': [
            {**publisher, 'id': i + 10, 'name': 'unrelated'} for i in range(100)]}
        records[prefix + '2'] = {'total_count': 101, 'jobs': [publisher]}
        self.require_unused(records=records)

    def test_same_run_initial_upload_needs_no_history_but_rerun_checks_previous_attempts(self):
        repo, run, records = self.history(attempts=2)
        top = records[f'repos/{repo}/actions/runs/66']
        top['status'] = 'in_progress'
        top['run_attempt'] = 1
        self.p.require_unattempted_publication(repo, '66', 'b' * 40,
                                              '.github/workflows/release.yml', 'maven',
                                              current_run_id='66', current_attempt='1', api=records.__getitem__)
        top['run_attempt'] = 2
        first = records[f'repos/{repo}/actions/runs/66/attempts/1/jobs?per_page=100&page=1']['jobs'][0]
        first['steps'][-1]['conclusion'] = 'failure'
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.p.require_unattempted_publication(repo, '66', 'b' * 40,
                                                  '.github/workflows/release.yml', 'maven',
                                                  current_run_id='66', current_attempt='2', api=records.__getitem__)

    def test_skipped_known_publisher_with_empty_steps_is_proven_never_attempted(self):
        repo, _, records = self.history()
        job = records[f'repos/{repo}/actions/runs/66/attempts/1/jobs?per_page=100&page=1']['jobs'][0]
        job.update({'conclusion': 'skipped', 'steps': []})
        self.require_unused(records=records)

    FAKE_GH = r'''#!/usr/bin/env python3
import json, os, re, sys
if '--slurp' in sys.argv and ('--jq' in sys.argv or '-q' in sys.argv):
    sys.exit('the `--slurp` option is not supported with `--jq` or `--template`')
if '.head_sha' in sys.argv:
    print('b' * 40)
    sys.exit(0)
run = re.search(r'/runs/([0-9]+)/artifacts', ' '.join(sys.argv))[1]
for created, name in json.loads(os.environ['RECEIPTS']).get(run, []):
    print(created + '\t' + name)
'''

    def locate_receipt(self, registry, receipts, recovery='66'):
        # The gh double prints what the step's per-page --jq filter would print.
        gh = self.root / 'gh'
        gh.write_text(self.FAKE_GH)
        gh.chmod(0o755)
        workflow = SCRIPT.parents[1] / 'workflows' / f'hyper-publish-{registry}.yml'
        tail = workflow.read_text().split('      - name: Locate the latest preserved publication receipt\n', 1)[1]
        lines = []
        for line in tail.split('        run: |\n', 1)[1].splitlines():
            if line and not line.startswith('          '):
                break
            lines.append(line)
        script = textwrap.dedent('\n'.join(lines))
        outputs = self.root / (registry + '-outputs')
        outputs.unlink(missing_ok=True)
        result = subprocess.run(['bash', '-c', script], env={**os.environ,
                                'PATH': str(self.root) + ':' + os.environ['PATH'],
                                'RECOVERY_RUN': recovery, 'GITHUB_RUN_ID': '77',
                                'GITHUB_REPOSITORY': 'SkunkWerkx/HyperUuid',
                                'GITHUB_SHA': 'b' * 40, 'GITHUB_OUTPUT': str(outputs),
                                'RECEIPTS': json.dumps(receipts)},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return outputs.read_text()

    def test_other_registry_receipt_does_not_abort_maven_or_crate_reconciliation(self):
        for registry in ('maven', 'crate'):
            self.assertEqual(self.locate_receipt(registry, {}), 'name=\nrun_id=\n')

    def test_receipt_lookup_prefers_this_runs_earlier_attempts_over_the_recovered_run(self):
        for registry in ('maven', 'crate'):
            name = f'{registry}-publication-receipt-'
            older = {'66': [['2026-10-20T00:00:00Z', name + '1']]}
            self.assertEqual(self.locate_receipt(registry, older), f'name={name}1\nrun_id=66\n')
            both = {**older, '77': [['2026-10-21T02:00:00Z', name + '2'], ['2026-10-21T01:00:00Z', name + '1']]}
            self.assertEqual(self.locate_receipt(registry, both), f'name={name}2\nrun_id=77\n')
            self.assertEqual(self.locate_receipt(registry, both, recovery=''), f'name={name}2\nrun_id=77\n')

    def test_recovery_upload_check_covers_this_runs_earlier_attempts_too(self):
        environment = {'GITHUB_REPOSITORY': 'SkunkWerkx/HyperUuid', 'GITHUB_SHA': 'b' * 40,
                       'CALLER_WORKFLOW_REF': 'SkunkWerkx/HyperUuid/.github/workflows/release.yml@refs/heads/master',
                       'GITHUB_RUN_ID': '77', 'GITHUB_RUN_ATTEMPT': '2'}
        for recovery, checked in (('66', ['77', '66']), ('77', ['77']), ('', ['77'])):
            with patch.dict(os.environ, {**environment, 'RECOVERY_RUN_ID': recovery}), \
                    patch.object(self.p, 'require_unattempted_publication') as require:
                self.p.prior_upload_check('maven')
            self.assertEqual([call.args[1] for call in require.call_args_list], checked)
            for call in require.call_args_list:
                self.assertEqual(call.kwargs, {'current_run_id': '77', 'current_attempt': '2'})

    def test_missing_receipt_still_allows_verified_explicit_central_deployment(self):
        package = self.root / 'package'
        package.mkdir()
        with zipfile.ZipFile(package / 'central-bundle.zip', 'w') as bundle:
            bundle.writestr('io/github/skunkwerkx/lib/1/lib-1.jar', b'jar')
        receipt = self.root / 'receipt.json'
        def remote(method, url, **kwargs):
            if '/download/' in url:
                return b'jar'
            if '/status?' in url:
                return b'{"deploymentState":"PUBLISHED"}'
            self.fail('An exact recovered deployment must never be uploaded again')
        def refuse():
            raise ValueError('Prior upload was attempted')
        with patch.object(self.p, 'request', side_effect=remote):
            self.p.publish_maven(package, receipt, 'secret', deployment_id='verified-id', prior_check=refuse)
        self.assertEqual(json.loads(receipt.read_text())['deployment_id'], 'verified-id')
        self.assertEqual(json.loads(receipt.read_text())['state'], 'published')

    def test_missing_receipt_cannot_start_a_fresh_upload_without_history_proof(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'central-bundle.zip').write_bytes(b'exact bundle')
        def refuse():
            raise ValueError('Prior upload is ambiguous')
        with patch.object(self.p, 'request', side_effect=AssertionError('No upload permitted')):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                self.p.publish_maven(package, self.root / 'receipt.json', 'secret', prior_check=refuse)

    def test_maven_ambiguous_upload_preserves_intent_and_forbids_blind_retry(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'central-bundle.zip').write_bytes(b'exact bundle')
        receipt = self.root / 'receipt.json'
        with patch.object(self.p, 'request', side_effect=TimeoutError('response lost')):
            with self.assertRaises(TimeoutError):
                self.p.publish_maven(package, receipt, 'secret', sleep=lambda _: None)
        saved = json.loads(receipt.read_text())
        self.assertEqual(saved['state'], 'upload-in-flight')
        self.assertEqual(saved['bundle_sha256'], hashlib.sha256(b'exact bundle').hexdigest())
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.p.publish_maven(package, receipt, 'secret', sleep=lambda _: None)

    def test_maven_resume_can_finish_publish_when_recorded_request_did_not_transition(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'central-bundle.zip').write_bytes(b'exact bundle')
        receipt = self.root / 'receipt.json'
        receipt.write_text(json.dumps({'state': 'publish-in-flight', 'deployment_id': 'deployment-1',
                                      'bundle_sha256': hashlib.sha256(b'exact bundle').hexdigest()}))
        responses = iter([b'{"deploymentState":"VALIDATED"}', b'', b'{"deploymentState":"PUBLISHED"}'])
        def remote(method, url, **kwargs):
            if '/upload' in url:
                self.fail('Recovery must never upload a second deployment')
            return next(responses)
        with patch.object(self.p, 'request', side_effect=remote):
            self.p.publish_maven(package, receipt, 'secret', sleep=lambda _: None)
        self.assertEqual(json.loads(receipt.read_text())['state'], 'published')

    def test_maven_requests_publication_once_while_central_still_reports_validated(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'central-bundle.zip').write_bytes(b'exact bundle')
        receipt = self.root / 'receipt.json'
        receipt.write_text(json.dumps({'state': 'uploaded', 'deployment_id': 'deployment-1',
                                      'bundle_sha256': hashlib.sha256(b'exact bundle').hexdigest()}))
        phases = iter(['VALIDATED', 'VALIDATED', 'VALIDATED', 'PUBLISHING', 'PUBLISHED'])
        publishes = []
        def remote(method, url, **kwargs):
            if '/status?' in url:
                return json.dumps({'deploymentState': next(phases)}).encode()
            self.assertIn('/deployment/deployment-1', url)
            publishes.append(url)
            return b''
        with patch.object(self.p, 'request', side_effect=remote):
            self.p.publish_maven(package, receipt, 'secret', sleep=lambda _: None)
        self.assertEqual(len(publishes), 1)
        self.assertEqual(json.loads(receipt.read_text())['state'], 'published')

    def test_maven_resume_polls_recorded_deployment_without_uploading_again(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'central-bundle.zip').write_bytes(b'exact bundle')
        receipt = self.root / 'receipt.json'
        receipt.write_text(json.dumps({'state': 'uploaded', 'deployment_id': 'deployment-1',
                                      'bundle_sha256': hashlib.sha256(b'exact bundle').hexdigest()}))
        responses = iter([b'{"deploymentState":"VALIDATED"}', b'', b'{"deploymentState":"PUBLISHED"}'])
        seen = []
        def remote(method, url, **kwargs):
            seen.append(url)
            return next(responses)
        with patch.object(self.p, 'request', side_effect=remote):
            self.p.publish_maven(package, receipt, 'secret', sleep=lambda _: None)
        self.assertFalse(any('/upload' in url for url in seen))
        self.assertEqual(json.loads(receipt.read_text())['state'], 'published')


if __name__ == '__main__':
    unittest.main()
