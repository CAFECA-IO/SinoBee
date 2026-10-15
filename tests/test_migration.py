"""Database migration (structure + data), SSH tunnels and CA settings.

SQLite-based tests always run. MySQL / PostgreSQL round trips run when servers are given:
  SINOBEE_TEST_MYSQL=mysql://user:pass@127.0.0.1:3306      (needs CREATE/DROP DATABASE)
  SINOBEE_TEST_PG=postgres://user@127.0.0.1:5432           (needs CREATE/DROP DATABASE)
"""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
import uuid

import pytest

from sinobee.config import Config
from sinobee.db import DbService, parse_dsn

SQLITE_FIXTURE = """
CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER REFERENCES categories(id), name TEXT NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR(12) NOT NULL UNIQUE, name TEXT NOT NULL, price DECIMAL(10,2) DEFAULT 0,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL, created DATETIME DEFAULT CURRENT_TIMESTAMP, img BLOB);
CREATE INDEX products_cat ON products(category_id);
CREATE TABLE stock (product_id INTEGER NOT NULL, warehouse TEXT NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY (product_id, warehouse));
CREATE TABLE notes (body TEXT, at TEXT);
CREATE VIEW cheap AS SELECT id, name FROM products WHERE price < 10;
INSERT INTO categories (id, parent_id, name) VALUES (1, NULL, '全部'), (2, 1, '飲料');
INSERT INTO notes VALUES ('第一筆 ''quote''', '2024-01-01'), (NULL, NULL);
"""


@pytest.fixture
def svc(tmp_path):
    return DbService(Config({}, tmp_path))


@pytest.fixture
def source(svc, tmp_path):
    path = tmp_path / "shop.db"
    con = sqlite3.connect(path)
    con.executescript(SQLITE_FIXTURE)
    con.executemany("INSERT INTO products (id, sku, name, price, category_id, created, img) VALUES (?,?,?,?,?,?,?)",
                    [(i, f"SKU-{i:05d}", f"商品 {i}", round(i * 1.25, 2), 2 if i % 2 else None,
                      "2024-05-01 10:00:00", bytes([i % 256]) * 3) for i in range(1, 5001)])
    con.executemany("INSERT INTO stock VALUES (?,?,?)", [(i, w, i * 2) for i in range(1, 301) for w in ("TPE", "KHH")])
    con.commit()
    con.close()
    return svc.store.save({"kind": "sqlite", "name": "shop", "path": str(path)})


def run_job(svc, source_id, target, **opts):
    j = svc.migrations.start(source_id, target, opts)
    job = svc.migrations.get(j["id"])
    job.thread.join(120)
    return job.public()


def test_sqlite_to_sqlite_copies_structure_data_and_verifies(svc, source, tmp_path):
    out = tmp_path / "copy.db"
    d = run_job(svc, source.id, {"type": "sqlite", "path": str(out)})
    assert d["status"] == "done", d.get("error")
    by = {t["name"]: t for t in d["tables"]}
    assert by["products"]["source_rows"] == by["products"]["target_rows"] == 5000
    assert by["stock"]["target_rows"] == 600 and by["notes"]["target_rows"] == 2
    assert all(t["sample_mismatches"] == 0 for t in d["tables"])
    con = sqlite3.connect(out)
    assert con.execute("SELECT name FROM products WHERE id = 7").fetchone()[0] == "商品 7"
    assert con.execute("SELECT COUNT(*) FROM cheap").fetchone()[0] == 7
    assert con.execute("SELECT body FROM notes WHERE body IS NOT NULL").fetchone()[0] == "第一筆 'quote'"
    assert "products_cat" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "sqlite"])
def test_sql_file_export(svc, source, tmp_path, dialect):
    d = run_job(svc, source.id, {"type": "sqlfile", "dialect": dialect, "path": str(tmp_path / f"dump-{dialect}.sql")})
    assert d["status"] == "done", d.get("error")
    text = (tmp_path / f"dump-{dialect}.sql").read_text()
    assert "CREATE TABLE" in text and "INSERT INTO" in text and "CREATE VIEW" in text
    assert ("`products`" if dialect == "mysql" else '"products"') in text
    if dialect == "sqlite":   # the script loads back into SQLite
        con = sqlite3.connect(":memory:")
        con.executescript(text)
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5000
        assert con.execute("SELECT qty FROM stock WHERE product_id = 3 AND warehouse = 'KHH'").fetchone()[0] == 6


