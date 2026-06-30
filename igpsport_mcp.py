"""
igpsport_mcp.py

MCP server exposing iGPSPORT custom-workout tools, built on the official Python
SDK (FastMCP). Thin by design: it validates agent input against the Pydantic
models in `igpsport_models.py`, maps to the transport dataclasses, and calls
`igpsport_client.py`.

Credentials are read from the OS keychain via `igpsport_credentials.py` — never
passed as tool arguments and never echoed back in responses.

Run directly (e.g. from a Claude Desktop config):

    uv run python igpsport_mcp.py
"""

from __future__ import annotations

from typing import Any, Optional

import requests
from mcp.server.fastmcp import FastMCP

from igpsport_client import (
    AuthError,
    IGPSportError,
    create_workout,
    list_custom_workouts,
    login,
)
from igpsport_credentials import CredentialsError, get_credentials
from igpsport_models import Block, to_workout

mcp = FastMCP("igpsport")


@mcp.tool()
def igpsport_list_workouts(page_size: int = 50) -> dict[str, Any]:
    """List the existing custom workouts in your iGPSPORT account.

    Returns {"ok": true, "count": int, "items": [{workout_id, title}, ...]} or
    {"ok": false, "error": "..."} on failure.
    """
    try:
        user, password = get_credentials()
        session = requests.Session()
        auth = login(session, user, password)
        data = list_custom_workouts(session, auth, page_size=page_size)
    except (CredentialsError, AuthError, IGPSportError) as exc:
        return {"ok": False, "error": str(exc)}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"network error: {exc}"}

    raw_items = (data.get("data") or {}).get("items") or []
    items = [
        {"workout_id": it.get("workoutId") or it.get("id"), "title": it.get("title")}
        for it in raw_items
    ]
    return {"ok": True, "count": len(items), "items": items}


@mcp.tool()
def igpsport_create_workout(
    title: str,
    description: str,
    blocks: list[Block],
    edit_workout_id: Optional[int] = None,
) -> dict[str, Any]:
    """Create a structured cycling workout in your iGPSPORT account.

    Pass `edit_workout_id` to overwrite an existing workout instead of creating
    a new one.

    `blocks` is an ordered list. Each block is one of:

    - A step:
        {"type": "step", "name": "Warm-up", "duration_seconds": 600,
         "intensity_class": "WarmUp"}
      `intensity_class` is one of WarmUp | Active | Rest | CoolDown.
      Optional target — a step may carry EITHER a power OR a heart-rate target,
      not both:
        - `power`: {"watts": [220, 240]} OR {"pct_ftp": [80, 90]}
        - `heart_rate`: {"bpm": [145, 160]} OR {"hr_zone": 3}  (zone 1-5)
      Set `open_duration: true` (and omit duration_seconds) for a step that runs
      until the lap button is pressed.

    - A repetition block:
        {"type": "repeat", "name": "Intervals", "reps": 4,
         "steps": [ ...steps... ]}

    Returns {"ok": true, "workout_id": int, "title": str, "total_time_seconds": int}
    or {"ok": false, "error": "..."} on failure.
    """
    try:
        user, password = get_credentials()
        workout = to_workout(title, description, blocks, edit_workout_id=edit_workout_id)
        return create_workout(user, password, workout)
    except (CredentialsError, AuthError, IGPSportError) as exc:
        return {"ok": False, "error": str(exc)}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"network error: {exc}"}


if __name__ == "__main__":
    mcp.run()
