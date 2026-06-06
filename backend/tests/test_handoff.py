"""Tests for Tier 3f: persona-handoff endpoint and core.persona_handoff().

The handoff generates a short in-character farewell from the outgoing persona and
a short in-character greeting from the incoming one. Both are LLM calls so the
tests monkeypatch the handler and validate the routing and system-prompt assembly
without hitting a real model.
"""

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

try:
    from backend.api import app
    from backend import core
except ImportError:
    from api import app
    import core


client = TestClient(app)

# ---------------------------------------------------------------------------
# Test-double helpers
# ---------------------------------------------------------------------------

_FAKE_PROVIDER = {
    "key": "fakekey",
    "models": ["claude-opus-4-7"],
    "default_model": "claude-opus-4-7",
    "label": "Anthropic",
}


def _ai_on(monkeypatch, handler=None):
    """Stub AI as enabled with a single fake Anthropic provider."""
    if handler is None:
        handler = lambda msgs, sys, model, key: "generated text"  # noqa: E731
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "provider_ready", lambda n: n == "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {"anthropic": _FAKE_PROVIDER})
    monkeypatch.setattr(core, "_AI_PROVIDERS", {"anthropic": handler})


def _ai_off(monkeypatch):
    """Stub AI as fully disabled."""
    monkeypatch.setattr(core, "ai_enabled", lambda: False)
    monkeypatch.setattr(core, "default_provider", lambda: None)
    monkeypatch.setattr(core, "provider_ready", lambda n: False)


# ---------------------------------------------------------------------------
# core.persona_handoff unit tests
# ---------------------------------------------------------------------------

