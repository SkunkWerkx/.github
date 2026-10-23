"""Contracts for the composite actions: actionlint never reads an action.yml, so this is
the only lint they get, and the release actions' gates are what keeps a tag run from
publishing."""
from pathlib import Path
import re
import unittest

import yaml_lite

ACTIONS = Path(__file__).resolve().parents[1] / 'actions'
RELEASE_ACTIONS = ('release-identity', 'release-guard', 'pack-gems', 'verify-wheels', 'release-gate',
                   'publish-nuget', 'publish-rubygems', 'publish-pypi', 'finalize-release')
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
        self.assertIn('verify --workspace .', verify['run'])
        self.assertNotIn('if', verify)
        names = [s.get('name') for s in self.steps('release-gate')]
        self.assertLess(names.index('Attest every release file from the tag'), names.index('Revalidate complete preserved bytes and every provenance source'))
        self.assertLess(names.index('Revalidate complete preserved bytes and every provenance source'), names.index('Preserve the complete verified release inputs'))

    def test_every_registry_mutation_is_gated_on_publish_and_pending(self):
        mutations = {'publish-nuget': ['Publish the preserved package and symbols', 'Record accepted package and symbols',
                                       'Attest successful NuGet receipt', 'Attest the finalized served package',
                                       'Attest the finalized served package from the tag'],
                     'publish-rubygems': ['Publish only missing preserved gems'],
                     'publish-pypi': ['Publish only missing preserved wheels (Trusted Publishing)']}
        waits = {'publish-nuget': ['Verify the finalized repository-signed package'],
                 'publish-rubygems': ['Verify every public gem matches the prepared bytes'],
                 'publish-pypi': ['Verify every public wheel matches the prepared bytes']}
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


if __name__ == '__main__':
    unittest.main()
