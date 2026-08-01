"""Validate that the committed audio fixtures load and are the format we expect.

The suite exercises only the fixtures and the shared harness: it opens each
fixture through mediafile and checks the reported type and length.
"""

from __future__ import annotations

import mediafile
import pytest

# Fixture filename -> the media type mediafile reports for it.
FIXTURES = {
    "empty.flac": "flac",
    "empty.mp3": "mp3",
}


@pytest.mark.parametrize("name, expected_type", sorted(FIXTURES.items()))
def test_fixture_opens_with_expected_type(rsrc_dir, name, expected_type):
    path = rsrc_dir / name
    assert path.is_file(), f"missing fixture: {name}"
    media = mediafile.MediaFile(str(path))
    assert media.type == expected_type
    assert media.length and media.length > 0


def test_all_declared_fixtures_present(rsrc_dir):
    present = {p.name for p in rsrc_dir.iterdir() if p.is_file()}
    assert set(FIXTURES) <= present
