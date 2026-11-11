from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from overlord.http import Remote


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.redirect_hits += 1
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/capture')
            self.end_headers()
            return
        if self.path == '/unavailable':
            self.send_response(503)
            self.end_headers()
        elif self.path == '/invalid':
            self.send_response(400)
            self.end_headers()
        elif self.headers.get('Authorization') == 'Bearer test-token':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"text":"Overlord Viper one one bogey dope"}')
        else:
            self.send_response(401)
            self.end_headers()


class FailoverTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.redirect_hits = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_does_not_forward_bearer_token_to_redirect_target(self):
        with self.assertRaisesRegex(ValueError, 'HTTP 302'):
            Remote(self.url, token='test-token').post('/redirect', b'{}')
        self.assertEqual(self.server.redirect_hits, 0)

    def test_fails_over_transport_failure(self):
        # Port is reserved but not listening, avoiding races with unrelated services.
        import socket
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            remote = Remote(f'http://127.0.0.1:{unused.getsockname()[1]},' + self.url, timeout=.2, token='test-token')
            self.assertIn(b'bogey dope', remote.post('/transcribe', b'pcm'))

    def test_reports_unavailable_and_does_not_retry_invalid_request(self):
        with self.assertRaises(ConnectionError):
            Remote(self.url).post('/unavailable', b'{}')
        with self.assertRaisesRegex(ValueError, 'HTTP 400'):
            Remote(self.url + ',' + self.url).post('/invalid', b'{}')
        with self.assertRaisesRegex(ValueError, 'HTTP 401'):
            Remote(self.url).post('/transcribe', b'pcm')


if __name__ == '__main__':
    unittest.main()
