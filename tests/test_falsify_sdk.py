"""Tests for the OpenAI SDK falsifier adapter (mesh_review.review.falsify_sdk).

All tests use mocks — no real API calls are made and the ``openai`` package
does not need to be installed in CI.
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to simulate the openai package being present or absent
# ---------------------------------------------------------------------------


def _make_mock_openai(response_text: str, raise_exc: Exception | None = None):
    """Return a minimal mock of the openai module + client for unit tests."""
    mock_openai = MagicMock(name="openai")

    mock_choice = MagicMock()
    mock_choice.message.content = response_text

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    if raise_exc is not None:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = raise_exc
    else:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion

    mock_openai.OpenAI.return_value = mock_client
    return mock_openai, mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_make_openai_falsifier_returns_callable():
    """make_openai_falsifier() returns a callable without importing openai at
    import time — only when the returned function is called."""
    mock_openai, _ = _make_mock_openai('{"falsified": false, "confidence": 0.0, "rationale": "ok"}')
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier(model="gpt-4o-mini")
    assert callable(f)


def test_make_openai_falsifier_valid_response():
    """A well-formed JSON response is parsed and returned correctly."""
    mock_openai, _ = _make_mock_openai(
        '{"falsified": true, "confidence": 0.9, "rationale": "MD5 is fine here"}'
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier(model="gpt-4o-mini")
        result = f("claude", "some falsification prompt")

    assert result["falsified"] is True
    assert result["confidence"] == 0.9
    assert "MD5" in result["rationale"]


def test_make_openai_falsifier_not_falsified():
    """A 'falsified: false' response is propagated correctly."""
    mock_openai, _ = _make_mock_openai(
        '{"falsified": false, "confidence": 0.1, "rationale": "genuine issue"}'
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier()
        result = f("gemini", "prompt")

    assert result["falsified"] is False
    assert result["confidence"] == 0.1


def test_make_openai_falsifier_clamps_confidence_above_one():
    """confidence > 1.0 from the model is clamped to 1.0."""
    mock_openai, _ = _make_mock_openai(
        '{"falsified": true, "confidence": 2.5, "rationale": "y"}'
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier()
        result = f("claude", "p")
    assert result["confidence"] == 1.0


def test_make_openai_falsifier_clamps_confidence_below_zero():
    """confidence < 0.0 from the model is clamped to 0.0."""
    mock_openai, _ = _make_mock_openai(
        '{"falsified": false, "confidence": -0.5, "rationale": "y"}'
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier()
        result = f("claude", "p")
    assert result["confidence"] == 0.0


def test_make_openai_falsifier_unparseable_response():
    """A non-JSON response returns a non-falsifying result, not an exception."""
    mock_openai, _ = _make_mock_openai("Sorry, I cannot help with that.")
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier()
        result = f("claude", "p")
    assert result["falsified"] is False
    assert "unparseable" in result["rationale"]


def test_make_openai_falsifier_api_exception():
    """When the API call raises, returns a non-falsifying error result."""
    mock_openai, _ = _make_mock_openai("", raise_exc=RuntimeError("network error"))
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier()
        result = f("claude", "p")
    assert result["falsified"] is False
    assert "openai error" in result["rationale"]


def test_make_openai_falsifier_missing_package_raises_import_error():
    """ImportError with helpful message when openai is not installed."""
    # Temporarily hide the openai module
    saved = sys.modules.pop("openai", None)
    # Also remove falsify_sdk from the module cache so the import check re-runs
    sys.modules.pop("mesh_review.review.falsify_sdk", None)
    try:
        import importlib
        import mesh_review.review.falsify_sdk as sdk_module
        importlib.reload(sdk_module)
        try:
            sdk_module.make_openai_falsifier()
        except ImportError as exc:
            assert "pip install openai" in str(exc)
        else:
            # openai may be installed in this environment; skip gracefully
            pass
    finally:
        if saved is not None:
            sys.modules["openai"] = saved


def test_make_openai_falsifier_fenced_response():
    """A response wrapped in ```json fences is still parsed correctly."""
    mock_openai, _ = _make_mock_openai(
        '```json\n{"falsified": true, "confidence": 0.8, "rationale": "ok"}\n```'
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from mesh_review.review.falsify_sdk import make_openai_falsifier
        f = make_openai_falsifier()
        result = f("claude", "prompt")
    assert result["falsified"] is True
    assert result["confidence"] == 0.8
