"""Tests for beetsplug.hitlisttag.fetch (the shared Fetcher)."""

from __future__ import annotations

import http.server
import socket
import ssl
import threading

import certifi
import pytest
import requests
from urllib3.util.retry import Retry

from beetsplug.hitlisttag.fetch import (
    CACHE_ENV,
    Fetcher,
    _cache_base,
    default_user_agent,
)
from beetsplug.hitlisttag.ingest import IngestError


class _Script:
    """Scripted responses per path, and a log of requests received."""

    def __init__(self) -> None:
        self.routes: dict[str, list[tuple[int, str]]] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.base = ""


@pytest.fixture
def server():
    """A local HTTP server: each path serves its queue of (status, body).

    The last entry of a queue repeats; unknown paths are 404.
    """
    script = _Script()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            script.requests.append((self.path, self.headers.get("User-Agent")))
            queue = script.routes.get(self.path, [(404, "")])
            status, body = queue.pop(0) if len(queue) > 1 else queue[0]
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    script.base = f"http://127.0.0.1:{srv.server_port}"
    yield script
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No proxies for 127.0.0.1, and no real backoff sleeps in urllib3."""
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Retry, "get_backoff_time", lambda self: 0)


class FakeClock:
    """A monotonic clock that only moves when sleep() is called."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make(clock: FakeClock | None = None, **kwargs) -> Fetcher:
    clock = clock or FakeClock()
    return Fetcher(clock=clock, sleep=clock.sleep, **kwargs)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestOutcomes:
    def test_200_returns_text(self, server):
        server.routes["/a"] = [(200, "hello")]
        assert make().get(server.base + "/a") == "hello"

    def test_404_returns_none(self, server):
        assert make().get(server.base + "/missing") is None

    def test_403_raises_without_retry(self, server):
        server.routes["/f"] = [(403, "")]
        with pytest.raises(IngestError, match="403"):
            make().get(server.base + "/f")
        assert len(server.requests) == 1

    def test_503_then_200_retries_once(self, server):
        server.routes["/r"] = [(503, ""), (200, "ok")]
        assert make().get(server.base + "/r") == "ok"
        assert len(server.requests) == 2

    def test_503_forever_raises_after_three_retries(self, server):
        server.routes["/r"] = [(503, "")]
        with pytest.raises(IngestError, match="cannot fetch"):
            make().get(server.base + "/r")
        assert len(server.requests) == 4

    def test_429_is_retried(self, server):
        server.routes["/t"] = [(429, ""), (200, "ok")]
        assert make().get(server.base + "/t") == "ok"

    def test_connection_error_names_url(self):
        url = f"http://127.0.0.1:{_free_port()}/x"
        with pytest.raises(IngestError, match="cannot fetch .*127.0.0.1"):
            make().get(url)

    def test_ssl_error_names_host_and_chain(self, monkeypatch):
        fetcher = make()

        def boom(*args, **kwargs):
            raise requests.exceptions.SSLError("unable to get local issuer")

        monkeypatch.setattr(fetcher._session, "get", boom)
        with pytest.raises(IngestError, match=r"www\.top40\.nl.*certificate chain"):
            fetcher.get("https://www.top40.nl/top40")


class TestRetryPolicy:
    def test_adapters_carry_the_retry_policy(self):
        fetcher = make()
        for prefix in ("http://", "https://"):
            retry = fetcher._session.get_adapter(prefix + "x/").max_retries
            assert retry.total == 3
            assert retry.backoff_factor == 1
            assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
            assert retry.respect_retry_after_header is True
            assert set(retry.allowed_methods) == {"GET"}


class TestUserAgent:
    def test_default_identifies_tool_contact_and_library(self):
        ua = default_user_agent()
        assert ua.startswith("beets-hitlisttag/")
        assert "(+https://github.com/pvliesdonk/beets-hitlisttag)" in ua
        assert ua.endswith(f"requests/{requests.__version__}")

    def test_default_is_sent(self, server):
        server.routes["/a"] = [(200, "x")]
        make().get(server.base + "/a")
        assert server.requests[0][1] == default_user_agent()

    def test_custom_is_sent(self, server):
        server.routes["/a"] = [(200, "x")]
        make(user_agent="my-script/1.0 (me@example.org)").get(server.base + "/a")
        assert server.requests[0][1] == "my-script/1.0 (me@example.org)"


class TestPacing:
    def test_first_request_does_not_wait(self, server):
        clock = FakeClock()
        server.routes["/a"] = [(200, "x")]
        make(clock).get(server.base + "/a")
        assert clock.slept == []

    def test_second_request_waits_out_the_interval(self, server):
        clock = FakeClock()
        server.routes["/a"] = [(200, "x")]
        fetcher = make(clock)
        fetcher.get(server.base + "/a")
        clock.now += 0.3
        fetcher.get(server.base + "/a")
        assert clock.slept == [pytest.approx(0.7)]

    def test_no_wait_once_interval_has_passed(self, server):
        clock = FakeClock()
        server.routes["/a"] = [(200, "x")]
        fetcher = make(clock)
        fetcher.get(server.base + "/a")
        clock.now += 1.5
        fetcher.get(server.base + "/a")
        assert clock.slept == []

    def test_custom_interval(self, server):
        clock = FakeClock()
        server.routes["/a"] = [(200, "x")]
        fetcher = make(clock, min_interval=2.0)
        fetcher.get(server.base + "/a")
        fetcher.get(server.base + "/a")
        assert clock.slept == [pytest.approx(2.0)]


def _first_certifi_pem() -> str:
    text = open(certifi.where(), encoding="ascii").read()
    end = "-----END CERTIFICATE-----"
    start = text.index("-----BEGIN CERTIFICATE-----")
    return text[start : text.index(end, start) + len(end)] + "\n"


