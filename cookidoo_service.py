"""
Cookidoo Service

Module to encapsulate all cookidoo-api logic for interacting with the Cookidoo platform.

Notes
-----
* Nothing in this module may write to stdout: the MCP server speaks JSON-RPC
  over stdio, so any stray ``print`` would corrupt the protocol stream. All
  diagnostics go through ``logging`` (stderr).
* TLS verification is always on (default ``aiohttp`` behaviour).
"""

from __future__ import annotations

import asyncio
import logging
import os
from http import HTTPStatus
from typing import Optional

from aiohttp import ClientSession, ClientTimeout, CookieJar
from cookidoo_api import Cookidoo, CookidooConfig
from cookidoo_api.helpers import get_localization_options
from cookidoo_api.types import CookidooLocalizationConfig
from dotenv import load_dotenv

_LOGGER = logging.getLogger(__name__)

DEFAULT_COUNTRY = "mx"
DEFAULT_LANGUAGE = "es-MX"

# Fallback path templates, used only if the library's well-known endpoint
# discovery does not expose the custom-recipe rels.
_FALLBACK_CREATE_PATH = "created-recipes/{language}"
_FALLBACK_DETAIL_PATH = "created-recipes/{language}/{id}"

# Delay between recipe creation (POST) and the full update (PATCH); the
# backend needs a moment before the freshly created draft is patchable.
_CREATE_PATCH_DELAY_SECONDS = 2.0


