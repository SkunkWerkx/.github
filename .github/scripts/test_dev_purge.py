"""dev_purge.sh: what it removes, and above all what it must not. Every run gets a throwaway HOME,
scratch root and checkout root, a stub docker, and agent and daemon names no real process has."""
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import uuid

SCRIPT = Path(__file__).resolve().parent / 'dev_purge.sh'
OLD = time.time() - 7200

STUB_DOCKER = """#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$1" in
  info|ps|system) exit 0 ;;
esac
if [ -n "$DOCKER_READ_ONLY" ]; then
  echo 'ERROR: write /var/lib/docker/metadata.db: read-only file system' >&2
  exit 1
fi
echo 'Total reclaimed space: 0B'
"""


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def age(root):
    for p in root.rglob('*'):
        os.utime(p, (OLD, OLD))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / 'home'
        self.scratch = self.tmp / 'scratch'
        self.dev = self.tmp / 'dev'
        for d in (self.home, self.scratch, self.dev, self.tmp / 'bin'):
            d.mkdir()
        docker = self.tmp / 'bin' / 'docker'
        docker.write_text(STUB_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
        self.docker_log = self.tmp / 'docker.log'
        self.docker_log.touch()
        self.token = 'purge' + uuid.uuid4().hex[:8]

    def run_purge(self, *args, **env):
        full = {**os.environ, 'HOME': str(self.home), 'PATH': f"{self.tmp / 'bin'}:{os.environ['PATH']}",
                'DOCKER_LOG': str(self.docker_log), 'DEV_PURGE_SCRATCH_ROOT': str(self.scratch),
                'DEV_PURGE_AGENT_NAME': self.token, 'DEV_PURGE_DAEMON_PATTERN': f'{self.token}Daemon', **env}
        return subprocess.run(['bash', str(SCRIPT), '--root', str(self.dev), *args], env=full,
                              capture_output=True, text=True, timeout=120)

    def spawn(self, *argv, **kwargs):
        proc = subprocess.Popen(argv, **kwargs)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        return proc

    def git(self, repo, *args):
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                        '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True)

    def repo(self, name='repo', ignore='target/\nbin/\n.env\n*.o\n.build/\n'):
        repo = self.dev / name
        repo.mkdir()
        self.git(repo, 'init', '-q')
        write(repo / '.gitignore', ignore)
        write(repo / 'src' / 'main.rs')
        return repo

    def commit(self, repo):
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-qm', 'x')


class Modes(Base):
    def test_a_dry_run_changes_nothing_and_says_so(self):
        cache = write(self.home / '.npm' / 'blob')
        repo = self.repo()
        self.commit(repo)
        out = write(repo / 'target' / 'lib.a')
        session = write(self.scratch / '-proj' / 's1' / 'f').parent
        age(session)
        run = self.run_purge()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('dry run', run.stdout)
        self.assertIn('would remove', run.stdout)
        for kept in (cache, out, session):
            self.assertTrue(kept.exists(), kept)
        self.assertNotIn('prune', self.docker_log.read_text())

    def test_only_limits_what_goes(self):
        cache = write(self.home / '.npm' / 'blob')
        repo = self.repo()
        self.commit(repo)
        out = write(repo / 'target' / 'lib.a')
        self.assertEqual(self.run_purge('--yes', '--only', 'caches').returncode, 0)
        self.assertFalse(cache.exists())
        self.assertTrue(out.exists())

    def test_bad_arguments_refuse_before_doing_anything(self):
        cache = write(self.home / '.npm' / 'blob')
        for args in (['--nope'], ['--only', 'everything'], ['--idle-minutes', 'soon'], ['--check', 'lots']):
            self.assertEqual(self.run_purge('--yes', *args).returncode, 2, args)
        self.assertTrue(cache.exists())

    def test_a_home_of_root_refuses(self):
        self.assertEqual(self.run_purge('--yes', HOME='/').returncode, 2)

    def test_check_passes_above_the_floor_and_fails_under_it(self):
        self.assertEqual(self.run_purge('--check', '0').returncode, 0)
        self.assertEqual(self.run_purge('--check', '999999999').returncode, 1)

    def test_help_prints_the_usage(self):
        run = self.run_purge('--help')
        self.assertEqual(run.returncode, 0)
        self.assertIn('--check GB', run.stdout)
        self.assertNotIn('set -euo', run.stdout)


class Caches(Base):
    def test_caches_go_and_toolchains_stay(self):
        gone = ['.nuget/packages/a', '.cargo/registry/a', '.cargo/git/a', '.npm/a', '.bun/install/cache/a',
                '.gradle/caches/a', '.gradle/wrapper/a', '.cache/a', 'go/pkg/mod/a',
                'Library/Developer/Xcode/DerivedData/a', 'Library/Caches/Homebrew/a', 'Library/Caches/go-build/a']
        kept = ['.rustup/toolchains/a', '.cargo/bin/rg', '.cargo/config.toml', '.dotnet/dotnet', '.sdkman/a',
                '.gradle/gradle.properties', 'go/bin/tool', '.bun/bin/bun', '.nuget/NuGet.Config',
                'Library/Caches/BraveSoftware/a', 'Library/Caches/Steam/a', '.local/opt/android-sdk/a']
        for name in gone + kept:
            write(self.home / name)
        run = self.run_purge('--yes', '--only', 'caches')
        self.assertEqual(run.returncode, 0, run.stderr)
        for name in gone:
            self.assertFalse((self.home / name).exists(), name)
        for name in kept:
            self.assertTrue((self.home / name).exists(), name)

    def test_read_only_module_caches_still_go(self):
        mod = write(self.home / 'go' / 'pkg' / 'mod' / 'm@v1' / 'f.go')
        mod.chmod(0o444)
        mod.parent.chmod(0o555)
        self.assertEqual(self.run_purge('--yes', '--only', 'caches').returncode, 0)
        self.assertFalse(mod.parent.exists())


class Builds(Base):
    def test_ignored_build_output_goes_and_source_stays(self):
        repo = self.repo()
        write(repo / 'swift' / 'Package.swift')
        self.commit(repo)
        out = write(repo / 'target' / 'release' / 'lib.a')
        nested = write(repo / 'swift' / '.build' / 'debug' / 'x.o')
        env = write(repo / '.env', 'SECRET=1')
        run = self.run_purge('--yes', '--only', 'builds')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(out.parent.parent.exists())
        self.assertFalse(nested.parent.parent.exists())
        self.assertTrue(env.exists())
        self.assertTrue((repo / 'src' / 'main.rs').exists())
        self.assertIn('kept 1 other ignored', run.stdout)

    def test_a_tracked_file_inside_an_ignored_build_directory_survives(self):
        repo = self.repo()
        tracked = write(repo / 'bin' / 'tool.sh')
        self.git(repo, 'add', '-A')
        self.git(repo, 'add', '-f', 'bin/tool.sh')
        self.git(repo, 'commit', '-qm', 'x')
        write(repo / 'bin' / 'junk.o')
        run = self.run_purge('--yes', '--only', 'builds')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(tracked.exists())

    def test_untracked_source_beside_an_ignored_object_survives(self):
        # The ignored file names the directory `bin`, but `bin` itself is not ignored here.
        repo = self.repo(ignore='*.o\n')
        self.commit(repo)
        write(repo / 'bin' / 'junk.o')
        notes = write(repo / 'bin' / 'unsaved.rs')
        run = self.run_purge('--yes', '--only', 'builds')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(notes.exists())
        self.assertTrue((repo / 'bin' / 'junk.o').exists())

    def test_a_build_directory_holding_nested_repositories_goes_whole(self):
        repo = self.repo()
        self.commit(repo)
        checkout = repo / '.build' / 'checkouts' / 'dep'
        checkout.mkdir(parents=True)
        self.git(checkout, 'init', '-q')
        write(checkout / 'f')
        write(repo / '.build' / 'manifest.pif')
        run = self.run_purge('--yes', '--only', 'builds')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse((repo / '.build').exists())

    def test_a_wholly_ignored_untracked_directory_stays(self):
        # git names `notes/`, not the build directory inside it, and it may hold anything else.
        repo = self.repo()
        self.commit(repo)
        out = write(repo / 'notes' / '.build' / 'x.o')
        self.assertEqual(self.run_purge('--yes', '--only', 'builds').returncode, 0)
        self.assertTrue(out.exists())

    def test_uncommitted_edits_are_left_alone(self):
        repo = self.repo()
        self.commit(repo)
        edited = write(repo / 'src' / 'main.rs', 'edited')
        untracked = write(repo / 'src' / 'new.rs')
        self.assertEqual(self.run_purge('--yes', '--only', 'builds').returncode, 0)
        self.assertEqual(edited.read_text(), 'edited')
        self.assertTrue(untracked.exists())


class Scratch(Base):
    def test_an_idle_session_goes_and_the_agents_own_files_stay(self):
        idle = write(self.scratch / '-proj' / 'idle' / 'f').parent
        age(idle)
        own = write(self.scratch / 'bash-edit-diff' / 'f')
        run = self.run_purge('--yes', '--only', 'scratch')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(idle.exists())
        self.assertFalse(idle.parent.exists())
        self.assertTrue(own.exists())

    def test_a_recently_written_session_is_kept(self):
        live = write(self.scratch / '-proj' / 'live' / 'f').parent
        run = self.run_purge('--yes', '--only', 'scratch')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(live.exists())
        self.assertIn('in use, kept', run.stdout)

    def test_idle_minutes_moves_the_line(self):
        session = write(self.scratch / '-proj' / 's' / 'f').parent
        age(session)
        self.assertEqual(self.run_purge('--yes', '--only', 'scratch', '--idle-minutes', '600').returncode, 0)
        self.assertTrue(session.exists())

    @unittest.skipUnless(shutil.which('lsof'), 'needs lsof')
    def test_a_session_a_process_is_working_in_is_kept(self):
        session = write(self.scratch / '-proj' / 'held' / 'f').parent
        age(session)
        self.spawn('sleep', '60', cwd=session)
        self.assertEqual(self.run_purge('--yes', '--only', 'scratch').returncode, 0)
        self.assertTrue(session.exists())


class Docker(Base):
    def test_prunes_but_never_touches_volumes(self):
        self.assertEqual(self.run_purge('--yes', '--only', 'docker').returncode, 0)
        log = self.docker_log.read_text()
        for command in ('container prune -f', 'image prune -af', 'builder prune -af'):
            self.assertIn(command, log)
        self.assertNotIn('volume', log)
        self.assertNotIn('system prune', log)

    def test_a_read_only_vm_fails_and_says_what_to_do(self):
        run = self.run_purge('--yes', '--only', 'docker', DOCKER_READ_ONLY='1')
        self.assertEqual(run.returncode, 1)
        self.assertIn('restart Docker', run.stdout)


class Processes(Base):
    def agent(self):
        """A process whose name is this test's token, with a child: a copy of bash in a loop. A copy
        of a system binary is killed on macOS, so this needs a bash that is not /bin/bash there."""
        binary = self.tmp / 'bin' / self.token
        shutil.copy(shutil.which('bash'), binary)
        proc = self.spawn(str(binary), '-c', 'while :; do sleep 1; done')
        deadline = time.time() + 5
        while time.time() < deadline:
            if subprocess.run(['pgrep', '-x', self.token], capture_output=True).returncode == 0:
                return proc
            time.sleep(0.05)
        self.skipTest('this platform will not run a copied bash under a new name')

    def daemon(self):
        return self.spawn('sh', '-c', 'sleep 60; true', f'{self.token}Daemon')

    def test_without_stop_it_names_them_and_refuses_to_clear_caches(self):
        agent = self.agent()
        cache = write(self.home / '.npm' / 'blob')
        run = self.run_purge('--yes')
        self.assertEqual(run.returncode, 2)
        self.assertIn('pass --stop', run.stderr)
        self.assertIn(str(agent.pid), run.stdout)
        self.assertIsNone(agent.poll())
        self.assertTrue(cache.exists())

    def test_scratch_and_docker_do_not_need_them_stopped(self):
        agent = self.agent()
        self.assertEqual(self.run_purge('--yes', '--only', 'scratch,docker').returncode, 0)
        self.assertIsNone(agent.poll())

    def test_a_dry_run_with_stop_stops_nothing(self):
        agent = self.agent()
        self.assertEqual(self.run_purge('--stop').returncode, 0)
        self.assertIsNone(agent.poll())

    def test_stop_stops_agents_and_daemons_then_clears(self):
        agent = self.agent()
        daemon = self.daemon()
        cache = write(self.home / '.npm' / 'blob')
        run = self.run_purge('--yes', '--stop')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(agent.wait(timeout=10), -signal.SIGTERM)
        self.assertEqual(daemon.wait(timeout=10), -signal.SIGTERM)
        self.assertFalse(cache.exists())

    def test_an_agent_running_the_script_does_not_stop_itself(self):
        # The script's parent carries the agent's name, so it finds itself among the agents.
        binary = self.tmp / 'bin' / self.token
        shutil.copy(shutil.which('bash'), binary)
        env = {**os.environ, 'HOME': str(self.home), 'DEV_PURGE_SCRATCH_ROOT': str(self.scratch),
               'PATH': f"{self.tmp / 'bin'}:{os.environ['PATH']}", 'DOCKER_LOG': str(self.docker_log),
               'DEV_PURGE_AGENT_NAME': self.token, 'DEV_PURGE_DAEMON_PATTERN': f'{self.token}Daemon'}
        run = subprocess.run(
            [str(binary), '-c', f'"$0" "{SCRIPT}" --yes --stop --root "{self.dev}"; echo survived=$?', 'bash'],
            env=env, capture_output=True, text=True, timeout=120)
        if 'survived=' not in run.stdout and run.returncode < 0 and run.returncode != -signal.SIGTERM:
            self.skipTest('this platform will not run a copied bash under a new name')
        self.assertIn('survived=0', run.stdout, run.stderr)


if __name__ == '__main__':
    unittest.main()
