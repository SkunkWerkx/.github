"""companion_gate.py: a release resolves the companion from the registries, so its version must
be live on every one of them but CRAN before any build starts.

The registry responses are recorded (fixtures/gate/README.md); no test touches the network.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import companion_gate

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'gate'
REGISTRIES = ('crates.io', 'NuGet', 'Maven Central', 'PyPI', 'RubyGems', 'npm', 'Go', 'Packagist', 'GitHub Release')
HYPERCAST = 'SkunkWerkx/HyperCast'
REPO_LOOKUP = 'https://api.github.com/repos/SkunkWerkx/hypercast'
PIN = {'repo': HYPERCAST, 'commit': 'a' * 40, 'run': 1}


def load(name):
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


class Replay:
    """Serves the recorded responses, refuses CRAN and any URL nobody recorded, and remembers
    what was asked."""

    def __init__(self, fixture):
        self.responses = fixture['responses']
        self.asked = []

    def __call__(self, url):
        if 'cran' in url.lower() or 'r-project.org' in url:
            raise AssertionError(f'the gate queried CRAN: {url}')
        if url not in self.responses:
            raise AssertionError(f'no recorded response for {url}')
        self.asked.append(url)
        response = self.responses[url]
        return response['status'], response['body']


def manifests(root, **override):
    """HyperTabular's manifests, each requiring hypercast in the form it does today, at 1.1.0."""
    files = {
        '.github/hyper.json': json.dumps({'crate': 'hypertabular', 'project': 'HyperTabular',
                                          'exports': {'sources': ['x'], 'patterns': ['x'], 'prefixes': ['x']},
                                          'companions': {'hypercast': PIN}}),
        'rust/Cargo.toml': '[dependencies]\nhypercast = { version = "1.1.0", default-features = false }\n',
        'go/go.mod': 'module github.com/SkunkWerkx/HyperTabular/go\n\nrequire (\n\tgithub.com/SkunkWerkx/HyperCast/go v1.1.0\n)\n',
        'python/pyproject.toml': '[project]\ndependencies = ["hypercast>=1.1.0,<2"]\n',
        'typescript/package.json': json.dumps({'peerDependencies': {'@skunkwerkx/hypercast': '^1.1.0'},
                                               'devDependencies': {'@skunkwerkx/hypercast': '1.1.0'}}),
        'ruby/hypertabular.gemspec': 'Gem::Specification.new do |spec|\n  spec.add_dependency "hypercast", "~> 1.1"\nend\n',
        'java/build.gradle.kts': 'dependencies {\n    api("io.github.skunkwerkx:hypercast:1.1.0")\n}\n',
        'csharp/Directory.Build.props': '<Project><PropertyGroup><HyperCastVersion>1.1.0</HyperCastVersion></PropertyGroup></Project>\n',
        'composer.json': json.dumps({'require': {'php': '>=8.3', 'ext-hypercast': '^1.1'}}),
        'Package.swift': '    .package(url: "https://github.com/SkunkWerkx/HyperCast", from: "1.1.0")\n',
        'r/DESCRIPTION': 'Package: hypertabular\nImports:\n    hypercast (>= 1.1.0)\n',
    }
    files.update(override)
    for relative, text in files.items():
        if text is None:
            continue
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


