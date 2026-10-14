"""Vault crypto and TOOL.json building (no network)."""
import json

import pytest

from sinobee import vault
from sinobee.db.hub import bump, manifest_for, tool_name
from sinobee.db.store import Connection
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def test_wrap_unwrap_and_wrong_passphrase():
    sk = X25519PrivateKey.generate()
    w = vault.wrap_private_key(sk, "correct horse 42")
    assert w["kdf"]["alg"] == "scrypt" and w["kdf"]["n"] >= 32768
    back = vault.unwrap_private_key(w, "correct horse 42")
    assert vault._raw(back) == vault._raw(sk)
    with pytest.raises(vault.VaultError, match="不正確"):
        vault.unwrap_private_key(w, "wrong horse")
    with pytest.raises(vault.VaultError, match="12"):
        vault.wrap_private_key(sk, "short")


def test_seal_open_pack_bound_to_name():
    sk = X25519PrivateKey.generate()
    pub = vault.b64e(vault._raw(sk.public_key()))
    data = vault.seal({"password": "pg-S3cret-9", "user": "demo"}, pub, "db:shop")
    assert "pg-S3cret-9" not in data and "demo" not in data
    env = json.loads(data)
    assert set(env) == {"v", "epk", "iv", "ct"}
    assert vault.open_pack(data, sk, "db:shop")["password"] == "pg-S3cret-9"
    with pytest.raises(vault.VaultError):
        vault.open_pack(data, sk, "db:other")          # cannot be swapped under another name
    with pytest.raises(vault.VaultError):
        vault.open_pack(data, X25519PrivateKey.generate(), "db:shop")


def test_manifest_never_contains_credentials():
    c = Connection(id="shop", name="Shop 資料庫", kind="postgres", host="db.example.com", port=5432, database="shop",
                   user="demo", sslmode="require")
    m = manifest_for(c, version="1.0.0", tables=[{"schema": "sales", "name": "orders", "columns": []}], vault_pack="db:shop")
    text = json.dumps(m)
    assert "demo" not in text and "password" not in text
    assert m["name"] == "shop" and m["database"]["host"] == "db.example.com" and m["database"]["readOnly"] is True
    no_host = manifest_for(c, version="1.0.0", include_host=False)
    assert "host" not in no_host["database"] and "port" not in no_host["database"]
    s = Connection(id="x", name="Local", kind="sqlite", path="/home/me/secret/data.db")
    assert manifest_for(s, version="1.0.0")["database"]["file"] == "data.db"   # the path stays local
    assert tool_name(Connection(id="y", name="訂單", kind="mysql", database="erp")) == "erp"
    assert bump(None) == "1.0.0" and bump("1.2.9") == "1.2.10"


def test_tool_folder_queries_notes_and_skill(config):
    from sinobee.db import toolfolder
    c = Connection(id="shop", name="商店", kind="sqlite", path="/tmp/x.db")
    toolfolder.save_query(config, c, "前五大客戶", "SELECT name FROM customers LIMIT 5")
    with pytest.raises(ValueError, match="唯讀"):
        toolfolder.save_query(config, c, "刪", "DELETE FROM customers")
    toolfolder.set_notes(config, c, {"main.customers": "客戶主檔", "main.customers.tier": "A/B/C 等級"})
    f = toolfolder.write(config, c, description="商店的訂單與客戶", tables=[{"schema": "main", "name": "customers",
                                                                  "columns": [{"name": "id"}, {"name": "tier"}]}])
    md = (f / "SKILL.md").read_text()
    assert "name: db-shop" in md and "sinobee db query shop" in md and "客戶主檔" in md and "A/B/C 等級" in md and "SELECT name FROM customers LIMIT 5" in md
    # a SKILL.md written by the user / agent is kept on later refreshes
    toolfolder.write(config, c, skill="---\nname: db-shop\ndescription: custom\n---\n# mine\n", custom_skill=True)
    toolfolder.write(config, c, description="again")
    assert "# mine" in (f / "SKILL.md").read_text()
    m = manifest_for(c, version="1.0.0", queries=toolfolder.queries(config, c), notes=toolfolder.notes(config, c),
                     skill=toolfolder.skill_md(config, c))
    assert m["queries"][0]["title"] == "前五大客戶" and m["notes"]["main.customers"] == "客戶主檔" and "# mine" in m["skill"]


def test_db_cli_is_read_only(config, tmp_path, capsys):
    import sqlite3
    from sinobee.cli import main
    from sinobee.tools.db_tools import service
    f = tmp_path / "s.db"
    con = sqlite3.connect(f)
    con.executescript("create table t(id int, name text); insert into t values (1,'a'),(2,'b');")
    con.close()
    service(config).store.save({"kind": "sqlite", "name": "Shop", "path": str(f)})
    main(["db", "query", "Shop", "select * from t order by id", "--format", "json"])
    assert json.loads(capsys.readouterr().out) == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    with pytest.raises(SystemExit, match="唯讀"):
        main(["db", "query", "Shop", "delete from t"])
    main(["db", "schema", "Shop", "t", "--format", "csv"])
    assert capsys.readouterr().out.splitlines()[1].startswith("id,")
