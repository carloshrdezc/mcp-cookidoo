"""Offline regression tests for the PRO-650 review fixes."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from yarl import URL

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cookidoo_service  # noqa: E402
from cookidoo_service import CookidooService  # noqa: E402
from server import _clean_step, _format_ingredient, _split_items  # noqa: E402


def ing(name, description):
    return SimpleNamespace(name=name, description=description)


# --- _format_ingredient: token-aware dedup ---------------------------------


def test_short_name_inside_other_word_is_kept():
    assert _format_ingredient(ing("Ei", "1 kleines")) == "1 kleines Ei"


def test_standalone_name_is_deduplicated():
    assert _format_ingredient(ing("Salz", "1 Prise Salz")) == "1 Prise Salz"


# --- _clean_step -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1. Coloque", "Coloque"),
        ("2) Agregue", "Agregue"),
        ("- Mezcle", "Mezcle"),
        ("• Sirva", "Sirva"),
        ("30 seg / vel 7 pique", "30 seg / vel 7 pique"),
        ("3 min / 120 °C / vel 1", "3 min / 120 °C / vel 1"),
    ],
)
def test_clean_step(raw, expected):
    assert _clean_step(raw) == expected


# --- _split_items ------------------------------------------------------------


def test_split_items_newline_input_is_not_comma_split():
    assert _split_items("1 diente de ajo, pelado\n2 tomates, en cubos\n") == [
        "1 diente de ajo, pelado",
        "2 tomates, en cubos",
    ]


def test_split_items_single_line_is_comma_split():
    assert _split_items("sal, pimienta , aceite") == ["sal", "pimienta", "aceite"]


# --- create_custom_recipe rollback -------------------------------------------


def _make_service(patch_error=None, remove_error=None):
    """Build a CookidooService wired to a stubbed, 'authenticated' client."""

    async def request_json(method, url, *args, **kwargs):
        if method == "post":
            return {"recipeId": "abc123"}
        if method == "patch":
            if patch_error is not None:
                raise patch_error
            return None
        raise AssertionError(f"unexpected method {method}")

    client = SimpleNamespace(
        api_endpoint=URL("https://example.invalid/"),
        localization=SimpleNamespace(language="es-MX", country_code="mx"),
        _ensure_endpoints=AsyncMock(),
        _path=lambda name: {
            "customer-recipes:recipe-create": "created-recipes/{language}",
            "customer-recipes:recipe-details": "created-recipes/{language}/{id}",
        }[name],
        _request_json=AsyncMock(side_effect=request_json),
        remove_custom_recipe=AsyncMock(side_effect=remove_error),
    )
    service = CookidooService("user@example.invalid", "x")
    service._api_client = client
    service._session = object()  # only checked for non-None
    return service, client


def _create(service):
    return service.create_custom_recipe(
        name="Salsa", ingredients=["1 tomate"], steps=["Pique el tomate"]
    )


def test_patch_failure_rolls_back_draft():
    service, client = _make_service(patch_error=RuntimeError("PATCH 500"))
    with patch.object(cookidoo_service.asyncio, "sleep", AsyncMock()):
        with pytest.raises(Exception) as excinfo:
            asyncio.run(_create(service))
    msg = str(excinfo.value)
    assert "rolled back" in msg
    assert "PATCH 500" in msg
    client.remove_custom_recipe.assert_awaited_once_with("abc123")


def test_patch_and_rollback_failure_reports_recipe_id():
    service, client = _make_service(
        patch_error=RuntimeError("PATCH 500"),
        remove_error=RuntimeError("DELETE 503"),
    )
    with patch.object(cookidoo_service.asyncio, "sleep", AsyncMock()):
        with pytest.raises(Exception) as excinfo:
            asyncio.run(_create(service))
    msg = str(excinfo.value)
    assert "abc123" in msg
    assert "manually" in msg
    assert "PATCH 500" in msg
    client.remove_custom_recipe.assert_awaited_once_with("abc123")


def test_happy_path_returns_id_without_rollback():
    service, client = _make_service()
    with patch.object(cookidoo_service.asyncio, "sleep", AsyncMock()):
        assert asyncio.run(_create(service)) == "abc123"
    client.remove_custom_recipe.assert_not_awaited()
    methods = [c.args[0] for c in client._request_json.await_args_list]
    assert methods == ["post", "patch"]
