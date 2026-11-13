import argparse
import hmac
import json
import os
import math
import threading
import time
from .http import METRICS, json_response, serve


def jtac_channel(coalition, frequency, modulation, awacs_frequency):
    if coalition not in (1, 2) or not 1000000 <= frequency <= 2000000000 or modulation not in (0, 1):
        raise ValueError('invalid JTAC coalition/frequency/modulation')
    # SRS receive windows extend less than 100 Hz either side of each tune.
    if abs(frequency - awacs_frequency) < 200:
        raise ValueError('JTAC requires a separate SRS frequency at least 200 Hz from AWACS')
    return f'{coalition}:{frequency}:{modulation}'


def main():
    parser = argparse.ArgumentParser(description='Fully local SRS AWACS/ATC/JTAC/carrier/airfield services')
    parser.add_argument('component', choices=['radio-gateway', 'carrier-gateways', 'airfield-gateways',
                                              'brain', 'stt-worker', 'tts-worker'])
    args = parser.parse_args()
    host = os.environ.get('HTTP_HOST', '0.0.0.0')
    default_ports = {'radio-gateway': 8080, 'carrier-gateways': 8084, 'airfield-gateways': 8085,
                     'brain': 8081, 'stt-worker': 8082, 'tts-worker': 8083}
    port = int(os.environ.get('HTTP_PORT', default_ports[args.component]))
    if args.component == 'radio-gateway':
        from .gateway import Gateway
        gateway = Gateway()
        gateway.start()
        serve({}, gateway.ready, port, host)
    elif args.component in ('carrier-gateways', 'airfield-gateways'):
        from .radio_gateways import RadioGateways
        role = args.component.split('-', 1)[0]
        prefix = role.upper()
        pool = RadioGateways(role,
            poll_seconds=float(os.environ.get(prefix + '_GATEWAY_POLL_SECONDS', '1')),
            stale_seconds=float(os.environ.get(prefix + '_GATEWAY_STALE_SECONDS', '15')))
        try:
            pool.start()
            serve({'/status': lambda headers, body: json_response(pool.snapshot())}, pool.ready, port, host)
        finally:
            pool.close()
    elif args.component == 'brain':
        from .tacview import World
        from .brain import Brain, parse_wind
        roles = tuple(part.strip() for part in os.environ.get('BRAIN_ROLES', 'awacs,atc').split(','))
        if not roles or not set(roles) <= {'awacs', 'atc', 'jtac', 'carrier', 'airfield'}:
            raise ValueError('BRAIN_ROLES must contain awacs, atc, jtac, carrier and/or airfield')
        if 'airfield' in roles:
            roles = tuple(role for role in roles if role != 'atc')
        if set(roles) & {'carrier', 'airfield'} and not os.environ.get('API_TOKEN', '').strip():
            raise ValueError('API_TOKEN is required when carrier or airfield operations are enabled')
        telemetry_roles = bool(set(roles) & {'awacs', 'atc', 'jtac'})
        world = World(stale_seconds=float(os.environ.get('TACVIEW_STALE_SECONDS', '30')))
        if telemetry_roles:
            thread = threading.Thread(target=world.run, args=(os.environ.get('TACVIEW_HOST', 'tacview'),
                                      int(os.environ.get('TACVIEW_PORT', '42674')),
                                      os.environ.get('TACVIEW_PASSWORD', '')), daemon=True)
            thread.start()
        callsign = os.environ.get('BOT_CALLSIGN', 'Overlord')
        nlp = None
        if os.environ.get('LLM_URL'):
            from .nlp import LocalIntentClient
            nlp = LocalIntentClient(os.environ['LLM_URL'], os.environ.get('LLM_MODEL', ''),
                                    timeout=float(os.environ.get('LLM_TIMEOUT_SECONDS', '3')))
        jtac = None
        bridge_token = os.environ.get('DCS_BRIDGE_TOKEN', '')
        if set(roles) & {'jtac', 'carrier', 'airfield'} and not bridge_token:
            raise ValueError('DCS_BRIDGE_TOKEN required when JTAC, carrier or airfield operations enabled')
        if 'jtac' in roles:
            from .jtac import Controller
            coalition = int(os.environ.get('COALITION', '2'))
            frequency = int(os.environ.get('JTAC_SRS_FREQUENCY_HZ', '252000000'))
            awacs_frequency = int(os.environ.get('SRS_FREQUENCY_HZ', '251000000'))
            modulation = int(os.environ.get('JTAC_SRS_MODULATION', '0'))
            channel = jtac_channel(coalition, frequency, modulation, awacs_frequency)
            jtac = Controller(callsign=os.environ.get('JTAC_CALLSIGN', 'Axeman'),
                              channel=channel,
                              stale_seconds=float(os.environ.get('DCS_STALE_SECONDS', '10')))
        reserved = []
        coalition = int(os.environ.get('COALITION', '2'))
        if set(roles) & {'awacs', 'atc'}:
            frequency = int(os.environ.get('SRS_FREQUENCY_HZ', '251000000'))
            modulation = int(os.environ.get('SRS_MODULATION', '0'))
            reserved.append(f'{coalition}:{frequency}:{modulation}')
        if jtac is not None:
            reserved.append(jtac.channel)
        carriers = None
        if 'carrier' in roles:
            from .carrier import CarrierService
            carriers = CarrierService(reserved_channels=tuple(reserved),
                                      stale_seconds=float(os.environ.get('DCS_STALE_SECONDS', '10')))
        airfields = None
        if 'airfield' in roles:
            from .airfield import AirfieldService
            airfields = AirfieldService(reserved_channels=tuple(reserved),
                                        stale_seconds=float(os.environ.get('DCS_STALE_SECONDS', '10')))
        bullseye = None
        if os.environ.get('AWACS_BULLSEYE'):
            bullseye = json.loads(os.environ['AWACS_BULLSEYE'])
            if (not isinstance(bullseye, list) or len(bullseye) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in bullseye)
                    or not -90 <= bullseye[0] <= 90 or not -180 <= bullseye[1] <= 180):
                raise ValueError('AWACS_BULLSEYE must be JSON [latitude, longitude]')
            bullseye = tuple(bullseye)
        controller = Brain(world, callsign, parse_wind(os.environ.get('ATC_WIND_FROM_DEG', '')),
                           roles, os.environ.get('AWACS_PILOT') or None, nlp=nlp, jtac=jtac,
                           bullseye=bullseye, home_airfield=os.environ.get('AWACS_HOME_AIRFIELD') or None,
                           carriers=carriers, airfields=airfields)
        if telemetry_roles:
            controller.start()
        def respond(headers, body):
            request = json.loads(body)
            if not isinstance(request, dict):
                raise ValueError('expected JSON object')
            if (not controller.ready and not controller.is_native_request(request)
                    and (jtac is None or controller._channel(request) != jtac.channel)):
                return json_response({'error': 'telemetry unavailable or stale'}, 503)
            answer = controller.respond(request)
            if not answer.get('parsed') or answer['parsed'].get('intent') in (None, 'None'):
                METRICS.add('parse_failures_total')
            return json_response(answer)
        def events(headers, body):
            request = json.loads(body)
            if not isinstance(request, dict):
                raise ValueError('expected JSON object')
            if not controller.ready and not controller.is_native_request(request):
                return json_response({'error': 'telemetry unavailable or stale'}, 503)
            return json_response(controller.take_events(request))
        def validate_event(headers, body):
            return json_response(controller.validate_event(json.loads(body)))
        def dcs_exchange(headers, body):
            if jtac is None:
                return json_response({'error': 'JTAC disabled'}, 404)
            if not hmac.compare_digest(headers.get('X-DCS-Token', ''), bridge_token):
                return json_response({'error': 'unauthorized bridge'}, 401)
            return json_response(jtac.exchange(json.loads(body)))
        def carrier_exchange(headers, body):
            if carriers is None:
                return json_response({'error': 'Carrier operations disabled'}, 404)
            if not hmac.compare_digest(headers.get('X-DCS-Token', ''), bridge_token):
                return json_response({'error': 'unauthorized bridge'}, 401)
            return json_response(controller.exchange_native('carrier', json.loads(body)))
        def carrier_registry(headers, body):
            if not isinstance(json.loads(body), dict):
                raise ValueError('expected JSON object')
            if carriers is None:
                return json_response({'error': 'Carrier operations disabled'}, 404)
            return json_response(controller.native_registry('carrier'))
        def airfield_exchange(headers, body):
            if airfields is None:
                return json_response({'error': 'Airfield ATC disabled'}, 404)
            if not hmac.compare_digest(headers.get('X-DCS-Token', ''), bridge_token):
                return json_response({'error': 'unauthorized bridge'}, 401)
            return json_response(controller.exchange_native('airfield', json.loads(body)))
        def airfield_registry(headers, body):
            if not isinstance(json.loads(body), dict):
                raise ValueError('expected JSON object')
            if airfields is None:
                return json_response({'error': 'Airfield ATC disabled'}, 404)
            return json_response(controller.native_registry('airfield'))
        serve({'/respond': respond, '/events': events, '/validate-event': validate_event,
               '/dcs/exchange': dcs_exchange, '/carrier/exchange': carrier_exchange,
               '/carrier/registry': carrier_registry, '/airfield/exchange': airfield_exchange,
               '/airfield/registry': airfield_registry},
              lambda: bool(controller.ready if telemetry_roles else controller.native_ready()), port, host)
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