def test_gzip_export(svc, source, tmp_path):
    d = run_job(svc, source.id, {"type": "sqlfile", "dialect": "postgres"})   # default path: exports/*.sql.gz
    assert d["status"] == "done" and d["target"]["path"].endswith(".sql.gz")
    import gzip
    assert b"CREATE TABLE" in gzip.open(d["target"]["path"]).read()


def test_plan_reports_conflicts_and_refuses_without_replace(svc, source, tmp_path):
    out = tmp_path / "copy.db"
    assert run_job(svc, source.id, {"type": "sqlite", "path": str(out)})["status"] == "done"
    plan = svc.migrations.plan(source.id, {"type": "sqlite", "path": str(out)})
    assert set(plan["conflicts"]) >= {"products", "stock"}
    d = run_job(svc, source.id, {"type": "sqlite", "path": str(out)})
    assert d["status"] == "failed" and "同名資料表" in d["error"]
    d = run_job(svc, source.id, {"type": "sqlite", "path": str(out)}, if_exists="replace")
    assert d["status"] == "done"
    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5000


def test_selected_tables_only_and_dangling_foreign_keys_are_reported(svc, source, tmp_path):
    out = tmp_path / "part.db"
    d = run_job(svc, source.id, {"type": "sqlite", "path": str(out)}, tables=["products"], views=False)
    assert d["status"] == "done" and [t["name"] for t in d["tables"]] == ["products"]
    assert any("不在這次遷移的範圍內" in n["message"] for n in d["notes"])


def test_stop_and_resume_continues_after_the_last_key(svc, source, tmp_path):
    out = tmp_path / "resume.db"
    mm = svc.migrations
    j = mm.start(source.id, {"type": "sqlite", "path": str(out)}, {"batch_size": 100})
    job = mm.get(j["id"])
    import time
    deadline = time.time() + 30
    while time.time() < deadline and not any(t.rows_copied for t in job.tables.values()):
        time.sleep(0.01)
    mm.stop(job.id)
    job.thread.join(30)
    assert job.d["status"] in ("stopped", "done")
    if job.d["status"] == "stopped":
        partial = sum(t.rows_copied for t in job.tables.values())
        assert partial < 5000 + 600 + 4
        mm.resume(job.id)
        job.thread.join(60)
    d = job.public()
    assert d["status"] == "done", d.get("error")
    assert {t["name"]: t["target_rows"] for t in d["tables"]}["products"] == 5000
    con = sqlite3.connect(out)
    assert con.execute("SELECT COUNT(DISTINCT id) FROM products").fetchone()[0] == 5000


def test_bad_rows_stop_with_the_row_or_are_skipped(svc, tmp_path, monkeypatch):
    path = tmp_path / "loose.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER NOT NULL);"
                      "INSERT INTO t VALUES (1, 5), (2, 6), (3, 7), (4, 8);")
    con.close()
    src = svc.store.save({"kind": "sqlite", "name": "loose", "path": str(path)})
    from sinobee.db.migration import runner
    real = runner.SqliteFileWriter.insert

    def picky(self, table, cols, rows):   # a target that refuses n = 7 (like an out-of-range value)
        if any(r[1] == 7 for r in rows):
            raise ValueError("Out of range value for column 'n'")
        return real(self, table, cols, rows)
    monkeypatch.setattr(runner.SqliteFileWriter, "insert", picky)
    out = tmp_path / "strict.db"
    d = run_job(svc, src.id, {"type": "sqlite", "path": str(out)})
    assert d["status"] == "failed" and "id=3" in d["error"] and "Out of range" in d["error"]
    svc.migrations.resume(d["id"], {"skip_bad_rows": True})
    job = svc.migrations.get(d["id"])
    job.thread.join(30)
    d = job.public()
    assert d["status"] == "done", d.get("error")
    assert d["skipped_rows"]["t"]["count"] == 1 and d["skipped_rows"]["t"]["examples"][0]["row"] == "id=3"
    t = d["tables"][0]
    assert (t["rows_copied"], t["source_rows"], t["target_rows"], t["sample_mismatches"]) == (3, 4, 3, 0)
    assert [r[0] for r in sqlite3.connect(out).execute("SELECT id FROM t ORDER BY id")] == [1, 2, 4]   # no duplicates


