import base64
import io
import json
import threading
import wave

import httpx
from PIL import Image
import pytest

from station import providers


PROMPT_ID = "aba10d40-f0dc-491a-84df-dc470e399847"


def mock_http(monkeypatch, handler, *, with_inventory=True):
    original = httpx.Client

    def routed(request):
        if with_inventory and request.url.path == "/object_info/KSampler":
            return httpx.Response(200, json={"KSampler": {"input": {"required": {
                "sampler_name": [["dpm_2", "euler"]], "scheduler": [["sgm_uniform", "karras"]],
            }}}})
        return handler(request)

    monkeypatch.setattr(providers.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(routed), **kwargs))


def completion_report(**overrides):
    return {"schema_version": 1, "semantic_truncated": False, "frames": 25,
            "seconds": 1.0, "abc": "X:1\nK:C\nCDEF|", "abc_truncated": False, **overrides}


def wav_bytes():
    target = io.BytesIO()
    with wave.open(target, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)
    return target.getvalue()


def image_response():
    target = io.BytesIO()
    Image.new("RGB", (128, 128), "navy").save(target, format="PNG")
    return {"data": [{"b64_json": base64.b64encode(target.getvalue()).decode()}],
            "usage": {"input_tokens": 50, "output_tokens": 2000}}


def test_compose_cpu_json_contract(monkeypatch):
    brief = {"title": " River Light ", "style": "Country pop", "lyrics": "[verse]\nA new day",
             "cover_prompt": "Moonlit river with wildflowers"}

    def handler(request):
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["think"] is False
        assert payload["stream"] is False
        assert payload["format"] == "json"
        assert payload["options"]["num_gpu"] == 0
        assert payload["keep_alive"] == 0
        request_brief = json.loads(payload["messages"][1]["content"])
        assert request_brief["duration_seconds"] == 60
        assert request_brief["sung_word_budget"] == {"target_min": 36, "maximum": 48}
        system_prompt = payload["messages"][0]["content"]
        assert "36-48 sung words TOTAL" in system_prompt
        assert "never exceed 48 sung words" in system_prompt
        assert "omit the bridge and extra verses" in system_prompt
        assert "instrumental gaps" in system_prompt
        return httpx.Response(200, json={"message": {"content": json.dumps(brief)}})

    mock_http(monkeypatch, handler)
    result = providers.compose_song("River song", "Country", "Female", 60)
    assert result["title"] == "River Light"


@pytest.mark.parametrize("body", [
    {"message": {"content": "bad json"}},
    {"message": {"content": json.dumps({"title": "Missing fields"})}},
    {"message": {"content": "[]"}},
])
def test_compose_rejects_unusable_brief(monkeypatch, body):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(providers.ProviderError, match="brief"):
        providers.compose_song("River song", "Country", "Female", 60)


def test_music_workflow_success_downloads_only_save_node(monkeypatch, tmp_path):
    seen = []
    events = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            assert graph["2"]["inputs"]["style"] == "Country"
            assert graph["2"]["inputs"]["lyrics"] == "[verse]\nA new day"
            assert graph["2"]["inputs"]["seed"] == 88
            assert graph["2"]["inputs"]["max_duration"] == 240
            assert graph["8"]["inputs"]["style"] == "Country"
            assert graph["8"]["inputs"]["lyrics"] == "[verse]\nA new day"
            assert graph["8"]["inputs"]["seed"] == 88
            assert graph["8"]["class_type"] == "BoogieYuE2GenerateABC"
            assert graph["5"]["inputs"]["seed"] == 88
            assert graph["5"]["inputs"]["steps"] == 32
            assert graph["9"]["inputs"]["abc_truncated"] == ["8", 1]
            assert graph["7"]["inputs"]["filename_prefix"].startswith("boogie/")
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == f"/history/{PROMPT_ID}":
            return httpx.Response(200, json={PROMPT_ID: {
                "status": {"status_str": "success", "completed": True},
                "outputs": {
                    "999": {"audio": [{"filename": "unrelated.wav"}]},
                    "7": {"audio": [{"filename": "song.wav", "subfolder": "boogie", "type": "output"}]},
                    "9": {"text": [json.dumps(completion_report())]},
                },
            }})
        if request.url.path == "/view":
            assert request.url.params["filename"] == "song.wav"
            assert request.url.params["type"] == "output"
            return httpx.Response(200, content=wav_bytes())
        pytest.fail(f"Unexpected provider path: {request.url.path}")

    mock_http(monkeypatch, handler)
    result = providers.generate_music("Country", "[verse]\nA new day", 88, 60,
                                      tmp_path / "song.wav", on_progress=events.append)
    assert result["prompt_id"] == PROMPT_ID
    assert result["duration"] == pytest.approx(1.0)
    assert result["completion"] == completion_report()
    assert result["music"]["decoder_seed"] == "88"
    assert result["music"]["max_frames"] == 6000
    assert result["graph"]["2"]["inputs"]["seed"] == "88"
    assert (tmp_path / "song.wav").read_bytes() == wav_bytes()
    assert events[0] == {"stage": "submitted", "prompt_id": PROMPT_ID}
    assert seen == ["/prompt", f"/history/{PROMPT_ID}", "/view"]


