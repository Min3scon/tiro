"""The updater's network behaviour against a local server: what a check sends, and resuming a dropped download."""
import hashlib
import http.server
import threading

import pytest

from tiro import __version__
from tiro.models import download_file
from tiro.update import net

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB


class Handler(http.server.BaseHTTPRequestHandler):
    seen: list = []
    drop_first = True

    def do_GET(self):  # noqa: N802
        Handler.seen.append((self.path, dict(self.headers)))
        if self.path.startswith("/feed"):
            body = b'{"payloadType": "x"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        data = PAYLOAD[start:]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if Handler.drop_first and not rng:
            Handler.drop_first = False
            self.wfile.write(data[: len(data) // 3])  # the connection drops a third of the way in
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.seen = []
    Handler.drop_first = True
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_check_sends_only_version_os_and_processor(server):
    net.fetch_feed(f"{server}/feed/stable.json", 4096)
    path, headers = Handler.seen[-1]
    assert path == "/feed/stable.json"  # no query string, nothing appended
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered["user-agent"] == net.user_agent()
    assert lowered["user-agent"].startswith(f"Tiro/{__version__} (")
    assert not {"cookie", "authorization", "x-install-id"} & set(lowered)


def test_a_dropped_download_resumes_and_is_verified(server, tmp_path):
    dest = tmp_path / "pack.zip"
    download_file(f"{server}/pack.zip", dest, len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest(), retries=3)
    assert dest.read_bytes() == PAYLOAD
    ranges = [h.get("Range") for p, h in Handler.seen if p == "/pack.zip"]
    assert ranges[0] is None and ranges[-1] and ranges[-1].startswith("bytes=")  # second request resumed


def test_a_corrupted_download_is_refused(server, tmp_path):
    Handler.drop_first = False
    with pytest.raises(OSError):
        download_file(f"{server}/pack.zip", tmp_path / "pack.zip", len(PAYLOAD), "00" * 32, retries=0)
    assert not (tmp_path / "pack.zip").exists()


def test_only_github_or_this_computer(server):
    for url in ("http://example.com/feed.json", "https://example.com/feed.json", "ftp://127.0.0.1/x"):
        with pytest.raises(ValueError):
            net.fetch_feed(url, 1024)
