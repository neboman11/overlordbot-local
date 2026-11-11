"""Measure this compose project's ready containers before/during an actual command."""
import argparse
import json
import platform
from pathlib import Path
import re
import subprocess
import threading
import time


def cpu_seconds(value):
    scales = {'ns': 1e-9, 'us': 1e-6, 'µs': 1e-6, 'ms': .001, 's': 1, 'm': 60, 'h': 3600}
    return sum(float(number) * scales[unit] for number, unit in re.findall(r'([0-9.]+)(ns|us|µs|ms|s|m|h)', value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default='podman', choices=['podman', 'docker'])
    parser.add_argument('--output', default='artifacts/resources.json')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('provide command after --')
    label = 'io.podman.compose.project=overlordbot-local' if args.runtime == 'podman' else 'com.docker.compose.project=overlordbot-local'
    rows = subprocess.check_output([args.runtime, 'ps', '--filter', 'label=' + label,
                                   '--format', '{{.ID}} {{.Names}}'], text=True).splitlines()
    ids = [row.split()[0] for row in rows]
    gateway = next((row.split()[0] for row in rows if 'radio-gateway' in row), None)
    if not ids or gateway is None:
        raise RuntimeError('No running overlordbot-local stack')
    deadline = time.monotonic() + 180
    probe = "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('HTTP_PORT','8080')+'/readyz',timeout=3)"
    while subprocess.run([args.runtime, 'exec', gateway, 'python', '-c', probe],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        if time.monotonic() > deadline:
            raise RuntimeError('Gateway/dependency readiness timeout before measurement')
        time.sleep(1)
    samples, errors, previous = [], [], {}
    stopped = threading.Event()
    def sample(phase):
        result = subprocess.run([args.runtime, 'stats', '--no-stream', '--format', 'json', *ids],
                                text=True, capture_output=True, timeout=15, check=True)
        try:
            containers = json.loads(result.stdout)
        except json.JSONDecodeError:
            containers = [json.loads(line) for line in result.stdout.splitlines()]
        now = time.monotonic()
        for container in containers:
            if 'cpu_time' in container:
                identity = container['id']
                total = cpu_seconds(container['cpu_time'])
                if identity in previous:
                    before, timestamp = previous[identity]
                    container['cpu_percent_interval'] = round(max(0, total-before)/(now-timestamp)*100, 3)
                previous[identity] = (total, now)
        samples.append({'phase': phase, 'time_monotonic': now, 'containers': containers})
    for _ in range(3):
        sample('idle')
    def collect():
        try:
            while not stopped.is_set():
                sample('load')
                stopped.wait(1)
        except Exception as error:
            errors.append(str(error))
    thread = threading.Thread(target=collect)
    thread.start()
    try:
        completed = subprocess.run(command)
    finally:
        stopped.set()
        thread.join()
    report = {'machine': platform.platform(), 'processor': platform.processor(),
              'measurement': 'After gateway and dependencies ready: 3 idle samples, then load sampling every >=1 sec. Podman CPU percentages derive from cumulative CPU-time deltas; 100% equals one core.',
              'command': command, 'exit_code': completed.returncode, 'collection_errors': errors, 'samples': samples}
    output = Path(args.output)
    output.parent.mkdir(exist_ok=True, parents=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    if errors:
        raise RuntimeError('; '.join(errors))
    raise SystemExit(completed.returncode)


if __name__ == '__main__':
    main()
