"""Application entry point for the Quantum Circuit Playground.

The backend is split into two modules:

* ``core`` — all domain logic (config, the gate whitelist + validation, circuit
  building, the quantum run paths, personas, and the AI explainer dispatch).
* ``api``  — the FastAPI app and its routes, a thin adapter over ``core``.

This module exists so the long-standing entry point ``backend.main:app`` (used by
the Makefile, the preview server, and any tooling) keeps working after the split.
It re-exports the FastAPI ``app`` from ``api`` and, for backward compatibility,
mirrors ``core``'s public names onto this module so ``import main; main.<name>``
and ``from backend.main import <name>`` still resolve.
"""
# Work whether loaded as a package (``backend.main`` via uvicorn) or as a
# top-level module (``import main`` with backend/ on sys.path, e.g. under pytest).
try:
    from . import api, core
except ImportError:  # pragma: no cover - top-level import path
    import api
    import core

# The canonical entry point: `uvicorn backend.main:app` resolves to this.
app = api.app

# Back-compat re-exports: surface core's public symbols on this module so older
# references (`main.validate`, `main.PROVIDERS`, ...) keep working. These are
# convenience aliases bound at import; code that needs to monkeypatch should
# patch the owning module (`core`) directly.
_globals = globals()
for _name in dir(core):
    if not _name.startswith("__"):
        _globals.setdefault(_name, getattr(core, _name))
del _globals, _name
