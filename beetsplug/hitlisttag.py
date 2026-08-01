import pprint
from typing import Any

from beets import library, ui
from beets.dbcore import Results, types
from beets.dbcore.query import SQLiteType
from beets.importer import ImportSession, ImportTask
from beets.library import Item, Library
from beets.plugins import BeetsPlugin
from beets.ui import CommonOptionsParser, Subcommand

from beetsplug.charts import (
    Chart,
    ChartList,
    ChartsParseException,
    _collapse_range,
    charts_field,
    my_song_id_field,
)

HITLISTS_DEFINITION = {
    "top2000": ["year"],
    "top100": ["year"],
    "top40": ["year", "week"],
    "zwaarstelijst": ["year"],
    "kerst": ["year"],
}
HITLISTS = list(HITLISTS_DEFINITION.keys())

FIELDS = ["", "score", "highest", "when"]


class ChartListType(types.Type[ChartList, None]):
    sql: str = "TEXT"

    @property
    def null(self) -> None:
        """The value to be exposed when the underlying value is None."""
        return None

    def format(self, value: ChartList | None) -> str:
        """Given a value of this type, produce a Unicode string
        representing the value. This is used in template evaluation.
        """
        if value is None:
            return ""
        s = value.to_json_string()
        #        print(f"ChartListType format: {s}")
        return s

    def parse(self, string: str) -> ChartList | None:
        """Parse a (possibly human-written) string and return the
        indicated value of this type.
        """
        try:
            #            print(f"ChartListType parse: {string}")
            return ChartList.from_json_string(string)
        except ChartsParseException:
            return None

    def normalize(self, value: Any) -> ChartList | None:
        """Given a value that will be assigned into a field of this
        type, normalize the value to have the appropriate type. This
        base implementation only reinterprets `None`.
        """
        if value is None:
            return ""
        elif isinstance(value, ChartList):
            s = value.to_json_string()
            #            print(f"ChartListType normalize from ChartList: {s}")
            return s

        elif isinstance(value, str):
            #            print(f"ChartListType normalize from atr: {value}")
            v = ChartList.from_json_string(value)
            s = v.to_json_string()
            return s
        else:
            print(f"ERROR: Could not interpret ChartType of type '{type(value)}' ")
            pprint.pp(value)
            return self.null

    def to_sql(self, model_value: "ChartList") -> SQLiteType:
        if isinstance(model_value, ChartList):
            s = model_value.to_json_string()
            #            print(f"ChartListType to_sql: {s}")
            return s
        elif model_value is None:
            return ""
        else:
            raise ChartsParseException(
                f"Could not encode modelvalue of type {type(model_value)} : "
                f"{model_value}"
            )


NULLINTEGER = types.NullInteger()
CHARTLISTTYPE = ChartListType()


