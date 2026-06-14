"""Media capability layer — deterministic stubs, hybrid transport, capability grant.

Zero mocks: the offline image/speech stubs produce byte-identical artifacts, the tools
enforce the manifest grant, and the transport inlines stub output (a ``data:`` URI) while
writing live-model output to a served file (the lean-trace path).
"""

from __future__ import annotations

import pytest

from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import default_registry
from src.media.inference import build_media_router
from src.media.provider import MediaResult, StubImageProvider, StubSpeechProvider
from src.media.router import MediaRouter, MediaSpec
from src.media.tools import _to_ref, register_media_tools
from src.tools.builtins import default_tool_registry
from src.tools.registry import CapabilityViolation, ToolRegistry


class TestStubImage:
    def test_deterministic_png(self):
        a = StubImageProvider().generate("a mossy ticket booth")
        b = StubImageProvider().generate("a mossy ticket booth")
        assert a.data == b.data
        assert a.data[:8] == b"\x89PNG\r\n\x1a\n"
        assert a.mime == "image/png"
        assert a.model_id == "stub:image"

    def test_prompt_changes_the_image(self):
        a = StubImageProvider().generate("a mossy booth")
        b = StubImageProvider().generate("a ladder to the moon")
        assert a.data != b.data

    def test_data_uri_prefix(self):
        assert StubImageProvider().generate("x").data_uri().startswith("data:image/png;base64,")


class TestStubSpeech:
    def test_deterministic_wav(self):
        a = StubSpeechProvider().synthesize("bold choice, mushrooms")
        b = StubSpeechProvider().synthesize("bold choice, mushrooms")
        assert a.data == b.data
        assert a.data[:4] == b"RIFF"
        assert a.mime == "audio/wav"
        assert a.usage.get("audio_seconds", 0) > 0

    def test_text_changes_the_audio(self):
        assert StubSpeechProvider().synthesize("one").data != StubSpeechProvider().synthesize("two").data


class TestRouter:
    def test_offline_returns_stubs(self):
        router = MediaRouter(offline=True)
        assert isinstance(router.image_for(), StubImageProvider)
        assert isinstance(router.speech_for(), StubSpeechProvider)

    def test_missing_base_url_falls_back_to_stub(self):
        router = MediaRouter(offline=False, image_spec=MediaSpec(model="m", base_url=""))
        assert isinstance(router.image_for(), StubImageProvider)


class TestTransport:
    def test_stub_inlines_a_data_uri(self, tmp_path):
        ref = _to_ref(StubImageProvider().generate("a booth"), media_dir=tmp_path, run_id="r1", slug="003-img")
        assert ref["src"].startswith("data:image/png;base64,")
        assert not list(tmp_path.iterdir())  # a stub writes no file

    def test_live_writes_a_served_file(self, tmp_path):
        # A non-stub model_id routes to the file transport, keeping the exported trace lean.
        result = MediaResult("image/png", b"\x89PNG\r\n\x1a\nDATA", "img-model-1b", {"images": 1})
        ref = _to_ref(result, media_dir=tmp_path, run_id="run-1", slug="003-img")
        assert ref["src"].startswith("/file=")
        written = tmp_path / "run-1" / "003-img.png"
        assert written.is_file() and written.read_bytes() == result.data


class TestCapability:
    def test_grant_enforced_through_registry(self):
        reg = default_registry()
        tools = default_tool_registry()  # media stubbed offline by conftest
        critic = reg.agents["rafters-critic"]  # granted image.render / tts.speak
        whisperer = reg.agents["scene-whisperer"]  # tools: []
        out = tools.call("rafters-critic", critic, "image.render", {"prompt": "a mossy booth"})
        assert out["src"].startswith("data:image/png;base64,")
        with pytest.raises(CapabilityViolation):
            tools.call("scene-whisperer", whisperer, "image.render", {"prompt": "x"})


class TestCommentatorFoldsMedia:
    def test_beat_carries_image_and_audio(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "1")
        reg = default_registry()
        tools = ToolRegistry()
        register_media_tools(tools, MediaRouter(offline=True), tmp_path)
        critic = reg.build_agent("rafters-critic", reg.build_router(), tools)
        critic.cast_names = ["scene-whisperer", "rafters-critic"]
        events = (Event(run_id="r", turn=1, kind="world.observed", actor="scene-whisperer", payload={"text": "hums"}),)
        event = critic.act("r", 2, StageProjection(current_scene="the wood"), events)
        assert event is not None and event.kind == "commentary.posted"
        assert event.payload["image"]["src"].startswith("data:image/png;base64,")
        assert event.payload["audio"]["src"].startswith("data:audio/wav;base64,")


@pytest.mark.real_infra
class TestBuildMediaRouter:
    def test_no_backend_gracefully_falls_back_to_stub(self):
        # The deliberate divergence from the strict text path: no media backend → stub,
        # so the commentator's beat survives a no-key run instead of refusing to start.
        assert build_media_router(env={}).offline is True
