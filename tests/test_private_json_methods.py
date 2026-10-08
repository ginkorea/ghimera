"""Shared admitted-origin invariants apply to GET/PUT and the unchanged POST."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ghimera.private_json import PinnedJsonHttp
from ghimera.private_service_config import PrivateJsonConfig


def config(port):
    return PrivateJsonConfig(
        endpoint=f"http://127.0.0.1:{port}/base",
        approved_addresses=("127.0.0.1",),
        allow_plaintext=True,
        allow_plaintext_credentials=False,
        authorization="none",
        timeout_seconds=1,
        max_request_bytes=10000,
        max_response_bytes=10000,
        max_header_bytes=10000,
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "//host/path",
        "/../x",
        "/./x",
        "/%2e%2e/x",
        "/x?secret=1",
        "/x#fragment",
        "/user@host",
        "https://host/x",
        "/x\\y",
    ],
)
def test_suffix_cannot_expand_authority_or_traverse_paths(suffix):
    client = PinnedJsonHttp(config(1))
    with pytest.raises(ValueError, match="suffix"):
        asyncio.run(client.request("GET", endpoint_suffix=suffix))


def test_get_body_refused_before_io():
    with pytest.raises(ValueError, match="method/body"):
        asyncio.run(PinnedJsonHttp(config(1)).request("GET", b"forbidden"))


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_real_native_curl_methods_preserve_verb_body_origin_and_post_path(method):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            observed.append((self.command, self.path, data))
            body = json.dumps({"method": self.command}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PinnedJsonHttp(config(server.server_port))
        if method == "POST":
            response = asyncio.run(client.post(b'{"payload":1}'))
            assert observed == [("POST", "/base", b'{"payload":1}')]
        else:
            payload = b"" if method == "GET" else b'{"payload":1}'
            response = asyncio.run(client.request(method, payload, endpoint_suffix="/item"))
            assert observed == [(method, "/base/item", payload)]
        assert response.status == 200 and json.loads(response.body)["method"] == method
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
