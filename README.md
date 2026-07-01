# igpsport-workouts-mcp

Standalone tool **and MCP server** to create/edit custom cycling workouts on
iGPSPORT, **without going through intervals.icu**. It uses the same unofficial
endpoint discovered by the [intervalssync](https://github.com/jorge-huxley/intervalssync)
project (MIT license), reimplemented here as an independent module with no
intervals.icu dependency.

Ask an agent *"create me a 4x4 interval workout at 220-240W"* and it lands
directly in your iGPSPORT account.

## Files

| File | Purpose |
|------|---------|
| `igpsport_client.py` | Transport layer: login, list, upload. Pure, stateless functions. |
| `igpsport_credentials.py` | Credential storage/lookup via the OS keychain (`keyring`). |
| `igpsport_models.py` | Strict Pydantic schema for the agent-facing API + converter. |
| `igpsport_mcp.py` | The MCP server (FastMCP). Exposes `igpsport_list_workouts` and `igpsport_create_workout`. |
| `igpsport_cli.py` | Thin CLI to exercise the client from a terminal. |
| `tests/` | Offline tests for the workout mapping. |
| `examples/claude_desktop_config.json` | Ready-to-copy MCP registration. |

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python ≥ 3.10.

```bash
uv sync   # creates .venv and installs dependencies
```

### Store your credentials (once)

Credentials live in the OS keychain (macOS Keychain / Windows Credential
Manager / Linux Secret Service) — never in a plaintext config file.

```bash
uv run python igpsport_credentials.py set      # prompts for user + password
uv run python igpsport_credentials.py status   # check what's stored
uv run python igpsport_credentials.py delete   # remove them
```

For quick local dev you can instead export `IGPSPORT_USER` and
`IGPSPORT_PASSWORD`; the keychain takes precedence when both are present.

## CLI usage

```bash
# List existing custom workouts in your account
uv run python igpsport_cli.py list

# Upload the example workout
# (10' warm-up, 4x[4' @ 220-240W / 3' @ 120-140W], 10' cool-down)
uv run python igpsport_cli.py upload-example

# Delete a workout by id (get the id from `list`)
uv run python igpsport_cli.py delete 260231

# JSON output (for scripting / debugging) — the global --json flag goes first
uv run python igpsport_cli.py --json upload-example
```

## MCP server

### Register with Claude Desktop

Merge `examples/claude_desktop_config.json` into your
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "igpsport-workouts": {
      "command": "uv",
      "args": ["run", "--directory",
               "/Users/ijonas/Projects/personal/igpsport_tool",
               "python", "igpsport_mcp.py"]
    }
  }
}
```

No credentials go in this file — the server reads them from the keychain.
Restart Claude Desktop after editing.

### Register with Claude Code

```bash
claude mcp add igpsport-workouts -- uv run --directory /Users/ijonas/Projects/personal/igpsport_tool python igpsport_mcp.py
```

### Tools

- **`igpsport_list_workouts(page_size=50)`** — lists existing custom workouts.
- **`igpsport_create_workout(title, description, blocks, edit_workout_id=None)`**
  — creates (or, with `edit_workout_id`, overwrites) a structured workout.
- **`igpsport_delete_workout(workout_id)`** — permanently deletes a workout by id.

Both return `{"ok": true, ...}` or `{"ok": false, "error": "..."}` — failures
never echo your password.

### Block schema

`blocks` is an ordered list. Each block is either a **step** or a **repeat**:

```jsonc
// Step
{
  "type": "step",
  "name": "Warm-up",
  "duration_seconds": 600,            // omit only when open_duration is true
  "intensity_class": "WarmUp",        // WarmUp | Active | Rest | CoolDown
  // Optional target — a step takes EITHER power OR heart_rate, not both:
  "power": {"watts": [220, 240]},     // OR {"pct_ftp": [80, 90]}
  "heart_rate": {"bpm": [145, 160]},  // OR {"hr_zone": 3}  (zone 1-5)
  "open_duration": false              // optional; true = "until lap pressed"
}

// Repeat block
{
  "type": "repeat",
  "name": "Intervals",
  "reps": 4,
  "steps": [ /* nested steps */ ]
}
```

Example agent payload for the classic 4x4:

```json
[
  {"type": "step", "name": "Warm-up", "duration_seconds": 600, "intensity_class": "WarmUp"},
  {"type": "repeat", "name": "Intervals", "reps": 4, "steps": [
    {"type": "step", "name": "Interval", "duration_seconds": 240, "intensity_class": "Active", "power": {"watts": [220, 240]}},
    {"type": "step", "name": "Recovery", "duration_seconds": 180, "intensity_class": "Rest", "power": {"watts": [120, 140]}}
  ]},
  {"type": "step", "name": "Cool-down", "duration_seconds": 600, "intensity_class": "CoolDown"}
]
```

## Programmatic use

```python
from igpsport_client import Workout, WorkoutStep, RepeatBlock, PowerTarget, create_workout

workout = Workout(
    title="My workout",
    blocks=[
        WorkoutStep(name="Warm-up", duration_seconds=600, intensity_class="WarmUp"),
        RepeatBlock(name="Intervals", reps=4, steps=[
            WorkoutStep(name="Interval", duration_seconds=240, intensity_class="Active",
                        power=PowerTarget(min_watts=220, max_watts=240)),
            WorkoutStep(name="Recovery", duration_seconds=180, intensity_class="Rest",
                        power=PowerTarget(min_watts=120, max_watts=140)),
        ]),
        WorkoutStep(name="Cool-down", duration_seconds=600, intensity_class="CoolDown"),
    ],
)
result = create_workout("you@example.com", "password", workout)
print(result)  # {"ok": True, "workout_id": 12345, ...}
```

Pass `existing_workout_id=...` to `Workout(...)` to edit instead of create.

## Tests

```bash
uv run pytest
```

These are offline (no network): they verify the example 4x4 maps to the exact
iGPSPORT body with `totalTime == 2880`, plus schema validation.

## Known limitations

- **Unofficial** iGPSPORT API (reverse-engineered, like `intervalssync`). It can
  break if iGPSPORT changes its backend.
- Cycling only (`workoutType: "bike"`).
- No OAuth — direct username/password against the login endpoint (over HTTPS).
