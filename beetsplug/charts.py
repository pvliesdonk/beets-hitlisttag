import json
import math
from json import JSONDecodeError
from typing import Optional, Iterator

import mediafile
from mediafile import (MediaField, MP3DescStorageStyle, MP4StorageStyle, StorageStyle)


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
            for k,v in d.items():
                if isinstance(v, dict):
                    for w in _inner(v):
                        yield (int(k),) + w
                else:
                    yield (int(k),int(v))

        return _inner(self.positions)

    def get_position(self, *position) -> Optional[int]:
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
            "positions": self.positions
        }
        return d

    def how_long(self) -> int:

        def _inner(d) -> int:
            count = 0
            if isinstance(d, dict):
                for k, v in d.items():
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

        unit = self.chart_type[level] + ("s" if len(c) > 1 else '')
        return f"{prefix_string}{unit}: {_collapse_range([int(y) for y in c])}"

    def how_long_string(self) -> str:
        i = self.how_long()
        return f"{i} {self.chart_type[-1]}{'s' if i > 1 else ''}"

    def to_string(self) -> str:

        max_length = [len(x) for x in self.chart_type + ['position']]

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
            s += "\t".join([repr(x[0]).rjust(x[1]) for x in list(zip(entry, max_length))]) + "\n"

        return s

    def summary(self):
        a = self.chart_type[0]  # year, probably
        c = [int(y) for y in self.positions.keys()]
        b = min(c)

        s = f"{self.name}:\t first {a} {b}, {self.how_long_string()}, highest position: {self.highest}, score: {self.score}"
        if len(c) > 1:
            s += ", " + self.chart_type[0] + 's: ' + _collapse_range(c)

        return s

    def to_json_string(self):
        return json.dumps(self.to_dict())

    def __str__(self) -> str:
        return self.to_json_string()

    def __repr__(self) -> str:
        return f"<Chart {self.name}, score={self.score}, highest={self.highest}>"

    def __eq__(self, other: "Chart"):
        val =  self.to_dict() == other.to_dict()
        return val

    @staticmethod
    def from_dict(d: dict) -> "Chart":
        name = d["name"]
        chart = Chart(name)
        chart.score = d["score"]
        if "highest" in d.keys():
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
    def from_json_string(s: str) -> "ChartList":
        chartlist = ChartList()
        if isinstance(s, ChartList):
            return s
        elif isinstance(s, str):
            if not s:
                return chartlist
            try:
                d = json.loads(s)
            except JSONDecodeError as ex:
                raise ChartsParseException(f"Could not decode string: '{s}' ")
        elif isinstance(s, list) and all([isinstance(Chart, x) for x in s]):
            for chart in s:
                chartlist.append(chart)
        else:
            raise ChartsParseException("Received invalid type to deserialize")

        for chart in d:
            chartlist.append(Chart.from_dict(chart))
        return chartlist

    def get_chart(self, hitlist: str) -> Optional[Chart]:
        for h in self:
            if h.name == hitlist:
                return h
        return None

    def to_json_string(self) -> str:
        return json.dumps([ch.to_dict() for ch in self])

    def __repr__(self):
        s = f"<Chartlist: {", ".join([c.name for c in self])}>"
        return s

    def __str__(self):
        return self.to_json_string()


charts_field = MediaField(
    MP3DescStorageStyle(u'CHARTS'),
    MP4StorageStyle(u'----:nl.liesdonk.tagger:CHARTS'),
    StorageStyle(u'CHARTS'),
    out_type=str
)
# mediafile.MediaFile.add_field("charts_json", charts_field)

my_song_id_field = MediaField(
    MP3DescStorageStyle(u'MY_SONG_ID'),
    MP4StorageStyle(u'----:nl.liesdonk.tagger:MY_SONG_ID'),
    StorageStyle(u'MY_SONG_ID'),
    out_type=int
)

backup_artist_field = MediaField(
    MP3DescStorageStyle(u'BACKUP_ARTIST'),
    MP4StorageStyle(u'----:nl.liesdonk.tagger:BACKUP_ARTIST'),
    StorageStyle(u'BACKUP_ARTIST'),
    StorageStyle(u'BACKUP_ORIGINAL_ARTIST', read_only=True),
    out_type=str
)

backup_title_field = MediaField(
    MP3DescStorageStyle(u'BACKUP_TITLE'),
    MP4StorageStyle(u'----:nl.liesdonk.tagger:BACKUP_TITLE'),
    StorageStyle(u'BACKUP_TITLE'),
    StorageStyle(u'BACKUP_ORIGINAL_TITLE', read_only=True),
    out_type=str
)



def install() -> None:
    mediafile.MediaFile.add_field("charts", charts_field)
    mediafile.MediaFile.add_field("my_song_id", my_song_id_field)
    mediafile.MediaFile.add_field("backup_title", backup_title_field)
    mediafile.MediaFile.add_field("backup_artist", backup_artist_field)


if __name__ == "__main__":
    JSON = """[{"name": "top2000", "score": 14065, "highest": 529, "chart_type": ["year"], "positions": {"2015": 585, 
    "2016": 582, "2017": 691, "2018": 551, "2019": 715, "2020": 594, "2021": 555, "2022": 560, "2023": 583, 
    "2024": 529}}, {"name": "top100", "score": 79, "highest": 22, "chart_type": ["year"], "positions": {"1999": 22}}, 
    {"name": "top40", "score": 393, "highest": 1, "chart_type": ["year", "week"], "positions": {"1999": {"7": 1, 
    "8": 1, "9": 1, "10": 2, "11": 3, "12": 3, "13": 6, "14": 8, "15": 13, "16": 16, "17": 21, "18": 31, "19": 36, 
    "20": 39}}}]"""

    jd = json.loads(JSON)

    chartlist: ChartList = ChartList.from_json_string(JSON)

    chart0: Chart = chartlist[0]

    print(chartlist[2].to_string())

    print(chartlist[0].when())
    print(chartlist[1].when())
    print(chartlist[2].when())

    pass
