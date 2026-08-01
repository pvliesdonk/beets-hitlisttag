"""Smoke tests for the hitlisttag plugin.

These tests prove the plugin imports and registers its declared flexible
fields against a real, temporary beets library.
"""

from __future__ import annotations

from beets import config
from beets.library import Item
from beets.plugins import find_plugins, load_plugins

from beetsplug.hitlisttag import HitlistTag


def test_plugin_loads_and_declares_flexible_fields():
    """A real beets library sees the plugin's declared item_types."""
    from beets.test.helper import TestHelper

    helper = TestHelper()
    with helper:
        config["plugins"] = ["hitlisttag"]
        load_plugins()

        plugin = next(p for p in find_plugins() if isinstance(p, HitlistTag))
        expected = plugin.item_types
        assert expected, "plugin declared no item_types"
        for field in expected:
            assert field in Item._types, f"plugin field {field!r} not registered"

        assert "charts" in Item._types
        assert "my_song_id" not in Item._types
        assert "top2000" in Item._types
        assert "top2000_score" in Item._types
