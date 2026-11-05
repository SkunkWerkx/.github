"""dev_purge_hook.sh against the real disk check, with the floor set out of reach or to zero."""
import json
import os
from pathlib import Path
import subprocess
import unittest

HOOK = Path(__file__).resolve().parent / 'dev_purge_hook.sh'
UNREACHABLE = '999999999'


def run(command, floor=UNREACHABLE, payload=None):
    text = payload if payload is not None else json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}})
    return subprocess.run([str(HOOK)], input=text, capture_output=True, text=True, timeout=30,
                          env={**os.environ, 'DEV_PURGE_MIN_FREE_GB': floor})


class Hook(unittest.TestCase):
    def test_above_the_floor_everything_runs(self):
        self.assertEqual(run('cargo build --release', floor='0').returncode, 0)

    def test_under_the_floor_work_is_refused_and_says_why(self):
        for command in ('cargo build --release', 'docker run rust:latest', 'docker build .', 'git clone x',
                        'cp -r a b', 'npm install', 'swift build', 'python3 x.py', 'go build ./...',
                        'dotnet build', './gradlew test', 'docker system info', 'brew install x',
                        'tar xf big.tar', 'curl -O https://example.com/x'):
            with self.subTest(command):
                result = run(command)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Blocked', result.stderr)

    def test_under_the_floor_cleanup_and_looking_around_run(self):
        for command in ('df -h /', 'du -sh ~/x 2>/dev/null | sort -hr | head -5', 'rm -rf /tmp/x',
                        'docker builder prune -af', 'docker system df', 'docker rm -f x', 'docker ps -a',
                        'brew cleanup -s', 'go clean -cache', 'cargo clean', 'npm cache clean --force',
                        'dotnet nuget locals all --clear', 'swift package reset', 'git status',
                        'git worktree remove x', './gradlew --stop', 'kill 123', 'cd /tmp && rm -rf x',
                        '/x/dev_purge.sh --yes', 'bash /x/dev_purge.sh', 'ls\ndf -h'):
            with self.subTest(command):
                self.assertEqual(run(command).returncode, 0, run(command).stderr)

    def test_work_cannot_ride_along_with_cleanup(self):
        for command in ('rm -rf x && cargo build', 'df; cargo build', 'ls || make', 'ls | tee x',
                        'ls\ncargo build', 'rm -rf x & cargo build', 'echo x > big', 'echo $(cargo build)',
                        'echo `make`', '(cargo build)', 'cat < x'):
            with self.subTest(command):
                self.assertEqual(run(command).returncode, 2)

    def test_a_command_name_only_counts_as_the_whole_word(self):
        self.assertEqual(run('rmfoo x').returncode, 2)
        self.assertEqual(run('/x/not_dev_purge.sh').returncode, 2)

    def test_nothing_to_read_is_refused_under_the_floor(self):
        self.assertEqual(run('', payload='not json').returncode, 2)
        self.assertEqual(run('', payload='{}').returncode, 2)

    def test_a_broken_check_lets_commands_through(self):
        self.assertEqual(run('cargo build', floor='lots').returncode, 0)


if __name__ == '__main__':
    unittest.main()