def test_value_conversions_mysql_style_values_to_postgres_and_sqlite():
    import datetime as dt
    from sinobee.db.migration import dialects as D
    from sinobee.db.migration.schema import Column
    issues = D.ValueIssues()
    t = D._converter(Column("t", "time", "time"), "mysql", "postgres", issues, "x", "time")
    assert t(dt.timedelta(hours=1, minutes=2, seconds=3)) == dt.time(1, 2, 3)
    assert t(dt.timedelta(hours=838)) is None
    z = D._converter(Column("d", "date", "date"), "mysql", "postgres", issues, "x", "date")
    assert z("0000-00-00") is None
    b = D._converter(Column("b", "bit", "bit(8)", length=8), "mysql", "postgres", issues, "x", "bigint")
    assert b(b"\x05") == 5
    txt = D._converter(Column("s", "text", "text"), "mysql", "postgres", issues, "x", "text")
    assert txt("a\x00b") == "ab"
    big = D._converter(Column("n", "bigint", "bigint unsigned", unsigned=True), "mysql", "sqlite", issues, "x", "INTEGER")
    assert big(2**64 - 1) == str(2**64 - 1)
    msgs = {m for (_t, _c, m) in issues}
    assert any("TIME" in m for m in msgs) and any("0000-00-00" in m for m in msgs) and any("NUL" in m for m in msgs)
    assert D.literal("it's \\", "mysql") == "'it\\'s \\\\'"
    assert D.literal("it's \\", "postgres") == "'it''s \\'"
    assert D.literal(b"\x01\xff", "postgres") == "'\\x01ff'::bytea"


def test_type_mapping_notes():
    from sinobee.db.migration import dialects as D
    from sinobee.db.migration.schema import Column
    n = D.Notes()
    assert D.map_type(Column("id", "int", "int(10) unsigned", unsigned=True), "mysql", "postgres", n, "t") == "bigint"
    assert D.map_type(Column("b", "bigint", "bigint unsigned", unsigned=True), "mysql", "postgres", n, "t") == "numeric(20,0)"
    assert D.map_type(Column("j", "json", "json"), "mysql", "postgres", n, "t") == "jsonb"
    assert D.map_type(Column("e", "enum", "enum('a')", values=["a"]), "postgres", "mysql", n, "t") == "enum('a')"
    assert D.map_type(Column("s", "text", "text"), "postgres", "mysql", n, "t", in_index=True) == "varchar(768)"
    assert D.map_type(Column("u", "uuid", "uuid"), "postgres", "mysql", n, "t") == "char(36)"
    assert D.map_type(Column("x", "int", "int(11)"), "mysql", "mysql", n, "t") == "int(11)"