class RegistryTests(unittest.TestCase):
    def test_hypercast_1_1_0_missing_everywhere(self):
        fixture = load('hypercast-1.1.0')
        replay = Replay(fixture)
        missing = companion_gate.check('hypercast', '1.1.0', http=replay, repo=HYPERCAST)
        for where in REGISTRIES:
            with self.subTest(where=where):
                self.assertTrue([m for m in missing if m.startswith(f'{where}:')], f'{where} not listed in {missing}')
        self.assertFalse([m for m in missing if not m.startswith(tuple(f'{w}:' for w in REGISTRIES))])
        # The root package and each of the nine platform packages, and every platform gem.
        self.assertEqual(len([m for m in missing if m.startswith('npm:')]), 10)
        self.assertEqual(len([m for m in missing if m.startswith('RubyGems:')]), 1 + len(companion_gate.hyper_config.RUBY_PLATFORMS))
        # No GitHub Release at all is one line, not every asset.
        self.assertEqual([m for m in missing if m.startswith('GitHub Release:')],
                         ['GitHub Release: SkunkWerkx/HyperCast has no release v1.1.0 (PIE zips, Swift zips, C archives, R tarball)'])
        # Every recorded probe is read; the repository lookup only when no repo is given.
        self.assertEqual(sorted(replay.asked), sorted(u for u in fixture['responses'] if u != REPO_LOOKUP))

    def test_hyperuuid_1_1_0_live_but_cran(self):
        fixture = load('hyperuuid-1.1.0')
        replay = Replay(fixture)
        missing = companion_gate.check('hyperuuid', '1.1.0', http=replay, repo='SkunkWerkx/HyperUuid')
        # Recorded live: HyperUuid 1.1.0 predates finalize-release attaching the R tarball, so
        # its GitHub Release lacks exactly that, and nothing else is missing anywhere.
        self.assertEqual(missing, ['GitHub Release: SkunkWerkx/HyperUuid v1.1.0 lacks hyperuuid_1.1.0.tar.gz'])
        self.assertEqual(sorted(replay.asked), sorted(fixture['responses']))
        # With the tarball attached, the gate passes.
        release = next(u for u in fixture['responses'] if '/releases/tags/' in u)
        body = json.loads(fixture['responses'][release]['body'])
        body['assets'].append({'name': 'hyperuuid_1.1.0.tar.gz'})
        fixture['responses'][release]['body'] = json.dumps(body)
        self.assertEqual(companion_gate.check('hyperuuid', '1.1.0', http=Replay(fixture), repo='SkunkWerkx/HyperUuid'), [])

    def test_cran_is_never_queried(self):
        asked = []

        def spy(url):
            asked.append(url)
            return 404, ''
        companion_gate.check('hypercast', '1.1.0', http=spy, repo=HYPERCAST)
        self.assertTrue(asked)
        self.assertFalse([u for u in asked if 'cran' in u.lower() or 'r-project.org' in u])

    def test_repo_defaults_to_the_owners_repository_by_its_github_name(self):
        fixture = load('hypercast-1.1.0')
        replay = Replay(fixture)
        self.assertEqual(companion_gate.check('hypercast', '1.1.0', http=replay),
                         companion_gate.check('hypercast', '1.1.0', http=Replay(fixture), repo=HYPERCAST))
        self.assertIn(REPO_LOOKUP, replay.asked)

    def test_a_probe_that_errors_is_listed_not_passed(self):
        fixture = load('hyperuuid-1.1.0')
        crates = next(u for u in fixture['responses'] if u.startswith('https://index.crates.io/'))
        fixture['responses'][crates] = {'status': 503, 'body': 'unavailable'}
        missing = companion_gate.check('hyperuuid', '1.1.0', http=Replay(fixture), repo='SkunkWerkx/HyperUuid')
        self.assertIn(f'crates.io: probe failed, HTTP 503 from {crates}', missing)

    def test_a_yanked_crate_is_not_live(self):
        fixture = load('hyperuuid-1.1.0')
        crates = next(u for u in fixture['responses'] if u.startswith('https://index.crates.io/'))
        lines = [json.loads(line) for line in fixture['responses'][crates]['body'].splitlines()]
        for line in lines:
            if line['vers'] == '1.1.0':
                line['yanked'] = True
        fixture['responses'][crates]['body'] = '\n'.join(json.dumps(line) for line in lines)
        missing = companion_gate.check('hyperuuid', '1.1.0', http=Replay(fixture), repo='SkunkWerkx/HyperUuid')
        self.assertTrue([m for m in missing if m.startswith('crates.io:')])

    def test_go_module_path_is_case_encoded(self):
        self.assertEqual(companion_gate.go_proxy_path('github.com/SkunkWerkx/HyperCast/go'),
                         'github.com/!skunk!werkx/!hyper!cast/go')


    def test_a_malformed_200_is_listed_and_the_rest_still_probed(self):
        fixture = load('hyperuuid-1.1.0')
        pypi = next(u for u in fixture['responses'] if u.startswith('https://pypi.org/'))
        nuget = next(u for u in fixture['responses'] if u.startswith('https://api.nuget.org/'))
        fixture['responses'][pypi] = {'status': 200, 'body': '<html>interstitial</html>'}
        fixture['responses'][nuget] = {'status': 200, 'body': '{"data": []}'}
        replay = Replay(fixture)
        missing = companion_gate.check('hyperuuid', '1.1.0', http=replay, repo='SkunkWerkx/HyperUuid')
        self.assertIn(f'PyPI: probe failed, unreadable 200 from {pypi}', missing)
        self.assertIn(f'NuGet: probe failed, unreadable 200 from {nuget}', missing)
        self.assertEqual(sorted(replay.asked), sorted(fixture['responses']))

    def test_a_network_failure_names_the_error(self):
        fixture = load('hyperuuid-1.1.0')
        go = next(u for u in fixture['responses'] if u.startswith('https://proxy.golang.org/'))
        fixture['responses'][go] = {'status': 0, 'body': '<urlopen error [Errno 8] nodename nor servname provided>'}
        missing = companion_gate.check('hyperuuid', '1.1.0', http=Replay(fixture), repo='SkunkWerkx/HyperUuid')
        self.assertIn(f'Go: probe failed (network: <urlopen error [Errno 8] nodename nor servname provided>) from {go}',
                      missing)

    def test_repo_lookup_failure_is_not_a_missing_repository(self):
        for status, body, says in ((404, '', 'no repository SkunkWerkx/hypercast'),
                                   (0, 'connection reset', 'network: connection reset'),
                                   (503, 'unavailable', 'HTTP 503')):
            with self.subTest(status=status):
                with self.assertRaises(ValueError) as caught:
                    companion_gate.resolve_repo('hypercast', lambda url: (status, body))
                self.assertIn(says, str(caught.exception))
                if status != 404:
                    self.assertNotIn('no repository', str(caught.exception))