class TestCorePersonaHandoff:
    def test_same_persona_returns_empty_no_lm_call(self, monkeypatch):
        """No-op switch (same key both sides) returns empty strings without an LLM call."""
        calls = []
        _ai_on(monkeypatch, handler=lambda *a, **k: (calls.append(1) or "oops"))
        result = core.persona_handoff("professor", "professor")
        assert result == {"farewell": "", "greeting": ""}
        assert calls == [], "should make zero LLM calls for same-persona handoff"

    def test_unknown_from_persona_raises_422(self, monkeypatch):
        _ai_on(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            core.persona_handoff("nonexistent_persona", "professor")
        assert exc.value.status_code == 422

    def test_unknown_to_persona_raises_422(self, monkeypatch):
        _ai_on(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            core.persona_handoff("professor", "nonexistent_persona")
        assert exc.value.status_code == 422

    def test_provider_not_ready_raises_403(self, monkeypatch):
        monkeypatch.setattr(core, "ai_enabled", lambda: True)
        monkeypatch.setattr(core, "default_provider", lambda: None)
        monkeypatch.setattr(core, "provider_ready", lambda n: False)
        with pytest.raises(HTTPException) as exc:
            core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert exc.value.status_code == 403

    def test_makes_exactly_two_lm_calls(self, monkeypatch):
        """Handoff makes one farewell call and one greeting call — no more, no less."""
        calls = []
        _ai_on(monkeypatch, handler=lambda msgs, sys, model, key: (calls.append({"sys": sys}) or "response"))
        result = core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert len(calls) == 2
        assert result["farewell"] == "response"
        assert result["greeting"] == "response"

    def test_farewell_uses_from_voice(self, monkeypatch):
        """The farewell call's system prompt embeds the outgoing persona's voice."""
        calls = []
        _ai_on(monkeypatch, handler=lambda msgs, sys, model, key: (calls.append(sys) or "ok"))
        core.persona_handoff("professor", "tony_stark", provider="anthropic")
        # First call → farewell; its system prompt should contain professor's voice
        prof_voice_snippet = core.PERSONAS["professor"]["voice"][:40]
        assert prof_voice_snippet in calls[0]

    def test_greeting_uses_to_voice(self, monkeypatch):
        """The greeting call's system prompt embeds the incoming persona's voice."""
        calls = []
        _ai_on(monkeypatch, handler=lambda msgs, sys, model, key: (calls.append(sys) or "ok"))
        core.persona_handoff("professor", "tony_stark", provider="anthropic")
        # Second call → greeting; its system prompt should contain Tony Stark's voice
        stark_voice_snippet = core.PERSONAS["tony_stark"]["voice"][:40]
        assert stark_voice_snippet in calls[1]

    def test_farewell_names_recipient_in_system(self, monkeypatch):
        """The farewell system prompt mentions the incoming persona's name."""
        calls = []
        _ai_on(monkeypatch, handler=lambda msgs, sys, model, key: (calls.append(sys) or "ok"))
        core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert "Tony Stark" in calls[0]

    def test_greeting_names_sender_in_system(self, monkeypatch):
        """The greeting system prompt mentions the outgoing persona's name."""
        calls = []
        _ai_on(monkeypatch, handler=lambda msgs, sys, model, key: (calls.append(sys) or "ok"))
        core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert "The Professor" in calls[1]

    def test_output_hard_trimmed(self, monkeypatch):
        """Very long model replies are hard-trimmed at 2 × _HANDOFF_MAX_CHARS."""
        long_text = "x" * 2000
        _ai_on(monkeypatch, handler=lambda *a, **k: long_text)
        result = core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert len(result["farewell"]) <= core._HANDOFF_MAX_CHARS * 2
        assert len(result["greeting"]) <= core._HANDOFF_MAX_CHARS * 2

    def test_strips_leading_trailing_whitespace(self, monkeypatch):
        _ai_on(monkeypatch, handler=lambda *a, **k: "  hello world  ")
        result = core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert result["farewell"] == "hello world"
        assert result["greeting"] == "hello world"

    def test_uses_default_model_when_none_given(self, monkeypatch):
        """When no model is specified, the provider's default model is used."""
        used = {}
        def handler(msgs, sys, model, key):
            used["model"] = model
            return "ok"
        _ai_on(monkeypatch, handler=handler)
        core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert used["model"] == _FAKE_PROVIDER["default_model"]

    def test_empty_lm_response_returns_empty_strings(self, monkeypatch):
        """An LLM that returns an empty string should not crash — result is empty strings."""
        _ai_on(monkeypatch, handler=lambda *a, **k: "")
        result = core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert result["farewell"] == ""
        assert result["greeting"] == ""

    def test_whitespace_only_lm_response_returns_empty_strings(self, monkeypatch):
        """A whitespace-only LLM reply is stripped to empty, not stored as blank padding."""
        _ai_on(monkeypatch, handler=lambda *a, **k: "   \n\t  ")
        result = core.persona_handoff("professor", "tony_stark", provider="anthropic")
        assert result["farewell"] == ""
        assert result["greeting"] == ""


# ---------------------------------------------------------------------------
# /persona/handoff endpoint tests
# ---------------------------------------------------------------------------

class TestHandoffEndpoint:
    def test_returns_farewell_and_greeting(self, monkeypatch):
        _ai_on(monkeypatch)
        monkeypatch.setattr(
            core, "persona_handoff",
            lambda f, t, p, m, **kw: {"farewell": "goodbye!", "greeting": "hello!"},
        )
        res = client.post(
            "/persona/handoff",
            json={"from_persona": "professor", "to_persona": "tony_stark"},
        )
        assert res.status_code == 200
        assert res.json() == {"farewell": "goodbye!", "greeting": "hello!"}

    def test_same_persona_empty_strings(self, monkeypatch):
        _ai_on(monkeypatch)
        monkeypatch.setattr(
            core, "persona_handoff",
            lambda f, t, p, m, **kw: {"farewell": "", "greeting": ""},
        )
        res = client.post(
            "/persona/handoff",
            json={"from_persona": "professor", "to_persona": "professor"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["farewell"] == ""
        assert data["greeting"] == ""

    def test_403_when_ai_disabled(self, monkeypatch):
        _ai_off(monkeypatch)
        res = client.post(
            "/persona/handoff",
            json={"from_persona": "professor", "to_persona": "tony_stark"},
        )
        assert res.status_code == 403

    def test_422_unknown_from_persona(self, monkeypatch):
        _ai_on(monkeypatch)
        res = client.post(
            "/persona/handoff",
            json={"from_persona": "totally_fake", "to_persona": "professor"},
        )
        assert res.status_code == 422

    def test_422_unknown_to_persona(self, monkeypatch):
        _ai_on(monkeypatch)
        res = client.post(
            "/persona/handoff",
            json={"from_persona": "professor", "to_persona": "totally_fake"},
        )
        assert res.status_code == 422

    def test_422_unknown_provider(self, monkeypatch):
        _ai_on(monkeypatch)
        res = client.post(
            "/persona/handoff",
            json={
                "from_persona": "professor",
                "to_persona": "tony_stark",
                "provider": "nonexistent_provider",
            },
        )
        assert res.status_code == 422

    def test_422_unknown_model(self, monkeypatch):
        _ai_on(monkeypatch)
        res = client.post(
            "/persona/handoff",
            json={
                "from_persona": "professor",
                "to_persona": "tony_stark",
                "provider": "anthropic",
                "model": "gpt-totally-not-real",
            },
        )
        assert res.status_code == 422

    def test_provider_and_model_forwarded_to_core(self, monkeypatch):
        """The endpoint passes validated provider + model down to core.persona_handoff."""
        received = {}
        _ai_on(monkeypatch)
        monkeypatch.setattr(
            core, "persona_handoff",
            lambda f, t, p, m, **kw: (received.update({"provider": p, "model": m}) or {"farewell": "", "greeting": ""}),
        )
        client.post(
            "/persona/handoff",
            json={
                "from_persona": "professor",
                "to_persona": "tony_stark",
                "provider": "anthropic",
                "model": "claude-opus-4-7",
            },
        )
        assert received["provider"] == "anthropic"
        assert received["model"] == "claude-opus-4-7"

    def test_default_provider_used_when_none_sent(self, monkeypatch):
        """When the client omits provider, the server's default is used."""
        received = {}
        _ai_on(monkeypatch)
        monkeypatch.setattr(
            core, "persona_handoff",
            lambda f, t, p, m, **kw: (received.update({"provider": p}) or {"farewell": "", "greeting": ""}),
        )
        client.post(
            "/persona/handoff",
            json={"from_persona": "professor", "to_persona": "tony_stark"},
        )
        assert received["provider"] == "anthropic"

    def test_history_forwarded_to_core(self, monkeypatch):
        """Conversation history sent by the client is cleaned and passed to core."""
        received = {}
        _ai_on(monkeypatch)
        monkeypatch.setattr(
            core, "persona_handoff",
            lambda f, t, p, m, **kw: (received.update({"history": kw.get("history")}) or {"farewell": "", "greeting": ""}),
        )
        client.post(
            "/persona/handoff",
            json={
                "from_persona": "professor",
                "to_persona": "tony_stark",
                "history": [
                    {"role": "user", "content": "What is superposition?"},
                    {"role": "assistant", "content": "Great question — superposition means..."},
                ],
            },
        )
        assert received["history"] is not None
        assert len(received["history"]) == 2
        assert received["history"][0]["role"] == "user"

    def test_history_absent_passes_none_to_core(self, monkeypatch):
        """When the client omits history, core receives None."""
        received = {}
        _ai_on(monkeypatch)
        monkeypatch.setattr(
            core, "persona_handoff",
            lambda f, t, p, m, **kw: (received.update({"history": kw.get("history")}) or {"farewell": "", "greeting": ""}),
        )
        client.post(
            "/persona/handoff",
            json={"from_persona": "professor", "to_persona": "tony_stark"},
        )
        assert received["history"] is None
