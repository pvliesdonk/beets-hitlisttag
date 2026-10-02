"""Fixture: a drop-in whose axes is a string instead of a tuple."""


class BadAxes:
    chart = "badaxes"
    axes = "year"

    def editions(self):
        return []

    def fetch(self, ref):
        raise NotImplementedError


INGESTOR = BadAxes()
