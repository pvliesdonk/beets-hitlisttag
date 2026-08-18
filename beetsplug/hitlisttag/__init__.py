from pathlib import Path
from typing import Any

import confuse
from beets import library, ui
from beets import logging as beets_logging
from beets.dbcore import Results, types
from beets.dbcore.query import SQLiteType
from beets.importer import ImportSession, ImportTask
from beets.library import Item, Library
from beets.plugins import BeetsPlugin
from beets.ui import CommonOptionsParser, Subcommand
from beets.util import syspath
from mediafile import MediaFile, UnreadableFileError

from .charts import (
    Chart,
    ChartList,
    ChartsParseException,
    _collapse_range,
    charts_field,
)
from .dataset import DatasetError, read_dataset
from .generate import RunReport, build_chart, merge_charts
from .lookup import SongLookupIndex

log = beets_logging.getLogger("beets.hitlisttag")

# Shipped hitlist definitions: name -> list of axis names. Used as the
# default for the `hitlists` config key. A present key replaces these
# defaults (it does not merge); the resolved definitions at runtime come
# from `HitlistTag.hitlists`, which reads that config key.
DEFAULT_HITLISTS = {
    "top2000": ["year"],
    "top100": ["year"],
    "top40": ["year", "week"],
    "zwaarstelijst": ["year"],
    "kerst": ["year"],
}

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
        return value.to_json_string()

    def parse(self, string: str) -> ChartList | None:
        """Parse a (possibly human-written) string and return the
        indicated value of this type.
        """
        try:
            return ChartList.from_json_string(string)
        except ChartsParseException:
            return None

    def normalize(self, value: Any) -> ChartList | None:
        """Normalize a value assigned to a charts field.

        None maps to the null value, a ChartList passes through, and a
        string is parsed as JSON via ChartList.from_json_string. Parse
        failures degrade to null with a warning so a corrupt CHARTS tag
        is diagnosable rather than silently lost.
        """
        if value is None:
            return self.null
        elif isinstance(value, ChartList):
            return value
        elif isinstance(value, str):
            try:
                return ChartList.from_json_string(value)
            except ChartsParseException as err:
                log.warning(
                    "Could not parse charts value, falling back to null: {}", err
                )
                return self.null
        else:
            log.error(
                "Could not interpret ChartList value of type '{}'",
                type(value).__name__,
            )
            return self.null

    def to_sql(self, model_value: "ChartList") -> SQLiteType:
        if isinstance(model_value, ChartList):
            return model_value.to_json_string()
        elif model_value is None:
            return None
        else:
            raise ChartsParseException(
                f"Could not encode modelvalue of type {type(model_value)} : "
                f"{model_value}"
            )


CHARTLISTTYPE = ChartListType()


