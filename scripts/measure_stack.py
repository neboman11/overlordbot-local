"""Sample only this compose project's containers before/during an actual command."""
import argparse
import json
import platform
from pathlib import Path
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default='podman', choices=['podman', 'docker'])
    parser.add_argument('--output', default='artifacts/resources.json')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('provide command after --')
    label = 'io.podman.compose.project=overlordbot-local' if args.runtime == 'podman' else 'com.docker.compose.project=overlordbot-local'
    ids = subprocess.check_output([args.runtime, 'ps', '--filter', 'label=' + label, '--format', '{{.ID}}'], text=True).split()
    if not ids:
        raise RuntimeError('No running overlordbot-local compose containers')
    samples = []
    lock = threading.Lock()
    stopped = threading.Event()
    def sample(phase):
        result = subprocess.run([args.runtime, 'stats', '--no-stream', '--format', 'json', *ids], text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr)
        raw = result.stdout.strip()
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in raw.splitlines()]
        with lock:
            samples.append({'phase': phase, 'time_monotonic': time.monotonic(), 'containers': rows})
    for _ in range(3):
        sample('idle')
    def collect():
        while not stopped.is_set():
            sample('load')
            stopped.wait(1)
    thread = threading.Thread(target=collect)
    thread.start()
    try:
        completed = subprocess.run(command)
    finally:
        stopped.set()
        thread.join()
    result = {'machine': platform.platform(), 'processor': platform.processor(),
              'measurement': 'container runtime instantaneous stats; 3 idle samples then load sampling every >=1 sec',
              'command': command, 'exit_code': completed.returncode, 'samples': samples}
    output = Path(args.output)
    output.parent.mkdir(exist_ok=True, parents=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(completed.returncode)


if __name__ == '__main__':
    main()
