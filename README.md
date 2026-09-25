# Cookidoo MCP Server

An MCP (Model Context Protocol) server for the Thermomix Cookidoo platform, built with
`fastmcp` on top of [cookidoo-api](https://github.com/miaucl/cookidoo-api).

This is Carlos's fork of [alexandrepa/mcp-cookidoo](https://github.com/alexandrepa/mcp-cookidoo),
hardened for use as a stdio MCP server in [Hermes](https://hermes-agent.nousresearch.com/docs):
TLS verification is always on, credentials and locale come from the environment, the
default locale is Mexico (`mx` / `es-MX`), and it targets `cookidoo-api` 0.18.

> **Disclaimer:** Unofficial project. Not affiliated with, endorsed by, or connected to
> Cookidoo, Vorwerk, Thermomix, or any of their subsidiaries or trademarks.

## Features

- **Authentication** against your own Cookidoo account (OAuth2 flow handled by `cookidoo-api`)
- **Recipe details** lookup by recipe ID
- **Recipe structuring/validation** for a new custom recipe (Pydantic)
- **Upload** of a new **private** custom recipe to your account

That's the whole surface: 4 tools, no delete/edit/list, no shopping list, no calendar.
Uploaded recipes are always created with `workStatus: PRIVATE` and `tools: ["TM6"]`.

## Setup

1. **Clone and create a virtual environment:**
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **Provide configuration via environment variables** (see below). A `.env` file in the
   project directory works as an optional fallback; variables already set in the
   environment always win.

3. **Run the server (stdio transport):**
   ```bash
   .venv/bin/python server.py
   ```

## Environment variables

| Variable            | Required | Default   | Description                                       |
| ------------------- | -------- | --------- | ------------------------------------------------- |
| `COOKIDOO_EMAIL`    | yes      | –         | Cookidoo account email                            |
| `COOKIDOO_PASSWORD` | yes      | –         | Cookidoo account password (never logged/returned) |
| `COOKIDOO_COUNTRY`  | no       | `mx`      | Cookidoo country code (e.g. `mx`, `es`, `fr`)     |
| `COOKIDOO_LANGUAGE` | no       | `es-MX`   | Locale for that country (e.g. `es-MX`, `fr-FR`)   |
| `COOKIDOO_LOG_LEVEL`| no       | `INFO`    | Log level for stderr diagnostics                  |

If the country/language pair does not match a known Cookidoo localization, the server
returns a clear configuration error listing the valid languages for that country.

## Hermes configuration

```yaml
mcp_servers:
  cookidoo:
    command: /home/carlos/tools/mcp-cookidoo/.venv/bin/python
    args: [/home/carlos/tools/mcp-cookidoo/server.py]
    cwd: /home/carlos/tools/mcp-cookidoo
    env:
      COOKIDOO_EMAIL: you@example.com
      COOKIDOO_PASSWORD: your-password
      COOKIDOO_COUNTRY: mx
      COOKIDOO_LANGUAGE: es-MX
```

## Available Tools

- `connect_to_cookidoo` — authenticate and store the session (call this first).
  Echoes the email and resolved locale only, never the password.
- `get_recipe_details(recipe_id)` — fetch name, servings, active/total time (shown in
  minutes), difficulty, ingredients, utensils, steps and notes for a recipe ID
  (e.g. `r59322`).
- `generate_recipe_structure(name, ingredients, steps, servings, prep_time, total_time, hints)`
  — validate the recipe fields and return the JSON payload to upload.
- `upload_custom_recipe(recipe_json)` — create the recipe as a **private** custom recipe
  and return its ID and URL.

## Notes for contributors

- **Never `print()`**: stdout carries the MCP JSON-RPC stream. Use `logging` (stderr).
- **Never disable TLS verification.**
- Dev tooling lives in `requirements-dev.txt`; runtime deps are pinned in `requirements.txt`.

## Acknowledgments

Built on top of [cookidoo-api](https://github.com/miaucl/cookidoo-api), which provides the
Python interface to the Cookidoo platform, and on
[alexandrepa/mcp-cookidoo](https://github.com/alexandrepa/mcp-cookidoo) upstream.

## License

MIT — see [LICENSE](LICENSE).
