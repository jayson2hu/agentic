from pathlib import Path

from alembic import command
from alembic.config import Config
from judgment_graph.persist import models
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from sqlalchemy import create_engine, insert, inspect, select, text

ROOT = Path(__file__).resolve().parents[3]
NEW_TABLES = {"content_judgment_state", "judgment_outbox"}


def test_incremental_migration_and_downgrade_preserve_existing_products(tmp_path: Path):
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    engine = create_engine(url)
    old_tables = [table for name, table in models.metadata.tables.items() if name not in NEW_TABLES]
    models.metadata.create_all(engine, tables=old_tables)
    with engine.begin() as conn:
        conn.execute(insert(models.content_translations).values(
            content_id=7, lang="en", fields={"title": "existing"}, model="fake-model"
        ))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.stamp(config, "20260530_0001")
    command.upgrade(config, "head")
    assert NEW_TABLES <= set(inspect(engine).get_table_names())
    state_columns = {column["name"] for column in inspect(engine).get_columns(
        "content_judgment_state"
    )}
    assert {"source_run_id", "source_revision"} <= state_columns
    outbox_columns = {
        column["name"] for column in inspect(engine).get_columns("judgment_outbox")
    }
    assert {
        "event_id",
        "attempt_count",
        "sent_at",
        "acked_at",
        "dead_lettered_at",
        "last_error",
    } <= outbox_columns
    repo = SqlAlchemyJudgmentRepository(engine)
    repo.set_status(9, "WAIT_SCORE")
    repo.mark_completed(9)
    assert len(repo.outbox()) == 1
    # Historical product rows have no trustworthy historical in-memory lifecycle to backfill.
    assert repo.get_status(7) is None
    command.downgrade(config, "20260530_0001")
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert conn.execute(select(models.content_translations.c.fields)).scalar_one() == {"title": "existing"}
    engine.dispose()


def test_postgresql_incremental_sql_has_only_new_owned_tables():
    from io import StringIO

    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    config.set_main_option("sqlalchemy.url", "postgresql+psycopg://localhost/offline")
    command.upgrade(config, "20260530_0001:head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE content_judgment_state" in sql
    assert "CREATE TABLE judgment_outbox" in sql
    assert "content_id BIGINT NOT NULL" in sql
    assert "content_id BIGSERIAL" not in sql
    assert "payload JSONB NOT NULL" in sql
    assert "UNIQUE (event_type, content_id)" in sql
    assert "source_revision INTEGER DEFAULT '0' NOT NULL" in sql
    assert "CREATE TABLE verticals" not in sql
    assert "CREATE TABLE content_items" not in sql
    output.seek(0)
    output.truncate(0)
    command.downgrade(config, "20260912_0002:20260530_0001", sql=True)
    sql = output.getvalue()
    assert "DROP TABLE judgment_outbox" in sql
    assert "DROP TABLE content_judgment_state" in sql
    assert "DROP TABLE content_vertical_scores" not in sql


def test_delivery_migration_backfills_stable_event_id(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'delivery-migration.db'}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url)
    old_tables = [
        table for name, table in models.metadata.tables.items() if name not in NEW_TABLES
    ]
    models.metadata.create_all(engine, tables=old_tables)
    command.stamp(config, "20260530_0001")
    command.upgrade(config, "20260916_0003")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO judgment_outbox "
                "(event_type, content_id, source_revision, payload, created_at) "
                "VALUES (:event_type, :content_id, :source_revision, :payload, :created_at)"
            ),
            {
                "event_type": "content.completed",
                "content_id": 77,
                "source_revision": 3,
                "payload": '{"content_id": 77}',
                "created_at": "2026-09-16 00:00:00",
            },
        )
    command.upgrade(config, "head")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT event_id, attempt_count, sent_at, acked_at, "
                "dead_lettered_at FROM judgment_outbox"
            )
        ).mappings().one()
    assert row == {
        "event_id": "content.completed:77-r3",
        "attempt_count": 0,
        "sent_at": None,
        "acked_at": None,
        "dead_lettered_at": None,
    }
    engine.dispose()
