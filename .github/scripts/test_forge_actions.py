"""Contracts for the composite actions: actionlint never reads an action.yml, so this is
the only lint they get, and the release actions' gates are what keeps a tag run from
publishing."""
import json
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

import yaml_lite

ACTIONS = Path(__file__).resolve().parents[1] / 'actions'
RELEASE_ACTIONS = ('release-identity', 'release-guard', 'pack-gems', 'pack-npm', 'pack-r', 'verify-wheels',
                   'release-gate', 'publish-nuget', 'publish-rubygems', 'publish-npm', 'publish-pypi',
                   'finalize-release')
PREDICATE = 'https://github.com/SkunkWerkx/.github/predicates/release/v1'


class ActionFileTests(unittest.TestCase):
    def actions(self):
        paths = sorted(ACTIONS.glob('*/action.yml'))
        self.assertGreaterEqual(len(paths), len(RELEASE_ACTIONS) + 10)
        return paths

    def test_every_action_is_composite_with_a_shell_on_every_run_step(self):
        for path in self.actions():
            with self.subTest(action=path.parent.name):
                action = yaml_lite.load(path.read_text())
                self.assertEqual(action['runs']['using'], 'composite')
                self.assertTrue(action.get('description'))
                for step in action['runs']['steps']:
                    self.assertTrue(('run' in step) ^ ('uses' in step), step)
                    if 'run' in step:
                        self.assertIn('shell', step, f"{path.parent.name}: run step without shell: {step.get('name')}")
                for name, spec in action.get('inputs', {}).items():
                    self.assertIn('description', spec, name)
                    self.assertTrue(spec.get('required') is True or 'default' in spec, f'{name} needs required or default')

    def test_every_uses_is_sha_pinned_or_a_forge_local_action(self):
        for path in self.actions():
            for ref in re.findall(r'^\s*(?:- )?uses:\s*(\S+)', path.read_text(), re.M):
                with self.subTest(action=path.parent.name, ref=ref):
                    if ref.startswith('./'):
                        self.assertRegex(ref, r'^\./\.hyperforge/\.github/actions/[\w-]+$')
                        self.assertTrue((ACTIONS / ref.rsplit('/', 1)[1] / 'action.yml').exists(), ref)
                    else:
                        self.assertRegex(ref, r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$')

    def test_release_actions_exist_and_find_forge_scripts_through_action_path_or_identity(self):
        for name in RELEASE_ACTIONS:
            text = (ACTIONS / name / 'action.yml').read_text()
            with self.subTest(action=name):
                if name == 'release-identity':
                    self.assertIn('FORGE_SCRIPTS: ${{ github.action_path }}/../../scripts', text)
                    self.assertIn('>> "$GITHUB_ENV"', text)
                elif '$FORGE_SCRIPTS' in text:
                    self.assertTrue('uses: ./.hyperforge/.github/actions/release-identity' in text
                                    or 'FORGE_SCRIPTS: ${{ github.action_path }}/../../scripts' in text, name)
                self.assertNotIn('${{ secrets.', text, 'secrets are unavailable inside an action; take an input')
                self.assertNotIn('${{ vars.', text, 'vars are unavailable inside an action; take an input')


WORKFLOWS = ACTIONS.parent / 'workflows'
# A gate on one input's emptiness: `inputs.prepared_run_id == ''` and its `!=` complement
# between them cover every run, the fresh path and the recovery path.
EMPTINESS_GATE = re.compile(r"^(inputs\.[\w-]+) (==|!=) ''$")


def step_sequences():
    """(label, steps) for every composite action and every job of every reusable workflow."""
    for path in sorted(ACTIONS.glob('*/action.yml')):
        yield path.parent.name, yaml_lite.load(path.read_text())['runs']['steps']
    for path in sorted(WORKFLOWS.glob('*.yml')):
        for name, job in yaml_lite.load(path.read_text())['jobs'].items():
            if 'steps' in job:
                yield f'{path.name}:{name}', job['steps']


def written_files(run):
    """Files a run script writes by `--output` or a redirection, minus the runner's own."""
    files = set()
    for match in re.finditer(r'''(?:--output|>>?)\s+("?)([^\s"'|;&()]+)\1(?=[\s;|&)]|$)''', run):
        path = match[2]
        if not path.startswith(('$GITHUB_', '/dev/', '-')):
            files.add(path)
    return files


def step_text(step):
    def strings(node):
        if isinstance(node, dict):
            return [s for value in node.values() for s in strings(value)]
        if isinstance(node, list):
            return [s for value in node for s in strings(value)]
        return [node] if isinstance(node, str) else []
    return '\n'.join(strings({k: v for k, v in step.items() if k != 'if'}))


def conjuncts(condition):
    return {part.strip() for part in condition.split('&&')} if condition else set()


def written_on_every_path_the_reader_runs(writer_ifs, reader_if):
    if None in writer_ifs:
        return True
    if any(conjuncts(w) <= conjuncts(reader_if) for w in writer_ifs):
        return True
    gates = {EMPTINESS_GATE.match(w).groups() for w in writer_ifs if EMPTINESS_GATE.match(w)}
    return any((name, '==') in gates and (name, '!=') in gates for name, _ in gates)


class StepHandoffTests(unittest.TestCase):
    """A file one step writes and a later step reads must exist on every path the reader runs.
    The release gate once read release-checksums.txt on the publish dispatch, where only the
    tag run had written it, so the dispatch failed before any publisher ran."""

    def handoff_gaps(self, label, steps):
        gaps = []
        writers = {}
        for step in steps:
            condition = step.get('if')
            written = written_files(step.get('run', ''))
            for path, conditions in writers.items():
                if path in written:
                    continue
                if re.search(rf'(?<![\w/.-]){re.escape(path)}(?![\w.-])', step_text(step)) and \
                        not written_on_every_path_the_reader_runs(conditions, condition):
                    gaps.append(f"{label}: {step.get('name') or step.get('uses')} reads {path}, "
                                f"written only when {' or '.join(map(str, conditions))}")
            for path in written:
                writers.setdefault(path, []).append(condition)
        return gaps

    def test_no_step_reads_a_file_written_only_on_another_path(self):
        gaps = [gap for label, steps in step_sequences() for gap in self.handoff_gaps(label, steps)]
        self.assertEqual(gaps, [])

    def test_the_check_sees_a_file_written_on_one_path_and_read_on_both(self):
        gated = "inputs.prepared_run_id == ''"
        steps = [{'name': 'write', 'if': gated, 'run': 'tool checksums --output sums.txt'},
                 {'name': 'read', 'run': 'cat sums.txt'}]
        self.assertEqual(len(self.handoff_gaps('fixture', steps)), 1)
        steps.insert(1, {'name': 'rewrite', 'if': "inputs.prepared_run_id != ''",
                         'run': 'tool checksums --output sums.txt'})
        self.assertEqual(self.handoff_gaps('fixture', steps), [])
        same = [{'name': 'write', 'if': gated, 'run': 'echo > "$RUNNER_TEMP/a"'},
                {'name': 'attest', 'if': f'{gated} && inputs.publish', 'with': {'subject': '$RUNNER_TEMP/a'}}]
        self.assertEqual(self.handoff_gaps('fixture', same), [])


class ReleaseGateTests(unittest.TestCase):
    def steps(self, name):
        return yaml_lite.load((ACTIONS / name / 'action.yml').read_text())['runs']['steps']

    def test_gate_attests_every_file_from_the_inventory_with_the_release_predicate(self):
        steps = {s.get('name'): s for s in self.steps('release-gate')}
        create = steps['Verify every final package and create the source/run inventory']
        self.assertIn('release_guard.py" paths', create['run'])
        self.assertIn('create --natives', create['run'])
        self.assertIn('checksums --output release-checksums.txt', create['run'])
        attest = steps['Attest every release file from the tag']
        self.assertTrue(attest['uses'].startswith('actions/attest@'))
        self.assertEqual(attest['with']['subject-checksums'], 'release-checksums.txt')
        self.assertEqual(attest['with']['predicate-type'], PREDICATE)
        self.assertEqual(attest['with']['predicate-path'], 'release-packages/inventory.json')
        self.assertEqual(attest['if'], "inputs.prepared_run_id == ''")
        self.assertEqual(steps['Attest the complete package inventory']['if'], "inputs.prepared_run_id == ''")
        verify = steps['Revalidate complete preserved bytes and every provenance source']
        # Both paths: per-file build provenance is create's job on a fresh run and each
        # publisher's, just before its upload, on a recovery run.
        self.assertIn('verify --inventory-only --workspace .', verify['run'])
        self.assertNotIn('if', verify)
        self.assertNotIn('env', verify)
        names = [s.get('name') for s in self.steps('release-gate')]
        self.assertLess(names.index('Attest every release file from the tag'), names.index('Revalidate complete preserved bytes and every provenance source'))
        self.assertLess(names.index('Revalidate complete preserved bytes and every provenance source'), names.index('Preserve the complete verified release inputs'))
        # The recovery path downloads release-packages alone; the summary's checksums come
        # from the revalidated inventory there.
        relist = steps['List the release attestation subjects from the preserved inventory']
        self.assertEqual(relist['if'], "inputs.prepared_run_id != ''")
        self.assertIn('checksums --output release-checksums.txt', relist['run'])
        self.assertLess(names.index('Revalidate complete preserved bytes and every provenance source'),
                        names.index('List the release attestation subjects from the preserved inventory'))
        self.assertLess(names.index('List the release attestation subjects from the preserved inventory'),
                        names.index('Summarize the release'))

    def test_every_registry_mutation_is_gated_on_publish_and_pending(self):
        mutations = {'publish-nuget': ['Publish the preserved package and symbols', 'Record accepted package and symbols',
                                       'Attest successful NuGet receipt', 'Attest the finalized served package',
                                       'Attest the finalized served package from the tag'],
                     'publish-rubygems': ['Publish only missing preserved gems'],
                     'publish-npm': ['Publish only missing preserved npm packages']}
        waits = {'publish-nuget': ['Verify the finalized repository-signed package'],
                 'publish-rubygems': ['Verify every public gem matches the prepared bytes'],
                 'publish-npm': ['Verify every public npm package matches the prepared bytes']}
        for action, names in mutations.items():
            steps = {s.get('name'): s for s in self.steps(action)}
            for name in names:
                with self.subTest(action=action, step=name):
                    self.assertEqual(steps[name]['if'], "steps.pending.outputs.pending == 'true' && inputs.publish == 'true'")
            for name in waits[action]:
                with self.subTest(action=action, step=name):
                    self.assertEqual(steps[name]['if'], "inputs.publish == 'true'")
            first = next(s for s in self.steps(action) if 'release_inventory.py' in s.get('run', ''))
            self.assertIn('verify --registry', first['run'])
            self.assertIn('--workspace .', first['run'])
        # The OIDC exchanges prove registration on the tag run, so they wait on pending only.
        nuget = {s.get('name'): s for s in self.steps('publish-nuget')}
        self.assertEqual(nuget['NuGet login (OIDC)']['if'], "steps.pending.outputs.pending == 'true'")
        rubygems = next(s for s in self.steps('publish-rubygems') if s.get('uses', '').startswith('rubygems/configure-rubygems-credentials@'))
        self.assertEqual(rubygems['if'], "steps.pending.outputs.pending == 'true'")
        finalized = nuget['Attest the finalized served package from the tag']
        self.assertEqual(finalized['with']['predicate-type'], PREDICATE)
        self.assertEqual(finalized['with']['predicate-path'], 'release-packages/inventory.json')

    def test_gate_takes_npm_and_cran_packages_only_when_asked(self):
        # Off by default: HyperCast and HyperTabular ship neither, and their gates must not
        # change. On, each lands under its inventory group on the fresh path only.
        action = yaml_lite.load((ACTIONS / 'release-gate' / 'action.yml').read_text())
        for flag, artifact, group in (('npm_packages', 'npm-packages', 'npm'),
                                      ('cran_packages', 'cran-packages', 'cran')):
            with self.subTest(artifact=artifact):
                self.assertEqual(action['inputs'][flag]['default'], 'false')
                self.assertTrue(action['inputs'][flag]['description'])
                download = [s for s in action['runs']['steps']
                            if s.get('uses', '').startswith('actions/download-artifact@')
                            and s.get('with', {}).get('name') == artifact]
                self.assertEqual(len(download), 1)
                self.assertEqual(download[0]['with']['path'], f'release-packages/{group}')
                self.assertEqual(download[0]['if'], f"inputs.prepared_run_id == '' && inputs.{flag} == 'true'")
        names = [s.get('name') or s.get('with', {}).get('name') for s in action['runs']['steps']]
        verify = names.index('Verify every final package and create the source/run inventory')
        self.assertLess(names.index('npm-packages'), verify)
        self.assertLess(names.index('cran-packages'), verify)


    def test_gate_takes_the_php_zips_from_the_ci_run_into_the_php_group(self):
        # Every php-ext-<rid>-<minor> artifact of the ci.yml run at A, merged flat into
        # release-packages/php on the fresh path, before the inventory is created (which checks
        # their provenance and the exact name set); a recovery run has them in release-packages.
        steps = self.steps('release-gate')
        download = [s for s in steps if s.get('uses', '').startswith('actions/download-artifact@')
                    and s.get('with', {}).get('pattern') == 'php-ext-*']
        self.assertEqual(len(download), 1)
        self.assertEqual(download[0]['if'], "inputs.prepared_run_id == ''")
        self.assertEqual(download[0]['with']['path'], 'release-packages/php')
        self.assertIs(download[0]['with']['merge-multiple'], True)
        self.assertEqual(download[0]['with']['run-id'], '${{ inputs.ci_run_id }}')
        self.assertEqual(download[0]['with']['github-token'], '${{ inputs.token }}')
        self.assertLess(steps.index(download[0]),
                        [s.get('name') for s in steps].index('Verify every final package and create the source/run inventory'))

    def test_pypi_action_prepares_and_leaves_the_upload_to_the_caller(self):
        # pypa/gh-action-pypi-publish nested in a composite action looks for its image at
        # ghcr.io/<consumer>:<tag> (actions/runner#2473): both v1.0.0 uploads failed that way.
        action = yaml_lite.load((ACTIONS / 'publish-pypi' / 'action.yml').read_text())
        steps = action['runs']['steps']
        self.assertFalse([s for s in steps if 'pypa/' in s.get('uses', '')])
        self.assertNotIn('publish', action['inputs'])
        self.assertEqual(action['outputs']['pending']['value'], '${{ steps.pending.outputs.pending }}')
        runs = [s['run'] for s in steps if 'run' in s]
        self.assertIn('verify --registry pypi --workspace .', runs[0])
        self.assertIn('registry-plan --registry pypi --output pending-pypi', runs[-1])
        self.assertEqual(steps[-1].get('id'), 'pending')

    def test_finalizer_writes_notes_from_the_changelog(self):
        steps = {s.get('name'): s for s in self.steps('finalize-release')}
        self.assertIn('release_notes.py', steps['Write the release notes from the CHANGELOG']['run'])
        self.assertIn('finalize --notes', steps['Create or validate the Go tag and GitHub Release at B']['run'])
        self.assertIn('verify --inventory-only --workspace .', steps['Verify the final release inventory']['run'])

    def test_publishers_keep_their_registered_identity_and_gate_on_publish(self):
        workflows = ACTIONS.parent / 'workflows'
        for name, step in (('hyper-publish-crate.yml', 'Publish the preserved crate request'),
                           ('hyper-publish-maven.yml', 'Publish the preserved Central bundle')):
            text = (workflows / name).read_text()
            with self.subTest(workflow=name):
                self.assertRegex(text, r'      publish:\n(?:        .*\n)*?        type: boolean\n        default: true')
                tail = text.split(f'      - name: {step}\n', 1)[1]
                self.assertRegex(tail.splitlines()[0], r"^        if: inputs\.publish == true")
                self.assertIn('\njobs:\n  publish:\n', text)


class NpmAndCranActionTests(unittest.TestCase):
    def action(self, name):
        return yaml_lite.load((ACTIONS / name / 'action.yml').read_text())

    def steps(self, name):
        return self.action(name)['runs']['steps']

    def uses(self, name, prefix):
        return [s for s in self.steps(name) if s.get('uses', '').startswith(prefix)]

    def test_pack_npm_verifies_every_addon_then_packs_nine_tarballs_off_the_checkout(self):
        action = self.action('pack-npm')
        self.assertEqual(action['inputs']['node_version']['default'], '24')
        for name in ('run_id', 'source_sha', 'token'):
            self.assertIs(action['inputs'][name]['required'], True)
        download = self.uses('pack-npm', 'actions/download-artifact@')
        self.assertEqual([d['with']['pattern'] for d in download], ['node-*'])
        self.assertEqual(download[0]['with']['path'], 'artifacts/node')
        self.assertEqual(download[0]['with']['run-id'], '${{ inputs.run_id }}')
        runs = '\n'.join(s.get('run', '') for s in self.steps('pack-npm'))
        verify = next(s for s in self.steps('pack-npm') if 'gh attestation verify' in s.get('run', ''))
        self.assertIn('artifacts/node/node-*/*', verify['run'])
        self.assertIn('--signer-repo SkunkWerkx/.github', verify['run'])
        self.assertIn('--source-digest "$SOURCE_SHA"', verify['run'])
        # Packed on a copy: `npm pkg set` must never touch the committed package.json, and
        # napi's prepublish would publish and rewrite on its own.
        self.assertIn('$RUNNER_TEMP/pack', runs)
        self.assertIn('napi create-npm-dirs', runs)
        self.assertIn('npm pkg set "optionalDependencies.', runs)
        self.assertNotIn('prepublish', runs)
        self.assertNotIn('npm publish', runs)
        self.assertRegex(runs, r"grep -q optionalDependencies typescript/package\.json")
        names = [s.get('name') or s.get('uses') for s in self.steps('pack-npm')]
        attest = self.uses('pack-npm', 'actions/attest-build-provenance@')
        upload = self.uses('pack-npm', 'actions/upload-artifact@')
        self.assertEqual(len(attest), 1)
        self.assertEqual(len(upload), 1)
        self.assertEqual(attest[0]['with']['subject-path'], upload[0]['with']['path'])
        self.assertTrue(attest[0]['with']['subject-path'].endswith('*.tgz'))
        self.assertEqual(upload[0]['with']['name'], 'npm-packages')
        self.assertEqual(upload[0]['with']['retention-days'], 90)
        self.assertEqual(upload[0]['with']['if-no-files-found'], 'error')
        self.assertLess(names.index(attest[0]['name']), names.index(upload[0]['uses']))
        self.assertIn('release_package_metadata', runs)

    def test_pack_r_stages_verifies_builds_and_attests_the_source_tarball(self):
        action = self.action('pack-r')
        self.assertEqual(action['inputs']['r_version']['default'], 'release')
        self.assertNotIn('run_id', action['inputs'])
        setup = self.uses('pack-r', 'r-lib/actions/setup-r@')
        self.assertEqual(len(setup), 1)
        self.assertEqual(setup[0]['uses'], 'r-lib/actions/setup-r@f9a764fea8d5c63df6ef9a5c7795bf7deb5d7e05')
        self.assertEqual(setup[0]['with']['use-public-rspm'], True)
        self.assertEqual(setup[0]['with']['r-version'], '${{ inputs.r_version }}')
        self.assertEqual(len(self.uses('pack-r', 'dtolnay/rust-toolchain@')), 1)
        runs = '\n'.join(s.get('run', '') for s in self.steps('pack-r'))
        self.assertIn('bash r/tools/vendor.sh\n', runs)
        self.assertIn('bash r/tools/vendor.sh --verify', runs)
        self.assertLess(runs.index('bash r/tools/vendor.sh\n'), runs.index('bash r/tools/vendor.sh --verify'))
        # vendor.sh regenerates inst/AUTHORS from the vendored crates: a stale committed copy
        # fails the pack rather than shipping, and it is checked before R CMD build packs it.
        authors = 'git diff --exit-code -- r/inst/AUTHORS'
        self.assertIn(authors, runs)
        self.assertLess(runs.index('bash r/tools/vendor.sh\n'), runs.index(authors))
        self.assertLess(runs.index(authors), runs.index('R CMD build'))
        self.assertIn('R CMD build', runs)
        self.assertIn("grep -c 'tests/testthat/corpus/.*\\.json$'", runs)
        self.assertIn('r_source_tarball', runs)
        attest = self.uses('pack-r', 'actions/attest-build-provenance@')
        upload = self.uses('pack-r', 'actions/upload-artifact@')
        self.assertEqual(attest[0]['with']['subject-path'], upload[0]['with']['path'])
        self.assertTrue(upload[0]['with']['path'].endswith('.tar.gz'))
        self.assertEqual(upload[0]['with']['name'], 'cran-packages')
        self.assertEqual(upload[0]['with']['retention-days'], 90)
        self.assertEqual(upload[0]['with']['if-no-files-found'], 'error')

    def test_publish_npm_trusts_the_job_oidc_token_and_confirms_bytes(self):
        action = self.action('publish-npm')
        for name in ('version', 'source_sha', 'ci_run_id', 'forge_sha', 'token'):
            self.assertIs(action['inputs'][name]['required'], True)
        self.assertEqual(action['inputs']['publish']['default'], 'true')
        self.assertEqual(action['inputs']['publication_run_id']['default'], '')
        self.assertEqual(action['inputs']['node_version']['default'], '24')
        steps = {s.get('name') or s.get('uses'): s for s in self.steps('publish-npm')}
        node = self.uses('publish-npm', 'actions/setup-node@')
        self.assertEqual(node[0]['with']['registry-url'], 'https://registry.npmjs.org')
        plan = steps['Reconcile existing npm packages by exact public bytes']
        self.assertEqual(plan['id'], 'pending')
        self.assertIn('registry-plan --registry npm --output pending-npm', plan['run'])
        self.assertIn('registry-confirm --registry npm',
                      steps['Verify every public npm package matches the prepared bytes']['run'])
        # npm exchanges the OIDC token itself; there is no credentials action to call.
        self.assertFalse([s for s in self.steps('publish-npm') if 'credentials' in s.get('uses', '')])
        self.assertNotIn('NODE_AUTH_TOKEN', (ACTIONS / 'publish-npm' / 'action.yml').read_text())

    def publish_order(self, present):
        run = next(s for s in self.steps('publish-npm')
                   if s.get('name') == 'Publish only missing preserved npm packages')['run']
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'bin').mkdir()
            stub = work / 'bin' / 'npm'
            stub.write_text('#!/bin/sh\necho "$*" >> "$NPM_LOG"\n')
            stub.chmod(0o755)
            (work / 'pending-npm').mkdir()
            for name in present:
                (work / 'pending-npm' / name).write_bytes(b'')
            result = subprocess.run(['bash', '-c', run], cwd=work, capture_output=True, text=True,
                                    env={**os.environ, 'PATH': f"{work / 'bin'}:{os.environ['PATH']}",
                                         'NPM_LOG': str(work / 'log'), 'CRATE': 'hyperuuid',
                                         'VERSION': '1.2.3'})
            log = (work / 'log').read_text().splitlines() if (work / 'log').exists() else []
            return result.returncode, log

    def test_publish_npm_pushes_every_platform_package_before_the_root(self):
        # registry-plan copies the pending tarballs into one flat directory, and `hyperuuid-`
        # sorts before `skunkwerkx-`: a bare *.tgz glob would publish the root first, whose
        # optionalDependencies would then name packages that do not exist yet.
        suffixes = ['win32-arm64-msvc', 'darwin-arm64', 'linux-x64-gnu', 'linux-arm64-musl',
                    'linux-x64-musl', 'linux-arm64-gnu', 'darwin-x64', 'win32-x64-msvc']
        platforms = [f'skunkwerkx-hyperuuid-{s}-1.2.3.tgz' for s in suffixes]
        code, log = self.publish_order(['hyperuuid-1.2.3.tgz', *platforms])
        self.assertEqual(code, 0)
        self.assertEqual(len(log), 9)
        self.assertEqual(log[-1], 'publish pending-npm/hyperuuid-1.2.3.tgz --access public')
        self.assertEqual(sorted(log[:-1]), sorted(f'publish pending-npm/{p} --access public' for p in platforms))
        # A half-published first release: only what is missing, platform first.
        code, log = self.publish_order(['hyperuuid-1.2.3.tgz', platforms[3]])
        self.assertEqual((code, log), (0, [f'publish pending-npm/{platforms[3]} --access public',
                                           'publish pending-npm/hyperuuid-1.2.3.tgz --access public']))
        code, log = self.publish_order(platforms[:2])
        self.assertEqual((code, len(log)), (0, 2))
        # Anything else in the plan is not a package this release ships: nothing is pushed.
        code, log = self.publish_order(['hyperuuid-1.2.3.tgz', 'stray-1.2.3.tgz'])
        self.assertNotEqual(code, 0)
        self.assertEqual(log, [])