def test_migration_api(svc, source, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sinobee.server import db_api
    import sinobee.tools.db_tools as dbt
    monkeypatch.setitem(dbt._SERVICES, str(svc.config.home), svc)

    class RT:
        config = svc.config
        skills = None
    app = FastAPI()
    db_api.register(app, lambda: RT)
    c = TestClient(app)
    target = {"type": "sqlfile", "dialect": "mysql", "path": str(tmp_path / "api.sql")}
    plan = c.post("/api/db/migrations/plan", json={"source": source.id, "target": target}).json()
    assert {t["name"] for t in plan["tables"]} == {"categories", "products", "stock", "notes"} and plan["views"] == ["cheap"]
    assert c.post("/api/db/migrations", json={"source": source.id, "target": target}).status_code == 400   # needs confirm
    j = c.post("/api/db/migrations", json={"source": source.id, "target": target, "confirm": True}).json()
    svc.migrations.get(j["id"]).thread.join(60)
    got = c.get(f"/api/db/migrations/{j['id']}").json()
    assert got["status"] == "done" and got["progress"]["rows_copied"] == 5000 + 600 + 2 + 2
    r = c.get(f"/api/db/migrations/{j['id']}/download")
    assert r.status_code == 200 and b"INSERT INTO `products`" in r.content
    assert any(x["id"] == j["id"] for x in c.get("/api/db/migrations").json()["jobs"])
    assert c.delete(f"/api/db/migrations/{j['id']}").json()["ok"]


# ------------------------------------------------------------------ connections: CA + SSH settings
def test_ssh_and_ca_settings_are_validated_and_secrets_stay_in_secrets_json(svc):
    c = svc.store.save({"kind": "mysql", "name": "aurora", "host": "x.cluster-abc.ap-northeast-1.rds.amazonaws.com",
                        "database": "app", "user": "admin", "sslmode": "verify-full",
                        "ssh_host": "bastion.example.com", "ssh_user": "ec2-user"},
                       password="pw", ssh_secret="-----BEGIN OPENSSH PRIVATE KEY-----\nxx\n-----END OPENSSH PRIVATE KEY-----",
                       ssh_passphrase="pp")
    assert c.ssl_ca == "aws-rds"          # RDS host + verify -> Amazon RDS CA bundle
    assert c.ssh_port == 22 and c.ssh_auth == "key"
    pub = c.public(True, True)
    assert pub["has_ssh_secret"] and "PRIVATE KEY" not in str(pub)
    assert svc.store.ssh_secret(c) == ("-----BEGIN OPENSSH PRIVATE KEY-----\nxx\n-----END OPENSSH PRIVATE KEY-----", "pp")
    with pytest.raises(ValueError):
        svc.store.save({"kind": "mysql", "host": "db", "ssh_host": "bastion"})          # no SSH user
    # turning SSH off removes the stored key; deleting the connection removes everything
    svc.store.save({"kind": "mysql", "name": "aurora", "host": c.host, "database": "app", "user": "admin"}, cid=c.id)
    assert svc.store.ssh_secret(c) == (None, None)
    svc.store.delete(c.id)
    assert svc.config.secrets.get_secret(f"db:{c.id}") is None


def test_private_key_parsing_errors():
    from sinobee.db.tunnel import TunnelError, load_private_key
    import paramiko
    k = paramiko.ECDSAKey.generate()
    import io
    buf = io.StringIO()
    k.write_private_key(buf, password="secret")
    with pytest.raises(TunnelError, match="私鑰密碼"):
        load_private_key(buf.getvalue(), None)
    assert load_private_key(buf.getvalue(), "secret").get_fingerprint() == k.get_fingerprint()
    with pytest.raises(TunnelError, match="PuTTY"):
        load_private_key("PuTTY-User-Key-File-3: ssh-rsa\n", None)


def _echo_server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(5)

    def serve():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=lambda c=c: [c.sendall(d) for d in iter(lambda: c.recv(1024), b"")], daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    return s, s.getsockname()[1]


def test_ssh_tunnel_forwards_and_pins_the_host_key():
    import paramiko
    from sshserver import SSHServer, private_key_text
    from sinobee.db.store import Connection
    from sinobee.db.tunnel import POOL, TunnelError
    ck = paramiko.ECDSAKey.generate()
    ssh = SSHServer(client_key=ck)
    echo, port = _echo_server()
    c = Connection(id="t" + uuid.uuid4().hex[:6], name="x", kind="mysql", host="127.0.0.1", port=port,
                   ssh_host="127.0.0.1", ssh_port=ssh.port, ssh_user="ec2-user")
    t = POOL.get(c, private_key_text(ck), None)
    s = socket.create_connection(("127.0.0.1", t.port), timeout=5)
    s.sendall(b"hello")
    assert s.recv(5) == b"hello"
    s.close()
    assert t.fingerprint.startswith("SHA256:")
    assert POOL.get(c, private_key_text(ck), None) is t          # shared while alive
    POOL.close(c.id)
    c.ssh_fingerprint = "SHA256:not-the-right-key"
    with pytest.raises(TunnelError, match="主機金鑰改變"):
        POOL.get(c, private_key_text(ck), None)
    c.ssh_fingerprint = ""
    with pytest.raises(TunnelError, match="驗證失敗"):
        POOL.get(c, private_key_text(paramiko.ECDSAKey.generate()), None)
    ssh.close()
    echo.close()


# ------------------------------------------------------------------ live MySQL / PostgreSQL (optional)
MYSQL = os.environ.get("SINOBEE_TEST_MYSQL")
PG = os.environ.get("SINOBEE_TEST_PG")


def _base(dsn: str) -> str:
    import re
    return re.sub(r"^(\w+://[^/]+).*$", r"\1", dsn)


def _server_conn(svc, dsn: str, db: str, name: str):
    d = parse_dsn(_base(dsn) + "/" + db)
    return svc.store.save({**{k: v for k, v in d.items() if k != "password"}, "name": name, "sslmode": "disable"},
                          password=d.get("password") or None)


@pytest.mark.skipif(not (MYSQL and PG), reason="set SINOBEE_TEST_MYSQL and SINOBEE_TEST_PG")
def test_mysql_to_postgres_and_back(svc):
    import psycopg
    import pymysql
    m = parse_dsn(_base(MYSQL) + "/x")
    p = parse_dsn(_base(PG) + "/postgres")
    tag = uuid.uuid4().hex[:6]
    my_src, my_dst, pg_db = f"sb_src_{tag}", f"sb_dst_{tag}", f"sb_pg_{tag}"
    mc = pymysql.connect(host=m["host"], port=m["port"], user=m["user"], password=m["password"], autocommit=True, charset="utf8mb4")
    pc = psycopg.connect(host=p["host"], port=p["port"], user=p["user"], password=p["password"] or None, dbname="postgres", autocommit=True)
    try:
        cur = mc.cursor()
        for q in (f"CREATE DATABASE {my_src} CHARACTER SET utf8mb4", f"CREATE DATABASE {my_dst} CHARACTER SET utf8mb4",
                  f"USE {my_src}", "SET SESSION sql_mode = ''",
                  "CREATE TABLE c (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(50) NOT NULL, j JSON, "
                  "s SET('a','b'), e ENUM('x','y') NOT NULL DEFAULT 'x', t TIME, d DATE, big BIGINT UNSIGNED, f BIT(1) DEFAULT b'0', "
                  "g VARCHAR(50) AS (UPPER(name)) VIRTUAL, UNIQUE KEY uq (name))",
                  "CREATE TABLE o (id BIGINT AUTO_INCREMENT PRIMARY KEY, c_id INT UNSIGNED NOT NULL, total DECIMAL(10,2), "
                  "FOREIGN KEY (c_id) REFERENCES c(id) ON DELETE CASCADE)",
                  "INSERT INTO c (name, j, s, e, t, d, big, f) VALUES ('王', '{\"a\": 1}', 'a,b', 'y', '12:00:00', '2024-01-02', 18446744073709551615, b'1'),"
                  "('Bob', NULL, NULL, 'x', '30:00:00', '0000-00-00', 1, b'0')",
                  "INSERT INTO o (c_id, total) VALUES (1, 9.99), (1, 10.00), (2, 0.01)",
                  "CREATE VIEW v AS SELECT c.name, SUM(o.total) AS total FROM c JOIN o ON o.c_id = c.id GROUP BY c.name"):
            cur.execute(q)
        pc.execute(f"CREATE DATABASE {pg_db}")
        src = _server_conn(svc, MYSQL, my_src, "src")
        pg = _server_conn(svc, PG, pg_db, "pg")
        back = _server_conn(svc, MYSQL, my_dst, "back")
        d = run_job(svc, src.id, {"type": "connection", "id": pg.id})
        assert d["status"] == "done", d.get("error")
        assert all(t["source_rows"] == t["target_rows"] and t["sample_mismatches"] == 0 for t in d["tables"])
        msgs = " ".join(v["message"] for v in d["value_issues"])
        assert "TIME" in msgs and "0000-00-00" in msgs
        with psycopg.connect(host=p["host"], port=p["port"], user=p["user"], password=p["password"] or None, dbname=pg_db) as x:
            assert x.execute("SELECT j->>'a', big::text, f, g FROM c WHERE id = 1").fetchone() == ("1", str(2**64 - 1), True, "王")
            assert str(x.execute("SELECT total FROM v WHERE name = '王'").fetchone()[0]) == "19.99"
            x.execute("INSERT INTO o (c_id, total) VALUES (2, 1) RETURNING id")       # identity continues after 3
            assert x.execute("SELECT max(id) FROM o").fetchone()[0] == 4
        d2 = run_job(svc, pg.id, {"type": "connection", "id": back.id})
        assert d2["status"] == "done", d2.get("error")
        assert all(t["source_rows"] == t["target_rows"] for t in d2["tables"])
        d3 = run_job(svc, src.id, {"type": "connection", "id": back.id}, if_exists="replace")   # same engine: SHOW CREATE TABLE
        assert d3["status"] == "done", d3.get("error")
        cur.execute(f"SELECT d, t, g FROM {my_dst}.c WHERE name = 'Bob'")
        assert [str(x) for x in cur.fetchone()] == ["0000-00-00", "1 day, 6:00:00", "BOB"]
    finally:
        for q in (f"DROP DATABASE IF EXISTS {my_src}", f"DROP DATABASE IF EXISTS {my_dst}"):
            mc.cursor().execute(q)
        pc.execute(f"DROP DATABASE IF EXISTS {pg_db} WITH (FORCE)")
        mc.close()
        pc.close()
