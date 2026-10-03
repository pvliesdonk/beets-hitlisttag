"""top40.nl specifics shared by the Top 40 and Top 100 ingestors (#117, #118).

top40.nl sends the wrong intermediate certificate, so a client that does not
fetch missing intermediates (Python's ``ssl`` does not) cannot verify the
site (#116). ``SECTIGO_R36_PEM`` is the intermediate that actually issued
the site's certificate; giving it to ``Fetcher`` keeps verification on. If
the site changes CA, fetches fail with an ``IngestError`` saying the
certificate chain may have changed.

``parse_list`` reads a top40.nl list page (a Top 100 year list or a weekly
Top 40) into ``ListItem``s, in page order, without validating them; each
ingestor decides what a usable edition is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from beetsplug.hitlisttag.fetch import Fetcher

BASE_URL = "https://www.top40.nl"

MIN_INTERVAL = 1.0
"""Seconds between requests to the site."""

SECTIGO_R36_PEM = """\
-----BEGIN CERTIFICATE-----
MIIGTDCCBDSgAwIBAgIQOXpmzCdWNi4NqofKbqvjsTANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNMzYwMzIxMjM1OTU5WjBgMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gQ0EgRFYgUjM2MIIBojANBgkqhkiG9w0B
AQEFAAOCAY8AMIIBigKCAYEAljZf2HIz7+SPUPQCQObZYcrxLTHYdf1ZtMRe7Yeq
RPSwygz16qJ9cAWtWNTcuICc++p8Dct7zNGxCpqmEtqifO7NvuB5dEVexXn9RFFH
12Hm+NtPRQgXIFjx6MSJcNWuVO3XGE57L1mHlcQYj+g4hny90aFh2SCZCDEVkAja
EMMfYPKuCjHuuF+bzHFb/9gV8P9+ekcHENF2nR1efGWSKwnfG5RawlkaQDpRtZTm
M64TIsv/r7cyFO4nSjs1jLdXYdz5q3a4L0NoabZfbdxVb+CUEHfB0bpulZQtH1Rv
38e/lIdP7OTTIlZh6OYL6NhxP8So0/sht/4J9mqIGxRFc0/pC8suja+wcIUna0HB
pXKfXTKpzgis+zmXDL06ASJf5E4A2/m+Hp6b84sfPAwQ766rI65mh50S0Di9E3Pn
2WcaJc+PILsBmYpgtmgWTR9eV9otfKRUBfzHUHcVgarub/XluEpRlTtZudU5xbFN
xx/DgMrXLUAPaI60fZ6wA+PTAgMBAAGjggGBMIIBfTAfBgNVHSMEGDAWgBRWc1hk
lfmSGrASKgRieaFAFYghSTAdBgNVHQ4EFgQUaMASFhgOr872h6YyV6NGUV3LBycw
DgYDVR0PAQH/BAQDAgGGMBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0lBBYwFAYI
KwYBBQUHAwEGCCsGAQUFBwMCMBsGA1UdIAQUMBIwBgYEVR0gADAIBgZngQwBAgEw
VAYDVR0fBE0wSzBJoEegRYZDaHR0cDovL2NybC5zZWN0aWdvLmNvbS9TZWN0aWdv
UHVibGljU2VydmVyQXV0aGVudGljYXRpb25Sb290UjQ2LmNybDCBhAYIKwYBBQUH
AQEEeDB2ME8GCCsGAQUFBzAChkNodHRwOi8vY3J0LnNlY3RpZ28uY29tL1NlY3Rp
Z29QdWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYucDdjMCMGCCsGAQUF
BzABhhdodHRwOi8vb2NzcC5zZWN0aWdvLmNvbTANBgkqhkiG9w0BAQwFAAOCAgEA
YtOC9Fy+TqECFw40IospI92kLGgoSZGPOSQXMBqmsGWZUQ7rux7cj1du6d9rD6C8
ze1B2eQjkrGkIL/OF1s7vSmgYVafsRoZd/IHUrkoQvX8FZwUsmPu7amgBfaY3g+d
q1x0jNGKb6I6Bzdl6LgMD9qxp+3i7GQOnd9J8LFSietY6Z4jUBzVoOoz8iAU84OF
h2HhAuiPw1ai0VnY38RTI+8kepGWVfGxfBWzwH9uIjeooIeaosVFvE8cmYUB4TSH
5dUyD0jHct2+8ceKEtIoFU/FfHq/mDaVnvcDCZXtIgitdMFQdMZaVehmObyhRdDD
4NQCs0gaI9AAgFj4L9QtkARzhQLNyRf87Kln+YU0lgCGr9HLg3rGO8q+Y4ppLsOd
unQZ6ZxPNGIfOApbPVf5hCe58EZwiWdHIMn9lPP6+F404y8NNugbQixBber+x536
WrZhFZLjEkhp7fFXf9r32rNPfb74X/U90Bdy4lzp3+X1ukh1BuMxA/EEhDoTOS3l
7ABvc7BYSQubQ2490OcdkIzUh3ZwDrakMVrbaTxUM2p24N6dB+ns2zptWCva6jzW
r8IWKIMxzxLPv5Kt3ePKcUdvkBU/smqujSczTzzSjIoR5QqQA6lN1ZRSnuHIWCvh
JEltkYnTAH41QJ6SAWO66GrrUESwN/cgZzL4JLEqz1Y=
-----END CERTIFICATE-----
"""
"""Sectigo Public Server Authentication CA DV R36, valid until 2036-03-21."""


def fetcher() -> Fetcher:
    """A ``Fetcher`` configured for top40.nl."""
    return Fetcher(min_interval=MIN_INTERVAL, extra_ca_pem=SECTIGO_R36_PEM)


_SUBTITLE = re.compile(r"/uploads/subtitle/([^/]+)/")
_TITLE_ID = re.compile(r"-(\d+)/?$")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ListItem:
    """One item of a top40.nl list page, as published.

    ``position`` is None for a dropout ("-") or an unreadable number.
    ``title`` and ``artist`` are "" when absent. ``title_id`` is the
    trailing number of the title link; ``subtitle`` the raw value of the
    item image's ``uploads/subtitle/<id>/`` path, when there is one.
    """

    position: int | None
    title: str
    artist: str
    title_id: str | None
    subtitle: str | None


def _classes(attrs: list[tuple[str, str | None]]) -> list[str]:
    for name, value in attrs:
        if name == "class" and value:
            return value.split()
    return []


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


class _ListParser(HTMLParser):
    """Collects ``top40-list__item`` blocks; each ends at its own ``</div>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[ListItem] = []
        self._item: dict | None = None
        self._depth = 0  # div nesting inside the current item
        self._field: str | None = None  # "position", "title" or "artist"
        self._field_tag: str | None = None
        self._field_depth = 0  # nesting of _field_tag inside the field

    def handle_starttag(self, tag, attrs):
        classes = _classes(attrs)
        if self._item is None:
            if tag == "div" and "top40-list__item" in classes:
                self._item = {"title_id": None, "subtitle": None, "text": {}}
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        if self._field is not None:
            if tag == self._field_tag:
                self._field_depth += 1
            return
        item = self._item
        if tag == "img" and item["subtitle"] is None:
            match = _SUBTITLE.search(dict(attrs).get("src") or "")
            if match:
                item["subtitle"] = match.group(1)
        elif "number-block" in classes and "position" not in item["text"]:
            self._open("position", tag)
        elif tag == "a" and "h3" in classes and "title" not in item["text"]:
            match = _TITLE_ID.search(dict(attrs).get("href") or "")
            item["title_id"] = match.group(1) if match else None
            self._open("title", tag)
        elif tag == "a" and "lead" in classes and "artist" not in item["text"]:
            self._open("artist", tag)

    def _open(self, field: str, tag: str) -> None:
        self._field, self._field_tag, self._field_depth = field, tag, 1
        self._item["text"][field] = []

    def handle_endtag(self, tag):
        if self._item is None:
            return
        if self._field is not None and tag == self._field_tag:
            self._field_depth -= 1
            if self._field_depth == 0:
                self._field = None
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._finish()

    def handle_data(self, data):
        if self._item is not None and self._field is not None:
            self._item["text"][self._field].append(data)

    def _finish(self) -> None:
        item, self._item, self._field = self._item, None, None
        text = {key: _clean("".join(parts)) for key, parts in item["text"].items()}
        raw = text.get("position", "")
        self.items.append(
            ListItem(
                position=int(raw) if raw.isascii() and raw.isdigit() else None,
                title=text.get("title", ""),
                artist=text.get("artist", ""),
                title_id=item["title_id"],
                subtitle=item["subtitle"],
            )
        )


def parse_list(html: str) -> list[ListItem]:
    """The list items on a top40.nl list page, in page order, unvalidated."""
    parser = _ListParser()
    parser.feed(html)
    parser.close()
    return parser.items
