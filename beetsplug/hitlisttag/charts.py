import json
import math
from collections.abc import Iterator
from json import JSONDecodeError

from mediafile import MediaField, MP3DescStorageStyle, MP4StorageStyle, StorageStyle


class ChartsParseException(Exception):
    pass


def _collapse_range(missing: list[int]) -> str:
    prev = None  # previous in list
    last = None  # last written
    in_range = False
    out = ""
    for m in missing:
        if last is None:  # first digit
            last = m
            out = out + str(m)
        elif in_range:
            if m != prev + 1:  # end range
                out = out + "-" + str(prev) + " / " + str(m)
                in_range = False
        else:
            if m == prev + 1:  # start range
                in_range = True
            else:
                out = out + " / " + str(m)

        prev = m
    if in_range:
        out = out + "-" + str(prev)
    return out


class Chart:
    name: str
    score: int
    highest: int
    chart_type: list[str]
    positions: dict
    maximum: int

    def __init__(self, name: str):
        self.name: str = name

    def __iter__(self):

        def _inner(d: dict) -> Iterator:
            for k, v in d.items():
                if isinstance(v, dict):
                    for w in _inner(v):
                        yield (int(k),) + w
                else:
                    yield (int(k), int(v))

        return _inner(self.positions)

    def get_position(self, *position) -> int | None:
        if len(position) != len(self.chart_type):
            return None

        d = self.positions
        for p in position:
            if not (int(p) in d.keys() or str(p) in d.keys()):
                return None
            try:
                d = d[int(p)]
            except KeyError:
                d = d[str(p)]
        return d

    def to_dict(self):
        d = {
            "name": self.name,
            "score": self.score,
            "highest": self.highest,
            "chart_type": self.chart_type,
            "positions": self.positions,
        }
        return d

    def how_long(self) -> int:

        def _inner(d) -> int:
            count = 0
            if isinstance(d, dict):
                for _k, v in d.items():
                    count += _inner(v)
            else:
                count += 1
            return count

        return _inner(self.positions)

    def when(self) -> str:
        level = 0
        prefix = []
        prefix_string = ""
        d = self.positions
        c = list(d.keys())
        while len(c) == 1 and len(self.chart_type) > level + 1:
            prefix.append(self.chart_type[level] + " " + c[0])
            level += 1
            d = d[c[0]]
            c = list(d.keys())
        if len(prefix) > 0:
            prefix_string = " / ".join(prefix) + ": "

        unit = self.chart_type[level] + ("s" if len(c) > 1 else "")
        return f"{prefix_string}{unit}: {_collapse_range([int(y) for y in c])}"

    def how_long_string(self) -> str:
        i = self.how_long()
        return f"{i} {self.chart_type[-1]}{'s' if i > 1 else ''}"

    def to_string(self) -> str:

        max_length = [len(x) for x in self.chart_type + ["position"]]

        def _do_inner(root, level) -> list:
            keys = sorted(root.keys(), key=lambda x: int(x))
            values = []

            for k in keys:
                k_length = int(math.log10(int(k))) + 1
                if k_length > max_length[level]:
                    max_length[level] = k_length
                if isinstance(root[k], dict):
                    for v in _do_inner(root[k], level + 1):
                        values.append([int(k)] + v)
                else:
                    v = int(root[k])
                    v_length = int(math.log10(v)) + 1
                    if v_length > max_length[-1]:
                        max_length[-1] = v_length
                    values.append([int(k), v])
            return values

        inner = _do_inner(self.positions, 0)

        # s = f"{self.name}\n" + ''.join(['-']*len(self.name)) + "\n"
        s = "\t".join(self.chart_type + ["position"]) + "\n"
        for entry in inner:
            formatted = [
                repr(value).rjust(width)
                for value, width in zip(entry, max_length, strict=False)
            ]
            s += "\t".join(formatted) + "\n"

        return s

    def summary(self):
        a = self.chart_type[0]  # year, probably
        c = [int(y) for y in self.positions.keys()]
        b = min(c)

        s = (
            f"{self.name}:\t first {a} {b}, {self.how_long_string()}, "
            f"highest position: {self.highest}, score: {self.score}"
        )
        if len(c) > 1:
            s += ", " + self.chart_type[0] + "s: " + _collapse_range(c)

        return s

    def to_json_string(self):
        return json.dumps(self.to_dict())

    def __str__(self) -> str:
        return self.to_json_string()

    def __repr__(self) -> str:
        return f"<Chart {self.name}, score={self.score}, highest={self.highest}>"

    def __eq__(self, other: "Chart"):
        val = self.to_dict() == other.to_dict()
        return val

    @staticmethod
    def from_dict(d: dict) -> "Chart":
        if not isinstance(d, dict):
            raise ChartsParseException(
                f"Expected dict to build Chart from, got {type(d).__name__}"
            )
        required = {
            "name": str,
            "score": int,
            "highest": int,
            "chart_type": list,
            "positions": dict,
        }
        for key, expected in required.items():
            if key not in d:
                raise ChartsParseException(f"Chart dict missing required key '{key}'")
            if not isinstance(d[key], expected):
                raise ChartsParseException(
                    f"Chart dict key '{key}' should be {expected.__name__}, "
                    f"got {type(d[key]).__name__}"
                )
        chart = Chart(d["name"])
        chart.score = d["score"]
        chart.highest = d["highest"]
        chart.chart_type = d["chart_type"]
        chart.positions = d["positions"]
        return chart

    @staticmethod
    def from_json_string(s: str) -> "Chart":
        d = json.loads(s)
        chart = Chart.from_dict(d)
        return chart


