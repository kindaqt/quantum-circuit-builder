"""Alembic migration environment.

Migrations are hand-written SQL (no ORM metadata), so target_metadata stays
None and autogenerate is unused. The database URL comes from QCB_DATABASE_URL
(via backend.db, which also loads .env) rather than alembic.ini, so the same
config works for local docker, CI, and any deployment.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the backend package importable so we can reuse its .env-aware URL helper.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
import db  # noqa: E402  (path set up just above)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_url = db.database_url()
if not _url:
    raise SystemExit(
        "QCB_DATABASE_URL is not set. Start Postgres with `make db-up` and set the "
        "connection string in .env (see .env.sample) before running migrations."
    )
# We store the URL in psycopg3's native libpq form (postgresql://...), which the
# app's pool reads directly. SQLAlchemy (which drives Alembic) would otherwise
# default to psycopg2 — name psycopg3 explicitly so the one installed driver is used.
_sa_url = _url.replace("postgresql://", "postgresql+psycopg://", 1) if _url.startswith("postgresql://") else _url
config.set_main_option("sqlalchemy.url", _sa_url)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
