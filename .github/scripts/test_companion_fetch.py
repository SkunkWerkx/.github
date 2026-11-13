"""companion_fetch.py: what the fetch-companions action does in a consumer leg. In release mode
it only guards (release_guard); in companion mode it confirms the pin the companions job
checked, stages and verifies the leg's artifacts, writes .companions/env and wires each
ecosystem."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import companion_fetch
import companion_map
import hyper_config

SCRIPTS = Path(__file__).resolve().parent
COMMIT = 'c' * 40
PIN = {'repo': 'SkunkWerkx/HyperCast', 'commit': COMMIT, 'run': 41346771778}
LEG = 'hyper-test-ruby.yml:test:linux-x64:abi=3.4'


def consumer(root, companions=None):
    data = {'crate': 'hypertabular', 'project': 'HyperTabular',
            'exports': {'sources': ['rust/src/ffi.rs'], 'patterns': ['x'], 'prefixes': ['x']},
            'companions': {'hypercast': PIN} if companions is None else companions}
    path = Path(root) / '.github' / 'hyper.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return hyper_config.HyperConfig(data, root)


def tree(root, files):
    for relative, text in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


# What HyperTabular's own manifests look like when nothing is wired: the registry requirements,
# its own path packages (the Swift smoke test's self-reference, the wasm smoke's env-named gem),
# and Cargo configs with no patch. None of it is an override.
CLEAN = {
    'rust/Cargo.toml': '[dependencies]\nhypercast = { version = "1.1.0", default-features = false }\n',
    'rust/.cargo/config.toml': '[target.x86_64-unknown-linux-musl]\nrustflags = ["-C", "target-feature=-crt-static"]\n'
                               '[alias]\ncdylib = ["rustc", "--release"]\n',
    'python/.cargo/config.toml': '[build]\ntarget-dir = "target"\n',
    'java/jni/.cargo/config.toml': '[target.x86_64-pc-windows-msvc]\nrustflags = ["-C", "target-feature=+crt-static"]\n',
    'go/go.mod': 'module github.com/SkunkWerkx/HyperTabular/go\n\ngo 1.26\n\nrequire (\n'
                 '\tgithub.com/SkunkWerkx/HyperCast/go v1.1.0\n)\n\nreplace example.com/other => ../other\n',
    'go.work': 'go 1.26\n\nuse ./go\n',
    'typescript/package.json': json.dumps({'name': '@skunkwerkx/hypertabular',
                                           'peerDependencies': {'@skunkwerkx/hypercast': '^1.1.0'},
                                           'devDependencies': {'@skunkwerkx/hypercast': '1.1.0', 'local': 'file:../x'},
                                           'overrides': {'other': {'@skunkwerkx/hypercast': '1.1.0'}}}),
    'typescript/package-lock.json': json.dumps({'packages': {
        'node_modules/@skunkwerkx/hypercast': {
            'resolved': 'https://registry.npmjs.org/@skunkwerkx/hypercast/-/hypercast-1.1.0.tgz'},
        'node_modules/local': {'resolved': 'file:../x', 'link': True}}}),
    'typescript/browser-smoke/package.json': json.dumps({'dependencies': {'@skunkwerkx/hypercast-wasm32-wasip1': '1.1.0'}}),
    'ruby/Gemfile': 'source "https://rubygems.org"\n\ngemspec\n\ngroup :development do\n  gem "hypercast",\n'
                    '    "~> 1.1"\n  gem "rspec", "~> 3.13"\nend\n',
    # HyperTabular's own lock: its PATH section is the gemspec, which depends on hypercast at
    # six spaces; hypercast itself resolves from GEM.
    'ruby/Gemfile.lock': 'PATH\n  remote: .\n  specs:\n    hypertabular (1.0.0)\n      fiddle\n      hypercast (~> 1.0)\n\n'
                         'GEM\n  remote: https://rubygems.org/\n  specs:\n    hypercast (1.0.0)\n',
    'ruby/wasm-smoke/Gemfile': 'gem ENV.fetch("WASM_GEM_NAME"), path: ENV.fetch("WASM_GEM_DIR")\n',
    'csharp/nuget.config': '<configuration><packageSources><add key="nuget.org" '
                           'value="https://api.nuget.org/v3/index.json" /></packageSources></configuration>\n',
    'python/pyproject.toml': '[project]\nname = "hypertabular"\ndependencies = ["hypercast>=1.1.0,<2"]\n'
                             '# a comment on hypercast @ nothing\n[tool.uv.sources]\nother = { path = "../other" }\n',
    'pyproject.toml': '[tool.ruff]\nline-length = 120\n',
    'python/requirements-dev.txt': 'pytest>=8\nhypercast==1.1.0\n',
    'python/constraints.txt': 'hypercast==1.1.0\n',
    'Package.swift': '.package(url: "https://github.com/SkunkWerkx/HyperCast", from: "1.1.0")\n',
    'swift/StaticSmokeTest/Package.swift': '.package(name: "HyperTabular", path: "../..")\n',
    'swift/Benchmarks/Package.swift': '.package(path: "../"),\n',
    'swift/.build/workspace-state.json': json.dumps({'version': 7, 'object': {'dependencies': [
        {'packageRef': {'identity': 'hypercast', 'name': 'HyperCast', 'kind': 'remoteSourceControl'},
         'state': {'name': 'sourceControlCheckout', 'checkoutState': {'version': '1.1.0'}}}]}}),
}
PATCH = '{ path = "x" }'
# HyperTabular's ruby/Gemfile hook for HyperForge's workspace/Gemfile.peers, two levels up: from a
# fixture's ruby/Gemfile, the checkout's parent.
PEERS_HOOK = ('peers = File.expand_path("../../Gemfile.peers", __dir__)\n'
              'eval_gemfile peers if File.exist?(peers) && ENV["CI"].nil?\n')

# One fixture per kind, and per location, of override release_guard finds: (files in the
# checkout, env, home files, pip inspect entries, a word the finding must carry). A file path
# starting `../` lies in the checkout's parent, an ancestor Cargo reads; `{home}` in env is the
# fixture home, a directory of its own outside the checkout.
KINDS = {
    'companions dir': ({'.companions/hypercast/README.md': 'x'}, {}, {}, [], '.companions'),
    'cargo patch table': ({'.cargo/config.toml': f'[patch.crates-io]\nhypercast = {PATCH}\n'}, {}, {}, [], 'patch.crates-io'),
    'cargo patch one-crate table': ({'.cargo/config.toml': '[patch.crates-io.hypercast]\npath = "x"\n'},
                                    {}, {}, [], 'patch.crates-io'),
    'cargo patch quoted table': ({'.cargo/config.toml': '[patch."crates-io".hypercast]\npath = "x"\n'},
                                 {}, {}, [], 'patch.crates-io'),
    'cargo patch under [patch]': ({'.cargo/config.toml': f'[patch]\ncrates-io = {{ hypercast = {PATCH} }}\n'},
                                  {}, {}, [], 'patch.crates-io'),
    'cargo patch inline': ({'.cargo/config.toml': f'patch = {{ crates-io = {{ hypercast = {PATCH} }} }}\n'},
                           {}, {}, [], 'patch.crates-io'),
    'cargo patch dotted': ({'rust/.cargo/config.toml': 'patch.crates-io.hypercast.path = "x"\n'},
                           {}, {}, [], 'patch.crates-io'),
    'cargo patch in legacy config': ({'.cargo/config': f'[patch.crates-io]\nhypercast = {PATCH}\n'},
                                     {}, {}, [], 'patch.crates-io'),
    'cargo patch two levels down (java/jni)': ({'java/jni/.cargo/config.toml': f'[patch.crates-io]\nhypercast = {PATCH}\n'},
                                               {}, {}, [], 'patch.crates-io'),
    'cargo patch in an ancestor': ({'../.cargo/config.toml': f'[patch.crates-io]\nhypercast = {PATCH}\n'},
                                   {}, {}, [], 'patch.crates-io'),
    'cargo patch in CARGO_HOME': ({}, {'CARGO_HOME': '{home}/cargo'},
                                  {'cargo/config.toml': f"[patch.'crates-io']\nhypercast = {PATCH}\n"},
                                  [], 'patch.crates-io'),
    'cargo patch in ~/.cargo, no CARGO_HOME': ({}, {}, {'.cargo/config.toml': f'[patch.crates-io]\nhypercast = {PATCH}\n'},
                                               [], 'patch.crates-io'),
    'cargo patch in ~/.cargo, CARGO_HOME elsewhere': ({}, {'CARGO_HOME': '{home}/cargo'},
                                                      {'.cargo/config.toml': f'[patch.crates-io]\nhypercast = {PATCH}\n'},
                                                      [], 'patch.crates-io'),
    'cargo config that is not TOML': ({'.cargo/config.toml': '[patch.crates-io\n'}, {}, {}, [], 'cannot be parsed'),
    'go replace': ({'go/go.mod': 'module x\n\nreplace github.com/SkunkWerkx/HyperCast/go => ../../HyperCast/go\n'},
                   {}, {}, [], 'replace'),
    'go replace block': ({'go/go.mod': 'module x\n\nreplace (\n\tgithub.com/SkunkWerkx/HyperCast/go v1.1.0 => /x/go\n)\n'},
                         {}, {}, [], 'replace'),
    'go.work replace': ({'go.work': 'go 1.26\nuse ./go\nreplace github.com/SkunkWerkx/HyperCast/go => ./x\n'},
                        {}, {}, [], 'go.work has a replace'),
    'go.work use': ({'go/go.work': 'go 1.26\nuse (\n\t.\n\t../.companions/hypercast/go\n)\n'}, {}, {}, [], 'uses'),
    'go.work use of a sibling': ({'go.work': 'go 1.26\nuse ../HyperCast/go\n'}, {}, {}, [], 'uses'),
    'bun lock resolved from a tarball': ({'typescript/bun.lock': '{"packages": {"@skunkwerkx/hypercast": '
                                          '["@skunkwerkx/hypercast@../x.tgz", {}, "sha512-a"],},}'},
                                         {}, {}, [], 'bun.lock resolves'),
    'bun lock link': ({'typescript/bun.lock': '{"packages": {"@skunkwerkx/hypercast": '
                       '["@skunkwerkx/hypercast@link:@skunkwerkx/hypercast", {}],},}'}, {}, {}, [], 'bun.lock resolves'),
    'bun lock unreadable': ({'typescript/bun.lock': '{"packages": ['}, {}, {}, [], 'cannot be parsed as a Bun lock'),
    'npm file dependency': ({'typescript/package.json': json.dumps(
        {'devDependencies': {'@skunkwerkx/hypercast': 'file:../.companions/hypercast.tgz'}})}, {}, {}, [], 'file:'),
    'npm file platform package': ({'typescript/browser-smoke/package.json': json.dumps(
        {'dependencies': {'@skunkwerkx/hypercast-wasm32-wasip1': 'file:/tmp/x.tgz'}})}, {}, {}, [], 'file:'),
    'npm bare path dependency': ({'typescript/package.json': json.dumps(
        {'peerDependencies': {'@skunkwerkx/hypercast': '../x.tgz'}})}, {}, {}, [], 'file:'),
    'npm nested override': ({'typescript/package.json': json.dumps(
        {'overrides': {'some-lib': {'@skunkwerkx/hypercast@1': {'.': 'file:../x.tgz'}}}})}, {}, {}, [], 'override'),
    'npm lock resolved from a file': ({'typescript/package-lock.json': json.dumps({'packages': {
        'node_modules/@skunkwerkx/hypercast': {'resolved': 'file:../x.tgz'}}})}, {}, {}, [], 'package-lock.json'),
    'npm lock link': ({'typescript/package-lock.json': json.dumps({'packages': {
        'node_modules/@skunkwerkx/hypercast-linux-x64': {'resolved': '../x', 'link': True}}})},
                      {}, {}, [], 'package-lock.json'),
    'gem path': ({'ruby/Gemfile': 'gemspec\ngem "hypercast", path: "/x/ruby"\n'}, {}, {}, [], 'path:'),
    'gem path on a second line': ({'ruby/Gemfile': 'gemspec\ngem "hypercast",\n  path: "../HyperCast/ruby"\n'},
                                  {}, {}, [], 'path:'),
    'gem path in parentheses': ({'ruby/Gemfile': 'gem(\n  "hypercast",\n  :path => "../x"\n)\n'}, {}, {}, [], 'path:'),
    'gem in a path block': ({'ruby/Gemfile': 'gemspec\npath "../HyperCast/ruby" do\n  gem "hypercast"\nend\n'},
                            {}, {}, [], 'path:'),
    'gem in a nested path block': ({'ruby/Gemfile': 'group :test do\n  path("../x") do |p|\n    gem "hypercast"\n'
                                                    '  end\nend\n'}, {}, {}, [], 'path:'),
    'gems.rb path': ({'ruby/gems.rb': 'gem "hypercast", path: "../x"\n'}, {}, {}, [], 'path:'),
    'gemfile peers hook with its file there': ({'ruby/Gemfile': 'gemspec\n' + PEERS_HOOK,
                                                '../Gemfile.peers': 'gem "hypercast", path: "x"\n'},
                                               {}, {}, [], 'development peers file'),
    'companion checkout beside': ({'../HyperCast/README.md': 'x'}, {}, {}, [], 'beside this one'),
    'cargo paths override in an ancestor': ({'../.cargo/config.toml': 'paths = ["HyperCast/rust"]\n'},
                                            {}, {}, [], 'paths override'),
    'cargo paths override in the checkout': ({'rust/.cargo/config.toml': 'paths = ["../x"]\n'},
                                             {}, {}, [], 'paths override'),
    'go.work above the checkout': ({'../go.work': 'go 1.26\nuse ./HyperCast/go\n'}, {}, {}, [], 'go.work is above'),
    'msbuild targets above the checkout': ({'../Directory.Build.targets': '<Project />\n'}, {}, {}, [], 'MSBuild imports'),
    'msbuild props above the checkout': ({'../Directory.Build.props': '<Project />\n'}, {}, {}, [], 'MSBuild imports'),
    'bun workspace above the checkout': ({'../package.json': '{"workspaces": ["HyperCast/typescript"]}'},
                                         {}, {}, [], 'workspaces'),
    'unreadable package.json above the checkout': ({'../package.json': '{"workspaces": ['}, {}, {}, [], 'workspaces'),
    'gemfile evaluating another gemfile': ({'ruby/Gemfile': 'gemspec\neval_gemfile "../Gemfile.local"\n'},
                                           {}, {}, [], 'cannot read'),
    'gemfile peers hook without its guard': ({'ruby/Gemfile': 'gemspec\n'
                                              'peers = File.expand_path("../../Gemfile.peers", __dir__)\n'
                                              'eval_gemfile peers\n'}, {}, {}, [], 'cannot read'),
    'gemfile peers hook naming another file': ({'ruby/Gemfile': 'gemspec\n' + PEERS_HOOK.replace('Gemfile.peers',
                                                                                                 'Gemfile.local')},
                                               {}, {}, [], 'cannot read'),
    'Gemfile.lock PATH': ({'ruby/Gemfile.lock': 'PATH\n  remote: ../HyperCast/ruby\n  specs:\n    hypercast (1.0.0)\n\n'
                                                'GEM\n  remote: https://rubygems.org/\n  specs:\n'},
                          {}, {}, [], 'PATH source'),
    'gems.locked PATH': ({'ruby/gems.locked': 'PATH\n  remote: ../x\n  specs:\n    hypercast (1.0.0-x86_64-linux)\n'},
                         {}, {}, [], 'PATH source'),
    'nuget source': ({'csharp/NuGet.Config': '<configuration><packageSources>\n  <add key="companions" '
                                             'value=".companions/nuget" />\n</packageSources></configuration>\n'},
                     {}, {}, [], 'companions'),
    'nuget source at home': ({}, {}, {'.nuget/NuGet/NuGet.Config': '<add key="companions" value="/x" />'},
                             [], 'companions'),
    'nuget source in APPDATA': ({}, {'APPDATA': '{home}/AppData/Roaming'},
                                {'AppData/Roaming/NuGet/NuGet.Config': '<add key="companions" value="C:/x" />'},
                                [], 'companions'),
    'gradle init script': ({}, {}, {'.gradle/init.d/companions.gradle': 'allprojects {}'}, [], 'init'),
    'gradle init script in GRADLE_USER_HOME': ({}, {'GRADLE_USER_HOME': '{home}/g'},
                                              {'g/init.d/companions-hypercast.gradle': 'x'}, [], 'init'),
    'gradle init script in ~/.gradle, GRADLE_USER_HOME elsewhere': (
        {}, {'GRADLE_USER_HOME': '{home}/g'}, {'.gradle/init.d/companions.gradle': 'x'}, [], 'init'),
    'python file reference': ({'python/pyproject.toml': 'dependencies = ["hypercast @ file:///x/hypercast.whl"]\n'},
                              {}, {}, [], '@'),
    'python url reference': ({'python/requirements.txt': 'HyperCast @ https://example.com/hypercast-1.1.0.whl\n'},
                             {}, {}, [], '@'),
    'python constraints reference': ({'python/constraints.txt': 'hypercast @ file:///x.whl\n'}, {}, {}, [], '@'),
    'python root pyproject reference': ({'pyproject.toml': '[dependency-groups]\ndev = ["hypercast @ file:///x.whl"]\n'},
                                        {}, {}, [], '@'),
    'python uv path source': ({'python/pyproject.toml': '[project]\ndependencies = ["hypercast>=1.1.0"]\n'
                                                        '[tool.uv.sources]\nHyperCast = { path = "../../x" }\n'},
                              {}, {}, [], 'tool.uv.sources'),
    'python uv git source list': ({'pyproject.toml': '[tool.uv.sources]\nhypercast = [{ git = "https://x", marker = "1" }]\n'},
                                  {}, {}, [], 'tool.uv.sources'),
    'pyproject that is not TOML': ({'python/pyproject.toml': '[project\n'}, {}, {}, [], 'cannot be parsed'),
    'python direct_url': ({}, {}, {}, [{'metadata': {'name': 'HyperCast', 'version': '1.0.0'},
                                        'direct_url': {'url': 'file:///x/hypercast.whl', 'archive_info': {}}}],
                          'direct_url'),
    'swift edited': ({'swift/.build/workspace-state.json': json.dumps({'object': {'dependencies': [
        {'packageRef': {'identity': 'hypercast', 'name': 'HyperCast'}, 'state': {'name': 'edited', 'path': '/x'}}]}})},
                     {}, {}, [], 'edited'),
    'swift edited at the root': ({'.build/workspace-state.json': json.dumps({'object': {'dependencies': [
        {'packageRef': {'identity': 'hypercast'}, 'state': {'name': 'edited'}}]}})}, {}, {}, [], 'edited'),
    'swift path package': ({'Package.swift': '.package(path: ".companions/hypercast/swift")\n'}, {}, {}, [], '.package(path:'),
    'swift named path package': ({'swift/Package.swift': '.package(name: "HyperCast", path: "../../x")\n'},
                                 {}, {}, [], '.package(path:'),
    'packages variable': ({}, {'HYPERCAST_PACKAGES': '/x'}, {}, [], 'HYPERCAST_PACKAGES'),
    'php extension variable': ({}, {'HYPERCAST_PHP_EXTENSION': '/x/hypercast.so'}, {}, [], 'HYPERCAST_PHP_EXTENSION'),
    'npm tarballs variable': ({}, {'HYPERCAST_TARBALLS': '/x/npm'}, {}, [], 'HYPERCAST_TARBALLS'),
    'R crate variable': ({}, {'HYPERCAST_PATH': '/x/HyperCast/rust'}, {}, [], 'HYPERCAST_PATH'),
}


class Checkout:
    """A consumer checkout one level inside a scratch directory (so a fixture can reach an
    ancestor), and a home of its own beside it, never inside the checkout."""

    def __enter__(self):
        self._outer = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self.root = Path(self._outer.name) / 'checkout'
        self.root.mkdir()
        self.home = Path(self._home.name)
        return self

    def __exit__(self, *exc):
        self._outer.cleanup()
        self._home.cleanup()

    def guard(self, env=None, home_files=None, installed=()):
        tree(self.home, home_files or {})
        env = {k: v.replace('{home}', str(self.home)) for k, v in (env or {}).items()}
        return companion_fetch.release_guard(self.root, env=env, home=self.home, inspect=lambda: list(installed))


class ReleaseGuardTests(unittest.TestCase):
    def test_release_guard_clean_tree(self):
        with Checkout() as c:
            consumer(c.root)
            tree(c.root, CLEAN)
            self.assertEqual(c.guard(installed=[{'metadata': {'name': 'hypercast', 'version': '1.1.0'}}]), [])

    def test_release_guard_finds_each_kind(self):
        for kind, (files, env, home_files, installed, word) in KINDS.items():
            with self.subTest(kind=kind), Checkout() as c:
                consumer(c.root)
                tree(c.root, CLEAN)
                tree(c.root, files)
                found = c.guard(env=env, home_files=home_files, installed=installed)
                self.assertEqual(len(found), 1, found)
                self.assertIn(word, found[0])

    def test_release_guard_takes_the_peers_hook_without_its_file(self):
        # A CI checkout is one repository: the hook's file, HyperForge's workspace/Gemfile.peers,
        # is never beside it, and the hook evaluates nothing.
        with Checkout() as c:
            consumer(c.root)
            tree(c.root, CLEAN)
            tree(c.root, {'ruby/Gemfile': CLEAN['ruby/Gemfile'] + PEERS_HOOK})
            self.assertEqual(c.guard(installed=[{'metadata': {'name': 'hypercast', 'version': '1.1.0'}}]), [])

    def test_release_guard_takes_what_is_beside_and_above_that_is_no_peer(self):
        # Another repository beside the checkout, and a package.json above it with no workspaces,
        # are nothing a build here takes a companion from.
        with Checkout() as c:
            consumer(c.root)
            tree(c.root, CLEAN)
            tree(c.root, {'../HyperUuid/README.md': 'x', '../package.json': '{"name": "x"}'})
            self.assertEqual(c.guard(installed=[{'metadata': {'name': 'hypercast', 'version': '1.1.0'}}]), [])

    def test_release_guard_without_a_block_checks_the_generic_kinds(self):
        with Checkout() as c:
            consumer(c.root, companions={})
            tree(c.root, {'.companions/x': 'x', '.cargo/config.toml': '[patch.crates-io]\nx = { path = "y" }\n'})
            self.assertEqual(len(c.guard()), 2)

    def test_pip_without_pip_is_nothing_installed_but_a_failure_is_a_finding(self):
        def run(returncode, stderr, stdout=''):
            return mock.Mock(returncode=returncode, stderr=stderr, stdout=stdout)
        with mock.patch.object(companion_fetch, '_interpreters', return_value=['/py']):
            with mock.patch.object(companion_fetch.subprocess, 'run', return_value=run(1, '/py: No module named pip')):
                self.assertEqual(companion_fetch.pip_installed(), [])
            with mock.patch.object(companion_fetch.subprocess, 'run', return_value=run(2, 'boom')):
                with self.assertRaisesRegex(ValueError, 'pip inspect.*boom'):
                    companion_fetch.pip_installed()
            report = {'installed': [{'metadata': {'name': 'hypercast'}, 'direct_url': {'url': 'file:///x'}}]}
            with mock.patch.object(companion_fetch.subprocess, 'run', return_value=run(0, '', json.dumps(report))) as call:
                self.assertEqual(companion_fetch.pip_installed(), report['installed'])
            # `-m` puts the working directory on sys.path: pip runs from an empty one, never the checkout.
            cwd = Path(call.call_args.kwargs['cwd'])
            self.assertNotEqual(cwd.resolve(), Path.cwd().resolve())
            self.assertFalse(cwd.exists())
        with Checkout() as c:
            consumer(c.root)

            def broken():
                raise ValueError('pip inspect with /py failed: boom')
            found = companion_fetch.release_guard(c.root, env={}, home=c.home, inspect=broken)
            self.assertEqual(len(found), 1)
            self.assertIn('pip inspect', found[0])

    def test_a_pip_in_the_checkout_is_not_the_pip_inspected(self):
        with Checkout() as c:
            # A planted pip that reports nothing installed; the real one lists at least pip.
            tree(c.root, {'pip/__init__.py': '', 'pip/__main__.py': 'print("{}")\n'})
            cwd = os.getcwd()
            os.chdir(c.root)
            try:
                with mock.patch.object(companion_fetch, '_interpreters', return_value=[sys.executable]):
                    try:
                        entries = companion_fetch.pip_installed()
                    except ValueError as error:
                        self.skipTest(f'no pip inspect here: {error}')
            finally:
                os.chdir(cwd)
            self.assertTrue(entries)

    def test_guard_cli_fails_naming_each_finding(self):
        with Checkout() as c:
            consumer(c.root)
            tree(c.root, {'ruby/Gemfile': 'gem "hypercast", path: "/x"\n', '.companions/env': 'x'})
            out = io.StringIO()
            # Only HOME and PATH: this machine's CARGO_HOME, GRADLE_USER_HOME or HYPERCAST_*
            # can't change the count.
            with mock.patch.object(companion_fetch, 'pip_installed', return_value=[]), \
                    mock.patch('sys.stdout', out), \
                    mock.patch.dict(os.environ, {'HOME': str(c.home), 'PATH': os.environ['PATH']}, clear=True):
                self.assertEqual(companion_fetch.main(['guard', '--root', str(c.root)]), 1)
            text = out.getvalue()
            self.assertEqual(text.count('::error::'), 2)
            self.assertIn('ruby/Gemfile', text)
            self.assertIn('.companions', text)

    def test_importing_needs_no_companion_map_or_tomllib(self):
        # A block-less leg's plan needs neither: they load only where a block is used.
        code = ('import sys; import companion_fetch; '
                'print("companion_map" in sys.modules, "tomllib" in sys.modules)')
        result = subprocess.run([sys.executable, '-c', code], cwd=SCRIPTS, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'False False', result.stderr)


def mirror(directory, names_files, pin=PIN):
    """A downloaded companion-<name> artifact, sealed as companion_mirror.seal seals it."""
    directory = Path(directory)
    lines = []
    for relative, data in sorted(names_files.items()):
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        lines.append(f'{hashlib.sha256(data).hexdigest()}  {relative}\n')
    (directory / 'SHA256SUMS').write_text(''.join(lines), encoding='utf-8')
    (directory / 'pin.json').write_text(json.dumps(pin) + '\n', encoding='utf-8')
    return directory


RUBY_FILES = {'native-linux-x64/libhypercast.so': b'\x7fELF native',
              'ruby-native-linux-x64-3.4/hypercast.so': b'\x7fELF ruby 3.4',
              'ruby-native-linux-x64-4.0/hypercast.so': b'\x7fELF ruby 4.0',
              'jni-linux-x64/libhypercast_jni.so': b'\x7fELF jni'}


class ConfirmTests(unittest.TestCase):
    def test_pin_json_matches(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            companion_fetch.confirm(root, mirror(Path(root) / 'm', RUBY_FILES), 'success')

    def test_pin_json_mismatch(self):
        # A run written as a float (or any non-int) is a different pin, not an equal number.
        for field, value in (('commit', 'd' * 40), ('run', 1), ('repo', 'SkunkWerkx/HyperUuid'),
                             ('run', float(PIN['run'])), ('run', str(PIN['run']))):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                consumer(root)
                directory = mirror(Path(root) / 'm', RUBY_FILES, {**PIN, field: value})
                with self.assertRaisesRegex(ValueError, 'companion-hypercast records .* pins '):
                    companion_fetch.confirm(root, directory, 'success')
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            directory = mirror(Path(root) / 'm', RUBY_FILES)
            (directory / 'pin.json').write_text(json.dumps({**PIN, 'extra': 1}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'companion-hypercast records'):
                companion_fetch.confirm(root, directory, 'success')

    def test_no_companions_artifact_in_the_run(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            for outcome, directory in (('failure', Path(root) / 'absent'), ('success', Path(root) / 'absent')):
                with self.subTest(outcome=outcome):
                    with self.assertRaisesRegex(ValueError, "this repo's `ci.yml` must call `hyper-companions.yml` and need it"):
                        companion_fetch.confirm(root, directory, outcome)


class StageTests(unittest.TestCase):
    def stage(self, root, leg=LEG, files=RUBY_FILES):
        consumer(root)
        directory = mirror(Path(root) / 'm', files)
        return companion_fetch.stage(root, leg, directory), directory

    def test_stage_copies_only_the_legs_files(self):
        with tempfile.TemporaryDirectory() as root:
            out, _ = self.stage(root)
            got = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
            self.assertEqual(got, ['SHA256SUMS', 'native-linux-x64/libhypercast.so',
                                   'ruby-native-linux-x64-3.4/hypercast.so'])
            self.assertEqual(out, Path(root) / '.companions' / 'hypercast-artifacts')
            sums = (out / 'SHA256SUMS').read_text().splitlines()
            self.assertEqual([line.split('  ')[1] for line in sums],
                             ['native-linux-x64/libhypercast.so', 'ruby-native-linux-x64-3.4/hypercast.so'])

    def test_absent_artifact_names_the_leg_and_the_artifact(self):
        files = {k: v for k, v in RUBY_FILES.items() if not k.startswith('ruby-native-linux-x64-3.4')}
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, f'{LEG} takes ruby-native-linux-x64-3.4'):
                self.stage(root, files=files)

    def test_tampered_byte_fails_verify(self):
        with tempfile.TemporaryDirectory() as root:
            out, directory = self.stage(root)
            companion_fetch.verify(out, directory / 'SHA256SUMS')
            target = out / 'ruby-native-linux-x64-3.4' / 'hypercast.so'
            data = bytearray(target.read_bytes())
            data[0] ^= 1
            target.write_bytes(bytes(data))
            with self.assertRaisesRegex(ValueError, 'ruby-native-linux-x64-3.4/hypercast.so'):
                companion_fetch.verify(out, directory / 'SHA256SUMS')
        # Tampered in the mirror itself, before staging: stage verifies, and fails.
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            directory = mirror(Path(root) / 'm', RUBY_FILES)
            (directory / 'native-linux-x64' / 'libhypercast.so').write_bytes(b'\x7fELF nativf')
            with self.assertRaisesRegex(ValueError, 'native-linux-x64/libhypercast.so'):
                companion_fetch.stage(root, LEG, directory)

    def test_unlisted_and_missing_files_fail_verify(self):
        with tempfile.TemporaryDirectory() as root:
            out, directory = self.stage(root)
            (out / 'native-linux-x64' / 'extra.so').write_bytes(b'x')
            with self.assertRaisesRegex(ValueError, 'native-linux-x64/extra.so'):
                companion_fetch.verify(out, directory / 'SHA256SUMS')
            (out / 'native-linux-x64' / 'extra.so').unlink()
            (out / 'native-linux-x64' / 'libhypercast.so').unlink()
            with self.assertRaisesRegex(ValueError, 'native-linux-x64/libhypercast.so'):
                companion_fetch.verify(out, directory / 'SHA256SUMS')

    def test_a_sums_path_outside_the_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            out, directory = self.stage(root)
            for bad in ('../escape', 'C:/x/libhypercast.so', 'native-linux-x64/c:x'):
                with self.subTest(path=bad):
                    (directory / 'SHA256SUMS').write_text(f'{"0" * 64}  {bad}\n')
                    with self.assertRaisesRegex(ValueError, re.escape(bad)):
                        companion_fetch.verify(out, directory / 'SHA256SUMS')


def source(root, version='1.0.0'):
    tree(root, {'.companions/hypercast/rust/Cargo.toml': f'[package]\nname = "hypercast"\nversion = "{version}"\n'})


def read_env(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines())


class EnvTests(unittest.TestCase):
    def test_env_paths_are_relative(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            source(root)
            directory = mirror(Path(root) / 'm', {
                'pypi-wheels-linux-x64/hypercast-1.0.0-cp311-abi3-manylinux_2_28_x86_64.whl': b'w',
                'pypi-wheels-sdist/hypercast-1.0.0.tar.gz': b's',
                'php-ext-linux-x64-8.4/hypercast.so': b'p', 'c-packages-ci/hypercast-1.0.0-linux-x64.tar.gz': b'c',
                'native-linux-x64/libhypercast.so': b'n', 'static-libs/x/libhypercast.a': b'a',
                'jni-linux-x64/libhypercast_jni.so': b'j'})
            leg = 'hyper-build-wheels.yml:sdist-fallthrough:linux-x64'
            out = companion_fetch.stage(root, leg, directory)
            files = [p for p in out.rglob('*') if p.is_file()]
            ecosystems = list(companion_map.ECOSYSTEMS)
            path = companion_fetch.write_env(root, hyper_config.load_companions(root)['hypercast'], files, ecosystems)
            self.assertEqual(path, Path(root) / '.companions' / 'env')
            env = read_env(path)
            self.assertEqual(env['HYPERCAST_SOURCE'], '.companions/hypercast')
            self.assertEqual(env['HYPERCAST_ARTIFACTS'], '.companions/hypercast-artifacts')
            self.assertEqual(env['HYPERCAST_COMMIT'], COMMIT)
            self.assertEqual(env['HYPERCAST_VERSION'], '1.0.0')
            self.assertEqual(sorted(k for k in env if k.endswith('_REQ')),
                             sorted(f'HYPERCAST_{eco.upper()}_REQ' for eco in ecosystems))
            self.assertEqual(env['HYPERCAST_PYTHON_REQ'],
                             '.companions/hypercast-artifacts/pypi-wheels-linux-x64/'
                             'hypercast-1.0.0-cp311-abi3-manylinux_2_28_x86_64.whl,'
                             '.companions/hypercast-artifacts/pypi-wheels-sdist/hypercast-1.0.0.tar.gz')
            self.assertEqual(env['HYPERCAST_RUST_REQ'], '.companions/hypercast/rust')
            # Java and C# build their package from the pinned source (publishToMavenLocal, dotnet pack).
            self.assertEqual(env['HYPERCAST_JAVA_REQ'], '.companions/hypercast/java')
            self.assertEqual(env['HYPERCAST_CSHARP_REQ'], '.companions/hypercast/csharp')
            # Swift points every manifest at the pin's root package (its Package.swift links the
            # bundle wire_swift unpacks); C at the staged archives, which wire_c unpacks.
            self.assertEqual(env['HYPERCAST_SWIFT_REQ'], '.companions/hypercast')
            self.assertEqual(env['HYPERCAST_C_REQ'], '.companions/hypercast-artifacts/c-packages-ci')
            for key, value in env.items():
                if key in ('HYPERCAST_COMMIT', 'HYPERCAST_VERSION'):
                    continue
                for part in filter(None, value.split(',')):
                    with self.subTest(key=key, part=part):
                        self.assertFalse(Path(part).is_absolute())
                        self.assertTrue(part.startswith('.companions/'))
                        self.assertNotIn('\\', part)
            # A container that mounts the checkout at /work resolves the same tree.
            with tempfile.TemporaryDirectory() as other:
                moved = Path(other) / 'work'
                os.rename(root, moved)
                try:
                    for part in env['HYPERCAST_PYTHON_REQ'].split(','):
                        self.assertTrue((moved / part).is_file())
                    self.assertTrue((moved / env['HYPERCAST_SOURCE'] / 'rust' / 'Cargo.toml').is_file())
                finally:
                    os.rename(moved, root)

    def test_a_second_fetch_adds_its_ecosystems(self):
        # A job whose suites need different toolchains on PATH fetches twice, `ecosystems:` naming
        # each pass's (HyperTabular's test-android: C# and Go first, Swift after setup-swift,
        # which must follow the APKs). The second pass keeps the first's requirements, which its
        # later origin proofs read.
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            source(root)
            companion = hyper_config.load_companions(root)['hypercast']
            companion_fetch.write_env(root, companion, [], ['csharp', 'go'])
            path = companion_fetch.write_env(root, companion, [], ['swift'])
            env = read_env(path)
            self.assertEqual(sorted(k for k in env if k.endswith('_REQ')),
                             ['HYPERCAST_CSHARP_REQ', 'HYPERCAST_GO_REQ', 'HYPERCAST_SWIFT_REQ'])
            self.assertEqual(env['HYPERCAST_COMMIT'], COMMIT)
            self.assertEqual(len(path.read_text().splitlines()), len(env))

    def test_env_needs_the_sources_version_and_refuses_whitespace(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            companion = hyper_config.load_companions(root)['hypercast']
            with self.assertRaisesRegex(ValueError, 'rust/Cargo.toml'):
                companion_fetch.write_env(root, companion, [], ['rust'])
            source(root)
            bad = Path(root) / '.companions' / 'hypercast-artifacts' / 'php-ext-linux-x64-8.4' / 'a b.so'
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b'x')
            with self.assertRaisesRegex(ValueError, 'whitespace'):
                companion_fetch.write_env(root, companion, [bad], ['php'])
            with self.assertRaisesRegex(ValueError, 'not an ecosystem'):
                companion_fetch.write_env(root, companion, [], ['cobol'])


class SharedHelperTests(unittest.TestCase):
    def test_wire_and_origin_share_companion_fetchs_helpers(self):
        # One definition each, in companion_fetch, which both import: companion_wire never imports
        # companion_origin, and companion_origin takes none of these from companion_wire.
        import companion_origin
        import companion_wire
        self.assertIs(companion_wire._pep503, companion_fetch._pep503)
        self.assertIs(companion_wire._go_module, companion_fetch._go_module)
        wire_text = (Path(companion_wire.__file__)).read_text()
        origin_text = (Path(companion_origin.__file__)).read_text()
        for name in ('_pep503', '_go_module', '_find_bash', 'find_bash'):
            with self.subTest(helper=name):
                self.assertNotIn(f'def {name}(', wire_text)
                self.assertNotIn(f'companion_wire.{name}', origin_text)
        self.assertNotIn('import companion_origin', wire_text)
        self.assertEqual(companion_fetch._pep503('Hyper_Cast.x'), 'hyper-cast-x')

    def test_find_bash_names_what_it_found(self):
        environ = {'ProgramFiles': r'C:\Program Files', 'SystemRoot': r'C:\Windows', 'PATH': ''}
        wsl = r'C:\Windows\System32\bash.exe'
        with self.assertRaisesRegex(ValueError, 'WSL'):
            companion_fetch.find_bash(environ, nt=True, isfile=lambda p: False, which=lambda *_, **__: wsl)
        with self.assertRaisesRegex(ValueError, 'no bash on PATH'):
            companion_fetch.find_bash({'PATH': ''}, nt=False, which=lambda *_, **__: None)


class WireTests(unittest.TestCase):
    def test_every_ecosystem_is_wired(self):
        # Each companion_map ecosystem has companion_wire.wire_<eco> and companion_origin.origin_<eco>:
        # one companion_map gains needs both before any leg can take it.
        self.assertEqual([eco for eco in companion_map.ECOSYSTEMS if not companion_fetch.available(eco)], [])

    def test_missing_wiring_fails(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(companion_fetch, 'available', return_value=False):
            with self.assertRaisesRegex(ValueError, 'companion_wire.wire_rust'):
                companion_fetch.wire(root, ['rust'])

    def test_wire_refuses_a_development_peer_layout(self):
        # HyperForge's workspace/: the companion's checkout beside this one, or a file above it
        # through which Cargo, Go, Bun or MSBuild takes one, would stand in for the pin.
        layouts = {'checkout beside': {'../HyperCast/README.md': 'x'},
                   'cargo paths above': {'../.cargo/config.toml': 'paths = ["HyperCast/rust"]\n'},
                   'go.work above': {'../go.work': 'go 1.26\n'},
                   'msbuild targets above': {'../Directory.Build.targets': '<Project />\n'},
                   'bun workspace above': {'../package.json': '{"workspaces": ["HyperCast/typescript"]}'}}
        for layout, files in layouts.items():
            with self.subTest(layout=layout), Checkout() as c, \
                    mock.patch.object(companion_fetch, 'available', return_value=True), \
                    mock.patch.object(companion_fetch.subprocess, 'run') as run:
                consumer(c.root)
                tree(c.root, files)
                with self.assertRaisesRegex(ValueError, 'companion mode tests the pin'):
                    companion_fetch.wire(c.root, ['rust'])
                run.assert_not_called()

    def test_wired_ecosystem_runs_wire_then_origin(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(companion_fetch, 'available', return_value=True), \
                mock.patch.object(companion_fetch.subprocess, 'run',
                                  return_value=mock.Mock(returncode=0)) as run, mock.patch('sys.stdout', io.StringIO()):
            companion_fetch.wire(root, ['rust', 'go'])
            calls = [c.args[0][1:] for c in run.call_args_list]
            self.assertEqual([[Path(c[0]).name] + c[1:] for c in calls], [
                ['companion_wire.py', 'rust', '--root', str(root)], ['companion_origin.py', 'rust', '--root', str(root)],
                ['companion_wire.py', 'go', '--root', str(root)], ['companion_origin.py', 'go', '--root', str(root)]])
            for c in run.call_args_list:
                self.assertEqual(c.args[0][0], sys.executable)
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(companion_fetch, 'available', return_value=True), \
                mock.patch.object(companion_fetch.subprocess, 'run', return_value=mock.Mock(returncode=1)), \
                mock.patch('sys.stdout', io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'companion_wire.py rust'):
                companion_fetch.wire(root, ['rust'])


class PlanTests(unittest.TestCase):
    def plan(self, root, leg=LEG, ecosystems=''):
        return companion_fetch.plan(root, leg, ecosystems)

    def test_no_block_is_inactive_and_reads_no_leg(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root, companions={})
            self.assertEqual(self.plan(root, leg='nonsense'), {'has_block': 'false'})

    def test_block_plans_the_leg(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root)
            self.assertEqual(self.plan(root), {'has_block': 'true', 'name': 'hypercast', 'repo': 'SkunkWerkx/HyperCast',
                                               'commit': COMMIT, 'ecosystems': 'rust ruby'})
            self.assertEqual(self.plan(root, ecosystems='ruby')['ecosystems'], 'ruby')
            with self.assertRaisesRegex(ValueError, 'not an ecosystem'):
                self.plan(root, ecosystems='ruby cobol')
            with self.assertRaisesRegex(ValueError, 'not mapped'):
                self.plan(root, leg='hyper-test-nope.yml:test')

    def test_two_companions_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root, companions={'hypercast': PIN, 'hyperuuid': {**PIN, 'repo': 'SkunkWerkx/HyperUuid'}})
            with self.assertRaisesRegex(ValueError, 'one companion'):
                self.plan(root)


class CliTests(unittest.TestCase):
    def test_cli_runs_as_a_script(self):
        with tempfile.TemporaryDirectory() as root:
            consumer(root, companions={})
            result = subprocess.run([sys.executable, str(SCRIPTS / 'companion_fetch.py'), 'plan', '--root', root,
                                     '--leg', LEG], capture_output=True, text=True, env={**os.environ, 'GITHUB_OUTPUT': ''})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'has_block=false\n')


if __name__ == '__main__':
    unittest.main()