class VerifyWheelsTests(unittest.TestCase):
    """The step that holds the preparation run's wheels and sdist to what hyper.json expects."""
    def step(self):
        action = yaml_lite.load((ACTIONS / 'verify-wheels/action.yml').read_text())
        return action, next(s for s in action['runs']['steps'] if s.get('name', '').startswith('Verify the wheels'))

    def run_step(self, files, sdist):
        action, step = self.step()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / '.github').mkdir()
        config = {'crate': 'hypercast', 'project': 'HyperCast',
                  'exports': {'sources': ['x'], 'patterns': ['x'], 'prefixes': ['hypercast_']}}
        if sdist is not None:
            config['sdist'] = sdist
        (root / '.github/hyper.json').write_text(json.dumps(config))
        (root / 'rust').mkdir()
        (root / 'rust/Cargo.toml').write_text('[package]\nversion = "0.10.0"\n')
        (root / 'dist').mkdir()
        for name in files:
            (root / 'dist' / name).write_bytes(b'x')
        (root / 'bin').mkdir()
        log = root / 'gh.log'
        (root / 'bin/gh').write_text('#!/bin/sh\necho "$@" >> "$GH_LOG"\nexit 0\n')
        (root / 'bin/gh').chmod(0o755)
        env = {**os.environ, 'PATH': f"{root / 'bin'}:{os.environ['PATH']}", 'GH_LOG': str(log),
               'FORGE_SCRIPTS': str(Path(__file__).resolve().parent), 'GH_TOKEN': 't',
               'SOURCE_SHA': 'a' * 40, 'RUN_ID': '1', 'GITHUB_REPOSITORY': 'SkunkWerkx/HyperCast'}
        result = subprocess.run(['bash', '-c', step['run']], cwd=root, env=env, capture_output=True, text=True)
        logged = log.read_text().splitlines() if log.exists() else []
        return result, logged

    def wheels(self, count=17):
        return [f'hypercast-0.10.0-abi{i}-none-platform.whl' for i in range(count)]

    def test_verify_wheels_counts_names_and_verifies_the_sdist(self):
        result, logged = self.run_step(self.wheels() + ['hypercast-0.10.0.tar.gz'], True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(logged), 18)
        self.assertTrue(any('hypercast-0.10.0.tar.gz' in line for line in logged))
        result, logged = self.run_step(self.wheels(), None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(logged), 17)
        for label, files, sdist in (
                ('sdist with the key off', self.wheels() + ['hypercast-0.10.0.tar.gz'], None),
                ('sdist with the key false', self.wheels() + ['hypercast-0.10.0.tar.gz'], False),
                ('key on, no sdist', self.wheels(), True),
                ('stale sdist', self.wheels() + ['hypercast-0.9.0.tar.gz'], True),
                ('two sdists', self.wheels() + ['hypercast-0.10.0.tar.gz', 'hypercast-0.10.0-1.tar.gz'], True)):
            with self.subTest(label):
                result, _ = self.run_step(files, sdist)
                self.assertNotEqual(result.returncode, 0, label)
        action, _ = self.step()
        upload = next(s for s in action['runs']['steps'] if 'upload-artifact' in s.get('uses', ''))
        self.assertIn('dist/*.whl', upload['with']['path'])
        self.assertIn('dist/*.tar.gz', upload['with']['path'])


