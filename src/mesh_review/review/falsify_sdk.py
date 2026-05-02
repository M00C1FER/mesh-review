"""Optional OpenAI SDK falsifier adapter.

Provides `make_openai_falsifier()` — a factory that returns a falsifier
function wired to the OpenAI Chat Completions API (or any compatible
endpoint, such as local Ollama with ``base_url``).

Usage
-----
    from mesh_review.review.falsify_sdk import make_openai_falsifier
    from mesh_review import sigma_gate, build_consensus

    falsifier = make_openai_falsifier(model="gpt-4o-mini")
    gate = sigma_gate(consensus, falsifier=falsifier, threshold=0.7)

The ``openai`` package is an *optional* runtime dependency — it is NOT listed
in ``[project].dependencies``.  Install it separately::

    pip install openai>=1

If the package is absent a clear ``ImportError`` is raised at call time with
installation instructions, not at import time, so the rest of mesh-review
continues to work without it.

Compatibility
-------------
Any OpenAI-compatible endpoint works via ``base_url`` + ``api_key``::

    # Ollama (local, no API key)
    falsifier = make_openai_falsifier(
        model="qwen2.5-coder:7b",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )

    # Azure OpenAI
    falsifier = make_openai_falsifier(
        model="gpt-4o",
        base_url="https://<resource>.openai.azure.com/openai/deployments/<deployment>",
        api_key=os.environ["AZURE_OPENAI_KEY"],
    )
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable, Dict, Optional

from .falsify import _parse_falsifier_output, _FALSIFY_PROMPT, _esc
from .consensus import ConsensusFinding

logger = logging.getLogger(__name__)


def make_openai_falsifier(
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 60.0,
    max_tokens: int = 512,
) -> Callable[[str, str], Dict]:
    """Return a falsifier function that uses the OpenAI Chat Completions API.

    Args:
        model:      Model name, e.g. ``"gpt-4o-mini"``, ``"gpt-4o"``.
                    For Ollama use the Ollama model tag, e.g. ``"qwen2.5-coder:7b"``.
        api_key:    API key.  Defaults to the ``OPENAI_API_KEY`` env var.
        base_url:   Override the API base URL for compatible endpoints
                    (Ollama, Azure, LM Studio, …).  ``None`` → OpenAI default.
        timeout:    HTTP request timeout in seconds.
        max_tokens: Maximum tokens to generate.

    Returns:
        A ``(cli: str, prompt: str) → dict`` callable suitable for passing
        to :func:`mesh_review.review.falsify.sigma_gate` as the ``falsifier``
        argument.

    Raises:
        ImportError: If the ``openai`` package is not installed.
    """
    try:
        import openai  # noqa: F401 — checked here, used in inner function
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The 'openai' package is required for make_openai_falsifier(). "
            "Install it with: pip install openai>=1"
        ) from exc

    _api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    def falsifier(cli: str, prompt: str) -> Dict:
        # Late import so the package is only required when this function is called.
        import openai as _openai  # noqa: PLC0415

        client = _openai.OpenAI(
            api_key=_api_key or "no-key",
            base_url=base_url,
            timeout=timeout,
        )
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an adversarial code reviewer. "
                            "Output ONLY a JSON object with keys "
                            "'falsified' (bool), 'confidence' (0.0-1.0), "
                            "and 'rationale' (string). No prose, no fences."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("openai falsifier error for %s: %s", cli, exc)
            return {
                "falsified": False,
                "confidence": 0.0,
                "rationale": f"{cli}: openai error: {exc}",
            }

        raw = response.choices[0].message.content or ""
        parsed = _parse_falsifier_output(raw)
        if parsed is None:
            return {
                "falsified": False,
                "confidence": 0.0,
                "rationale": f"{cli}: unparseable response",
            }
        return {
            "falsified": bool(parsed.get("falsified", False)),
            "confidence": min(1.0, max(0.0, float(parsed.get("confidence", 0.0)))),
            "rationale": str(parsed.get("rationale", ""))[:500],
        }

    return falsifier
