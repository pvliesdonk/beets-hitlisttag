"""Shared pytest fixtures.

beets does not ship its own test resources in a way a downstream suite can
rely on, so this package carries its own audio fixtures under ``tests/rsrc/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from beets.util import cached_classproperty

RSRC = Path(__file__).parent / "rsrc"


@pytest.fixture(autouse=True)
def _reset_model_type_cache():
    """Clear beets' per-process ``Item._types`` cache around each test.

    ``Item._types`` is a ``cached_classproperty`` computed once per process, so
    a test that reads it with no plugins loaded would freeze an empty type map
    for the rest of the session. ``TestHelper``'s setup/teardown does not clear
    it.
    """
    cached_classproperty.cache.clear()
    yield
    cached_classproperty.cache.clear()


@pytest.fixture(autouse=True)
def _isolate_beets_config():
    """Undo any ``config.set()`` a test makes, per test.

    beets' ``config`` is a process-global confuse singleton, so a scalar
    ``.set()`` — whether ``config[...].set(...)`` in a test or ``config.add`` in
    ``HitlistTag.__init__`` — prepends an overlay source that outlives the test
    and leaks into every later one. Confuse resolves values through
    ``config.sources`` on each access, so snapshotting that list and restoring it
    around each test drops the test's overlays without disturbing the underlying
    user/default sources.

    ``resolve()`` first is load-bearing: ``LazyConfig`` buffers pre-read
    ``set``/``add`` calls and only unspools them into ``sources`` on the first
    ``resolve()``, which then latches ``_materialized`` so files are never
    re-read. Snapshotting before that first resolve would capture an empty
    ``sources`` and the teardown would wipe the base config for good.
    """
    from beets import config

    config.resolve()  # force materialization so the snapshot includes base sources
    saved = list(config.sources)
    try:
        yield
    finally:
        config.sources[:] = saved


@pytest.fixture(autouse=True)
def _reset_plugin_instances():
    """Clear beets' loaded plugin instances around each test.

    ``beets.plugins._instances`` is a process-global list. ``load_plugins()``
    only populates it once, so a test that loads plugins must start from an
    empty list to avoid seeing state from earlier tests.
    """
    import beets.plugins

    saved = list(beets.plugins._instances)
    beets.plugins._instances.clear()
    try:
        yield
    finally:
        beets.plugins._instances[:] = saved


@pytest.fixture(autouse=True)
def _idempotent_media_field():
    """Make ``MediaFile.add_field`` idempotent across test re-loads.

    The hitlisttag plugin registers a custom ``charts`` media field in
    ``__init__`` via ``add_media_field``. ``_reset_plugin_instances`` clears
    the loaded instances per test, so the plugin re-instantiates on the next
    ``load_plugins()`` — but mediafile raises ``ValueError`` if a field is
    registered twice on the class. Since the descriptor is a module-level
    singleton, re-registering the same field is a safe no-op.
    """
    import mediafile

    original = mediafile.MediaFile.__dict__["add_field"]

    def patched(cls, name, descriptor):
        if name in cls.__dict__:
            return  # already registered with the same singleton descriptor
        return original.__func__(cls, name, descriptor)

    mediafile.MediaFile.add_field = classmethod(patched)
    try:
        yield
    finally:
        mediafile.MediaFile.add_field = original


@pytest.fixture
def rsrc_dir() -> Path:
    """Directory holding the committed audio test fixtures."""
    return RSRC