def _env(name: str) -> Optional[str]:
    """Read a stripped environment variable, treating blanks as unset."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_cookidoo_credentials() -> tuple[str, str]:
    """
    Load Cookidoo credentials from the process environment.

    Hermes (or any MCP host) passes credentials via the server's ``env``
    block. A ``.env`` file next to this module is only an optional fallback
    and never overrides variables already present in the environment.

    Returns:
        tuple[str, str]: Email and password

    Raises:
        ValueError: If credentials are not found
    """
    # Optional fallback only: never override what the MCP host provided.
    load_dotenv(override=False)

    email = _env("COOKIDOO_EMAIL")
    password = _env("COOKIDOO_PASSWORD")

    missing = [
        name
        for name, value in (("COOKIDOO_EMAIL", email), ("COOKIDOO_PASSWORD", password))
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing Cookidoo credentials: "
            + ", ".join(missing)
            + ". Set them in the MCP server's env block (or in a .env file in "
            "the server directory)."
        )

    return str(email), str(password)


async def resolve_localization() -> CookidooLocalizationConfig:
    """
    Resolve the Cookidoo localization from the environment.

    Uses ``COOKIDOO_COUNTRY`` (default ``mx``) and ``COOKIDOO_LANGUAGE``
    (default ``es-MX``).

    Raises:
        ValueError: If no localization option matches the requested pair
    """
    country = (_env("COOKIDOO_COUNTRY") or DEFAULT_COUNTRY).lower()
    language = _env("COOKIDOO_LANGUAGE") or DEFAULT_LANGUAGE

    options = await get_localization_options(country=country, language=language)
    if not options:
        available = await get_localization_options(country=country)
        if available:
            raise ValueError(
                f"No Cookidoo localization for COOKIDOO_COUNTRY={country!r} with "
                f"COOKIDOO_LANGUAGE={language!r}. Available languages for "
                f"{country!r}: {', '.join(o.language for o in available)}"
            )
        raise ValueError(
            f"No Cookidoo localization for COOKIDOO_COUNTRY={country!r}. "
            "Check the country code (e.g. 'mx', 'es', 'fr', 'de')."
        )

    localization = options[0]
    _LOGGER.info(
        "Using Cookidoo localization: country=%s language=%s url=%s",
        localization.country_code,
        localization.language,
        localization.url,
    )
    return localization


class CookidooService:
    """Service class for managing Cookidoo API interactions."""

    def __init__(self, email: str, password: str):
        """
        Initialize the Cookidoo service with credentials.

        Args:
            email: Cookidoo account email
            password: Cookidoo account password
        """
        self.email = email
        self.password = password
        self._api_client: Optional[Cookidoo] = None
        self._session: Optional[ClientSession] = None

    async def login(self) -> Cookidoo:
        """
        Authenticate with Cookidoo and return the API client.

        Any session from a previous login is closed first, so calling this
        twice does not leak aiohttp sessions.

        Returns:
            Cookidoo: Authenticated Cookidoo API client

        Raises:
            ValueError: If the configured localization is invalid
            Exception: If authentication fails
        """
        # Drop any previous session/client before creating a new one.
        await self.close()

        localization = await resolve_localization()

        # Default connector => TLS certificate verification enabled.
        # cookidoo-api requires an unsafe cookie jar for the cross-domain
        # OAuth2 login redirects.
        session = ClientSession(
            timeout=ClientTimeout(total=60),
            cookie_jar=CookieJar(unsafe=True),
        )
        self._session = session

        try:
            config = CookidooConfig(
                email=self.email,
                password=self.password,
                localization=localization,
            )
            self._api_client = Cookidoo(session=session, cfg=config)
            await self._api_client.login()
            return self._api_client
        except Exception as e:
            self._api_client = None
            await self.close()
            raise Exception(f"Failed to authenticate with Cookidoo: {e}") from e

    async def close(self) -> None:
        """Close the aiohttp session, if any."""
        session, self._session = self._session, None
        if session is not None and not session.closed:
            await session.close()

    def _custom_recipe_path(self, name: str, fallback: str, **fmt: object) -> str:
        """Resolve a custom-recipe path template via the library, with fallback."""
        client = self._api_client
        if client is None:
            raise Exception("Not authenticated. Please call login() first.")
        try:
            template = client._path(name)
        except KeyError:
            _LOGGER.warning(
                "Endpoint rel %r not resolved by cookidoo-api discovery, "
                "falling back to %r",
                name,
                fallback,
            )
            template = fallback
        return template.format(**client.localization.__dict__, **fmt)

    def custom_recipe_url(self, recipe_id: str) -> str:
        """Return the web URL of a created (custom) recipe."""
        client = self._api_client
        if client is None:
            raise Exception("Not authenticated. Please call login() first.")
        return str(
            client.api_endpoint
            / self._custom_recipe_path(
                "customer-recipes:recipe-details",
                _FALLBACK_DETAIL_PATH,
                id=recipe_id,
            )
        )

    async def create_custom_recipe(
        self,
        name: str,
        ingredients: list[str],
        steps: list[str],
        servings: int = 4,
        prep_time: int = 30,
        total_time: int = 60,
        hints: Optional[list[str]] = None,
    ) -> str:
        """
        Create a new PRIVATE custom recipe from scratch.

        Uses the cookidoo-api client's own authenticated request path
        (``_request_json``), which injects the bearer token and transparently
        refreshes it, and the library's well-known endpoint resolution for the
        created-recipes paths.

        Args:
            name: Recipe name
            ingredients: List of ingredient descriptions
            steps: List of cooking step descriptions
            servings: Number of servings (default: 4)
            prep_time: Preparation time in minutes (default: 30)
            total_time: Total cooking time in minutes (default: 60)
            hints: Optional list of hints/tips for the recipe

        Returns:
            str: The created recipe ID

        Raises:
            Exception: If recipe creation fails
        """
        client = self._api_client
        if client is None or self._session is None:
            raise Exception("Not authenticated. Please call login() first.")

        try:
            # Resolve live endpoint paths (no-op after the first call).
            await client._ensure_endpoints()

            create_url = client.api_endpoint / self._custom_recipe_path(
                "customer-recipes:recipe-create", _FALLBACK_CREATE_PATH
            )

            # Step 1: create the recipe draft with just the name.
            created = await client._request_json(
                "post",
                create_url,
                "creating custom recipe",
                json={"recipeName": name},
                accepted_statuses=(HTTPStatus.OK, HTTPStatus.CREATED),
            )
            if not isinstance(created, dict):
                raise Exception("Unexpected response while creating the recipe")

            recipe_id = created.get("recipeId") or created.get("id")
            if not recipe_id:
                raise Exception("No recipe ID returned from creation")
            recipe_id = str(recipe_id)

            # Step 2: PATCH the full recipe body. The API requires the whole
            # structure, not a partial diff.
            update_data = {
                "name": name,
                # null, or: ^((prod|nonprod)/img/customer-recipe/)?[A-Za-z0-9-_]+\.(bmp|jpe|jpeg|jpg|png)$
                "image": None,
                "isImageOwnedByUser": False,
                "tools": ["TM6"],
                "yield": {"value": servings, "unitText": "portion"},
                "prepTime": prep_time * 60,  # seconds
                "cookTime": 0,
                "totalTime": total_time * 60,  # seconds
                "ingredients": [
                    {"type": "INGREDIENT", "text": ing} for ing in ingredients
                ],
                "instructions": [{"type": "STEP", "text": step} for step in steps],
                "hints": "\n".join(hints) if hints else "",
                # Custom recipes stay private; this server never publishes.
                "workStatus": "PRIVATE",
                "recipeMetadata": {"requiresAnnotationsCheck": False},
            }

        except Exception as e:
            raise Exception(f"Failed to create custom recipe: {e}") from e

        # From here on the draft exists: any failure must roll it back.
        try:
            await asyncio.sleep(_CREATE_PATCH_DELAY_SECONDS)

            update_url = client.api_endpoint / self._custom_recipe_path(
                "customer-recipes:recipe-details",
                _FALLBACK_DETAIL_PATH,
                id=recipe_id,
            )
            await client._request_json(
                "patch",
                update_url,
                "updating custom recipe",
                json=update_data,
                accepted_statuses=(HTTPStatus.OK, HTTPStatus.NO_CONTENT),
                parse_response=False,
            )
        except Exception as patch_error:
            # The draft already exists: roll it back so no orphan is left.
            try:
                await client.remove_custom_recipe(recipe_id)
            except Exception as rollback_error:
                _LOGGER.error(
                    "Rollback of orphan custom recipe draft %s failed: %s",
                    recipe_id,
                    rollback_error,
                )
                raise Exception(
                    f"Failed to create custom recipe: {patch_error}. "
                    f"The draft recipe {recipe_id} was created but could not be "
                    f"removed automatically ({rollback_error}); delete it "
                    "manually in Cookidoo."
                ) from patch_error
            _LOGGER.warning(
                "PATCH failed; rolled back custom recipe draft %s", recipe_id
            )
            raise Exception(
                f"Failed to create custom recipe: {patch_error}. "
                f"The partially created draft {recipe_id} was rolled back "
                "(removed); nothing was left in your account."
            ) from patch_error

        _LOGGER.info("Created private custom recipe %s", recipe_id)
        return recipe_id

    @property
    def api_client(self) -> Optional[Cookidoo]:
        """Get the current API client instance."""
        return self._api_client
