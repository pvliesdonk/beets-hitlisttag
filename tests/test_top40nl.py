"""Tests for beetsplug.hitlisttag.top40nl (top40.nl specifics)."""

from __future__ import annotations

import ssl
import time

from beetsplug.hitlisttag import top40nl
from beetsplug.hitlisttag.fetch import Fetcher


def _loaded_cert() -> dict:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=top40nl.SECTIGO_R36_PEM)
    [cert] = ctx.get_ca_certs()
    return cert


def test_embedded_intermediate_is_sectigo_r36():
    subject = dict(pair[0] for pair in _loaded_cert()["subject"])
    assert subject["commonName"] == "Sectigo Public Server Authentication CA DV R36"


def test_embedded_intermediate_has_not_expired():
    not_after = ssl.cert_time_to_seconds(_loaded_cert()["notAfter"])
    assert not_after > time.time(), "SECTIGO_R36_PEM expired; refresh it (#126)"


def test_fetcher_is_configured_for_the_site():
    fetcher = top40nl.fetcher()
    assert isinstance(fetcher, Fetcher)
    assert fetcher._min_interval == 1.0
    adapter = fetcher._session.get_adapter(top40nl.BASE_URL + "/")
    assert "ssl_context" in adapter.poolmanager.connection_pool_kw


def test_base_url():
    assert top40nl.BASE_URL == "https://www.top40.nl"
