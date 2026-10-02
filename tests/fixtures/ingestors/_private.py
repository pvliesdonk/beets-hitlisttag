"""Fixture: underscore-prefixed, so discovery must skip it."""

from beetsplug.hitlisttag.ingest import AcquiredEdition


class Hidden:
    chart = "hidden"
    axes = ("year",)

    def editions(self):
        return []

    def fetch(self, ref):
        return AcquiredEdition(ref, 1, ())


INGESTOR = Hidden()