class FetchTests(unittest.TestCase):
    def test_the_token_never_follows_a_redirect(self):
        seen = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{}'

        def urlopen(request, timeout):
            seen.append(request)
            return Response()
        with mock.patch.dict('os.environ', {'GH_TOKEN': 'secret-token'}), \
                mock.patch('urllib.request.urlopen', urlopen):
            companion_gate.fetch('https://api.github.com/repos/SkunkWerkx/HyperCast/releases/tags/v1.1.0')
            companion_gate.fetch('https://pypi.org/pypi/hypercast/1.1.0/json')
        github, pypi = seen
        # Unredirected headers are sent to this URL only; urllib copies `headers` to a redirect.
        self.assertEqual(github.unredirected_hdrs.get('Authorization'), 'Bearer secret-token')
        self.assertNotIn('Authorization', github.headers)
        self.assertFalse(pypi.has_header('Authorization'))

    def test_a_reset_connection_is_status_0(self):
        def urlopen(request, timeout):
            raise ConnectionResetError(54, 'Connection reset by peer')
        with mock.patch('urllib.request.urlopen', urlopen):
            status, body = companion_gate.fetch('https://index.crates.io/hy/pe/hypercast')
        self.assertEqual(status, 0)
        self.assertIn('Connection reset by peer', body)


class ManifestTests(unittest.TestCase):
    def test_manifests_disagree(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'rust/Cargo.toml': '[dependencies]\nhypercast = { version = "1.0.0", default-features = false }\n',
                               'ruby/hypertabular.gemspec': 'spec.add_dependency "hypercast", "~> 1.0"\n'})
            with self.assertRaises(ValueError) as caught:
                companion_gate.required_version(root)
            message = str(caught.exception)
            self.assertIn('rust/Cargo.toml 1.0.0', message)
            self.assertIn('ruby/hypertabular.gemspec 1.0.0', message)
            self.assertIn('go/go.mod 1.1.0', message)

    def test_manifests_agree(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root)
            self.assertEqual(companion_gate.required_version(root), '1.1.0')
            self.assertEqual(len(companion_gate.requirements(root, 'hypercast', HYPERCAST)), 11)

    def test_an_absent_manifest_is_skipped_but_one_without_the_requirement_fails(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'Package.swift': None})
            self.assertEqual(companion_gate.required_version(root), '1.1.0')
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'java/build.gradle.kts': 'dependencies {}\n'})
            with self.assertRaisesRegex(ValueError, 'java/build.gradle.kts names no requirement on hypercast'):
                companion_gate.required_version(root)

    def test_csharp_takes_a_literal_package_reference_too(self):
        # HyperTabular's csproj pins the version in the PackageReference today.
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'csharp/Directory.Build.props': '<Project />\n',
                               'csharp/HyperTabular/HyperTabular.csproj':
                                   '<Project><ItemGroup><PackageReference Include="HyperCast" Version="1.0.0" /></ItemGroup></Project>\n'})
            with self.assertRaisesRegex(ValueError, r'csharp/HyperTabular/HyperTabular.csproj 1\.0\.0'):
                companion_gate.required_version(root)

    def test_lower_bounds(self):
        # Ruby's idiom: several >= / ~> lower bounds take the highest.
        for spec, bound in (('~> 1.1, >= 1.1.2', '1.1.2'), ('>=1.0, >=1.1', '1.1.0'), ('~> 1.1', '1.1.0'), ('>= 1.1.0', '1.1.0'), ('^1.1.0', '1.1.0'), ('=1.1.0', '1.1.0'),
                            ('1.1.0', '1.1.0'), ('>=1.1.0,<2', '1.1.0'), ('^1.1', '1.1.0'), ('v1.1.0', '1.1.0'),
                            ('~=1.1', '1.1.0'), ('== 1.2.3', '1.2.3'), ('~1.1.0', '1.1.0')):
            with self.subTest(spec=spec):
                self.assertEqual(companion_gate.lower_bound(spec), bound)
        for spec in ('<2', '*', 'latest', '^1.1, >=1.2', '=1.1.0, >=1.0', '>1.1'):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                companion_gate.lower_bound(spec)

    def test_no_block_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'.github/hyper.json': json.dumps({'crate': 'x'})})
            with self.assertRaisesRegex(ValueError, 'no companions'):
                companion_gate.main(['--root', root], http=Replay({'responses': {}}))


