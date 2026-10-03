"""Shared HTTP fetching for ingestors.

``Fetcher`` is the one way an ingestor fetches from the web: it paces
requests (at least ``min_interval`` seconds apart), retries transient
failures, identifies the project in its User-Agent, and turns every failure
into ``IngestError``. ``get`` returns the page text, or ``None`` for a 404 so
the caller decides what absence means.

For development, setting ``HITLISTTAG_HTTP_CACHE`` to a directory makes
``Fetcher`` keep every page it fetches there as a readable file and serve it
from there afterwards, without touching the site.
"""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
import tempfile
import time
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from urllib.parse import quote, urlsplit

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from beetsplug.hitlisttag.ingest import IngestError

CACHE_ENV = "HITLISTTAG_HTTP_CACHE"
"""Environment variable naming the development cache directory."""

TIMEOUT = 30
"""Seconds per request."""

_RETRY_STATUSES = (429, 500, 502, 503, 504)

_MAX_NAME = 200
"""Bytes allowed for a cache file name before it is shortened (suffix excluded)."""


def _encode(segment: str) -> str:
    """A URL piece made safe as one file name; existing %-escapes are kept."""
    if segment in (".", ".."):
        return segment.replace(".", "%2E")
    return quote(segment, safe="-_.~%")


def _cache_base(root: Path, url: str) -> Path:
    """Where ``url`` is cached under ``root``, without the suffix.

    ``https://host/a/b?q`` maps to ``root/host/a/b@q``; the root path maps to
    ``index``. Every piece is encoded so it is one safe file name, and a name
    too long for common filesystems is shortened with a hash.
    """
    parts = urlsplit(url)
    segments = [_encode(s) for s in parts.path.split("/") if s] or ["index"]
    name = segments[-1]
    if parts.query:
        name += "@" + _encode(parts.query)
    if len(name.encode()) > _MAX_NAME:
        digest = hashlib.sha256(name.encode()).hexdigest()[:16]
        name = f"{name[:100]}-{digest}"
    return root.joinpath(_encode(parts.netloc), *segments[:-1], name)


_log = logging.getLogger("beets.hitlisttag")


def default_user_agent() -> str:
    """Wikimedia's User-Agent policy shape: tool/version (contact) library/version."""
    try:
        version = metadata.version("beets-hitlisttag")
    except metadata.PackageNotFoundError:
        version = "dev"
    return (
        f"beets-hitlisttag/{version} "
        f"(+https://github.com/pvliesdonk/beets-hitlisttag) "
        f"requests/{requests.__version__}"
    )


class _Adapter(HTTPAdapter):
    """An HTTPAdapter that can carry its own SSL context."""

    def __init__(self, ssl_context: ssl.SSLContext | None = None, **kwargs) -> None:
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs) -> None:
        if self._ssl_context is not None:
            kwargs["ssl_context"] = self._ssl_context
        super().init_poolmanager(*args, **kwargs)


class Fetcher:
    """Paced, retrying HTTP GETs for one ingestor.

    ``min_interval``: seconds between the starts of network requests.
    ``extra_ca_pem``: CA certificates (PEM) trusted on top of certifi's, for
    a site that serves an incomplete certificate chain.
    ``user_agent``: defaults to ``default_user_agent()``.
    ``cache_dir``, ``clock``, ``sleep``: mainly for tests; ``cache_dir``
    defaults to ``$HITLISTTAG_HTTP_CACHE`` when that is set.
    """

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        extra_ca_pem: str | None = None,
        user_agent: str | None = None,
        cache_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None
        if cache_dir is None and os.environ.get(CACHE_ENV):
            cache_dir = Path(os.environ[CACHE_ENV])
        self._cache_dir = cache_dir

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=_RETRY_STATUSES,
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=True,
        )
        context = None
        if extra_ca_pem is not None:
            context = ssl.create_default_context(cafile=certifi.where())
            context.load_verify_locations(cadata=extra_ca_pem)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent or default_user_agent()
        self._session.mount("https://", _Adapter(context, max_retries=retry))
        self._session.mount("http://", _Adapter(max_retries=retry))

    def get(self, url: str) -> str | None:
        """The page text; ``None`` for a 404; ``IngestError`` otherwise."""
        base = None
        if self._cache_dir is not None:
            base = _cache_base(self._cache_dir, url)
            page = base.with_name(base.name + ".page")
            if page.is_file():
                _log.debug(f"cache hit: {url}")
                return page.read_text(encoding="utf-8")
            if base.with_name(base.name + ".404").is_file():
                _log.debug(f"cache hit (404): {url}")
                return None
            _log.debug(f"cache miss: {url}")
        text = self._fetch(url)
        if base is not None:
            self._store(base, text)
        return text

    def _fetch(self, url: str) -> str | None:
        """One paced network GET, outcomes mapped as ``get`` documents."""
        self._pace()
        try:
            response = self._session.get(url, timeout=TIMEOUT)
        except requests.exceptions.SSLError as err:
            host = urlsplit(url).netloc
            raise IngestError(
                f"TLS verification failed for {host}; its certificate chain "
                f"may have changed ({err})"
            ) from err
        except requests.RequestException as err:
            raise IngestError(f"cannot fetch {url}: {err}") from err
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise IngestError(f"cannot fetch {url}: HTTP {response.status_code}")
        return response.text

    def _store(self, base: Path, text: str | None) -> None:
        """Write a page (or a 404 marker) atomically: temp file, then replace."""
        target = base.with_name(base.name + (".404" if text is None else ".page"))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text or "")
                os.replace(tmp, target)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as err:
            raise IngestError(
                f"cannot use the development cache at {self._cache_dir}: {err}"
            ) from err

    def _pace(self) -> None:
        """Wait until ``min_interval`` has passed since the last request began."""
        if self._last_start is not None:
            wait = self._min_interval - (self._clock() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._clock()
