import argparse
import json
import os
import threading
import time
from .http import METRICS, json_response, serve


def main():
    parser = argparse.ArgumentParser(description='Fully local SRS AWACS/ATC services')
    parser.add_argument('component', choices=['radio-gateway', 'brain', 'stt-worker', 'tts-worker'])
    args = parser.parse_args()
    host = os.environ.get('HTTP_HOST', '0.0.0.0')
    default_ports = {'radio-gateway': 8080, 'brain': 8081, 'stt-worker': 8082, 'tts-worker': 8083}
    port = int(os.environ.get('HTTP_PORT', default_ports[args.component]))
    if args.component == 'radio-gateway':
        from .gateway import Gateway
        gateway = Gateway()
        gateway.start()
        serve({}, gateway.ready, port, host)
    elif args.component == 'brain':
        from .tacview import World
        from .brain import Brain, parse_wind
        world = World(stale_seconds=float(os.environ.get('TACVIEW_STALE_SECONDS', '30')))
        thread = threading.Thread(target=world.run, args=(os.environ.get('TACVIEW_HOST', 'tacview'),
                                  int(os.environ.get('TACVIEW_PORT', '42674')),
                                  os.environ.get('TACVIEW_PASSWORD', '')), daemon=True)
        thread.start()
        callsign = os.environ.get('BOT_CALLSIGN', 'Overlord')
        roles = tuple(part.strip() for part in os.environ.get('BRAIN_ROLES', 'awacs,atc').split(','))
        if not roles or not set(roles) <= {'awacs', 'atc'}:
            raise ValueError('BRAIN_ROLES must contain awacs and/or atc')
        controller = Brain(world, callsign, parse_wind(os.environ.get('ATC_WIND_FROM_DEG', '')),
                           roles, os.environ.get('AWACS_PILOT') or None)
        controller.start()
        def respond(headers, body):
            request = json.loads(body)
            if not isinstance(request, dict):
                raise ValueError('expected JSON object')
            if not controller.ready:
                return json_response({'error': 'telemetry unavailable or stale'}, 503)
            answer = controller.respond(request)
            if not answer.get('parsed') or answer['parsed'].get('intent') in (None, 'None'):
                METRICS.add('parse_failures_total')
            return json_response(answer)
        def events(headers, body):
            if not controller.ready:
                return json_response({'error': 'telemetry unavailable or stale'}, 503)
            return json_response(controller.take_events(json.loads(body)))
        def validate_event(headers, body):
            return json_response(controller.validate_event(json.loads(body)))
        serve({'/respond': respond, '/events': events, '/validate-event': validate_event},
              lambda: controller.ready, port, host)
    elif args.component == 'stt-worker':
        from .speech import NativeSTT
        worker = NativeSTT()
        def transcribe(headers, body):
            if headers.get_content_type() != 'audio/pcm' or not 3200 <= len(body) <= 480000 or len(body) % 2:
                raise ValueError('expected 0.1-15 seconds 16000 Hz mono s16le audio/pcm')
            prompt = headers.get('X-Prompt', '')
            if len(prompt) > 2048:
                raise ValueError('prompt too long')
            start = time.monotonic()
            result = worker.transcribe(body, prompt)
            METRICS.observe('stt', time.monotonic() - start)
            METRICS.add('transmissions_total')
            return json_response(result)
        serve({'/transcribe': transcribe}, lambda: bool(worker.ready), port, host)
    else:
        from .speech import NativeTTS
        worker = NativeTTS()
        def synthesize(headers, body):
            request = json.loads(body)
            text = request['text']
            if not isinstance(text, str) or not text.strip() or len(text) > 1000:
                raise ValueError('text must contain 1-1000 characters')
            start = time.monotonic()
            audio = worker.synthesize(text, request.get('voice'))
            METRICS.observe('tts', time.monotonic() - start)
            return 200, 'audio/pcm', audio
        serve({'/synthesize': synthesize}, lambda: bool(worker.ready), port, host)


if __name__ == '__main__':
    main()
