"""Tests for the normalized exact song lookup (beetsplug.hitlisttag.lookup)."""

from __future__ import annotations

import logging

import pytest

from beetsplug.hitlisttag.lookup import normalize

log = logging.getLogger("test.lookup")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Beyoncé", "beyonce"),
        ("BEYONCE", "beyonce"),
        ("Mötley Crüe", "motley crue"),
        ("Straße", "strasse"),
        ("Encyclopædia", "encyclopaedia"),
        ("Encyclopaedia", "encyclopaedia"),
        ("Cœur", "coeur"),
        ("Søren", "soren"),
        ("Łódź", "lodz"),
        ("Þórr", "thorr"),
        ("Đevojka", "devojka"),
        ("Håkan", "hakan"),
        ("İstanbul", "istanbul"),
        ("naïve café", "naive cafe"),
        ("Café  del   Mar!", "cafe del mar"),
        ("AC/DC", "ac dc"),
        ("...", ""),
        ("   ", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected
