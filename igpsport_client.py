"""
igpsport_client.py

Standalone client to create, edit and delete iGPSPORT custom workouts.

Reuses the authentication flow and the unofficial endpoint discovered in the
open-source project intervalssync (jorge-huxley/intervalssync, MIT license):
login via the `loginToken` cookie -> Bearer token, then POST to
`EditCustomWorkOut` with a structure of steps.

Designed for:
  1) Direct CLI use (see igpsport_cli.py)
  2) Being wrapped as MCP server tools (every public function here is pure,
     with no global state, and returns JSON-serializable dicts/dataclasses).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote

import requests

# --- Endpoints (unofficial, reverse-engineered from the intervalssync repo) ---

LOGIN_URL = "https://i.igpsport.com/Auth/Login"
IGPS_API = "https://prod.en.igpsport.com"
IGPS_WORKOUT_LIST_URL = f"{IGPS_API}/service/mobile/api/WorkOut/CustomWorkout"
IGPS_WORKOUT_EDIT_URL = f"{IGPS_API}/service/mobile/api/WorkOut/EditCustomWorkOut"
# Captured from the iGPSPORT iPhone app: POST with the id as a query param and an
# empty JSON body -> {"code": 0, "data": true}.
IGPS_WORKOUT_DELETE_URL = f"{IGPS_API}/service/mobile/api/WorkOut/CustomWorkOutDel"


class IGPSportError(Exception):
    """Generic iGPSPORT API error."""


class AuthError(IGPSportError):
    """Login failed or no usable token was obtained."""


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def login(session: requests.Session, user: str, password: str) -> dict[str, str]:
    """Authenticate against iGPSPORT and return headers with the Bearer token.

    The token comes from the `loginToken` cookie (URL-encoded) set after login;
    it must be decoded to be used as a Bearer token against the gateway.
    """
    resp = session.post(LOGIN_URL, json={"username": user, "password": password})
    if not resp.ok:
        raise AuthError(f"iGPSPORT login failed: HTTP {resp.status_code}")

    token = session.cookies.get("loginToken")
    if not token:
        raise AuthError("iGPSPORT did not return a loginToken cookie")

    return {"Authorization": f"Bearer {unquote(token)}"}


# --------------------------------------------------------------------------
# Workout model — intermediate, self-contained representation
# --------------------------------------------------------------------------

@dataclass
class PowerTarget:
    """Power target. Use absolute (watts) or %FTP, not both."""
    min_watts: Optional[int] = None
    max_watts: Optional[int] = None
    min_pct_ftp: Optional[int] = None
    max_pct_ftp: Optional[int] = None

    def to_igps(self) -> dict[str, Any]:
        if self.min_pct_ftp is not None or self.max_pct_ftp is not None:
            lo = self.min_pct_ftp if self.min_pct_ftp is not None else self.max_pct_ftp
            hi = self.max_pct_ftp if self.max_pct_ftp is not None else self.min_pct_ftp
            return {"unit": "PercentOfFTP", "minValue": int(lo), "maxValue": int(hi)}
        lo = self.min_watts if self.min_watts is not None else self.max_watts
        hi = self.max_watts if self.max_watts is not None else self.min_watts
        return {"unit": "PowerCustom", "value": 0, "minValue": int(lo), "maxValue": int(hi)}


@dataclass
class HeartRateTarget:
    """Heart-rate target. Use an absolute BPM range or an HR zone (1-5), not both."""
    min_bpm: Optional[int] = None
    max_bpm: Optional[int] = None
    zone: Optional[int] = None  # 1-5

    def to_igps(self) -> dict[str, Any]:
        if self.zone is not None:
            return {"unit": "HeartRate", "value": int(self.zone)}
        lo = self.min_bpm if self.min_bpm is not None else self.max_bpm
        hi = self.max_bpm if self.max_bpm is not None else self.min_bpm
        return {"unit": "HeartRateCustom", "minValue": int(lo), "maxValue": int(hi)}


@dataclass
class WorkoutStep:
    """A single workout step (warm-up, interval, recovery, cool-down)."""
    name: str
    duration_seconds: Optional[int] = None  # None only when open_duration=True
    intensity_class: str = "Active"  # "WarmUp" | "Active" | "Rest" | "CoolDown"
    power: Optional[PowerTarget] = None
    heart_rate: Optional[HeartRateTarget] = None
    open_duration: bool = False  # True = "until lap is pressed" (no fixed duration)

    def to_igps(self) -> dict[str, Any]:
        step: dict[str, Any] = {
            "type": "Step",
            "name": self.name[:64],
            "uuid": str(uuid.uuid4()),
            "intensityClass": self.intensity_class,
            "openDuration": "true" if self.open_duration else "false",
        }
        if not self.open_duration:
            if self.duration_seconds is None:
                raise ValueError(
                    f"Step '{self.name}' needs duration_seconds (or open_duration=True)"
                )
            step["length"] = {"unit": "Second", "value": int(self.duration_seconds)}
        # iGPSPORT steps carry a single intensityTarget: power XOR heart rate.
        if self.power is not None and self.heart_rate is not None:
            raise ValueError(
                f"Step '{self.name}' can target either power or heart rate, not both"
            )
        if self.power is not None:
            step["intensityTarget"] = self.power.to_igps()
        elif self.heart_rate is not None:
            step["intensityTarget"] = self.heart_rate.to_igps()
        return step


@dataclass
class RepeatBlock:
    """A repetition block, e.g. 4x (interval + recovery)."""
    name: str
    reps: int
    steps: list[WorkoutStep] = field(default_factory=list)

    def to_igps(self) -> dict[str, Any]:
        return {
            "type": "Repetition",
            "name": self.name[:64],
            "uuid": str(uuid.uuid4()),
            "intensityClass": "Active",
            "openDuration": "false",
            "length": {"unit": "Repetition", "value": int(self.reps)},
            "steps": [s.to_igps() for s in self.steps],
        }


@dataclass
class Workout:
    """A full workout, ready to be mapped to the iGPSPORT format."""
    title: str
    description: str = ""
    blocks: list[Any] = field(default_factory=list)  # WorkoutStep | RepeatBlock
    existing_workout_id: Optional[int] = None  # set to edit instead of create

    def total_time_seconds(self) -> int:
        total = 0

        def walk(block: Any, repeat: int = 1) -> None:
            nonlocal total
            if isinstance(block, RepeatBlock):
                for s in block.steps:
                    walk(s, repeat * block.reps)
            elif isinstance(block, WorkoutStep):
                if not block.open_duration and block.duration_seconds:
                    total += block.duration_seconds * repeat

        for b in self.blocks:
            walk(b)
        return total

    def to_igps_body(self) -> dict[str, Any]:
        structure = [b.to_igps() for b in self.blocks]
        data: dict[str, Any] = {
            "title": self.title[:64],
            "description": self.description[:500],
            "totalTime": self.total_time_seconds(),
            "workoutType": "bike",
            "sportBigType": 1,
            "allowDeletion": True,
            "structure": structure,
        }
        if self.existing_workout_id:
            data["id"] = str(self.existing_workout_id)
        return {"data": data}


# --------------------------------------------------------------------------
# API: list and upload workouts
# --------------------------------------------------------------------------

def list_custom_workouts(
    session: requests.Session,
    auth_headers: dict[str, str],
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """List the existing custom workouts in the iGPSPORT account."""
    resp = session.get(
        IGPS_WORKOUT_LIST_URL,
        params={"PageIndex": page_index, "PageSize": page_size},
        headers=auth_headers,
    )
    resp.raise_for_status()
    return resp.json()


def upload_workout(
    session: requests.Session,
    auth_headers: dict[str, str],
    workout: Workout,
) -> int:
    """Create (or edit, when existing_workout_id is set) a custom workout.

    Returns the workoutId assigned by iGPSPORT.
    Raises IGPSportError if the API rejects the request.
    """
    body = workout.to_igps_body()
    resp = session.post(IGPS_WORKOUT_EDIT_URL, json=body, headers=auth_headers)
    if not resp.ok:
        raise IGPSportError(f"HTTP {resp.status_code} while uploading workout: {resp.text[:300]}")

    data = resp.json()
    if data.get("code") != 0:
        raise IGPSportError(f"iGPSPORT rejected the workout: {data}")

    payload = data.get("data") or {}
    workout_id = payload.get("workoutId")
    if workout_id is None:
        raise IGPSportError(f"Response without workoutId: {data}")
    return int(workout_id)


def delete_workout(
    session: requests.Session,
    auth_headers: dict[str, str],
    workout_id: int,
) -> dict[str, Any]:
    """Delete a custom workout by id and return the raw iGPSPORT response.

    The id travels as a query parameter and the body is empty, matching the
    request the iGPSPORT app sends. Raises IGPSportError with the HTTP status /
    response body on failure.
    """
    resp = session.post(
        IGPS_WORKOUT_DELETE_URL,
        params={"id": int(workout_id)},
        json={},
        headers=auth_headers,
    )
    if not resp.ok:
        raise IGPSportError(
            f"HTTP {resp.status_code} while deleting workout {workout_id}: {resp.text[:500]}"
        )

    data = resp.json()
    if data.get("code") != 0:
        raise IGPSportError(f"iGPSPORT rejected the delete of {workout_id}: {data}")
    return data


# --------------------------------------------------------------------------
# High-level function — this is what gets exposed as an MCP tool
# --------------------------------------------------------------------------

def create_workout(
    igp_user: str,
    igp_password: str,
    workout: Workout,
) -> dict[str, Any]:
    """Login + upload in a single step. Meant as a direct MCP tool wrapper.

    Returns a JSON-serializable dict:
      {"ok": True, "workout_id": 123, "title": "...", "total_time_seconds": 1500}
    or raises IGPSportError / AuthError on failure.
    """
    session = requests.Session()
    auth_headers = login(session, igp_user, igp_password)
    workout_id = upload_workout(session, auth_headers, workout)
    return {
        "ok": True,
        "workout_id": workout_id,
        "title": workout.title,
        "total_time_seconds": workout.total_time_seconds(),
    }


def delete_custom_workout(
    igp_user: str,
    igp_password: str,
    workout_id: int,
) -> dict[str, Any]:
    """Login + delete in a single step. Meant as a direct MCP tool wrapper.

    Returns {"ok": True, "workout_id": 123} or raises IGPSportError / AuthError.
    """
    session = requests.Session()
    auth_headers = login(session, igp_user, igp_password)
    delete_workout(session, auth_headers, workout_id)
    return {"ok": True, "workout_id": int(workout_id)}