def test_music_rejects_missing_planned_workflow_without_silent_change(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "PLANNED_WORKFLOW_PATH", tmp_path / "missing.json")
    mock_http(monkeypatch, lambda request: pytest.fail("Missing workflow should not submit a job"))
    with pytest.raises(providers.ProviderError, match="workflow"):
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav")


def test_music_cancel_before_submit_never_calls_provider(monkeypatch, tmp_path):
    cancel = threading.Event()
    cancel.set()
    mock_http(monkeypatch, lambda request: pytest.fail("Precancelled job made a request"))
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav", cancel_event=cancel)
    assert error.value.cancelled


@pytest.mark.parametrize("targeted_supported", [True, False])
def test_music_cancel_targets_own_job_only(monkeypatch, tmp_path, targeted_supported):
    cancel = threading.Event()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            cancel.set()
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == f"/api/jobs/{PROMPT_ID}/cancel":
            return httpx.Response(200 if targeted_supported else 404, json={"cancelled": True})
        if request.url.path == "/queue":
            assert json.loads(request.content) == {"delete": [PROMPT_ID]}
            return httpx.Response(200)
        pytest.fail(f"Unexpected cancellation path: {request.url.path}")

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav", cancel_event=cancel)
    assert error.value.cancelled
    assert error.value.prompt_id == PROMPT_ID
    assert "/interrupt" not in calls
    assert ("/queue" in calls) is not targeted_supported


def test_music_rejects_traversal_reference(monkeypatch, tmp_path):
    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path.startswith("/history/"):
            return httpx.Response(200, json={PROMPT_ID: {
                "status": {"completed": True},
                "outputs": {"7": {"audio": [{"filename": "secret.wav", "subfolder": "../secrets"}]}},
            }})
        pytest.fail("Invalid reference must not be fetched")

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError, match="invalid saved-audio"):
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav")
    assert not (tmp_path / "song.wav").exists()


def test_music_error_hides_provider_log(monkeypatch, tmp_path):
    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        return httpx.Response(200, json={PROMPT_ID: {
            "status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "sensitive debug value"}]]},
        }})

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav")
    assert "sensitive" not in str(error.value)
    assert error.value.prompt_id == PROMPT_ID


def test_cover_success_returns_usage_and_valid_png(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.openai.com/v1/images/generations"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        prompt = payload.pop("prompt")
        assert "ARTWORK" in prompt and "no text" in prompt and "Do not write or invent a song" in prompt
        assert prompt.endswith("Visual inspiration from the song:\nA moonlit river")
        assert payload == {
            "model": "gpt-image-2.5-sunburst",
            "quality": "high", "size": "1024x1024", "n": 1, "output_format": "png",
        }
        return httpx.Response(200, json=image_response(), headers={"x-request-id": "req-safe-id"})

    mock_http(monkeypatch, handler)
    result = providers.generate_cover("A moonlit river", "test-key", out_path=tmp_path / "cover.png")
    assert result["estimated_cost_usd"] == pytest.approx(0.06025)
    assert result["request_id"] == "req-safe-id"
    assert len(calls) == 1
    with Image.open(tmp_path / "cover.png") as image:
        assert image.format == "PNG"
        assert image.size == (128, 128)


@pytest.mark.parametrize("status,ambiguous", [(401, False), (429, False), (500, True), (503, True)])
def test_cover_errors_are_safe_and_never_retried(monkeypatch, tmp_path, status, ambiguous):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "test-key and private provider data"}})

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png")
    assert error.value.ambiguous is ambiguous
    assert "test-key" not in str(error.value)
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_cover_timeout_is_ambiguous_and_not_retried(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private provider details", request=request)

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png")
    assert error.value.ambiguous
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_cover_invalid_image_preserves_previous_file(monkeypatch, tmp_path):
    output = tmp_path / "cover.png"
    output.write_bytes(b"previous cover")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={
        "data": [{"b64_json": base64.b64encode(b"not an image").decode()}],
    }))
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_cover("Moonlight", "test-key", out_path=output)
    assert error.value.ambiguous
    assert output.read_bytes() == b"previous cover"


