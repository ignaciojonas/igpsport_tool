# iGPSPORT MCP Server — Design

**Date:** 2026-06-30
**Status:** Approved

## Goal

Wrap the validated `igpsport_client.py` as a real, installable MCP server so an
agent (Claude Desktop / Claude Code) can create and list iGPSPORT custom cycling
workouts conversationally — "create me a 4x4 interval workout" → uploaded
directly to the user's iGPSPORT account.

This is a self-contained, standalone tool.

## Non-goals (YAGNI)

- Sports other than cycling (`workoutType: "bike"` only).
- OAuth (the API is username/password only).
- A full `src/` package restructure (flat modules are fine for a personal tool).

## Approach

Approach A: keep `igpsport_client.py` as the transport layer and add a small set
of focused modules around it, `uv`-managed via `pyproject.toml`. Each file has
one responsibility and the mapping layer is unit-testable offline.

## File layout (all English)

| File | Responsibility |
|------|----------------|
| `igpsport_client.py` | Transport: login, list, upload. Translated to English; logic unchanged (already validated). |
| `igpsport_credentials.py` | Resolve credentials from macOS Keychain via `keyring` (env-var fallback for dev). Provides `get_credentials()` plus `set`/`delete`/`status` for one-time setup. Single source of truth for CLI and MCP. |
| `igpsport_models.py` | Pydantic input schema for the agent-facing API (`StepIn`, `RepeatBlockIn` discriminated on `type`, `PowerIn`) + `to_workout()` converter to the client dataclasses. |
| `igpsport_mcp.py` | FastMCP server. Thin: validate via Pydantic, map, call client. |
| `igpsport_cli.py` | Translated to English; reads creds via `igpsport_credentials` (keyring) instead of raw env vars. |
| `pyproject.toml` | `uv` project. Deps: `mcp[cli]`, `requests`, `keyring`, `pydantic`. |
| `tests/test_workout_mapping.py` | Offline tests for the mapping layer. No network. |
| `README.md` | English. Keyring setup, `uv` usage, Claude Desktop config, schema docs. |

## MCP tools

- `igpsport_list_workouts(page_size: int = 50) -> dict`
  Lists existing custom workouts in the account.

- `igpsport_create_workout(title, description, blocks, edit_workout_id=None) -> dict`
  Creates a custom workout (or edits an existing one when `edit_workout_id` is
  given — edit folded into create rather than a separate tool). Returns
  `{"ok": true, "workout_id": int, "title": str, "total_time_seconds": int}`.

- `igpsport_delete_workout(workout_id) -> dict`
  Permanently deletes a workout. Endpoint captured from the iGPSPORT iPhone app
  (mitmproxy): `POST /service/mobile/api/WorkOut/CustomWorkOutDel?id=<id>` with
  an empty JSON body → `{"code": 0, "data": true}`. Returns
  `{"ok": true, "workout_id": int}`.

## Agent-facing block schema (Pydantic)

Discriminated union on a `type` field:

```jsonc
// Simple step
{
  "type": "step",
  "name": "Warm-up",
  "duration_seconds": 600,            // omit only when open_duration is true
  "intensity_class": "WarmUp",        // WarmUp | Active | Rest | CoolDown
  // Optional target — a step takes EITHER power OR heart_rate, not both
  // (iGPSPORT's intensityTarget is singular):
  "power": {"watts": [220, 240]},     // OR {"pct_ftp": [80, 90]}
  "heart_rate": {"bpm": [145, 160]},  // OR {"hr_zone": 3} (zone 1-5)
  "open_duration": false              // optional, default false ("until lap pressed")
}

// Repetition block
{
  "type": "repeat",
  "name": "Intervals",
  "reps": 4,
  "steps": [ /* nested steps */ ]
}
```

`PowerIn` validates that exactly one of `watts` / `pct_ftp` is set, each a
`[min, max]` pair. `HeartRateIn` validates exactly one of `bpm` (`[min, max]`)
or `hr_zone` (1-5), mapping to `HeartRateCustom` / `HeartRate` units
respectively. A step may carry a power **or** a heart-rate target, not both
(iGPSPORT's `intensityTarget` is singular). `to_workout()` maps these to the
existing `WorkoutStep` / `RepeatBlock` / `PowerTarget` / `HeartRateTarget`
dataclasses.

## Credentials flow

One-time setup: `uv run python igpsport_credentials.py set` prompts via
`getpass` and writes user + password to the macOS Keychain under service name
`igpsport_tool`. The MCP server and CLI read from Keychain at call time. No
secrets in `claude_desktop_config.json`. Env vars (`IGPSPORT_USER` /
`IGPSPORT_PASSWORD`) remain a dev fallback.

## Claude Desktop registration

```json
{ "mcpServers": { "igpsport-workouts": {
  "command": "uv",
  "args": ["run", "--directory",
           "/Users/ijonas/Projects/personal/igpsport_tool",
           "python", "igpsport_mcp.py"]
}}}
```

## Error handling

- MCP tools catch `AuthError` / `IGPSportError` and return
  `{"ok": false, "error": "..."}` — never leak the password.
- Pydantic validation errors surface to the agent before any network call.

## Testing

- **Offline (I run):** example 4x4 → `totalTime == 2880`, body shape matches the
  iGPSPORT spec, Pydantic parse/round-trip, watts vs %FTP, open-duration steps.
- **Live (user runs):** `uv run python igpsport_cli.py upload-example` — needs
  the user's Keychain credentials, so the user performs the real upload test.
