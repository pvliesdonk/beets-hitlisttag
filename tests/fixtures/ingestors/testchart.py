"""Fixture ingestor for a made-up chart. No network, no real chart data.

Lives outside beetsplug/ on purpose: it is the proof that a third party can
add a chart without modifying the package.
"""

from beetsplug.hitlisttag.ingest import AcquiredEdition, EditionRef, RawEntry, RawSong


class FixtureIngestor:
    chart = "testchart"
    axes = ("year",)

    def editions(self):
        return [EditionRef({"year": 2001}), EditionRef({"year": 2002})]

    def fetch(self, ref):
        year = ref.axes["year"]
        entries = (
            RawEntry(1, (RawSong("Fixture Artist", f"Song of {year}"),)),
            # A release the source lists as two songs at one rank.
            RawEntry(2, (RawSong("Pair", "Side A"), RawSong("Pair", "Side B"))),
        )
        return AcquiredEdition(ref, size=3, entries=entries)


INGESTOR = FixtureIngestor()