def test_cover_missing_usage_does_not_invent_price(monkeypatch, tmp_path):
    body = image_response()
    del body["usage"]
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    result = providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png")
    assert result["estimated_cost_usd"] is None


def test_cover_invalid_local_destination_never_makes_paid_request(monkeypatch, tmp_path):
    mock_http(monkeypatch, lambda request: pytest.fail("Invalid output destination made a paid request"))
    with pytest.raises(providers.ProviderError, match="No artwork request was sent") as error:
        providers.generate_cover("Moonlight", "test-key", out_path=tmp_path)
    assert not error.value.ambiguous


def test_cover_pre_cancelled_job_never_makes_paid_request(monkeypatch, tmp_path):
    cancel = threading.Event()
    cancel.set()
    mock_http(monkeypatch, lambda request: pytest.fail("Cancelled job made a paid request"))
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png", cancel_event=cancel)
    assert error.value.cancelled
    assert not error.value.ambiguous


def test_cover_inflight_cancellation_preserves_image_and_cost(monkeypatch, tmp_path):
    cancel = threading.Event()

    def handler(request):
        cancel.set()
        return httpx.Response(200, json=image_response())

    mock_http(monkeypatch, handler)
    result = providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png", cancel_event=cancel)
    assert (tmp_path / "cover.png").is_file()
    assert result["estimated_cost_usd"] == pytest.approx(.06025)


@pytest.mark.parametrize("body", ["invalid JSON", "[]", '{"data": []}'])
def test_cover_unreadable_success_remains_ambiguous(monkeypatch, tmp_path, body):
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=body,
              headers={"x-request-id": "req-uncertain"}))
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png")
    assert error.value.ambiguous
    assert error.value.request_id == "req-uncertain"


def test_cover_rejects_nonsquare_image_as_ambiguous(monkeypatch, tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (256, 64)).save(image, format="PNG")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={
        "data": [{"b64_json": base64.b64encode(image.getvalue()).decode()}],
    }))
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_cover("Moonlight", "test-key", out_path=tmp_path / "cover.png")
    assert error.value.ambiguous
    assert not (tmp_path / "cover.png").exists()


@pytest.mark.parametrize("usage", [
    {"input_tokens": True, "output_tokens": 10},
    {"input_tokens": 50, "output_tokens": 20.5},
    {"input_tokens": "50", "output_tokens": 2000},
    {"input_tokens": 50, "output_tokens": -1},
])
def test_cover_malformed_token_counts_have_no_estimate(usage):
    assert providers._estimated_image_cost(providers.DEFAULT_IMAGE_MODEL, usage) is None


def test_cover_unknown_model_variant_has_no_estimate():
    usage = {"input_tokens": 50, "output_tokens": 2000}
    assert providers._estimated_image_cost("gpt-image-2-unverified-variant", usage) is None
    assert providers._estimated_image_cost("gpt-image-2.5-sunburst-2026-09-08", usage) == pytest.approx(.06025)


def test_music_cancel_noop_does_not_claim_remote_confirmation(monkeypatch, tmp_path):
    cancel = threading.Event()

    def handler(request):
        if request.url.path == "/prompt":
            cancel.set()
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        assert request.url.path == f"/api/jobs/{PROMPT_ID}/cancel"
        return httpx.Response(200, json={"cancelled": False})

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError, match="may still finish") as error:
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav", cancel_event=cancel)
    assert error.value.cancelled
    assert error.value.prompt_id == PROMPT_ID


@pytest.mark.parametrize("status,outputs", [
    (None, {}),
    ({"completed": True}, []),
    ({"completed": True}, {"7": []}),
    ({"completed": True}, {}),
])
def test_music_malformed_completion_preserves_job_id(monkeypatch, tmp_path, status, outputs):
    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"cancelled": True})
        return httpx.Response(200, json={PROMPT_ID: {"status": status, "outputs": outputs}})

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav")
    assert error.value.prompt_id == PROMPT_ID


