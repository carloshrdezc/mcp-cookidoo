"""
Cookidoo MCP Server

Main server file containing MCP tool definitions for interacting with Cookidoo.

Run as a stdio MCP server:

    python server.py

Nothing may be written to stdout: stdout carries the MCP JSON-RPC stream.
Diagnostics go to stderr via ``logging``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

from fastmcp import FastMCP

from cookidoo_service import CookidooService, load_cookidoo_credentials
from schemas import CustomRecipe

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("COOKIDOO_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("cookidoo-mcp-server")

# Initialize FastMCP server
mcp = FastMCP("cookidoo-mcp-server")

# Module-level state to store the authenticated session
_cookidoo_service: CookidooService | None = None
_cookidoo_api = None

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Flatten the HTML markup the API returns for instruction text."""
    return " ".join(_TAG_RE.sub(" ", text or "").split())


def _minutes(seconds: int | None) -> str:
    """Render a duration in seconds as whole minutes."""
    if not seconds:
        return "0 min"
    return f"{round(seconds / 60)} min"


@mcp.tool()
async def connect_to_cookidoo() -> str:
    """
    Authenticate with Cookidoo and store the session.

    This tool must be called before using other Cookidoo tools. It will:
    1. Read COOKIDOO_EMAIL / COOKIDOO_PASSWORD from the environment
    2. Resolve the localization from COOKIDOO_COUNTRY / COOKIDOO_LANGUAGE
       (defaults: mx / es-MX)
    3. Authenticate with the Cookidoo platform and store the session

    Returns:
        str: Success message confirming connection (email only, never the password)
    """
    global _cookidoo_service, _cookidoo_api

    try:
        email, password = load_cookidoo_credentials()
    except ValueError as e:
        return (
            f"Configuration Error: {e}\n\n"
            "Required environment variables: COOKIDOO_EMAIL, COOKIDOO_PASSWORD. "
            "Optional: COOKIDOO_COUNTRY (default mx), COOKIDOO_LANGUAGE (default es-MX)."
        )

    try:
        # Replace any previous service, closing its session first.
        if _cookidoo_service is not None:
            await _cookidoo_service.close()

        _cookidoo_service = CookidooService(email, password)
        _cookidoo_api = await _cookidoo_service.login()

        localization = _cookidoo_api.localization
        return (
            f"Successfully connected to Cookidoo as {email} "
            f"(country={localization.country_code}, language={localization.language})"
        )

    except ValueError as e:
        # Bad localization configuration.
        _cookidoo_api = None
        return f"Configuration Error: {e}"

    except Exception as e:
        _cookidoo_api = None
        _LOGGER.warning("Cookidoo login failed: %s", e)
        return f"Connection Failed: {e}\n\nPlease check your credentials and try again."


@mcp.tool()
async def get_recipe_details(recipe_id: str) -> str:
    """
    Get detailed information about a specific recipe by its ID.

    Use this tool to get full details about a recipe for inspiration before creating
    your own custom recipe. You must be connected first using connect_to_cookidoo.

    Args:
        recipe_id: The Cookidoo recipe ID (e.g., "r59322", "r907015")

    Returns:
        str: Detailed recipe information including ingredients, steps, cooking time, etc.
    """
    global _cookidoo_api

    if not _cookidoo_api:
        return "Not connected. Please run 'connect_to_cookidoo' first."

    try:
        recipe = await _cookidoo_api.get_recipe_details(recipe_id)
    except Exception as e:
        return f"Failed to get recipe details: {e}"

    lines = [
        "Recipe Details:",
        "",
        f"Name: {recipe.name}",
        f"ID: {recipe.id}",
        f"Servings: {recipe.serving_size}",
        f"Active Time: {_minutes(recipe.active_time)}",
        f"Total Time: {_minutes(recipe.total_time)}",
        f"Difficulty: {recipe.difficulty}",
        "",
    ]

    if recipe.ingredients:
        lines.append("Ingredients:")
        for ingredient in recipe.ingredients:
            text = ingredient.description or ingredient.name
            lines.append(f"  - {text}")
        lines.append("")

    if recipe.utensils:
        lines.append("Utensils:")
        lines.extend(f"  - {utensil}" for utensil in recipe.utensils)
        lines.append("")

    if recipe.step_groups:
        lines.append("Steps:")
        counter = 1
        for group in recipe.step_groups:
            if group.title:
                lines.append(f"  [{group.title}]")
            for step in group.recipe_steps:
                lines.append(f"  {counter}. {_strip_html(step.formatted_text)}")
                counter += 1
        lines.append("")

    if recipe.notes:
        lines.append("Notes:")
        lines.extend(f"  - {_strip_html(note)}" for note in recipe.notes)
        lines.append("")

    if recipe.url:
        lines.append(f"URL: {recipe.url}")

    return "\n".join(lines)