class PrepareMavenPackageJniTests(unittest.TestCase):
    """The JNI libraries are staged and checked in the jar only when `jni` is 'true'."""

    def test_prepare_maven_package_stages_and_checks_jni(self):
        action = yaml_lite.load((ACTIONS / 'prepare-maven-package' / 'action.yml').read_text())
        self.assertEqual(action['inputs']['jni']['default'], 'false')
        self.assertIs(action['inputs']['jni']['required'], False)
        steps = action['runs']['steps']
        guard = "inputs.jni == 'true'"
        download = [s for s in steps if s.get('uses', '').startswith('actions/download-artifact@')]
        self.assertEqual([d['with']['pattern'] for d in download], ['native-*', 'jni-*'])
        self.assertNotIn('if', download[0])
        self.assertEqual(download[1]['if'], guard)
        self.assertEqual(download[1]['with']['path'], 'artifacts')
        stage = next(s for s in steps if s.get('name', '').startswith('Stage native/'))
        self.assertEqual(stage['env']['JNI'], '${{ inputs.jni }}')
        self.assertIn('JNI_LIB_NAMES', stage['run'])
        self.assertIn('artifacts/jni-$rid/${JNI_LIB_NAMES[$rid]}', stage['run'])
        self.assertIn('::error::', stage['run'].split('JNI_LIB_NAMES', 1)[1])
        check = next(s for s in steps if 'Multi-Release: true' in s.get('run', ''))
        self.assertEqual(check['if'], guard)
        self.assertIn('unzip -l', check['run'])
        self.assertIn('META-INF/MANIFEST.MF', check['run'])
        self.assertLess(steps.index(next(s for s in steps if 'publishMavenPublication' in s.get('run', ''))),
                        steps.index(check))
        for name in ('hyper-pack-maven.yml', 'hyper-publish-maven.yml'):
            text = (ACTIONS.parent / 'workflows' / name).read_text()
            workflow = yaml_lite.load(text)
            with self.subTest(workflow=name):
                spec = workflow['on']['workflow_call']['inputs']['java_jni']
                self.assertIs(spec['default'], False)
                self.assertEqual(spec['type'], 'boolean')
                self.assertIn('description', spec)
                calls = [s for j in workflow['jobs'].values() for s in j['steps']
                         if s.get('uses', '').endswith('actions/prepare-maven-package')]
                self.assertTrue(calls)
                for call in calls:
                    self.assertEqual(call['with']['jni'], '${{ inputs.java_jni }}')


if __name__ == '__main__':
    unittest.main()
