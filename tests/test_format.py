"""Offline tests for ingredient formatting in get_recipe_details."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import _format_ingredient  # noqa: E402


def ing(name, description):
    return SimpleNamespace(name=name, description=description)


def test_both_present():
    assert _format_ingredient(ing("Weizenkörner", "100 g")) == "100 g Weizenkörner"


def test_whitespace_stripped():
    assert _format_ingredient(ing("  Weizenkörner ", " 100 g  ")) == "100 g Weizenkörner"


def test_name_only():
    assert _format_ingredient(ing("Salz", "")) == "Salz"
    assert _format_ingredient(ing("Salz", None)) == "Salz"


def test_description_only():
    assert _format_ingredient(ing("", "1 Würfel")) == "1 Würfel"
    assert _format_ingredient(ing(None, "1 Würfel")) == "1 Würfel"


def test_description_already_contains_name():
    assert _format_ingredient(ing("hefe", "1 Würfel Hefe, frisch")) == "1 Würfel Hefe, frisch"
