"""Build the distribution and check what would actually be published.

The test suite runs against an editable install, which maps the source tree
and so cannot notice a packaging config that leaves a module out of the
wheel. This script checks the built artifact instead:

1. builds the sdist and a wheel from it, as the release workflow does;
2. checks the wheel holds exactly the ``beetsplug/`` Python files git
   tracks, and no ``beetsplug/__init__.py`` (a PEP 420 namespace);
3. installs the wheel into a fresh virtualenv and, from a directory outside
   the repository, imports the plugin, discovers the bundled ``top2000``
   ingestor, and checks the declared runtime dependencies.

Run from the repository root: ``python scripts/check_dist.py``. Exits
non-zero naming the first problem found.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

SMOKE = """
import logging
from importlib import metadata

from beetsplug.hitlisttag import HitlistTag
from beetsplug.hitlisttag.ingest import discover_ingestors

found = discover_ingestors(None, log=logging.getLogger("check_dist"))
assert "top2000" in found, f"bundled ingestors found: {sorted(found)}"
requires = " ".join(metadata.requires("beets-hitlisttag") or [])
for dep in ("beets", "mediafile", "requests"):
    assert dep in requires, f"{dep!r} missing from declared requirements: {requires}"
print("smoke import ok:", HitlistTag.__name__, sorted(found))
"""


def fail(message: str) -> None:
    print(f"check_dist: {message}", file=sys.stderr)
    sys.exit(1)


def run(*args: str | Path, cwd: Path = ROOT) -> None:
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def build() -> Path:
    for old in DIST.glob("*"):
        old.unlink()
    run(sys.executable, "-m", "build", "--outdir", DIST)
    wheels = sorted(DIST.glob("*.whl"))
    if len(wheels) != 1:
        fail(f"expected one wheel in {DIST}, found {[w.name for w in wheels]}")
    return wheels[0]


def check_contents(wheel: Path) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "beetsplug/*.py", "beetsplug/**/*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    expected = set(tracked)
    if not expected:
        fail("git lists no Python files under beetsplug/")
    with zipfile.ZipFile(wheel) as zf:
        shipped = {n for n in zf.namelist() if n.startswith("beetsplug/")}
    if "beetsplug/__init__.py" in shipped:
        fail("wheel ships beetsplug/__init__.py; beetsplug must stay a namespace")
    missing = sorted(expected - shipped)
    if missing:
        fail(f"wheel is missing tracked modules: {missing}")
    extra = sorted(shipped - expected)
    if extra:
        fail(f"wheel ships files git does not track under beetsplug/: {extra}")
    print(f"wheel contents ok: {len(shipped)} files under beetsplug/")


def smoke_install(wheel: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env_dir = Path(tmp) / "venv"
        venv.create(env_dir, with_pip=True)
        python = env_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        run(python, "-m", "pip", "install", "--quiet", wheel)
        # Run from an empty directory so nothing from the checkout is importable.
        workdir = Path(tmp) / "elsewhere"
        workdir.mkdir()
        run(python, "-c", SMOKE, cwd=workdir)


def main() -> None:
    wheel = build()
    check_contents(wheel)
    smoke_install(wheel)
    print(f"check_dist: {wheel.name} ok")


if __name__ == "__main__":
    main()
