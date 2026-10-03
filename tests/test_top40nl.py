"""Tests for beetsplug.hitlisttag.top40nl (top40.nl specifics)."""

from __future__ import annotations

import ssl
import time
from pathlib import Path

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
    ctx = adapter.poolmanager.connection_pool_kw["ssl_context"]
    trusted = {
        dict(pair[0] for pair in cert["subject"]).get("commonName")
        for cert in ctx.get_ca_certs()
    }
    assert "Sectigo Public Server Authentication CA DV R36" in trusted


def test_base_url():
    assert top40nl.BASE_URL == "https://www.top40.nl"


FIXTURES = Path(__file__).parent / "fixtures" / "top40nl"


def _items(name: str) -> list[top40nl.ListItem]:
    return top40nl.parse_list((FIXTURES / name).read_text(encoding="utf-8"))


class TestParseList:
    def test_year_layout(self):
        assert _items("year-layout.html") == [
            top40nl.ListItem(
                1,
                "Made Up Song ((1965)) / Made Up Song",
                "Imaginary Duo / Fictional Trio",
                "1001",
                "2001",
            ),
            top40nl.ListItem(2, "Side A ; Side B", "The Pretend Band", "1002", None),
            top40nl.ListItem(
                3, "Rock & Roll Dream", "Sinéad & The Nothings", "1003", "1003_4003"
            ),
            top40nl.ListItem(4, "Spaced Out", "Echo", "1004", None),
        ]

    def test_week_layout_with_dropouts_and_a_sidebar_image(self):
        assert _items("week-layout.html") == [
            top40nl.ListItem(1, "First Tune", "Invented Band", "2001", "2001_3001"),
            top40nl.ListItem(
                2, "Second Tune", "Make Believe feat. Someone", "2002", None
            ),
            top40nl.ListItem(None, "Gone Tune", "Vanished", "2004", "2004_3004"),
            # The sidebar's subtitle image after the list must not leak in.
            top40nl.ListItem(None, "Last Tune", "Phantom", "2005", None),
        ]

    def test_page_without_items(self):
        assert top40nl.parse_list("<html><body><p>Nothing</p></body></html>") == []

    def test_malformed_markup_does_not_raise(self):
        html = '<div class="top40-list__item"><div class="number-block"><h4>7'
        assert top40nl.parse_list(html) == []

    def test_link_without_trailing_id(self):
        html = (
            '<div class="top40-list__item"><div class="number-block"><h4>5</h4></div>'
            '<a href="https://www.top40.nl/x/y" class="h3">T</a>'
            '<a href="#" class="p lead lowercase">A</a></div>'
        )
        assert top40nl.parse_list(html) == [top40nl.ListItem(5, "T", "A", None, None)]
