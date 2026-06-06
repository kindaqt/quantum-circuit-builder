"""Local sentence embeddings for pgvector semantic recall (Tier 3d).

Wraps sentence-transformers with a lazy-loaded singleton so the model is
loaded only once per process. The first call to embed() or available() that
finds the model not yet loaded will trigger the load (~0.5–2 s on CPU, once).

Graceful degradation
--------------------
* sentence-transformers not installed  → available() returns False; embed()
  returns None; the interaction-storage and recall code paths skip silently.
* Model download fails (offline / first run with no cache) → same: the
  exception is captured in _model_error; we never retry in the same process.

The model is configurable via QCB_EMBED_MODEL (default all-MiniLM-L6-v2,
384 dimensions). If you change the model, also update the migration constant
EMBED_DIMS and re-run ``make migrate`` to create a fresh vector column.
"""
import os
from typing import Any

EMBED_MODEL: str = os.getenv("QCB_EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIMS: int = 384  # dimension for all-MiniLM-L6-v2

# Module-level singleton.  None = not loaded yet; set to an Exception if load
# failed (so we never retry and pay the cold-start cost repeatedly).
_model: Any = None
_model_error: Exception | None = None


def _load_model() -> None:
    """Load the sentence-transformers model on first use (not thread-safe, but
    CPython's GIL makes it safe enough for our single-worker FastAPI app)."""
    global _model, _model_error
    if _model is not None or _model_error is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
        _model = SentenceTransformer(EMBED_MODEL)
    except Exception as exc:  # ImportError, OSError (no network), etc.
        _model_error = exc


def available() -> bool:
    """True iff sentence-transformers is installed and the model loaded."""
    if _model is not None:
        return True
    if _model_error is not None:
        return False
    _load_model()
    return _model is not None


def embed(text: str) -> list[float] | None:
    """Return a normalised ``EMBED_DIMS``-dim float vector, or None if unavailable."""
    if not available():
        return None
    return _model.encode(text, normalize_embeddings=True).tolist()


def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """Embed a list of strings in one pass. Returns None if unavailable."""
    if not available():
        return None
    return _model.encode(texts, normalize_embeddings=True).tolist()
