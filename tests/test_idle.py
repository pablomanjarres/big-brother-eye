import json
import threading
import urllib.error
import urllib.request

import httpx
import pytest

from bbeye.signals.idle import build_idle_reader, most_recent_input, remote_idle_reader
from bbeye.signals.idle_server import allowed_client, make_server


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_remote_reader_reads_the_number():
    read = remote_idle_reader(
        "http://mac.test:8766/idle", client(lambda r: httpx.Response(200, json={"idle_s": 12}))
    )
    assert read() == 12.0


def test_remote_reader_skips_an_unreachable_computer_for_a_while():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    read = remote_idle_reader("http://mac.test:8766/idle", client(handler))
    assert [read() for _ in range(5)] == [None] * 5
    assert len(calls) == 1


@pytest.mark.parametrize("body", [{"idle_s": None}, {"idle_s": -1}, {"idle_s": True}, {"other": 1}, []])
def test_remote_reader_rejects_bad_answers(body):
    read = remote_idle_reader("http://mac.test/idle", client(lambda r: httpx.Response(200, json=body)))
    assert read() is None


def test_most_recent_input_wins_and_unknowns_are_ignored():
    assert most_recent_input([lambda: 900.0, lambda: None, lambda: 4.0])() == 4.0
    assert most_recent_input([lambda: None])() is None
    assert build_idle_reader(use_local=False, urls=())() is None


@pytest.mark.parametrize(
    "address, ok",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("100.85.207.62", True),
        ("::ffff:100.64.0.1", True),
        ("fd7a:115c:a1e0::1", True),
        ("192.168.1.10", False),
        ("8.8.8.8", False),
        ("not-an-ip", False),
    ],
)
def test_only_local_and_tailscale_clients(address, ok):
    assert allowed_client(address) is ok


def test_idle_server_end_to_end():
    server = make_server("127.0.0.1", 0, lambda: 7.5)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/idle", timeout=5) as response:
            assert json.loads(response.read()) == {"idle_s": 7.5}
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/other", timeout=5)
        assert error.value.code == 404
        assert remote_idle_reader(base + "/idle")() == 7.5
    finally:
        server.shutdown()
        server.server_close()
