"""Saved database connections. Settings live in $SINOBEE_HOME/db/connections.json; passwords live
only in secrets.json (key `db:<id>`) and are never returned by any API, tool or log."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

KINDS = {"postgres": 5432, "mysql": 3306, "sqlite": None}
SSL_MODES = ("disable", "prefer", "require", "verify-ca", "verify-full")


@dataclass
class Connection:
    id: str
    name: str
    kind: str                       # postgres | mysql | sqlite
    host: str = ""
    port: int | None = None
    database: str = ""
    user: str = ""
    sslmode: str = "prefer"
    path: str = ""                  # sqlite file
    allow_write: bool = False       # writes still need approval each time
    row_limit: int = 500
    timeout_s: int = 30
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tool: dict = field(default_factory=dict)   # registration on sinobee.ai (M2): {publisher, name, visibility, version}
    ssl_ca: str = ""                # "" (system), "aws-rds" (Amazon RDS global bundle) or a PEM file path
    # SSH bastion (jump host): the database is reached through a tunnel. The key / password lives in secrets.json
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_auth: str = "key"           # key | password
    ssh_fingerprint: str = ""       # SHA256 host key, pinned on first successful connect (trust on first use)

    @property
    def secret_name(self) -> str:
        return f"db:{self.id}"

    @property
    def ssh_secret_name(self) -> str:      # private key (PEM text) or SSH password
        return f"db:{self.id}:ssh"

    @property
    def ssh_passphrase_name(self) -> str:  # passphrase of an encrypted private key
        return f"db:{self.id}:ssh-pass"

    def label(self) -> str:
        if self.kind == "sqlite":
            return self.path
        return f"{self.user + '@' if self.user else ''}{self.host}:{self.port or KINDS[self.kind]}/{self.database}"

    def public(self, has_password: bool = False, has_ssh_secret: bool = False) -> dict:
        d = asdict(self)
        d["has_password"] = has_password
        d["has_ssh_secret"] = has_ssh_secret
        d["label"] = self.label()
        return d


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    return s or "db"


def validate(d: dict) -> dict:
    kind = str(d.get("kind", "")).lower()
    kind = {"postgresql": "postgres", "pg": "postgres", "mariadb": "mysql", "sqlite3": "sqlite"}.get(kind, kind)
    if kind not in KINDS:
        raise ValueError("資料庫類型需為 postgres、mysql 或 sqlite")
    out = {"kind": kind, "name": (str(d.get("name") or "").strip() or None)}
    if kind == "sqlite":
        path = str(d.get("path") or "").strip()
        if not path:
            raise ValueError("SQLite 需要資料庫檔案路徑")
        out["path"] = str(Path(path).expanduser())
    else:
        host = str(d.get("host") or "").strip()
        if not host or not re.match(r"^[A-Za-z0-9._:\-\[\]]+$", host):
            raise ValueError("主機位址格式不正確")
        out["host"] = host
        port = d.get("port") or KINDS[kind]
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError("連接埠需為數字") from None
        if not 0 < port < 65536:
            raise ValueError("連接埠需在 1–65535")
        out["port"] = port
        out["database"] = str(d.get("database") or "").strip()
        out["user"] = str(d.get("user") or "").strip()
        ssl = str(d.get("sslmode") or "prefer")
        if ssl not in SSL_MODES:
            raise ValueError("SSL 模式不正確")
        out["sslmode"] = ssl
        ca = str(d.get("ssl_ca") or "").strip()
        if ca and ca != "aws-rds":
            ca = str(Path(ca).expanduser())
        if ca == "" and host.endswith(".rds.amazonaws.com") and ssl in ("verify-ca", "verify-full"):
            ca = "aws-rds"
        out["ssl_ca"] = ca
        ssh_host = str(d.get("ssh_host") or "").strip()
        if ssh_host:
            if not re.match(r"^[A-Za-z0-9._:\-\[\]]+$", ssh_host):
                raise ValueError("SSH 跳板機位址格式不正確")
            try:
                ssh_port = int(d.get("ssh_port") or 22)
            except (TypeError, ValueError):
                raise ValueError("SSH 連接埠需為數字") from None
            if not 0 < ssh_port < 65536:
                raise ValueError("SSH 連接埠需在 1–65535")
            ssh_user = str(d.get("ssh_user") or "").strip()
            if not ssh_user:
                raise ValueError("SSH 跳板機需要帳號（例如 ec2-user 或 ubuntu）")
            auth = str(d.get("ssh_auth") or "key")
            if auth not in ("key", "password"):
                raise ValueError("SSH 驗證方式需為 key 或 password")
            out.update(ssh_host=ssh_host, ssh_port=ssh_port, ssh_user=ssh_user, ssh_auth=auth)
        else:
            out.update(ssh_host="", ssh_user="", ssh_port=22, ssh_auth="key", ssh_fingerprint="")
    for k, lo, hi in (("row_limit", 1, 10000), ("timeout_s", 1, 600)):
        if d.get(k) not in (None, ""):
            v = int(d[k])
            if not lo <= v <= hi:
                raise ValueError(f"{k} 需在 {lo}–{hi}")
            out[k] = v
    if "allow_write" in d:
        out["allow_write"] = bool(d["allow_write"])
    return out


class ConnectionStore:
    def __init__(self, home: Path, secrets):
        self.dir = Path(home) / "db"
        self.path = self.dir / "connections.json"
        self.secrets = secrets
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, items: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[Connection]:
        out = []
        for d in self._read():
            try:
                out.append(Connection(**{k: v for k, v in d.items() if k in Connection.__dataclass_fields__}))
            except TypeError:
                continue
        return out

    def get(self, cid: str) -> Connection | None:
        return next((c for c in self.list() if c.id == cid), None)

    def find(self, ref: str) -> Connection | None:
        """By id or (case-insensitive) name."""
        ref = (ref or "").strip()
        conns = self.list()
        return next((c for c in conns if c.id == ref), None) or next(
            (c for c in conns if c.name.lower() == ref.lower()), None)

    def password(self, c: Connection) -> str | None:
        return self.secrets.get_secret(c.secret_name)

    def ssh_secret(self, c: Connection) -> tuple[str | None, str | None]:
        """(private key PEM or password, key passphrase)"""
        return self.secrets.get_secret(c.ssh_secret_name), self.secrets.get_secret(c.ssh_passphrase_name)

    def save(self, data: dict, password: str | None = None, cid: str | None = None,
             keep_password: bool = True, ssh_secret: str | None = None,
             ssh_passphrase: str | None = None) -> Connection:
        clean = validate(data)
        with self._lock:
            items = self._read()
            existing = next((d for d in items if d.get("id") == cid), None) if cid else None
            if existing:
                if clean.get("ssh_host") != existing.get("ssh_host") or \
                        int(clean.get("ssh_port") or 22) != int(existing.get("ssh_port") or 22):
                    clean["ssh_fingerprint"] = ""   # a different jump host must be trusted again
                existing.update({k: v for k, v in clean.items() if v is not None})
                existing["updated_at"] = time.time()
                conn = Connection(**{k: v for k, v in existing.items() if k in Connection.__dataclass_fields__})
            else:
                name = clean.get("name") or (Path(clean["path"]).stem if clean["kind"] == "sqlite"
                                             else f"{clean.get('database') or clean['kind']}@{clean['host']}")
                base = _slug(name) if _slug(name) != "db" else _slug(clean.get("database") or clean["kind"])
                ids = {d.get("id") for d in items}
                new_id = base if base not in ids else f"{base}-{uuid.uuid4().hex[:4]}"
                conn = Connection(id=new_id, **{**clean, "name": name})
                items.append(asdict(conn))
            self._write([asdict(conn) if d.get("id") == conn.id else d for d in items])
        if password:
            self.secrets.set_secret(conn.secret_name, password)
        elif not keep_password:
            self.secrets.set_secret(conn.secret_name, None)
        if not conn.ssh_host:
            self.secrets.set_secret(conn.ssh_secret_name, None)
            self.secrets.set_secret(conn.ssh_passphrase_name, None)
        else:
            if ssh_secret:
                self.secrets.set_secret(conn.ssh_secret_name, ssh_secret)
            if ssh_passphrase is not None:
                self.secrets.set_secret(conn.ssh_passphrase_name, ssh_passphrase or None)
        return conn

    def update_meta(self, cid: str, **fields) -> Connection | None:
        with self._lock:
            items = self._read()
            for d in items:
                if d.get("id") == cid:
                    d.update(fields)
                    d["updated_at"] = time.time()
            self._write(items)
        return self.get(cid)

    def delete(self, cid: str) -> bool:
        with self._lock:
            items = self._read()
            keep = [d for d in items if d.get("id") != cid]
            if len(keep) == len(items):
                return False
            self._write(keep)
        for name in (f"db:{cid}", f"db:{cid}:ssh", f"db:{cid}:ssh-pass"):
            self.secrets.set_secret(name, None)
        (self.dir / "history" / f"{cid}.json").unlink(missing_ok=True)
        return True

    # ---- query history (last 100 per connection) ----
    def history(self, cid: str) -> list[dict]:
        p = self.dir / "history" / f"{cid}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except (OSError, json.JSONDecodeError):
            return []

    def add_history(self, cid: str, entry: dict) -> None:
        p = self.dir / "history" / f"{cid}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        items = [entry] + [h for h in self.history(cid) if h.get("sql") != entry.get("sql")]
        p.write_text(json.dumps(items[:100], ensure_ascii=False), encoding="utf-8")
