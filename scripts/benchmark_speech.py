#!/usr/bin/env python3
"""Benchmark NativeTTS and NativeSTT (whisper tiny.en/base.en/small.en) on this host.

Writes one JSON document (stdout or --output). The corpus is SYNTHETIC: radio phrases spoken
by the configured Piper voice, not human radio audio, so WER here is an optimistic bound.
Every number is measured on the host described in "host"; nothing here is a measurement of a
different CPU. "cold" = fresh process + model load (the OS page cache may still hold the model
file; see "page_cache_note"); "first" = first utterance after load; "warm" = repeats after that.

Run inside the speech image, e.g.:
  docker run --rm -v speech-bench:/bench --entrypoint python localhost/overlordbot-local-speech:dev \
    scripts/benchmark_speech.py --fetch --models-dir /bench --output /bench/result.json
"""
import argparse
import datetime
import json
import os
import pathlib
import platform
import re
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from overlord.speech import NativeSTT, NativeTTS, SAMPLE_RATE  # noqa: E402

PHRASES = [
    'Overlord, Viper one one, bogey dope.',
    'Overlord, Viper one one, request picture.',
    'Overlord, Enfield two one, declare, bullseye zero four five, thirty.',
    'Anapa ground, Hornet three one, ready to taxi.',
    'Kutaisi approach, Eagle one two, inbound.',
    'Overlord, Uzi one one, location of Batumi.',
    'Overlord, Dodge four one, checking in, angels twenty.',
    'Batumi tower, Viper two two, inbound for landing.',
]
DEFAULT_PROMPT = ('Overlord, Viper, Enfield, Hornet, Eagle, Uzi, Dodge, bogey dope, request picture, '
                  'declare, bullseye, angels, inbound, ready to taxi, Anapa, Kutaisi, Batumi.')
MODELS = {'tiny': 'ggml-tiny.en.bin', 'base': 'ggml-base.en.bin', 'small': 'ggml-small.en.bin'}
TICKS = os.sysconf('SC_CLK_TCK')


def proc_status(pid, keys=('VmRSS', 'VmHWM')):
    values = {}
    with open(f'/proc/{pid}/status') as handle:
        for line in handle:
            name, _, rest = line.partition(':')
            if name in keys:
                values[name.lower() + '_mib'] = round(int(rest.split()[0]) / 1024, 1)
    return values


