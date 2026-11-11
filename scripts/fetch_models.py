#!/usr/bin/env python3
"""Download pinned speech models from models.lock.json and verify size + SHA256.

Usage: fetch_models.py [--set default|benchmark|all] [--dest DIR] [NAME ...]
Existing files whose hash matches are kept; mismatches are replaced. Exit 1 on any failure.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.request

LOCK = pathlib.Path(__file__).resolve().parent.parent / 'models.lock.json'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def fetch(entry, dest):
    target = dest / entry['name']
    if target.is_file() and target.stat().st_size == entry['size'] and sha256(target) == entry['sha256']:
        print(f'ok      {target}')
        return
    partial = target.with_name(target.name + '.part')
    digest, size = hashlib.sha256(), 0
    request = urllib.request.Request(entry['url'], headers={'User-Agent': 'overlordbot-local-fetch-models'})
    with urllib.request.urlopen(request, timeout=60) as response, open(partial, 'wb') as out:
        while block := response.read(1 << 20):
            digest.update(block)
            size += len(block)
            out.write(block)
    if size != entry['size'] or digest.hexdigest() != entry['sha256']:
        partial.unlink()
        raise RuntimeError(f'{entry["name"]}: got {size} bytes sha256 {digest.hexdigest()}, '
                           f'expected {entry["size"]} bytes sha256 {entry["sha256"]}')
    os.chmod(partial, 0o644)
    os.replace(partial, target)
    print(f'fetched {target} sha256 {entry["sha256"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('names', nargs='*', help='model file names from models.lock.json')
    parser.add_argument('--set', default='default', choices=['default', 'benchmark', 'all'])
    parser.add_argument('--dest', default=os.environ.get('MODEL_DIR', '/models'))
    args = parser.parse_args()
    models = json.loads(LOCK.read_text())['models']
    known = {entry['name'] for entry in models}
    unknown = sorted(set(args.names) - known)
    if unknown:
        parser.error(f'unknown models {unknown}; known: {sorted(known)}')
    if args.names:
        selected = [entry for entry in models if entry['name'] in args.names]
    else:
        selected = [entry for entry in models if args.set == 'all' or args.set in entry['sets']]
    dest = pathlib.Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    failed = False
    for entry in selected:
        try:
            fetch(entry, dest)
        except (OSError, RuntimeError) as error:
            print(f'FAILED  {entry["name"]}: {error}', file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