def test_music_invalid_audio_preserves_job_id_and_previous_file(monkeypatch, tmp_path):
    output = tmp_path / "song.wav"
    output.write_bytes(b"previous audio")

    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"not an audio file")
        return httpx.Response(200, json={PROMPT_ID: {
            "status": {"completed": True},
            "outputs": {"7": {"audio": [{"filename": "song.wav"}]}},
        }})

    mock_http(monkeypatch, handler)
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_music("Country", "words", 1, 60, output)
    assert error.value.prompt_id == PROMPT_ID
    assert output.read_bytes() == b"previous audio"
    assert not list(tmp_path.glob(".audio-*"))


def test_music_maps_every_control_and_preserves_exact_seeds():
    values = {
        "ending_mode": "natural", "safety_seconds": 900, "planning_mode": "melody",
        "planning_seed": "18446744073709551615", "decoder_seed": 42,
        "temperature": .8, "top_p": .9, "top_k": 81, "repetition_penalty": 1.3,
        "planning_temperature": .6, "planning_top_p": .8, "planning_top_k": 28,
        "planning_repetition_penalty": 1.02, "planning_penalty_window": 200,
        "max_abc_tokens": 10000, "steps": 50, "cfg": 2, "sampler": "euler",
        "scheduler": "karras", "denoise": .9,
    }
    resolved = providers.resolve_music_controls(values, 9007199254740993, 120)
    graph = providers._build_music_graph("New style", "Complete lyrics", resolved)
    semantic = graph["2"]["inputs"]
    assert semantic["mode"] == "melody"
    assert semantic["abc"] == ["8", 0]
    assert semantic["seed"] == 9007199254740993
    assert semantic["max_duration"] == 240
    assert values["safety_seconds"] == 900  # Historical settings remain intact.
    for key in ("temperature", "top_p", "top_k", "repetition_penalty"):
        assert semantic[key] == values[key]
    planning = graph["8"]["inputs"]
    assert planning["seed"] == 18446744073709551615
    assert planning["mode"] == "melody"
    assert planning["max_abc_tokens"] == 10000
    for key in ("temperature", "top_p", "top_k", "repetition_penalty", "penalty_window"):
        assert planning[key] == values["planning_" + key]
    decoder = graph["5"]["inputs"]
    assert decoder["seed"] == 42
    for key in ("steps", "cfg", "scheduler", "denoise"):
        assert decoder[key] == values[key]
    assert decoder["sampler_name"] == "euler"
    assert graph["9"]["inputs"] == {"conditioning": ["2", 0], "clip": ["1", 1],
                                     "planning_generated": True, "abc_truncated": ["8", 1]}
    safe = providers._json_safe_graph(graph)
    assert safe["2"]["inputs"]["seed"] == "9007199254740993"
    assert safe["8"]["inputs"]["seed"] == "18446744073709551615"
    assert graph["2"]["inputs"]["seed"] == 9007199254740993


@pytest.mark.parametrize("music,abc,mode", [
    ({"planning_mode": "off"}, "", "full"),
    ({"planning_mode": "full", "abc": "X:1\nK:C\nCDEF|"}, "X:1\nK:C\nCDEF|", "full"),
    ({"planning_mode": "melody", "abc": "X:1\nK:C\nCDEF|"}, "X:1\nK:C\nCDEF|", "melody"),
])
def test_music_manual_and_off_modes_remove_planning_node(music, abc, mode):
    resolved = providers.resolve_music_controls({**music, "ending_mode": "clip"}, 7, 60)
    graph = providers._build_music_graph("Country", "Keep all lyrics", resolved)
    assert "8" not in graph
    assert graph["2"]["inputs"]["abc"] == abc
    assert graph["2"]["inputs"]["mode"] == mode
    assert graph["2"]["inputs"]["max_duration"] == 60
    assert graph["9"]["inputs"]["planning_generated"] is False
    assert graph["9"]["inputs"]["abc_truncated"] is False