def proc_cpu_seconds(pid):
    with open(f'/proc/{pid}/stat') as handle:
        fields = handle.read().rsplit(')', 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICKS


def host_info():
    model, flags = None, set()
    with open('/proc/cpuinfo') as handle:
        for line in handle:
            key, _, value = line.partition(':')
            key = key.strip()
            if key == 'model name' and model is None:
                model = value.strip()
            elif key == 'flags' and not flags:
                flags = set(value.split())
    interesting = ['sse4_2', 'avx', 'f16c', 'fma', 'avx2', 'bmi1', 'bmi2', 'avx512f', 'avx_vnni', 'amx_tile']
    is_e5_v2 = bool(model and re.search(r'E5-\d{4}[A-Z]? v2', model))
    info = {
        'cpu_model': model,
        'cpu_flags': {flag: flag in flags for flag in interesting},
        'logical_cpus': os.cpu_count(),
        'affinity_cpus': len(os.sched_getaffinity(0)),
        'kernel': platform.release(),
        'python': platform.python_version(),
        'xeon_e5_v2_measured': is_e5_v2,
        'measurement_scope': (f'Measured on {model}. ' + (
            'This is the AVX-only Xeon E5 v2 target class.' if is_e5_v2 else
            'Binaries are the AVX-only (no AVX2/FMA) build, but this host is NOT a Xeon E5 v2; '
            'these numbers are not Ivy Bridge latencies.')),
    }
    flags_file = pathlib.Path('/usr/local/share/whisper-build-flags.txt')
    if flags_file.is_file():
        info['whisper_cmake_cache'] = dict(line.split('=', 1) for line in flags_file.read_text().splitlines() if '=' in line)
    info['whisper_cpp_commit'] = os.environ.get('WHISPER_CPP_COMMIT')
    try:
        import numpy
        import onnxruntime
        from importlib.metadata import version
        info['onnxruntime'] = {'version': onnxruntime.__version__, 'device': onnxruntime.get_device(),
                               'providers': onnxruntime.get_available_providers()}
        info['piper_tts'] = version('piper-tts')
        info['numpy'] = numpy.__version__
        from numpy._core._multiarray_umath import __cpu_baseline__, __cpu_dispatch__, __cpu_features__
        info['numpy_simd'] = {'baseline': list(__cpu_baseline__),
                              'dispatch_active': [f for f in __cpu_dispatch__ if __cpu_features__.get(f)]}
    except ImportError as error:
        info['dependency_error'] = str(error)
    return info


def words(text):
    return re.sub(r"[^a-z0-9' ]+", ' ', text.lower().replace('-', ' ')).split()


def word_error_rate(reference, hypothesis):
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
    return row[-1] / max(len(ref), 1)


def summary(values):
    values = sorted(values)
    if not values:
        return None
    return {'n': len(values), 'min': round(values[0], 4), 'median': round(statistics.median(values), 4),
            'p90': round(values[min(len(values) - 1, int(0.9 * len(values)))], 4), 'max': round(values[-1], 4)}


def benchmark_tts(runs):
    rss_before = proc_status(os.getpid())
    start = time.monotonic()
    tts = NativeTTS()
    cold = time.monotonic() - start
    corpus, first, warm = [], [], []
    for text in PHRASES:
        start = time.monotonic()
        pcm = tts.synthesize(text)
        first_latency = time.monotonic() - start
        repeats = []
        for _ in range(runs):
            start = time.monotonic()
            tts.synthesize(text)
            repeats.append(time.monotonic() - start)
        duration = len(pcm) / 2 / SAMPLE_RATE
        first.append(first_latency)
        warm.extend(repeats)
        corpus.append({'text': text, 'pcm': pcm, 'duration_s': round(duration, 3),
                       'in_2_6s_range': 2.0 <= duration <= 6.0,
                       'synth_first_s': round(first_latency, 4),
                       'synth_warm_s': [round(value, 4) for value in repeats]})
    result = {'voice': tts.voice, 'model': tts.model, 'native_rate_hz': tts.native_rate, 'output_rate_hz': SAMPLE_RATE,
              'threads_setting': tts.threads, 'cold_load_and_warmup_s': round(cold, 4),
              'first_synthesis_s': summary(first), 'warm_synthesis_s': summary(warm),
              'process_rss_before_load': rss_before, 'process_rss_after': proc_status(os.getpid())}
    tts.close()
    return result, corpus


def benchmark_stt(name, path, threads, port, corpus, runs, prompt):
    start = time.monotonic()
    stt = NativeSTT(model=str(path), threads=threads, port=port)
    cold = time.monotonic() - start
    load_rss = proc_status(stt.pid)
    utterances, first, warm, warm_rtf = [], [], [], []
    try:
        for index, item in enumerate(corpus):
            latencies, cpu = [], []
            for attempt in range(runs + 1):
                cpu_before = proc_cpu_seconds(stt.pid)
                start = time.monotonic()
                result = stt.transcribe(item['pcm'], prompt)
                latencies.append(time.monotonic() - start)
                cpu.append(proc_cpu_seconds(stt.pid) - cpu_before)
                if attempt == 0:
                    first_result = result
            if index == 0:
                first.append(latencies[0])
            warm.extend(latencies[1:])
            warm_rtf.extend(value / item['duration_s'] for value in latencies[1:])
            utterances.append({
                'reference': item['text'], 'audio_s': item['duration_s'], 'text': first_result['text'],
                'confidence': first_result['confidence'],
                'wer': round(word_error_rate(item['text'], first_result['text']), 4),
                'latency_s': [round(value, 4) for value in latencies],
                'latency_kind': ['first_after_load' if index == 0 else 'first_for_utterance'] + ['warm'] * runs,
                'server_cpu_s': [round(value, 3) for value in cpu],
            })
        return {
            'model': name, 'file': str(path), 'size_bytes': path.stat().st_size, 'threads': threads,
            'command': list(stt.command), 'system_info': stt.system_info,
            'cold_start_s': round(cold, 4), 'first_inference_after_load_s': round(first[0], 4),
            'warm_latency_s': summary(warm), 'warm_real_time_factor': summary(warm_rtf),
            'mean_wer': round(statistics.mean(u['wer'] for u in utterances), 4),
            'server_rss_after_load': load_rss, 'server_rss_after_runs': proc_status(stt.pid),
            'utterances': utterances,
        }
    finally:
        stt.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--models', default='tiny,base,small', help='comma list from ' + ','.join(MODELS))
    parser.add_argument('--models-dir', default=os.environ.get('MODEL_DIR', '/models'))
    parser.add_argument('--fetch', action='store_true', help='download missing pinned models into --models-dir')
    parser.add_argument('--threads', default=os.environ.get('WHISPER_THREADS', '4'), help='comma list of thread counts')
    parser.add_argument('--runs', type=int, default=3, help='warm repeats per utterance')
    parser.add_argument('--port', type=int, default=int(os.environ.get('WHISPER_PORT', '18080')))
    parser.add_argument('--prompt', default=DEFAULT_PROMPT)
    parser.add_argument('--output')
    args = parser.parse_args()
    names = [name.strip() for name in args.models.split(',') if name.strip()]
    if not names or set(names) - set(MODELS):
        parser.error(f'--models must be a subset of {sorted(MODELS)}')
    threads = [int(value) for value in args.threads.split(',')]
    models_dir = pathlib.Path(args.models_dir)
    paths = {}
    for name in names:
        path = models_dir / MODELS[name]
        if not path.is_file() and pathlib.Path('/models', MODELS[name]).is_file():
            path = pathlib.Path('/models', MODELS[name])
        if not path.is_file():
            if not args.fetch:
                parser.error(f'{path} missing; pass --fetch to download pinned {MODELS[name]}')
            subprocess.run([sys.executable, str(ROOT / 'scripts/fetch_models.py'), '--dest', str(models_dir), MODELS[name]], check=True)
            path = models_dir / MODELS[name]
        paths[name] = path

    tts, corpus = benchmark_tts(args.runs)
    stt = []
    port = args.port
    for name in names:
        for count in threads:
            stt.append(benchmark_stt(name, paths[name], count, port, corpus, args.runs, args.prompt))
            port += 1
    report = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'host': host_info(),
        'page_cache_note': 'cold_start_s includes process start and model load; model files may already be in the OS page cache.',
        'corpus': {'kind': 'synthetic', 'label': f'SYNTHETIC: generated by Piper {tts["voice"]}, not human or radio-degraded speech',
                   'phrases': [{key: item[key] for key in ('text', 'duration_s', 'in_2_6s_range', 'synth_first_s', 'synth_warm_s')}
                               for item in corpus]},
        'stt_prompt': args.prompt,
        'tts': tts,
        'stt': stt,
    }
    text = json.dumps(report, indent=2)
    if args.output:
        pathlib.Path(args.output).write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