class HitlistTag(BeetsPlugin):
    @property
    def item_types(self):
        out = {
            "my_song_id": NULLINTEGER,
            "charts": CHARTLISTTYPE,
        }

        for h in HITLISTS:
            out[f"{h}"] = types.BOOLEAN
            out[f"{h}_score"] = types.INTEGER
            out[f"{h}_highest"] = types.STRING
            out[f"{h}_when"] = types.STRING

        return out

    def __init__(self):
        super().__init__()

        self.config.add(
            {"auto": False, "overwrite": False, "format": "$artist - $album - $title"}
        )

        if self.config["auto"]:
            self.import_stages = [self.imported]

        self.register_listener("pluginload", self.loaded)
        # potentially hook to database_change??

        self.add_media_field("charts", charts_field)
        self.add_media_field("my_song_id", my_song_id_field)

    def loaded(self):
        self._log.info("HitlistTag plugin loaded")

    def commands(self) -> list[Subcommand]:
        cmd1 = Subcommand("chartsupdate", help="Read a 'CHARTS' tag and process it ")
        cmd1.func = self.update
        cmd2 = Subcommand("charts", help="Show chart entries")
        cmd2.parser.add_option(
            "-F",
            "--full",
            dest="full",
            action="store_true",
            help="Show all positions",
        )
        cmd2.func = self.show_charts

        cmd3 = Subcommand("hitlist", help="Show hitlist")
        cmd3.parser.set_usage("%prog [options] hitlist year [week*/]")
        cmd3.parser.add_option(
            "-M",
            "--missing",
            dest="show_missing",
            action="store_true",
            help="Summarize missing items",
        )
        cmd3.parser.add_path_option()
        cmd3.parser.add_format_option(target=library.Item)
        cmd3.func = self.show_hitlist

        return [cmd1, cmd2, cmd3]

    def show_charts(
        self, lib: Library, opts: CommonOptionsParser, args: list[str]
    ) -> None:
        if opts.full:
            self.show_charts_full(lib, opts, args)
        else:
            self.show_charts_summary(lib, opts, args)

    def show_charts_full(
        self, lib: Library, opts: CommonOptionsParser, args: list[str]
    ) -> None:
        items = lib.items(ui.decargs(args))
        item: Item
        for item in items:
            if "charts" not in item._values_flex.keys():
                self._log.info("No charts information for {0}", item)
                continue

            chartlist: ChartList = item.charts
            for chart in chartlist:
                ui.print_(f"Chart {chart.name} for {item}")
                ui.print_(chart.to_string())

    def show_charts_summary(
        self, lib: Library, opts: CommonOptionsParser, args: list[str]
    ) -> None:
        items = lib.items(ui.decargs(args))
        item: Item
        for item in items:
            if "charts" not in item._values_flex.keys():
                self._log.info("No charts information for {0}", item)
                continue
            chartlist: ChartList = item.charts
            ui.print_(f"\nChart summary for {item}:")
            for chart in chartlist:
                ui.print_(chart.summary())

    def update_item(self, item: Item) -> None:
        if "charts" not in item._values_flex.keys():
            self._log.debug(f"No charts information for {item}")
            return

        chartlist: ChartList = item.charts
        self._log.debug(f"Parsing charts json for {item}:")
        for chart in chartlist:
            if chart.name not in HITLISTS:
                self._log.error(
                    f"Unknown hitlist: {chart.name}. "
                    f"Will not parse into flexible fields."
                )
                continue
            # existence
            item[f"{chart.name}"] = True
            # score
            item[f"{chart.name}_score"] = chart.score

            # highest
            item[f"{chart.name}_highest"] = chart.highest
            # when
            item[f"{chart.name}_when"] = chart.when()

        item.store()

    def update(self, lib: Library, opts: CommonOptionsParser, args: list[str]) -> None:
        items = lib.items(ui.decargs(args))
        self.update_items(items)

    def update_items(self, items: Results[Item]):
        item: Item
        for item in items:
            self.update_item(item)

    def imported(self, session: ImportSession, task: ImportTask) -> None:
        self.update_items(task.imported_items())

    def show_hitlist(
        self, lib: Library, opts: CommonOptionsParser, args: list[str]
    ) -> None:
        if len(args) < 1:
            self._log.error(f"No hitlist provided. Options are [{'/'.join(HITLISTS)}]")
            return
        # parse arguments
        if args[0] not in HITLISTS:
            self._log.error(
                f"Unknown hitlist requested ({args[0]}). "
                f"Options are [{'/'.join(HITLISTS)}]"
            )
            return
        else:
            hitlist = args[0]

        expected_args = HITLISTS_DEFINITION[hitlist]
        if len(args) != 1 + len(expected_args):
            self._log.error(
                f"Not enough arguments for hitlist {hitlist}. "
                f"Please provide: {', '.join(expected_args)}"
            )
            return

        if not all([x.isnumeric() for x in args[1:]]):
            self._log.error(
                f"Arguments are not numeric: Please provide: {', '.join(expected_args)}"
            )
            return

        when = ", ".join(map(" ".join, zip(expected_args, args[1:], strict=False)))
        self._log.info(f"Generating hitlist {hitlist} for {when}")

        result = []

        items = lib.items()
        for item in items:
            if "charts" not in item._values_flex.keys():
                continue  # no chart information

            chartlist: ChartList = item.charts
            chart: Chart = chartlist.get_chart(hitlist)
            if chart is None:
                continue

            position = chart.get_position(*map(int, args[1:]))
            if position is None:
                continue

            result += [(position, item)]

        result = sorted(result, key=lambda x: x[0])

        found = [x[0] for x in result]
        # TODO: find logical maximum
        missing = [x for x in range(1, max(found)) if x not in found]

        if opts.format:
            fmt = ui.decargs([opts.format])[0]
        else:
            fmt = self.config["format"].get(str)
        for item in [r[1] for r in result]:
            ui.print_(format(item, fmt))

        if opts.show_missing:
            ui.print_(f"Missing the following positions: {_collapse_range(missing)}")

        pass