def test_music_rejects_unsupported_sampler_before_post(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.url.path == "/object_info/KSampler"
        return httpx.Response(200, json={"KSampler": {"input": {"required": {
            "sampler_name": [["euler"]], "scheduler": [["karras"]],
        }}}})

    mock_http(monkeypatch, handler, with_inventory=False)
    with pytest.raises(providers.ProviderError, match="not supported"):
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav")
    assert calls == ["GET"]


def test_music_options_uses_installed_inventory_and_exposes_schema(monkeypatch):
    mock_http(monkeypatch, lambda request: pytest.fail("Unexpected route"))
    options = providers.get_music_options()
    assert options["samplers"] == ["dpm_2", "euler"]
    assert options["schedulers"] == ["sgm_uniform", "karras"]
    assert options["defaults"]["ending_mode"] == "natural"
    assert options["defaults"]["safety_seconds"] == 240
    assert options["schema"]["properties"]["safety_seconds"]["maximum"] == 240


@pytest.mark.parametrize("checkpoint", ["../private.safetensors", "/tmp/model.safetensors", "C:\\private.safetensors", "http://example.com/model"])
def test_music_rejects_unsafe_workflow_model_paths(monkeypatch, tmp_path, checkpoint):
    graph = json.loads(providers.WORKFLOW_PATH.read_text())
    graph["1"]["inputs"]["ckpt_name"] = checkpoint
    workflow = tmp_path / "bad-workflow.json"
    workflow.write_text(json.dumps(graph))
    monkeypatch.setattr(providers, "WORKFLOW_PATH", workflow)
    mock_http(monkeypatch, lambda request: pytest.fail("Unsafe workflow made a provider request"))
    with pytest.raises(providers.ProviderError, match="unsafe model path"):
        providers.generate_music("Country", "words", 1, 60, tmp_path / "song.wav", music={"planning_mode": "off"})


@pytest.mark.parametrize("report", [None, "bad json", json.dumps({}),
    json.dumps(completion_report(semantic_truncated="false")),
    json.dumps(completion_report(abc_truncated=None)),
    json.dumps(completion_report(frames=True)),
    json.dumps(completion_report(seconds=2)),
    json.dumps(completion_report(seconds=float("nan"))),
])
def test_music_missing_or_invalid_completion_preserves_audio_for_review(monkeypatch, tmp_path, report):
    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == "/view":
            return httpx.Response(200, content=wav_bytes())
        outputs = {"7": {"audio": [{"filename": "song.wav"}]}}
        if report is not None:
            outputs["9"] = {"text": [report]}
        return httpx.Response(200, json={PROMPT_ID: {"status": {"completed": True}, "outputs": outputs}})

    mock_http(monkeypatch, handler)
    output = tmp_path / "song.wav"
    with pytest.raises(providers.ProviderError, match="completion report") as error:
        providers.generate_music("Country", "words", 9, 60, output)
    assert output.read_bytes() == wav_bytes()
    assert error.value.audio_path == str(output)
    assert error.value.duration == 1
    assert error.value.prompt_id == PROMPT_ID
    assert error.value.completion is None
    assert error.value.music["seed"] == "9"
    assert error.value.graph["5"]["inputs"]["seed"] == "9"


@pytest.mark.parametrize("semantic,abc,ending_mode", [(True, False, "natural"), (False, True, "natural"), (True, True, "clip"), (False, False, "clip")])
def test_music_completion_uses_exact_flags_not_audio_length(monkeypatch, tmp_path, semantic, abc, ending_mode):
    report = completion_report(semantic_truncated=semantic, abc_truncated=abc)

    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == "/view":
            return httpx.Response(200, content=wav_bytes())
        return httpx.Response(200, json={PROMPT_ID: {"status": {"completed": True}, "outputs": {
            "7": {"audio": [{"filename": "song.wav"}]}, "9": {"text": [json.dumps(report)]},
        }}})

    mock_http(monkeypatch, handler)
    result = providers.generate_music("Country", "words", 4, 60, tmp_path / "song.wav", music={"ending_mode": ending_mode})
    assert result["completion"]["semantic_truncated"] is semantic
    assert result["completion"]["abc_truncated"] is abc
    assert result["duration"] == 1  # A much shorter file does not prove a complete ending.


def test_music_report_node_failure_retains_already_saved_audio(monkeypatch, tmp_path):
    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": PROMPT_ID})
        if request.url.path == "/view":
            return httpx.Response(200, content=wav_bytes())
        return httpx.Response(200, json={PROMPT_ID: {"status": {"status_str": "error"}, "outputs": {
            "7": {"audio": [{"filename": "song.wav"}]},
        }}})

    mock_http(monkeypatch, handler)
    output = tmp_path / "song.wav"
    with pytest.raises(providers.ProviderError) as error:
        providers.generate_music("Country", "words", 1, 60, output)
    assert error.value.audio_path == str(output)
    assert error.value.completion is None
    assert output.read_bytes() == wav_bytes()
