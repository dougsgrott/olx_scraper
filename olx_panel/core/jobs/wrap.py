"""Per-job wrapper: run one job command, forward cancel signals to it, and
record how it ended.

Only a process's parent can wait for it, so a panel that restarts mid-job
could never learn the job's exit code. The panel starts this wrapper in a new
session instead; the wrapper is the job command's parent and writes
exit.json when it ends, whoever is (or isn't) watching.

Signals go to the child's PID only, never its process group: patchright's
node driver closes chromium on SIGINT, which would turn a cooperative cancel
(finish the page, write the manifest) into a crash.

    python olx_panel/core/jobs/wrap.py --job-dir logs/jobs/<id> -- <command…>
"""
import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('no command given')

    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    write_json(os.path.join(args.job_dir, 'wrapper.json'),
               {'pid': os.getpid(), 'child_pid': child.pid, 'started_at': _now()})

    def forward(signum, frame):
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
        signal.signal(sig, forward)
    code = child.wait()
    write_json(os.path.join(args.job_dir, 'exit.json'), {
        'exit_code': code if code >= 0 else None,
        'signal': -code if code < 0 else None,
        'finished_at': _now(),
    })


if __name__ == '__main__':
    sys.exit(main())
