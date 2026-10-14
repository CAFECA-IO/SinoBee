"""Vault crypto and TOOL.json building (no network)."""
import json

import pytest

from sinobee import vault
from sinobee.db.hub import bump, manifest_for, tool_name
from sinobee.db.store import Connection
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def test_wrap_unwrap_and_wrong_passphrase():
    sk = X25519PrivateKey.generate()
    w = vault.wrap_private_key(sk, "correct horse")
    assert w["kdf"]["alg"] == "scrypt" and w["kdf"]["n"] >= 32768
    back = vault.unwrap_private_key(w, "correct horse")
    assert vault._raw(back) == vault._raw(sk)
    with pytest.raises(vault.VaultError, match="不正確"):
        vault.unwrap_private_key(w, "wrong horse")
    with pytest.raises(vault.VaultError, match="8"):
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
