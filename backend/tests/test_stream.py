"""Tests for Tier 3g: POST /explain/stream (SSE streaming endpoint) and the
core.explain_circuit_stream() generator.

The streaming handler for each provider is also unit-tested via the
_stream_* generators — they're exercised with fake HTTP responses so the
tests are deterministic and offline.
"""

import json
import textwrap

import pytest
from fastapi.testclient import TestClient

try:
    from backend.api import app
    from backend import core, db
except ImportError:
    from api import app
    import core
    import db


client = TestClient(app)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_MINIMAL_SPEC = {
    "num_qubits": 1,
    "shots": 128,
    "mode": "sim",
    "gates": [],
}

_FAKE_PROVIDER = {
    "key": "fakekey",
    "models": ["claude-opus-4-7"],
    "default_model": "claude-opus-4-7",
    "label": "Anthropic",
}


def _ai_on(monkeypatch, stream_chunks=("Hello ", "world")):
    """Stub AI as enabled; configure the streaming handler to yield fixed chunks."""
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "provider_ready", lambda n: n == "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {"anthropic": _FAKE_PROVIDER})

    def _fake_stream(messages, system, model, key):
        yield from stream_chunks

    monkeypatch.setattr(core, "_AI_STREAM_PROVIDERS", {"anthropic": _fake_stream})
    # Also stub validate and build_circuit so spec validation passes.
    monkeypatch.setattr(core, "validate", lambda spec: None)


def _ai_off(monkeypatch):
    monkeypatch.setattr(core, "ai_enabled", lambda: False)


