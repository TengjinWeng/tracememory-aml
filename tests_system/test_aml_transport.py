"""Exercise the secure curl transport with a Unicode project path and synthetic credentials."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import scripts.aml_smoke as smoke


class TransportTests(unittest.TestCase):
    def test_post_body_from_unicode_path_and_private_temporary_cleanup(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.headers.get('X-Leaderboard-Api-Key'), body))
                output = b'{"ok":true}'
                self.send_response(200); self.send_header('Content-Length', str(len(output)))
                self.end_headers(); self.wfile.write(output)
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='参赛-transport-') as directory:
                root = Path(directory); (root/'secrets').mkdir(mode=0o700)
                with patch.object(smoke, 'ROOT', root), patch.object(smoke, 'ORIGIN', 'http://127.0.0.1:'+str(server.server_port)):
                    result = smoke.Client('synthetic-transport-key')._curl('/test', {'version_name':'自建版本'})
                self.assertEqual(result, {'ok':True})
                self.assertEqual(received, [('synthetic-transport-key', {'version_name':'自建版本'})])
                self.assertEqual(list((root/'secrets').iterdir()), [])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
