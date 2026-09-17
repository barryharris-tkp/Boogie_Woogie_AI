import math

import pytest
from pydantic import ValidationError

from station.music_controls import MAX_SEED, MusicControls, normalize_generation_request, resolve_music_controls


def test_natural_defaults_and_decimal_seed_resolution():
    controls = MusicControls()
    assert controls.ending_mode == "natural"
    assert controls.safety_seconds == 240
    assert controls.planning_mode == "full"
    result = resolve_music_controls(controls, MAX_SEED, 60)
    assert result["seed"] == result["planning_seed"] == result["decoder_seed"] == str(MAX_SEED)
    assert result["lyric_target_seconds"] == 180
    assert result["max_duration"] == 240
    assert result["max_frames"] == 6000


def test_clip_uses_lyric_target_and_legacy_decoder_seed_is_explicit():
    result = resolve_music_controls({"ending_mode": "clip", "safety_seconds": 900,
                                     "decoder_seed": "42", "planning_seed": str(MAX_SEED)}, 123, 180)
    assert result["max_duration"] == 180
    assert result["max_frames"] == 4500
    assert result["seed"] == "123"
    assert result["decoder_seed"] == "42"
    assert result["planning_seed"] == str(MAX_SEED)


@pytest.mark.parametrize("field,value", [
    ("safety_seconds", 14.99), ("safety_seconds", 240.01),
    ("ending_mode", "extend"), ("planning_mode", "auto"),
    ("planning_seed", -1), ("decoder_seed", MAX_SEED + 1),
    ("decoder_seed", "18446744073709551616"), ("planning_seed", 1.5),
    ("planning_seed", True), ("decoder_seed", "1e4"),
    ("temperature", -1), ("temperature", 5.1),
    ("top_p", 0), ("top_p", 1.01), ("top_k", 0), ("top_k", 32769),
    ("repetition_penalty", 0), ("repetition_penalty", 10.01),
    ("planning_temperature", -1), ("planning_top_p", 0),
    ("planning_top_k", 32769), ("planning_repetition_penalty", 0),
    ("planning_penalty_window", 0), ("planning_penalty_window", 20001),
    ("max_abc_tokens", 31), ("max_abc_tokens", 20001),
    ("steps", 0), ("steps", 201), ("cfg", -1), ("cfg", 10.01),
    ("denoise", 0), ("denoise", 1.01), ("steps", True),
    ("sampler", "../../model"), ("scheduler", "/tmp/schedule"),
    ("checkpoint", "/tmp/model.safetensors"), ("unknown_control", 1),
    ("abc", "X:1\x00K:C"),
])
def test_invalid_controls_are_rejected(field, value):
    with pytest.raises(ValidationError):
        MusicControls.model_validate({field: value})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["safety_seconds", "temperature", "top_p", "repetition_penalty",
                                   "planning_temperature", "planning_top_p", "planning_repetition_penalty", "cfg", "denoise"])
def test_nonfinite_controls_are_rejected(field, value):
    with pytest.raises(ValidationError):
        MusicControls.model_validate({field: value})


def test_manual_abc_and_off_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        MusicControls(planning_mode="off", abc="X:1\nK:C\nCDEF|")
    assert MusicControls(planning_mode="off", abc=" \n ").abc == ""


def test_all_boundary_values_are_valid():
    controls = MusicControls(safety_seconds=240, temperature=0, top_p=.01, top_k=32768,
        repetition_penalty=10, planning_temperature=5, planning_top_p=1, planning_top_k=1,
        planning_repetition_penalty=.01, planning_penalty_window=20000, max_abc_tokens=32,
        steps=200, cfg=0, denoise=.01, planning_seed=0, decoder_seed=str(MAX_SEED))
    assert controls.decoder_seed == MAX_SEED


def test_prebuilt_model_cannot_bypass_finite_validation():
    controls = MusicControls().model_copy(update={"temperature": math.nan})
    with pytest.raises(ValidationError):
        resolve_music_controls(controls, 1, 60)


@pytest.mark.parametrize("mode,target", [("natural", 180), ("clip", 240)])
def test_old_limits_clamp_for_render_without_mutating_original(mode, target):
    original = {"ending_mode": mode, "safety_seconds": 900, "temperature": .8, "decoder_seed": "42"}
    result = resolve_music_controls(original, 7, 600)
    assert result["lyric_target_seconds"] == target
    assert result["max_duration"] == 240
    assert result["max_frames"] == 6000
    assert result["safety_seconds"] == 240
    assert result["decoder_seed"] == "42"
    assert original["safety_seconds"] == 900


@pytest.mark.parametrize("mode", ["natural", "clip"])
def test_new_oversize_inputs_reject_instead_of_silent_clamp(mode):
    with pytest.raises(ValueError):
        normalize_generation_request({"ending_mode": mode}, 241)
    with pytest.raises(ValidationError):
        normalize_generation_request({"ending_mode": mode, "safety_seconds": 241}, 180)


@pytest.mark.parametrize("desired", [15, 60, 180, 240])
def test_natural_mode_ignores_disabled_desired_length(desired):
    result = normalize_generation_request(None, desired)
    assert result["duration"] == 180
    assert result["music"]["safety_seconds"] == 240
