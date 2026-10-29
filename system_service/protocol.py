"""Model-free Smoke profile on a separate path; the frozen legacy API remains behind it."""
import http.client
import json
from pathlib import Path
import threading

from candidate.config import read_config
from memory_service import BODY_LIMIT, ContractError, MemoryHandler, MemoryServer
from system_service.app import Operations
from system_service.store import SystemStore

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.3.1-protocol'
PREFIX = '/v0.3.1-protocol'


class Handler(MemoryHandler):
    server_version = 'TraceMemory/'+VERSION

    def legacy(self):
        # Forward the old routes verbatim to the frozen 0.1 service. Never log headers or bodies.
        connection = http.client.HTTPConnection('127.0.0.1', self.server.legacy_port, timeout=60)
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ContractError('chunked request encoding is unsupported', 400)
            length = self.headers.get('Content-Length', '0')
            if not length.isdecimal() or int(length) > BODY_LIMIT:
                raise ContractError('invalid or oversized request', 413)
            body = self.rfile.read(int(length)) if int(length) else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection', 'transfer-encoding', 'proxy-authorization')}
            connection.request(self.command, self.path, body=body, headers=headers)
            upstream = connection.getresponse()
            data = upstream.read(BODY_LIMIT+1)
            if len(data) > BODY_LIMIT:
                raise ContractError('upstream response too large', 502)
            self.send_response(upstream.status)
            self.send_header('Content-Type', upstream.getheader('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(data)
        except ContractError as error:
            self.respond(error.status, {'detail': {'reason': error.reason}})
        except (OSError, http.client.HTTPException):
            self.respond(502, {'detail': {'reason': 'legacy upstream unavailable'}})
        finally:
            connection.close()

    def do_GET(self):
        if self.path == PREFIX+'/health':
            self.respond(200, {'status': 'ok', 'version': VERSION, 'mode': 'model-free-protocol',
                               'llm': None, 'embedding': None})
        elif self.path.startswith(PREFIX+'/'):
            self.respond(404, {'detail': {'reason': 'not_found'}})
        else:
            self.legacy()

    def do_POST(self):
        if self.path in (PREFIX+'/add', PREFIX+'/search'):
            self.path = self.path[len(PREFIX):]
            super().do_POST()
        elif self.path.startswith(PREFIX+'/'):
            self.respond(404, {'detail': {'reason': 'not_found'}})
        else:
            self.legacy()


def make_server(root=ROOT, port=18090, legacy_port=18086):
    config = read_config(root/'secrets/protocol-service.env')
    if len(config.get('MEMORY_API_KEY', '')) < 24:
        raise ValueError('missing private protocol credential')
    store = SystemStore(root/'data-system/protocol/memory.sqlite3', None, 'model-free-protocol-v0.3.1')
    server = MemoryServer(('127.0.0.1', port), Operations(store, 'protocol', root), config['MEMORY_API_KEY'])
    server.RequestHandlerClass = Handler
    server.legacy_port = legacy_port
    server.slots = threading.BoundedSemaphore(16)
    return server


def main():
    server = make_server()
    print(json.dumps({'event': 'listening', 'version': VERSION, 'port': 18090}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.store.closed.set(); server.server_close()


if __name__ == '__main__': main()
