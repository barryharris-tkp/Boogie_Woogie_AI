"""Validated YuE2 controls shared by API requests, saved takes, and providers."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_SEED = 2**64 - 1
SEMANTIC_FRAMES_PER_SECOND = 25
MAX_SONG_SECONDS = 240
NATURAL_LYRIC_SECONDS = 180


class MusicControls(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True, revalidate_instances="always")

    ending_mode: Literal["natural", "clip"] = "natural"
    safety_seconds: float = Field(default=MAX_SONG_SECONDS, ge=15, le=MAX_SONG_SECONDS, strict=True)
    planning_mode: Literal["full", "melody", "off"] = "full"
    abc: str = Field(default="", max_length=100_000, strict=True)
    planning_seed: int | None = Field(default=None, ge=0, le=MAX_SEED, strict=True)
    decoder_seed: int | None = Field(default=None, ge=0, le=MAX_SEED, strict=True)

    temperature: float = Field(default=1.0, ge=0, le=5, strict=True)
    top_p: float = Field(default=.95, ge=.01, le=1, strict=True)
    top_k: int = Field(default=100, ge=1, le=32768, strict=True)
    repetition_penalty: float = Field(default=1.2, ge=.01, le=10, strict=True)

    planning_temperature: float = Field(default=.7, ge=0, le=5, strict=True)
    planning_top_p: float = Field(default=.9, ge=.01, le=1, strict=True)
    planning_top_k: int = Field(default=30, ge=1, le=32768, strict=True)
    planning_repetition_penalty: float = Field(default=1.005, ge=.01, le=10, strict=True)
    planning_penalty_window: int = Field(default=100, ge=1, le=20000, strict=True)
    max_abc_tokens: int = Field(default=8192, ge=32, le=20000, strict=True)

    steps: int = Field(default=32, ge=1, le=200, strict=True)
    cfg: float = Field(default=1, ge=0, le=10, strict=True)
    sampler: str = Field(default="dpm_2", pattern=r"^[A-Za-z0-9_-]{1,80}$", strict=True)
    scheduler: str = Field(default="sgm_uniform", pattern=r"^[A-Za-z0-9_-]{1,80}$", strict=True)
    denoise: float = Field(default=1, ge=.01, le=1, strict=True)

    @field_validator("planning_seed", "decoder_seed", mode="before")
    @classmethod
    def parse_decimal_seed(cls, value):
        # Browser JSON cannot exactly represent all 64-bit seeds. Decimal
        # strings are accepted without ever passing through a float.
        if isinstance(value, str):
            if not value.isascii() or not value.isdigit() or len(value) > 20:
                raise ValueError("Seeds must be unsigned decimal 64-bit integers.")
            return int(value)
        return value

    @field_validator("abc")
    @classmethod
    def safe_notation(cls, value):
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("ABC notation cannot contain control characters.")
        return value.strip()

    @model_validator(mode="after")
    def consistent_planning(self):
        if self.planning_mode == "off" and self.abc:
            raise ValueError("Clear manual ABC notation when planning is off.")
        return self


def normalize_generation_request(music: MusicControls | dict | None, duration: float, *, legacy=False) -> dict:
    """Apply the current generation policy without modifying saved metadata.

    New inputs validate strictly. The worker and provider may receive older
    requests, whose formerly valid limits are lowered only for the new render.
    """
    upper_duration = 600 if legacy else MAX_SONG_SECONDS
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not 1 <= duration <= upper_duration):
        raise ValueError(f"The lyric target must be between 1 and {upper_duration} seconds.")
    values = music.model_dump(mode="json") if isinstance(music, MusicControls) else dict(music or {})
    inherited_cap = values.get("safety_seconds")
    if (legacy and type(inherited_cap) in {int, float} and math.isfinite(inherited_cap)
            and MAX_SONG_SECONDS < inherited_cap <= 900):
        values["safety_seconds"] = MAX_SONG_SECONDS
    controls = MusicControls.model_validate(values)
    target = NATURAL_LYRIC_SECONDS if controls.ending_mode == "natural" else min(duration, MAX_SONG_SECONDS)
    return {"music": controls.model_dump(mode="json"), "duration": target}


def resolve_music_controls(music: MusicControls | dict | None, seed: int, duration: float) -> dict:
    """Resolve inherited seeds and limits, returning JSON/browser-safe metadata.

    Native Comfy nodes receive integer seeds. This public representation uses
    decimal strings to preserve all 64 bits in saved takes and browser clients.
    """
    if type(seed) is not int or not 0 <= seed <= MAX_SEED:
        raise ValueError("The music seed must be an unsigned 64-bit integer.")
    request = normalize_generation_request(music, duration, legacy=True)
    controls = MusicControls.model_validate(request["music"])
    result = dict(request["music"])
    result["seed"] = str(seed)
    result["planning_seed"] = str(seed if controls.planning_seed is None else controls.planning_seed)
    result["decoder_seed"] = str(seed if controls.decoder_seed is None else controls.decoder_seed)
    result["lyric_target_seconds"] = float(request["duration"])
    result["max_duration"] = float(controls.safety_seconds if controls.ending_mode == "natural" else request["duration"])
    result["max_frames"] = max(1, round(result["max_duration"] * SEMANTIC_FRAMES_PER_SECOND))
    return result
