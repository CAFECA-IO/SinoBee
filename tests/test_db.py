"""Database browser: SQL guard, connection store, SQLite end to end, redaction.
PostgreSQL / MySQL run when SINOBEE_TEST_PG / SINOBEE_TEST_MYSQL hold a DSN (e.g. in CI)."""
import asyncio
import json
import os
import sqlite3

import pytest

from sinobee.config import Config
from sinobee.db import DbError, DbService, classify, parse_dsn, redact_dsn


@pytest.mark.parametrize("sql,kind,expect", [
    ("select * from t", "postgres", "read"),
    ("WITH a AS (SELECT 1) SELECT * FROM a", "postgres", "read"),
    ("select a from t union select b from u", "mysql", "read"),
    ("explain select * from t", "postgres", "read"),
    ("show tables", "mysql", "read"),
    ("pragma table_info(t)", "sqlite", "read"),
    ("select 1; drop table t", "postgres", "invalid"),
    ("selec * frm", "postgres", "invalid"),
    ("update t set a = 1", "mysql", "write"),
    ("with x as (delete from t returning *) select * from x", "postgres", "write"),
    ("select * from t for update", "postgres", "write"),
    ("select pg_terminate_backend(42)", "postgres", "write"),
    ("explain analyze delete from t", "postgres", "write"),
    ("select * into t2 from t", "postgres", "write"),
    ("pragma user_version = 3", "sqlite", "write"),
    ("attach database 'x.db' as x", "sqlite", "write"),
    ("set role admin", "postgres", "write"),
])
def test_guard(sql, kind, expect):
    assert classify(sql, kind).kind == expect


def test_dsn_parse_and_redact():
    d = parse_dsn("看一下 postgres://ann:p%40ss-123@db.local:6543/shop?sslmode=require")
    assert d == {"kind": "postgres", "user": "ann", "password": "p%40ss-123", "host": "db.local", "port": 6543,
                 "database": "shop", "sslmode": "require"}
    assert redact_dsn("mysql://root:hunter22@10.0.0.5/erp") == "mysql://root:••••@10.0.0.5/erp"
    assert parse_dsn("no dsn here") is None


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("SINOBEE_HOME", str(tmp_path))
    return DbService(Config({}, tmp_path))


def test_store_keeps_password_out_of_settings(svc, tmp_path):
    c = svc.store.save({"kind": "postgres", "host": "db.local", "database": "shop", "user": "ann", "name": "商店"},
                       password="Sup3r-Secret!")
    assert c.id == "shop" and svc.store.password(c) == "Sup3r-Secret!"
    assert "Sup3r-Secret!" not in (tmp_path / "db" / "connections.json").read_text()
    assert "Sup3r-Secret!" not in json.dumps(svc.listing())
    assert oct(os.stat(tmp_path / "secrets.json").st_mode)[-3:] == "600"
    with pytest.raises(ValueError):
        svc.store.save({"kind": "oracle", "host": "x"})
    assert svc.store.delete(c.id) and svc.store.password(c) is None


def test_sqlite_end_to_end(svc, tmp_path):
    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.executescript("create table items(id integer primary key, name text, price real);"
                     "insert into items(name, price) values ('a', 1.5), ('b', 2.5), ('c', null);")
    db.commit()
    db.close()
    c = svc.store.save({"kind": "sqlite", "path": str(path)})

    async def run():
        tree = await svc.schema_tree(c.id)
        assert tree["schemas"][0]["tables"][0]["name"] == "items"
        info = await svc.table_info(c.id, "main", "items")
        assert [x["name"] for x in info["columns"]] == ["id", "name", "price"] and info["columns"][0]["pk"]
        p = await svc.preview(c.id, "main", "items", sort="price", desc=True,
                              filters=[{"column": "price", "op": "not null"}])
        assert p["rows"] == [[2, "b", 2.5], [1, "a", 1.5]] and p["total"] == 2
        with pytest.raises(DbError):
            await svc.preview(c.id, "main", "items", sort="price; drop table items")
        r = await svc.query(c.id, "select count(*) from items")
        assert r["rows"] == [[3]]
        with pytest.raises(DbError, match="唯讀"):
            await svc.query(c.id, "delete from items")
        svc.store.update_meta(c.id, allow_write=True)
        with pytest.raises(DbError, match="確認"):
            await svc.query(c.id, "delete from items where id = 3")
        r = await svc.query(c.id, "delete from items where id = 3", allow_write=True)
        assert r["affected"] == 1 and r["write"]
        assert svc.store.history(c.id)[0]["sql"].startswith("delete")
    asyncio.run(run())


def test_sqlite_readonly_session_refuses_writes_without_guard(svc, tmp_path):
    from sinobee.db.drivers import Session
    path = tmp_path / "x.db"
    sqlite3.connect(path).executescript("create table t(a int); insert into t values (1);")
    c = svc.store.save({"kind": "sqlite", "path": str(path)})
    with Session(c, None) as s, pytest.raises(DbError, match="唯讀|readonly"):
        s.run("delete from t")


def test_agent_redacts_secrets(tmp_path, monkeypatch):
    from sinobee.agent.loop import Agent
    monkeypatch.setenv("SINOBEE_HOME", str(tmp_path))
    cfg = Config({}, tmp_path)
    cfg.secrets.set_secret("db:shop", "Sup3r-Secret!")
    a = Agent.__new__(Agent)
    a.config = cfg
    out = a._redact_secrets("密碼是 Sup3r-Secret! 連 mysql://root:other-pass@h/db")
    assert "Sup3r-Secret!" not in out and "other-pass" not in out


@pytest.mark.parametrize("env", ["SINOBEE_TEST_PG", "SINOBEE_TEST_MYSQL"])
def test_server_databases_read_only(svc, env):
    dsn = os.environ.get(env)
    if not dsn:
        pytest.skip(f"{env} not set")
    d = parse_dsn(dsn)
    pw = d.pop("password")
    c = svc.store.save(d, password=pw)

    async def run():
        info = await svc.test(d, pw)
        assert info["ok"]
        r = await svc.query(c.id, "select 1 as one")
        assert r["rows"] == [[1]]
        from sinobee.db.drivers import Session
        with Session(c, pw) as s, pytest.raises(DbError, match="唯讀"):
            s.run("create table sinobee_should_fail(a int)")
    asyncio.run(run())
