"""
igpsport_models.py

Strict Pydantic input schema for the agent-facing MCP API, plus a converter to
the transport-layer dataclasses in `igpsport_client.py`.

Keeping this separate from the client means the schema the LLM sees (validated,
documented, discriminated unions) is decoupled from the raw iGPSPORT wire
format. The MCP server validates input against these models, then calls
`to_workout()` to get a ready-to-upload `Workout`.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from igpsport_client import (
    HeartRateTarget,
    PowerTarget,
    RepeatBlock,
    Workout,
    WorkoutStep,
)

IntensityClass = Literal["WarmUp", "Active", "Rest", "CoolDown"]


class PowerIn(BaseModel):
    """Power target. Provide exactly one of `watts` or `pct_ftp` as [min, max]."""

    watts: Optional[tuple[int, int]] = Field(
        default=None, description="Absolute power range in watts, [min, max]."
    )
    pct_ftp: Optional[tuple[int, int]] = Field(
        default=None, description="Power range as percent of FTP, [min, max]."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "PowerIn":
        if (self.watts is None) == (self.pct_ftp is None):
            raise ValueError("power must set exactly one of `watts` or `pct_ftp`")
        rng = self.watts if self.watts is not None else self.pct_ftp
        if rng[0] > rng[1]:
            raise ValueError("power range must be [min, max] with min <= max")
        return self

    def to_power_target(self) -> PowerTarget:
        if self.watts is not None:
            return PowerTarget(min_watts=self.watts[0], max_watts=self.watts[1])
        return PowerTarget(min_pct_ftp=self.pct_ftp[0], max_pct_ftp=self.pct_ftp[1])


class HeartRateIn(BaseModel):
    """Heart-rate target. Provide exactly one of `bpm` ([min, max]) or `hr_zone` (1-5)."""

    bpm: Optional[tuple[int, int]] = Field(
        default=None, description="Absolute heart-rate range in beats per minute, [min, max]."
    )
    hr_zone: Optional[int] = Field(
        default=None, ge=1, le=5, description="Heart-rate zone, 1-5."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "HeartRateIn":
        if (self.bpm is None) == (self.hr_zone is None):
            raise ValueError("heart_rate must set exactly one of `bpm` or `hr_zone`")
        if self.bpm is not None and self.bpm[0] > self.bpm[1]:
            raise ValueError("bpm range must be [min, max] with min <= max")
        return self

    def to_heart_rate_target(self) -> HeartRateTarget:
        if self.bpm is not None:
            return HeartRateTarget(min_bpm=self.bpm[0], max_bpm=self.bpm[1])
        return HeartRateTarget(zone=self.hr_zone)


class StepIn(BaseModel):
    """A single workout step: warm-up, interval, recovery or cool-down."""

    type: Literal["step"] = "step"
    name: str = Field(description="Step label, e.g. 'Warm-up' or 'Interval'.")
    intensity_class: IntensityClass = Field(
        default="Active",
        description="WarmUp | Active | Rest | CoolDown.",
    )
    duration_seconds: Optional[int] = Field(
        default=None,
        gt=0,
        description="Step length in seconds. Required unless open_duration is true.",
    )
    power: Optional[PowerIn] = Field(
        default=None, description="Optional power target (watts or %FTP)."
    )
    heart_rate: Optional[HeartRateIn] = Field(
        default=None, description="Optional heart-rate target (BPM range or HR zone)."
    )
    open_duration: bool = Field(
        default=False,
        description="True = step runs until the lap button is pressed (no fixed duration).",
    )

    @model_validator(mode="after")
    def _validate(self) -> "StepIn":
        if not self.open_duration and self.duration_seconds is None:
            raise ValueError(
                f"step '{self.name}' needs duration_seconds (or open_duration=true)"
            )
        if self.power is not None and self.heart_rate is not None:
            raise ValueError(
                f"step '{self.name}' can target either power or heart_rate, not both"
            )
        return self

    def to_step(self) -> WorkoutStep:
        return WorkoutStep(
            name=self.name,
            duration_seconds=self.duration_seconds,
            intensity_class=self.intensity_class,
            power=self.power.to_power_target() if self.power else None,
            heart_rate=self.heart_rate.to_heart_rate_target() if self.heart_rate else None,
            open_duration=self.open_duration,
        )


class RepeatBlockIn(BaseModel):
    """A repetition block, e.g. 4x (interval + recovery)."""

    type: Literal["repeat"] = "repeat"
    name: str = Field(description="Block label, e.g. 'Intervals'.")
    reps: int = Field(gt=0, description="Number of repetitions.")
    steps: list[StepIn] = Field(
        min_length=1, description="Steps repeated on each rep (cannot be empty)."
    )

    def to_repeat_block(self) -> RepeatBlock:
        return RepeatBlock(
            name=self.name,
            reps=self.reps,
            steps=[s.to_step() for s in self.steps],
        )


# Discriminated on the `type` field: "step" vs "repeat".
Block = Annotated[Union[StepIn, RepeatBlockIn], Field(discriminator="type")]


def to_workout(
    title: str,
    description: str,
    blocks: list[Block],
    edit_workout_id: Optional[int] = None,
) -> Workout:
    """Convert validated agent input into a transport-layer Workout."""
    mapped: list = []
    for block in blocks:
        if isinstance(block, RepeatBlockIn):
            mapped.append(block.to_repeat_block())
        else:
            mapped.append(block.to_step())
    return Workout(
        title=title,
        description=description,
        blocks=mapped,
        existing_workout_id=edit_workout_id,
    )