class HitlistTag(BeetsPlugin):
    @property
    def item_types(self):
        out = {
            "charts": CHARTLISTTYPE,
        }

        for h in self.hitlists:
            out[f"{h}"] = types.BOOLEAN
            out[f"{h}_score"] = types.INTEGER
            out[f"{h}_highest"] = types.STRING
            out[f"{h}_when"] = types.STRING

        return out

    def __init__(self):
        super().__init__()

        self.config.add(
            {
                "auto": False,
                "overwrite": False,
                "format": "$artist - $album - $title",
                "hitlists": DEFAULT_HITLISTS,
                "dataset_dir": None,
            }
        )

        if self.config["auto"]:
            self.import_stages = [self.imported]

        self.register_listener("pluginload", self.loaded)
        # potentially hook to database_change??

        self.add_media_field("charts", charts_field)

    @property
    def hitlists(self) -> dict[str, list[str]]:
        """Resolved hitlist definitions (name -> list of axis names).

        The `hitlists` key is registered with the shipped DEFAULT_HITLISTS
        by `config.add`, so absent user configuration resolves to those
        defaults. A malformed or empty value degrades to an empty dict so
        no per-chart fields are generated and commands report an empty
        hitlist set rather than crash. Individual entries whose axes are
        not a non-empty list of strings are dropped with a warning; the rest
        are kept.
        """
        try:
            raw = self.config["hitlists"].get(dict)
        except confuse.NotFoundError:
            return dict(DEFAULT_HITLISTS)
        except confuse.ConfigValueError as err:
            self._log.warning("hitlists config is malformed, ignoring: {}", err)
            return {}

        if not isinstance(raw, dict) or not raw:
            self._log.warning(
                "hitlists config is empty; no per-chart fields will be generated"
            )
            return {}

        resolved: dict[str, list[str]] = {}
        for name, axes in raw.items():
            if (
                not isinstance(axes, list)
                or not axes
                or not all(isinstance(a, str) for a in axes)
            ):
                self._log.warning(
                    "hitlist '{}' has invalid axes {!r}; skipping", name, axes
                )
                continue
            resolved[name] = axes
        return resolved

    @property
    def dataset_dir(self) -> Path | None:
        """Resolved dataset directory, or None when unconfigured.

        `as_path` raises on a None value, so the unset default is guarded
        explicitly. A set value is returned absolute and tilde-expanded,
        resolved relative to the config directory.
        """
        if self.config["dataset_dir"].get() is None:
            return None
        return self.config["dataset_dir"].as_path()

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

        cmd4 = Subcommand(
            "chartsgen", help="Generate CHARTS tags from the chart dataset"
        )
        cmd4.func = self.generate

        return [cmd1, cmd2, cmd3, cmd4]

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
            if "charts" not in item._values_flex.keys() or item.charts is None:
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
            if "charts" not in item._values_flex.keys() or item.charts is None:
                self._log.info("No charts information for {0}", item)
                continue
            chartlist: ChartList = item.charts
            ui.print_(f"\nChart summary for {item}:")
            for chart in chartlist:
                ui.print_(chart.summary())

    def update_item(self, item: Item) -> None:
        if "charts" not in item._values_flex.keys() or item.charts is None:
            self._log.debug(f"No charts information for {item}")
            return

        hitlists = self.hitlists
        chartlist: ChartList = item.charts
        self._log.debug(f"Parsing charts json for {item}:")
        for chart in chartlist:
            if chart.name not in hitlists:
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
        hitlists = self.hitlists
        available = "/".join(hitlists)
        if len(args) < 1:
            self._log.error(f"No hitlist provided. Options are [{available}]")
            return
        # parse arguments
        if args[0] not in hitlists:
            self._log.error(
                f"Unknown hitlist requested ({args[0]}). Options are [{available}]"
            )
            return
        else:
            hitlist = args[0]

        expected_args = hitlists[hitlist]
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
            if "charts" not in item._values_flex.keys() or item.charts is None:
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

        if not result:
            ui.print_(f"No positions found for hitlist {hitlist} for {when}.")
            return

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

    def generate(
        self, lib: Library, opts: CommonOptionsParser, args: list[str]
    ) -> None:
        """Generate CHARTS tags from the chart dataset (chartsgen command)."""
        dataset_dir = self.dataset_dir
        if dataset_dir is None:
            raise ui.UserError(
                "hitlisttag: chartsgen requires the dataset_dir option; "
                "set hitlisttag.dataset_dir in your beets config"
            )
        hitlists = self.hitlists
        try:
            datasets = read_dataset(dataset_dir, hitlists, self._log)
        except DatasetError as err:
            raise ui.UserError(f"hitlisttag: {err}") from err
        if not datasets:
            raise ui.UserError(
                f"hitlisttag: no chart data found under {dataset_dir}; "
                "check dataset_dir and the dataset files' chart names"
            )
        index = SongLookupIndex.from_datasets(datasets, self._log)

        report = RunReport()
        fmt = self.config["format"].get(str)
        for item in lib.items(ui.decargs(args)):
            self._generate_item(item, index, report, fmt, hitlists)
        for line in report.lines():
            ui.print_(line)

    def _generate_item(
        self,
        item: Item,
        index: SongLookupIndex,
        report: RunReport,
        fmt: str,
        hitlists: dict[str, list[str]],
    ) -> None:
        """Generate and write one item's CHARTS tag; record the outcome.

        The existing tag is read from the *file*, not the database: the
        database may lag the file (post-import writes, rebuilt databases,
        unparseable tags degraded to None), and merging against the
        database's copy destroys file-only charts.

        The file write gates the database store: a track whose write fails
        is left untouched in both stores and reported, so the two can never
        disagree about generated data.
        """
        report.total += 1
        display = format(item, fmt)

        try:
            mediafile = MediaFile(syspath(item.path))
        except UnreadableFileError as err:
            self._log.warning("cannot read {0}: {1}", display, err)
            report.unreadable.append(display)
            return

        existing = ChartList()
        raw = mediafile.charts
        if raw:
            try:
                existing = ChartList.from_json_string(raw)
            except ChartsParseException:
                # Decision: an unparseable tag is treated as absent and, if
                # generation writes, overwritten wholesale. Always reported.
                report.unparseable_tags.append(display)

        result = index.lookup(item.artist, item.title)
        if result.unnormalizable:
            report.unnormalizable.append(display)
            return
        if result.ambiguous_charts:
            report.ambiguous.append((display, sorted(result.ambiguous_charts)))
        if result.is_miss:
            report.unmatched.append(display)
            return
        if not result.placements:
            return  # ambiguous in every matched chart; already reported

        generated = [
            build_chart(chart, hitlists[chart], placements)
            for chart, placements in sorted(result.placements.items())
        ]
        item.charts = merge_charts(existing, generated)
        if not item.try_write():
            report.unwritable.append(display)
            return
        self.update_item(item)
        report.generated += 1