class ChartList(list[Chart]):
    def is_empty(self):
        return len(self) == 0

    @staticmethod
    def from_json_string(s: "str | ChartList | list[Chart]") -> "ChartList":
        """Parse a ChartList from JSON string, Chart list, or existing ChartList.

        A ChartList never holds two charts with the same name; if two charts
        share a name in the input, raises ChartsParseException. This means a
        CHARTS tag with duplicate names is malformed and will be treated as
        absent (ChartListType.normalize degrades parse failures to null).
        """
        chartlist = ChartList()
        seen: set[str] = set()

        if isinstance(s, ChartList):
            return s
        elif isinstance(s, str):
            if not s:
                return chartlist
            try:
                d = json.loads(s)
            except JSONDecodeError as err:
                raise ChartsParseException(f"Could not decode string: '{s}' ") from err
            if not isinstance(d, list):
                raise ChartsParseException(
                    f"Expected a JSON array of charts, got {type(d).__name__}"
                )
            for chart in d:
                parsed_chart = Chart.from_dict(chart)
                if parsed_chart.name in seen:
                    raise ChartsParseException(
                        f"duplicate chart name '{parsed_chart.name}'"
                    )
                seen.add(parsed_chart.name)
                chartlist.append(parsed_chart)
        elif isinstance(s, list) and all(isinstance(x, Chart) for x in s):
            for chart in s:
                if chart.name in seen:
                    raise ChartsParseException(f"duplicate chart name '{chart.name}'")
                seen.add(chart.name)
                chartlist.append(chart)
        else:
            raise ChartsParseException("Received invalid type to deserialize")
        return chartlist

    def get_chart(self, hitlist: str) -> Chart | None:
        for h in self:
            if h.name == hitlist:
                return h
        return None

    def to_json_string(self) -> str:
        return json.dumps([ch.to_dict() for ch in self])

    def __repr__(self):
        s = f"<Chartlist: {', '.join([c.name for c in self])}>"
        return s

    def __str__(self):
        return self.to_json_string()


charts_field = MediaField(
    MP3DescStorageStyle("CHARTS"),
    MP4StorageStyle("----:nl.liesdonk.tagger:CHARTS"),
    StorageStyle("CHARTS"),
    out_type=str,
)
