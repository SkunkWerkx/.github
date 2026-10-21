"""Measure artifact-to-test waits from `gh run view --json jobs,startedAt,conclusion`.

The modeled finish removes the observed wait after required artifacts are ready and
keeps the measured test runtime. It does not model future runner queue contention,
changes in toolchain setup costs, or parallel PHP extension performance.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def summarize(run):
    if run['conclusion'] != 'success':
        raise ValueError('The baseline must be a successful complete run')
    origin = timestamp(run['startedAt'])
    jobs = run['jobs']
    def completed(producer):
        matches = [job for job in jobs if producer(job['name']) and job['conclusion'] == 'success']
        if not matches:
            raise ValueError('Missing successful producer in baseline')
        return max(timestamp(job['completedAt']) for job in matches)
    static = completed(lambda name: name.endswith('/ build-static-libs'))
    android = completed(lambda name: '/ build-native-android (' in name)
    rows = []
    for name, ready in [('test-apple-mobile', static), ('test-android', max(static, android))]:
        matches = [job for job in jobs if job['name'] == name and job['conclusion'] == 'success']
        if len(matches) != 1:
            raise ValueError(f'Expected one successful {name}')
        job = matches[0]
        started, finished = timestamp(job['startedAt']), timestamp(job['completedAt'])
        if started < ready:
            raise ValueError(f'{name} started before its required artifacts were ready')
        elapsed = lambda value: int((value - origin).total_seconds())
        duration = int((finished - started).total_seconds())
        rows.append({'job': name, 'artifact_ready_seconds': elapsed(ready),
                     'observed_start_seconds': elapsed(started),
                     'observed_wait_seconds': int((started - ready).total_seconds()),
                     'duration_seconds': duration, 'observed_finish_seconds': elapsed(finished),
                     'modeled_finish_seconds': elapsed(ready) + duration})
    return rows


def clock(seconds):
    return f'{seconds // 60}:{seconds % 60:02d}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_json', type=Path)
    args = parser.parse_args()
    rows = summarize(json.loads(args.run_json.read_text()))
    print('| Job | Artifacts ready | Observed start | Observed wait | Runtime | Observed finish | Modeled finish |')
    print('| --- | ---: | ---: | ---: | ---: | ---: | ---: |')
    for row in rows:
        columns = [row['job']] + [clock(row[key]) for key in (
            'artifact_ready_seconds', 'observed_start_seconds', 'observed_wait_seconds',
            'duration_seconds', 'observed_finish_seconds', 'modeled_finish_seconds')]
        print('| ' + ' | '.join(columns) + ' |')


if __name__ == '__main__':
    main()