def _parse_sse(raw: str) -> list[dict]:
    """Parse a raw SSE body into a list of JSON event payloads."""
    events = []
    for part in raw.split("\n\n"):
        part = part.strip()
        if part.startswith("data: "):
            try:
                events.append(json.loads(part[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# core.explain_circuit_stream unit tests
# ---------------------------------------------------------------------------

class TestExplainCircuitStream:
    """Test the generator function in isolation, without HTTP."""

    def _make_spec(self, **kw):
        from pydantic import BaseModel
        class S(core.CircuitSpec):
            pass
        return S(num_qubits=1, shots=128, mode="sim", gates=[], **kw)

    def test_yields_chunks_from_stream_provider(self, monkeypatch):
        """Stream handler chunks are forwarded verbatim."""
        monkeypatch.setattr(core, "ai_enabled", lambda: True)
        monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
        monkeypatch.setattr(core, "provider_ready", lambda n: True)
        monkeypatch.setattr(core, "PROVIDERS", {"anthropic": _FAKE_PROVIDER})
        monkeypatch.setattr(core, "_AI_STREAM_PROVIDERS", {
            "anthropic": lambda msgs, sys, model, key: iter(["chunk1", "chunk2", "chunk3"])
        })
        spec = self._make_spec()
        chunks = list(core.explain_circuit_stream(spec, provider="anthropic"))
        assert chunks == ["chunk1", "chunk2", "chunk3"]

    def test_fallback_when_no_stream_handler(self, monkeypatch):
        """Provider missing from _AI_STREAM_PROVIDERS → calls regular handler once."""
        monkeypatch.setattr(core, "ai_enabled", lambda: True)
        monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
        monkeypatch.setattr(core, "provider_ready", lambda n: True)
        monkeypatch.setattr(core, "PROVIDERS", {"anthropic": _FAKE_PROVIDER})
        monkeypatch.setattr(core, "_AI_STREAM_PROVIDERS", {})  # no streaming for anyone
        monkeypatch.setattr(core, "_AI_PROVIDERS", {
            "anthropic": lambda msgs, sys, model, key: "full response"
        })
        spec = self._make_spec()
        chunks = list(core.explain_circuit_stream(spec, provider="anthropic"))
        assert chunks == ["full response"]

    def test_raises_403_when_provider_not_ready(self, monkeypatch):
        from fastapi import HTTPException
        monkeypatch.setattr(core, "default_provider", lambda: None)
        monkeypatch.setattr(core, "provider_ready", lambda n: False)
        spec = self._make_spec()
        with pytest.raises(HTTPException) as exc:
            list(core.explain_circuit_stream(spec, provider="anthropic"))
        assert exc.value.status_code == 403

    def test_multiple_chunks_concatenate_correctly(self, monkeypatch):
        """Multiple small chunks are all yielded; consumers concatenate them."""
        monkeypatch.setattr(core, "ai_enabled", lambda: True)
        monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
        monkeypatch.setattr(core, "provider_ready", lambda n: True)
        monkeypatch.setattr(core, "PROVIDERS", {"anthropic": _FAKE_PROVIDER})
        words = ["The", " ", "Hadamard", " ", "gate", "."]
        monkeypatch.setattr(core, "_AI_STREAM_PROVIDERS", {
            "anthropic": lambda *a: iter(words)
        })
        spec = self._make_spec()
        result = "".join(core.explain_circuit_stream(spec, provider="anthropic"))
        assert result == "The Hadamard gate."


# ---------------------------------------------------------------------------
# /explain/stream endpoint tests
# ---------------------------------------------------------------------------

class TestExplainStreamEndpoint:
    def test_returns_text_event_stream(self, monkeypatch):
        """Content-Type must be text/event-stream."""
        _ai_on(monkeypatch)
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")

    def test_delta_events_arrive(self, monkeypatch):
        """Each chunk from the stream handler becomes a data: {"delta": "..."} event."""
        _ai_on(monkeypatch, stream_chunks=["Hello ", "world"])
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        events = _parse_sse(res.text)
        delta_events = [e for e in events if "delta" in e]
        assert len(delta_events) == 2
        assert delta_events[0]["delta"] == "Hello "
        assert delta_events[1]["delta"] == "world"

    def test_done_event_is_last(self, monkeypatch):
        """The stream ends with a done event containing provider/model/persona."""
        _ai_on(monkeypatch, stream_chunks=["hi"])
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        events = _parse_sse(res.text)
        assert events, "no events parsed"
        last = events[-1]
        assert last.get("done") is True
        assert "provider" in last
        assert "model" in last
        assert "persona" in last

    def test_done_event_has_correct_provider_and_model(self, monkeypatch):
        _ai_on(monkeypatch, stream_chunks=["x"])
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        last = _parse_sse(res.text)[-1]
        assert last["provider"] == "anthropic"
        assert last["model"] == "claude-opus-4-7"

    def test_403_when_ai_disabled(self, monkeypatch):
        _ai_off(monkeypatch)
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        assert res.status_code == 403

    def test_422_unknown_persona(self, monkeypatch):
        _ai_on(monkeypatch)
        spec = {**_MINIMAL_SPEC, "persona": "totally_fake"}
        res = client.post("/explain/stream", json=spec)
        assert res.status_code == 422

    def test_422_unknown_provider(self, monkeypatch):
        _ai_on(monkeypatch)
        spec = {**_MINIMAL_SPEC, "provider": "nonexistent_provider"}
        res = client.post("/explain/stream", json=spec)
        assert res.status_code == 422

    def test_422_unknown_model(self, monkeypatch):
        _ai_on(monkeypatch)
        spec = {**_MINIMAL_SPEC, "provider": "anthropic", "model": "gpt-999-fake"}
        res = client.post("/explain/stream", json=spec)
        assert res.status_code == 422

    def test_422_question_too_long(self, monkeypatch):
        _ai_on(monkeypatch)
        spec = {**_MINIMAL_SPEC, "question": "x" * (core.MAX_QUESTION_CHARS + 1)}
        res = client.post("/explain/stream", json=spec)
        assert res.status_code == 422

    def test_deltas_and_done_in_correct_order(self, monkeypatch):
        """Delta events must all come before the done event."""
        _ai_on(monkeypatch, stream_chunks=["a", "b", "c"])
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        events = _parse_sse(res.text)
        done_idx = next((i for i, e in enumerate(events) if e.get("done")), None)
        assert done_idx is not None
        delta_indices = [i for i, e in enumerate(events) if "delta" in e]
        assert all(i < done_idx for i in delta_indices), "all deltas must precede the done event"

    def test_no_db_interaction_without_learner_id(self, monkeypatch):
        """No DB calls when no learner_id is supplied."""
        _ai_on(monkeypatch, stream_chunks=["hi"])
        calls = []
        monkeypatch.setattr(db, "available", lambda: True)
        # If _store_interaction is called it would have to call memory.save_interaction —
        # monkeypatch that to record the call.
        import api as _api
        monkeypatch.setattr(_api, "_store_interaction", lambda *a, **k: calls.append(1))
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        assert res.status_code == 200
        assert calls == [], "_store_interaction should not be called without learner_id"

    def test_single_chunk_provider_still_emits_delta_and_done(self, monkeypatch):
        """Even a single-chunk (non-streaming fallback) path produces a delta + done."""
        _ai_on(monkeypatch, stream_chunks=["everything at once"])
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        events = _parse_sse(res.text)
        deltas = [e for e in events if "delta" in e]
        dones = [e for e in events if e.get("done")]
        assert len(deltas) >= 1
        assert len(dones) == 1

    def test_error_event_emitted_when_stream_raises(self, monkeypatch):
        """If the streaming generator raises mid-flight the response contains an error event."""
        monkeypatch.setattr(core, "ai_enabled", lambda: True)
        monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
        monkeypatch.setattr(core, "provider_ready", lambda n: n == "anthropic")
        monkeypatch.setattr(core, "PROVIDERS", {"anthropic": _FAKE_PROVIDER})
        monkeypatch.setattr(core, "validate", lambda spec: None)

        def _exploding_stream(messages, system, model, key):
            yield "partial text"
            raise RuntimeError("network dropped")

        monkeypatch.setattr(core, "_AI_STREAM_PROVIDERS", {"anthropic": _exploding_stream})
        res = client.post("/explain/stream", json=_MINIMAL_SPEC)
        # The response must complete with a 200 (headers were already sent);
        # the body must contain an error event.
        assert res.status_code == 200
        events = _parse_sse(res.text)
        error_events = [e for e in events if "error" in e]
        assert error_events, "expected at least one error event in the SSE body"
        assert "network dropped" in error_events[0]["error"]


# ---------------------------------------------------------------------------
# _stream_* unit tests (generator functions, no HTTP)
# ---------------------------------------------------------------------------

class TestStreamProviderGenerators:
    """Test the per-provider streaming generators with fake HTTP data.
    These are unit tests; they monkeypatch urllib so no real network is used."""

    def _fake_urlopen(self, monkeypatch, lines: list[str]):
        """Return a context manager that yields encoded lines as if from urlopen."""
        import io

        class _FakeResp:
            def __init__(self, lines):
                self._data = [l.encode("utf-8") for l in lines]

            def __enter__(self): return self
            def __exit__(self, *a): return False
            def __iter__(self): return iter(self._data)

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=60: _FakeResp(lines))

    # Gemini
    def test_stream_gemini_yields_text(self, monkeypatch):
        sse_line = lambda text: (
            "data: " + json.dumps({
                "candidates": [{"content": {"parts": [{"text": text}]}}]
            }) + "\n"
        )
        self._fake_urlopen(monkeypatch, [sse_line("Hello "), sse_line("world"), "\n"])
        chunks = list(core._stream_gemini(
            [{"role": "user", "content": "hi"}], "system", "gemini-pro", "key"
        ))
        assert "".join(chunks) == "Hello world"

    def test_stream_gemini_skips_non_data_lines(self, monkeypatch):
        sse_line = json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
        self._fake_urlopen(monkeypatch, [
            "\n",  # blank line
            ": this is a comment\n",
            f"data: {sse_line}\n",
        ])
        chunks = list(core._stream_gemini(
            [{"role": "user", "content": "hi"}], "sys", "m", "k"
        ))
        assert chunks == ["ok"]

    # OpenAI
    def test_stream_openai_yields_content_deltas(self, monkeypatch):
        def sse(content):
            return "data: " + json.dumps({
                "choices": [{"delta": {"content": content}}]
            }) + "\n"
        self._fake_urlopen(monkeypatch, [sse("foo"), sse(" bar"), "data: [DONE]\n"])
        chunks = list(core._stream_openai(
            [{"role": "user", "content": "hi"}], "sys", "gpt-4", "k"
        ))
        assert "".join(chunks) == "foo bar"

    def test_stream_openai_stops_at_done(self, monkeypatch):
        sse = lambda c: "data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) + "\n"
        self._fake_urlopen(monkeypatch, [
            sse("before"), "data: [DONE]\n", sse("after"),
        ])
        chunks = list(core._stream_openai(
            [{"role": "user", "content": "q"}], "sys", "m", "k"
        ))
        assert "".join(chunks) == "before"

    # Llama
    def test_stream_llama_yields_content_from_ndjson(self, monkeypatch):
        lines = [
            json.dumps({"message": {"content": "Hello "}, "done": False}) + "\n",
            json.dumps({"message": {"content": "world"}, "done": True}) + "\n",
        ]
        self._fake_urlopen(monkeypatch, lines)
        chunks = list(core._stream_llama(
            [{"role": "user", "content": "hi"}], "sys", "llama3", ""
        ))
        assert "".join(chunks) == "Hello world"

    def test_stream_llama_stops_when_done_true(self, monkeypatch):
        lines = [
            json.dumps({"message": {"content": "stop here"}, "done": True}) + "\n",
            json.dumps({"message": {"content": "never"}, "done": False}) + "\n",
        ]
        self._fake_urlopen(monkeypatch, lines)
        chunks = list(core._stream_llama(
            [{"role": "user", "content": "q"}], "sys", "m", ""
        ))
        assert "".join(chunks) == "stop here"
