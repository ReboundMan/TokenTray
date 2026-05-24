"""Canonical model-id normalization across all token-usage sources.

Different hosts log the same underlying model under different ids:

* Copilot CLI (assistant_usage.properties.model): ``"claude-opus-4.7"``
* Agency (assistant.message.data.model):           ``"claude-opus-4.7-1m-internal"``
* VS Code Copilot Chat (ccreq trace):              ``"GPT-5.5 -> gpt-5.5"``
* OpenAI date-stamped variants:                    ``"gpt-4.1-2025-04-01"``

Without normalization, the Advanced tab's per-model breakdown would show
three rows for what is really one model. :func:`normalize_model` collapses
those into one canonical lower-case id (e.g. ``"claude-opus-4.7"``,
``"gpt-5.5"``, ``"gpt-4.1"``).

The function is deliberately conservative: when an id contains no
recognizable suffix it is returned lower-cased and trimmed but otherwise
unchanged, so unknown future models do not silently mis-bucket.
"""
from __future__ import annotations

import re

# Suffixes that mark internal/preview variants of an underlying public
# model. Order matters: longer suffixes first so e.g. ``-1m-internal``
# is stripped as one piece rather than leaving a trailing ``-1m``.
_INTERNAL_SUFFIXES = (
    "-1m-internal",
    "-internal",
)

# OpenAI sometimes pins to a specific snapshot date, e.g. "gpt-4.1-2025-04-01".
# Strip the trailing date so all snapshots roll up into the parent model id.
_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def normalize_model(raw: str | None) -> str | None:
    """Return the canonical lower-case model id for *raw*.

    Returns ``None`` when *raw* is None or empty/whitespace. Never
    raises: an unrecognized id is returned lower-cased and stripped
    so the Advanced tab's per-model breakdown remains useful even
    for new model families that ship after this code did.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # VS Code logs render the model selection with an alias arrow,
    # e.g. ``"GPT-5.5 -> gpt-5.5"``. Take the right-hand side as
    # the resolved model id.
    if " -> " in s:
        s = s.split(" -> ")[-1].strip()
    s = s.lower()
    for suf in _INTERNAL_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    s = _DATE_SUFFIX_RE.sub("", s)
    return s or None


__all__ = ["normalize_model"]