@mcp.tool()
async def generate_recipe_structure(
    name: str,
    ingredients: str,
    steps: str,
    servings: int = 4,
    prep_time: int = 30,
    total_time: int = 60,
    hints: str = "",
) -> str:
    """
    Generate and validate a recipe structure ready for upload to Cookidoo.

    This tool helps you structure your recipe data properly before uploading.
    It validates all fields and returns a JSON structure that can be used with
    the upload_custom_recipe tool.

    Args:
        name: Recipe name (required)
        ingredients: Ingredients list, one per line or comma-separated
        steps: Cooking steps, one per line or numbered
        servings: Number of servings (default: 4, range: 1-20)
        prep_time: Preparation time in minutes (default: 30)
        total_time: Total cooking time in minutes (default: 60)
        hints: Optional cooking tips, one per line or comma-separated

    Returns:
        str: Validated recipe structure in JSON format, ready for upload
    """
    try:
        # Parse ingredients (split by newlines or commas)
        ingredients_list = [
            ing.strip()
            for ing in (
                ingredients.split("\n") if "\n" in ingredients else ingredients.split(",")
            )
            if ing.strip()
        ]

        # Parse steps (split by newlines, dropping any numbering)
        steps_list = [
            step.strip().lstrip("0123456789.)-• ")
            for step in steps.split("\n")
            if step.strip()
        ]

        # Parse hints if provided
        hints_list = None
        if hints:
            hints_list = [
                hint.strip()
                for hint in (hints.split("\n") if "\n" in hints else hints.split(","))
                if hint.strip()
            ]

        recipe = CustomRecipe(
            name=name,
            ingredients=ingredients_list,
            steps=steps_list,
            servings=servings,
            prep_time=prep_time,
            total_time=total_time,
            hints=hints_list,
        )

        recipe_json = recipe.model_dump_json(indent=2)

        return (
            "Recipe structure validated successfully!\n\n"
            f"{recipe_json}\n\n"
            "You can now use this with 'upload_custom_recipe'."
        )

    except Exception as e:
        return f"Validation failed: {e}\n\nPlease check your recipe data and try again."


@mcp.tool()
async def upload_custom_recipe(recipe_json: str) -> str:
    """
    Upload a custom recipe to your Cookidoo account as a PRIVATE recipe.

    This tool creates a brand new recipe from scratch on your Cookidoo account.
    Use 'generate_recipe_structure' first to validate your recipe data, then
    pass the resulting JSON to this tool. Uploaded recipes are always private.

    Args:
        recipe_json: The validated recipe JSON from generate_recipe_structure

    Returns:
        str: Success message with the created recipe ID
    """
    global _cookidoo_service, _cookidoo_api

    if not _cookidoo_service or not _cookidoo_api:
        return "Not connected. Please run 'connect_to_cookidoo' first."

    try:
        recipe_data = json.loads(recipe_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    try:
        recipe = CustomRecipe(**recipe_data)
    except Exception as e:
        return f"Invalid recipe data: {e}"

    try:
        recipe_id = await _cookidoo_service.create_custom_recipe(
            name=recipe.name,
            ingredients=recipe.ingredients,
            steps=recipe.steps,
            servings=recipe.servings,
            prep_time=recipe.prep_time,
            total_time=recipe.total_time,
            hints=recipe.hints,
        )
        recipe_url = _cookidoo_service.custom_recipe_url(recipe_id)
    except Exception as e:
        return f"Upload failed: {e}"

    return (
        f"Recipe '{recipe.name}' created successfully (private)!\n\n"
        f"Recipe ID: {recipe_id}\n"
        f"URL: {recipe_url}\n\n"
        "Your recipe is now saved in your Cookidoo account."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
