"""Optional Postgres access layer for the tutor's memory features.

The core playground — building, simulating, and explaining circuits — never
touches a database. This module backs the *memory* features (learner profiles,
study sessions, quizzes, retention tracking, and later pgvector semantic
recall). All of it is optional and degrades gracefully:

* the psycopg driver is imported lazily, so the app still runs if it isn't
  installed (``driver_available()`` is then False);
* if ``QCB_DATABASE_URL`` isn't set, ``configured()`` is False and callers
  should treat memory features as unavailable (a 503 at the HTTP layer);
* even when configured, a down or unreachable database makes ``healthy()``
  return False rather than raising — so a missing DB never crashes a request.

Connections are opened on demand with a short connect timeout, so a down
database fails fast and quietly instead of hanging a request. (A single-worker
dev server doesn't need a connection pool; one can be added later if a real
workload justifies it.)
"""
import os
from contextlib import contextmanager
from pathlib import Path

# psycopg (the driver) is optional. Guard the import so the app runs without it;
# the memory features just report themselves unavailable.
try:
    import psycopg

    _DRIVER_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the driver installed
    psycopg = None  # type: ignore[assignment]
    _DRIVER_AVAILABLE = False

# Seconds to wait for a connection before giving up. Keeps healthy() and any
# query fast when the database is down rather than blocking the request.
CONNECT_TIMEOUT = int(os.getenv("QCB_DB_CONNECT_TIMEOUT", "3"))


def _load_dotenv() -> None:
    """Load .env from the project root into os.environ (real env vars win).

    Mirrors core._load_dotenv so the DB URL is available whether the process is
    started via the Makefile (which exports .env), uvicorn directly, or the
    Alembic CLI (`alembic upgrade head`), which loads none of it on its own.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


# Read on each call (not cached) so tests can monkeypatch the environment.
def database_url() -> str:
    return os.getenv("QCB_DATABASE_URL", "").strip()


def driver_available() -> bool:
    """True if the psycopg driver is importable."""
    return _DRIVER_AVAILABLE


def configured() -> bool:
    """True if a database connection string is set."""
    return bool(database_url())


def available() -> bool:
    """True if memory features *could* work: driver present and URL configured.

    A cheap check (no connection attempt). Use healthy() to confirm the database
    is actually reachable.
    """
    return _DRIVER_AVAILABLE and configured()


@contextmanager
def connection():
    """Open an autocommit connection for the duration of the block.

    Raises RuntimeError if the driver is missing or no URL is set; callers that
    want graceful degradation should check available()/healthy() first rather
    than catching this.
    """
    if not _DRIVER_AVAILABLE:
        raise RuntimeError("psycopg is not installed; install it to use the memory features")
    url = database_url()
    if not url:
        raise RuntimeError("QCB_DATABASE_URL is not set")
    conn = psycopg.connect(url, autocommit=True, connect_timeout=CONNECT_TIMEOUT)
    try:
        yield conn
    finally:
        conn.close()


def healthy() -> bool:
    """True if a live connection can be made and a trivial query succeeds.

    Never raises: returns False for any failure (driver missing, URL unset,
    server down, auth error) so the HTTP layer can turn it into a clean 503.
    """
    if not available():
        return False
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def status() -> dict:
    """A small JSON-able summary for the /health endpoint."""
    return {
        "driver": _DRIVER_AVAILABLE,
        "configured": configured(),
        "healthy": healthy(),
    }
