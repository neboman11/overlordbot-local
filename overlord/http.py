"""Small bounded HTTP boundary shared by stateless workers and brain."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import socket
import threading
import time
import urllib.error
import urllib.request

MAX_BODY = 1024 * 1024


class Metrics:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def add(self, key, value=1):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + value

    def observe(self, key, seconds):
        with self.lock:
            self.values[key + '_seconds_sum'] = self.values.get(key + '_seconds_sum', 0) + seconds
            self.values[key + '_seconds_count'] = self.values.get(key + '_seconds_count', 0) + 1

    def render(self):
        with self.lock:
            return ''.join(f'overlord_{key} {value}\n' for key, value in sorted(self.values.items())).encode()


METRICS = Metrics()


def json_response(value, status=200):
    return status, 'application/json', json.dumps(value, allow_nan=False).encode()


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self.connections = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.connections.release()


def serve(routes, ready, port, host='0.0.0.0'):
    token = os.environ.get('API_TOKEN', '')
    slots = threading.BoundedSemaphore(4)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.settimeout(30)
            self.deadline = threading.Timer(30, self.expire_request)
            self.deadline.daemon = True
            self.deadline.start()

        def expire_request(self):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self):
            self.deadline.cancel()
            super().finish()

        def log_message(self, *_):
            pass

        def respond(self, status, content_type, body):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            self.deadline.cancel()
            if self.path == '/healthz':
                response = json_response({'status': 'alive'})
            elif self.path == '/readyz':
                available = ready()
                response = json_response({'ready': available}, 200 if available else 503)
            elif self.path == '/metrics':
                response = (200, 'text/plain; version=0.0.4', METRICS.render())
            else:
                response = json_response({'error': 'not found'}, 404)
            self.respond(*response)

        def do_POST(self):
            if token and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.respond(*json_response({'error': 'unauthorized'}, 401))
                return
            if self.path not in routes:
                self.respond(*json_response({'error': 'not found'}, 404))
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('chunked requests unsupported')
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_BODY:
                    self.respond(*json_response({'error': 'body must be at most 1 MiB'}, 413))
                    return
                if not slots.acquire(blocking=False):
                    self.respond(*json_response({'error': 'busy'}, 503))
                    return
                try:
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError('incomplete body')
                    self.deadline.cancel()
                    start = time.monotonic()
                    response = routes[self.path](self.headers, body)
                    METRICS.observe('http', time.monotonic() - start)
                    self.respond(*response)
                finally:
                    slots.release()
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                METRICS.add('request_errors_total')
                self.respond(*json_response({'error': str(error)}, 400))
            except Exception as error:
                METRICS.add('service_errors_total')
                print(json.dumps({'event': 'service_error', 'type': type(error).__name__}), flush=True)
                self.respond(*json_response({'error': 'inference unavailable'}, 503))

    server = ServiceServer((host, port), Handler)
    print(json.dumps({'event': 'listening', 'host': host, 'port': server.server_port}), flush=True)
    def terminate(signum, frame):
        raise SystemExit(0)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    finally:
        server.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class Remote:
    def __init__(self, urls, timeout=15, token=None):
        self.urls = [url.strip().rstrip('/') for url in urls.split(',') if url.strip()]
        if not self.urls or any(not url.startswith(('http://', 'https://')) for url in self.urls):
            raise ValueError('worker URL list must contain HTTP(S) URLs')
        self.timeout = timeout
        self.token = os.environ.get('API_TOKEN', '') if token is None else token
        self.opener = urllib.request.build_opener(NoRedirect)

    def post(self, path, body, content_type='application/json', headers=None):
        failures = []
        for url in self.urls:
            request_headers = {'Content-Type': content_type, **(headers or {})}
            if self.token:
                request_headers['Authorization'] = 'Bearer ' + self.token
            request = urllib.request.Request(url + path, data=body, headers=request_headers)
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    result = response.read(MAX_BODY + 1)
                    if len(result) > MAX_BODY:
                        raise ValueError('worker response exceeds 1 MiB')
                    return result
            except urllib.error.HTTPError as error:
                if error.code < 500:
                    raise ValueError(f'worker rejected request: HTTP {error.code}') from error
                failures.append(f'{url}: HTTP {error.code}')
            except (OSError, urllib.error.URLError) as error:
                failures.append(f'{url}: {type(error).__name__}')
        raise ConnectionError('all endpoints unavailable: ' + '; '.join(failures))

    def ready(self):
        for url in self.urls:
            try:
                with self.opener.open(url + '/readyz', timeout=min(self.timeout, 2)) as response:
                    if response.status == 200:
                        return True
            except (OSError, urllib.error.URLError):
                continue
        return False
