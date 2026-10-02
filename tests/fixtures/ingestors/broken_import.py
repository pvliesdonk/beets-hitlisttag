"""Fixture: a drop-in that fails at import time."""

raise RuntimeError("boom at import")
