"""TLS trust for database connections: the system store, a PEM file, or the Amazon RDS global CA bundle
(downloaded once from AWS and cached in $SINOBEE_HOME/db/ca/)."""
from __future__ import annotations

import time
from pathlib import Path

AWS_RDS_BUNDLE = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
_cache_dir: Path | None = None


def set_home(home: Path) -> None:
    global _cache_dir
    _cache_dir = Path(home) / "db" / "ca"


def ca_file(spec: str) -> str | None:
    """Path of the CA bundle for `spec` ("" = system trust, None returned)."""
    if not spec:
        return None
    if spec != "aws-rds":
        p = Path(spec).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"找不到 CA 憑證檔：{p}")
        return str(p)
    d = _cache_dir or Path.home() / ".sinobee" / "db" / "ca"
    p = d / "aws-rds-global-bundle.pem"
    if p.exists() and p.stat().st_size > 1000 and time.time() - p.stat().st_mtime < 180 * 86400:
        return str(p)
    import httpx
    try:
        r = httpx.get(AWS_RDS_BUNDLE, timeout=20, follow_redirects=True)
        r.raise_for_status()
        if b"BEGIN CERTIFICATE" not in r.content:
            raise ValueError("unexpected content")
    except Exception as e:  # noqa: BLE001
        if p.exists():
            return str(p)          # an older copy still works for existing certificates
        raise ConnectionError(f"無法下載 Amazon RDS CA 憑證（{AWS_RDS_BUNDLE}）：{e}") from None
    d.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(p)
    return str(p)