class RPackageTests(unittest.TestCase):
    """R in release mode (the spec's "R in release mode"): the R jobs add the companion's GitHub
    Release tarball to setup-r-dependencies as a url:: reference."""
    URL = 'url::https://github.com/SkunkWerkx/HyperCast/releases/download/v1.1.0/hypercast_1.1.0.tar.gz'

    def test_r_package_is_the_release_tarball_at_the_required_version(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root)
            self.assertEqual(companion_gate.r_release_package(root), self.URL)
            # The asset the gate requires on that Release, and finalize-release attaches.
            self.assertEqual(self.URL.rsplit('/', 1)[1],
                             companion_gate.release_assets('hypercast', 'HyperCast', '1.1.0')[-1])

    def test_r_package_follows_the_block_and_the_manifests(self):
        # The companion's repository from the block, never derived from its name; the version
        # every manifest requires (required_version), so a disagreement fails here too.
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'.github/hyper.json': json.dumps(
                {'crate': 'hypertabular', 'companions': {'hypercast': {**PIN, 'repo': 'Elsewhere/Cast'}}}),
                # Only the manifests that don't name the repository's owner or project.
                **{m: None for m in ('go/go.mod', 'Package.swift', 'typescript/package.json', 'java/build.gradle.kts',
                                     'csharp/Directory.Build.props', 'composer.json')}})
            self.assertEqual(companion_gate.r_release_package(root),
                             'url::https://github.com/Elsewhere/Cast/releases/download/v1.1.0/hypercast_1.1.0.tar.gz')
        with tempfile.TemporaryDirectory() as root:
            manifests(root, **{'r/DESCRIPTION': 'Package: hypertabular\nImports: hypercast (>= 1.2.0)\n'})
            with self.assertRaisesRegex(ValueError, 'r/DESCRIPTION 1.2.0'):
                companion_gate.r_release_package(root)

    def test_cli_prints_a_step_output_and_queries_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root)
            with mock.patch('builtins.print') as printed:
                self.assertEqual(companion_gate.main(['--root', root, '--r-package'], http=Replay({'responses': {}})), 0)
            self.assertEqual(printed.call_args_list, [mock.call(f'package={self.URL}')])
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            companion_gate.main(['--crate', 'hypercast', '--version', '1.1.0', '--r-package'])


class MainTests(unittest.TestCase):
    def test_main_fails_listing_every_missing_item(self):
        with tempfile.TemporaryDirectory() as root:
            manifests(root)
            with mock.patch('builtins.print') as printed:
                self.assertEqual(companion_gate.main(['--root', root], http=Replay(load('hypercast-1.1.0'))), 1)
            text = '\n'.join(str(c.args[0]) for c in printed.call_args_list)
            self.assertIn('hypercast 1.1.0 is not live', text)
            self.assertIn('CRAN is not checked', text)
            for where in REGISTRIES:
                self.assertIn(f'{where}:', text)

    def test_main_passes_a_live_version(self):
        fixture = load('hyperuuid-1.1.0')
        release = next(u for u in fixture['responses'] if '/releases/tags/' in u)
        body = json.loads(fixture['responses'][release]['body'])
        body['assets'].append({'name': 'hyperuuid_1.1.0.tar.gz'})
        fixture['responses'][release]['body'] = json.dumps(body)
        with mock.patch('builtins.print') as printed:
            self.assertEqual(companion_gate.main(['--crate', 'hyperuuid', '--version', '1.1.0',
                                                  '--repo', 'SkunkWerkx/HyperUuid'], http=Replay(fixture)), 0)
        self.assertIn('hyperuuid 1.1.0 is live on every registry but CRAN', printed.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