class TestExtraCA:
    def test_https_adapter_uses_verifying_context_with_extra_ca(self):
        pem = _first_certifi_pem()
        fetcher = make(extra_ca_pem=pem)
        adapter = fetcher._session.get_adapter("https://www.top40.nl/")
        ctx = adapter.poolmanager.connection_pool_kw["ssl_context"]
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
        probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        probe.load_verify_locations(cadata=pem)
        assert probe.get_ca_certs()[0] in ctx.get_ca_certs()

    def test_without_extra_ca_no_custom_context(self):
        adapter = make()._session.get_adapter("https://www.top40.nl/")
        assert "ssl_context" not in adapter.poolmanager.connection_pool_kw


class TestCache:
    def test_miss_writes_page_under_host_and_path(self, server, tmp_path):
        server.routes["/top40/1965/week-1"] = [(200, "week one")]
        make(cache_dir=tmp_path).get(server.base + "/top40/1965/week-1")
        host = server.base.removeprefix("http://").replace(":", "%3A")
        page = tmp_path / host / "top40" / "1965" / "week-1.page"
        assert page.read_text(encoding="utf-8") == "week one"

    def test_hit_makes_no_request_and_does_not_wait(self, server, tmp_path):
        server.routes["/a"] = [(200, "cached")]
        make(cache_dir=tmp_path).get(server.base + "/a")
        clock = FakeClock()
        fetcher = make(clock, cache_dir=tmp_path)
        assert fetcher.get(server.base + "/a") == "cached"
        assert fetcher.get(server.base + "/a") == "cached"
        assert len(server.requests) == 1
        assert clock.slept == []

    def test_404_is_stored_as_marker_and_served_offline(self, server, tmp_path):
        assert make(cache_dir=tmp_path).get(server.base + "/top40/1965/week-60") is None
        marker = next(tmp_path.rglob("week-60.404"))
        assert marker.read_text() == ""
        assert make(cache_dir=tmp_path).get(server.base + "/top40/1965/week-60") is None
        assert len(server.requests) == 1

    def test_errors_are_not_cached(self, server, tmp_path):
        server.routes["/e"] = [(500, "")]
        with pytest.raises(IngestError):
            make(cache_dir=tmp_path).get(server.base + "/e")
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    def test_page_and_directory_of_same_name_coexist(self, server, tmp_path):
        server.routes["/top40"] = [(200, "index")]
        server.routes["/top40/1965/week-1"] = [(200, "w1")]
        fetcher = make(cache_dir=tmp_path)
        assert fetcher.get(server.base + "/top40") == "index"
        assert fetcher.get(server.base + "/top40/1965/week-1") == "w1"
        again = make(cache_dir=tmp_path)
        assert again.get(server.base + "/top40") == "index"
        assert again.get(server.base + "/top40/1965/week-1") == "w1"

    def test_non_ascii_round_trips(self, server, tmp_path):
        text = "Zoë Livay · Mötley Crüe · Beyoncé 🎵"
        server.routes["/u"] = [(200, text)]
        assert make(cache_dir=tmp_path).get(server.base + "/u") == text
        assert make(cache_dir=tmp_path).get(server.base + "/u") == text

    def test_env_variable_activates_cache(self, server, tmp_path, monkeypatch):
        monkeypatch.setenv(CACHE_ENV, str(tmp_path))
        server.routes["/a"] = [(200, "x")]
        make().get(server.base + "/a")
        assert len(list(tmp_path.rglob("a.page"))) == 1

    def test_no_cache_leaves_filesystem_untouched(self, server, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        server.routes["/a"] = [(200, "x")]
        make().get(server.base + "/a")
        assert list(tmp_path.iterdir()) == []

    def test_cache_on_a_file_is_ingest_error(self, server, tmp_path):
        not_a_dir = tmp_path / "cache"
        not_a_dir.write_text("oops")
        server.routes["/a"] = [(200, "x")]
        with pytest.raises(IngestError, match="development cache"):
            make(cache_dir=not_a_dir).get(server.base + "/a")


class TestCacheNames:
    def test_root_path_is_index(self, tmp_path):
        assert (
            _cache_base(tmp_path, "https://h.example")
            == tmp_path / "h.example" / "index"
        )
        assert (
            _cache_base(tmp_path, "https://h.example/")
            == tmp_path / "h.example" / "index"
        )

    def test_query_is_appended_after_at(self, tmp_path):
        base = _cache_base(
            tmp_path,
            "https://nl.wikipedia.org/w/index.php"
            "?title=Lijst_van_Radio_2-Top_2000%27s&action=raw",
        )
        assert base.parent == tmp_path / "nl.wikipedia.org" / "w"
        assert (
            base.name
            == "index.php@title%3DLijst_van_Radio_2-Top_2000%27s%26action%3Draw"
        )

    def test_dot_segments_stay_under_root(self, tmp_path):
        base = _cache_base(tmp_path, "https://h.example/a/../../etc/passwd")
        assert base.resolve().is_relative_to(tmp_path.resolve())
        assert ".." not in base.relative_to(tmp_path).parts

    def test_long_names_are_shortened_deterministically(self, tmp_path):
        url = "https://h.example/search?q=" + "x" * 500
        first = _cache_base(tmp_path, url)
        assert len(first.name.encode()) <= 200
        assert first == _cache_base(tmp_path, url)
        assert first != _cache_base(tmp_path, url + "y")

    def test_unsafe_characters_are_encoded(self, tmp_path):
        base = _cache_base(tmp_path, "https://h.example:8080/a:b")
        assert base == tmp_path / "h.example%3A8080" / "a%3Ab"
