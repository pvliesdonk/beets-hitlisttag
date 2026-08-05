"""Pin the package layout the beetsplug.hitlisttag package rests on."""

import importlib


def test_hitlisttag_is_a_package_exposing_the_plugin():
    pkg = importlib.import_module("beetsplug.hitlisttag")
    # A package has __path__; a plain module does not.
    assert hasattr(pkg, "__path__"), "beetsplug.hitlisttag must be a package"
    assert hasattr(pkg, "HitlistTag"), "the plugin class must be importable"


def test_charts_is_a_submodule_of_the_package():
    charts = importlib.import_module("beetsplug.hitlisttag.charts")
    assert charts.__name__ == "beetsplug.hitlisttag.charts"


def test_beetsplug_stays_a_pep420_namespace():
    beetsplug = importlib.import_module("beetsplug")
    # PEP 420 namespace packages have no __init__.py, so __file__ is None
    # (or absent). A regular package would set it to an __init__.py path.
    assert getattr(beetsplug, "__file__", None) is None, (
        "beetsplug must stay a PEP 420 namespace package (no __init__.py)"
    )
