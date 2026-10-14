"""End-to-end encrypted vault on sinobee.ai.

Everything is encrypted here, on the user's machine; sinobee.ai only stores ciphertext.

* Vault key: an X25519 key pair. The private key is wrapped with a key derived from the user's vault
  passphrase (scrypt N=2^15, r=8, p=1 → AES-256-GCM) and uploaded with the public key.
* Packs (e.g. a database connection with its password) are sealed to the public key: ephemeral X25519,
  HKDF-SHA256 → AES-256-GCM. Writing needs only the public key.
* Device copies: each signed-in SinoBee has its own X25519 key (secrets.json). Once the vault is unlocked
  on a device, the vault private key is also sealed to that device, so it opens without the passphrase.
  A new device is unlocked with the passphrase once, or approved from a device that already has the key.
  Signing a device out on sinobee.ai deletes its copy.

The passphrase is never stored or sent anywhere; the unwrapped private key lives only in memory.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import account

SCRYPT = {"n": 1 << 15, "r": 8, "p": 1}
WRAP_AAD = b"sinobee/vault-key/v1"
PACK_INFO = b"sinobee/vault-pack/v1"


class VaultError(RuntimeError):
    pass


b64e = lambda b: base64.b64encode(b).decode()  # noqa: E731
b64d = lambda s: base64.b64decode(s)  # noqa: E731


def _raw(k) -> bytes:
    if isinstance(k, X25519PrivateKey):
        return k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return k.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _kek(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


COMMON = {"password1234", "123456789012", "qwertyuiopas", "iloveyou1234", "passwordpassword", "sinobeesinobee"}


def check_passphrase(p: str) -> None:
    """At least 12 characters and not trivially guessable (several character kinds, or long)."""
    if len(p) < 12:
        raise VaultError("保險箱密語至少 12 個字元")
    kinds = sum(bool(re.search(rx, p)) for rx in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if p.lower() in COMMON or len(set(p)) < 6 or (kinds < 2 and len(p) < 20) or (kinds < 3 and len(p) < 16):
        raise VaultError("保險箱密語太容易猜到：請混用大小寫、數字或符號，或用 4 個以上的詞組成較長的句子")


def wrap_private_key(sk: X25519PrivateKey, passphrase: str) -> dict:
    check_passphrase(passphrase)
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_kek(passphrase, salt, **SCRYPT)).encrypt(iv, _raw(sk), WRAP_AAD)
    return {"v": 1, "kdf": {"alg": "scrypt", **SCRYPT, "salt": b64e(salt)}, "iv": b64e(iv), "ct": b64e(ct)}


def unwrap_private_key(wrapped: dict, passphrase: str) -> X25519PrivateKey:
    k = wrapped["kdf"]
    try:
        raw = AESGCM(_kek(passphrase, b64d(k["salt"]), k["n"], k["r"], k["p"])).decrypt(b64d(wrapped["iv"]), b64d(wrapped["ct"]), WRAP_AAD)
    except Exception:  # noqa: BLE001 - InvalidTag
        raise VaultError("保險箱密語不正確") from None
    return X25519PrivateKey.from_private_bytes(raw)


def _pack_key(shared: bytes, epk: bytes, pk: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + pk, info=PACK_INFO).derive(shared)


def seal(obj: dict, public_key_b64: str, name: str) -> str:
    """Seal a JSON object to the vault public key. The pack name is bound as AAD (no swapping packs)."""
    pk = X25519PublicKey.from_public_bytes(b64d(public_key_b64))
    esk = X25519PrivateKey.generate()
    epk = _raw(esk.public_key())
    key = _pack_key(esk.exchange(pk), epk, _raw(pk))
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode(), name.encode())
    return json.dumps({"v": 1, "epk": b64e(epk), "iv": b64e(iv), "ct": b64e(ct)})


def open_pack(data: str, sk: X25519PrivateKey, name: str) -> dict:
    env = json.loads(data)
    if env.get("v") != 1:
        raise VaultError("不支援的保險箱格式")
    epk = b64d(env["epk"])
    key = _pack_key(sk.exchange(X25519PublicKey.from_public_bytes(epk)), epk, _raw(sk.public_key()))
    try:
        return json.loads(AESGCM(key).decrypt(b64d(env["iv"]), b64d(env["ct"]), name.encode()))
    except Exception:  # noqa: BLE001
        raise VaultError(f"無法解開保險箱項目 {name}") from None


class VaultClient:
    """sinobee.ai vault API (needs `sinobee login` with the vault:rw scope)."""

    _unlocked: dict[str, X25519PrivateKey] = {}   # account -> private key, memory only
    _lock = threading.Lock()

    def __init__(self, config, timeout: float = 30):
        self.config = config
        self.base = account.base_url(config)
        self.timeout = timeout

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = httpx.request(method, f"{self.base}{path}", headers=account.auth_headers(self.config, "vault:rw"),
                              timeout=self.timeout, **kw)
        except httpx.HTTPError as e:
            raise VaultError(f"無法連線到 sinobee.ai：{e}") from None
        if r.status_code == 401:
            raise VaultError("sinobee.ai 登入已失效，請重新登入")
        return r

    def _acct(self) -> str:
        return (account.account(self.config) or {}).get("account") or "?"

    def key(self) -> dict | None:
        r = self._req("GET", "/api/v1/vault/key")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise VaultError(f"保險箱讀取失敗（HTTP {r.status_code}）")
        return r.json()

    def _try_device_unlock(self, k: dict) -> bool:
        """Open the vault with this device's own copy of the key (no passphrase)."""
        if self._acct() in self._unlocked or not k or not k.get("deviceWrapped"):
            return self._acct() in self._unlocked
        dsk = account.device_private_key(self.config)
        if not dsk:
            return False
        try:
            raw = b64d(open_pack(k["deviceWrapped"], dsk, "vault-key")["sk"])
            sk = X25519PrivateKey.from_private_bytes(raw)
        except (VaultError, KeyError, ValueError):
            return False
        if b64e(_raw(sk.public_key())) != k["publicKey"]:
            return False
        with self._lock:
            self._unlocked[self._acct()] = sk
        return True

    def _seal_for_device(self, sk: X25519PrivateKey, device_public_key: str, device_id: str = "self") -> None:
        r = self._req("PUT", f"/api/v1/vault/devices/{device_id}",
                      json={"wrapped": seal({"sk": b64e(_raw(sk))}, device_public_key, "vault-key")})
        if r.status_code >= 400:
            raise VaultError(f"無法把保險箱金鑰交給裝置：{r.text[:200]}")

    def status(self) -> dict:
        k = self.key()
        unlocked = self._try_device_unlock(k) if k else False
        return {"exists": bool(k), "unlocked": unlocked, "device_key": bool(k and k.get("deviceWrapped"))}

    def devices(self) -> list[dict]:
        r = self._req("GET", "/api/v1/vault/devices")
        return r.json().get("items", []) if r.status_code == 200 else []

    def approve_device(self, device_id: str) -> None:
        """Give another signed-in SinoBee of this account a copy of the vault key (vault must be unlocked here)."""
        sk = self._unlocked.get(self._acct())
        if not sk:
            raise VaultError("保險箱已上鎖，請先輸入保險箱密語")
        d = next((x for x in self.devices() if x["id"] == device_id), None)
        if not d or not d.get("publicKey"):
            raise VaultError("找不到這台裝置，或它需要重新登入")
        self._seal_for_device(sk, d["publicKey"], device_id)

    def setup(self, passphrase: str) -> dict:
        """Create the vault (first time) or unlock it with the passphrase."""
        k = self.key()
        if k:
            return self.unlock(passphrase)
        sk = X25519PrivateKey.generate()
        body = {"publicKey": b64e(_raw(sk.public_key())), "wrapped": wrap_private_key(sk, passphrase)}
        r = self._req("PUT", "/api/v1/vault/key", json=body)
        if r.status_code >= 400:
            raise VaultError(f"建立保險箱失敗：{r.text[:200]}")
        with self._lock:
            self._unlocked[self._acct()] = sk
        self._remember_on_device(sk)
        return {"created": True, "unlocked": True}

    def _remember_on_device(self, sk: X25519PrivateKey) -> None:
        try:
            self._seal_for_device(sk, account.device_public_key(self.config))
        except VaultError:
            pass   # older tokens without a device key: the passphrase keeps working

    def unlock(self, passphrase: str) -> dict:
        k = self.key()
        if not k:
            raise VaultError("還沒有保險箱")
        sk = unwrap_private_key(k["wrapped"], passphrase)
        if b64e(_raw(sk.public_key())) != k["publicKey"]:
            raise VaultError("保險箱金鑰不一致")
        with self._lock:
            self._unlocked[self._acct()] = sk
        if not k.get("deviceWrapped"):
            self._remember_on_device(sk)
        return {"created": False, "unlocked": True}

    def lock(self) -> None:
        with self._lock:
            self._unlocked.pop(self._acct(), None)

    def change_passphrase(self, old: str, new: str) -> None:
        k = self.key()
        if not k:
            raise VaultError("還沒有保險箱")
        sk = unwrap_private_key(k["wrapped"], old)
        r = self._req("PUT", "/api/v1/vault/key", json={"publicKey": k["publicKey"], "wrapped": wrap_private_key(sk, new)})
        if r.status_code >= 400:
            raise VaultError(f"更換密語失敗：{r.text[:200]}")

    def put(self, name: str, obj: dict) -> int:
        k = self.key()
        if not k:
            raise VaultError("還沒有保險箱，請先設定保險箱密語")
        cur = self._req("GET", f"/api/v1/vault/packs/{name}")
        rev = cur.json().get("revision") if cur.status_code == 200 else 0
        r = self._req("PUT", f"/api/v1/vault/packs/{name}", json={"data": seal(obj, k["publicKey"], name), "revision": rev})
        if r.status_code >= 400:
            raise VaultError(f"寫入保險箱失敗：{r.text[:200]}")
        return r.json()["revision"]

    def get(self, name: str) -> dict:
        if self._acct() not in self._unlocked:
            self._try_device_unlock(self.key() or {})
        sk = self._unlocked.get(self._acct())
        if not sk:
            raise VaultError("保險箱已上鎖，請輸入保險箱密語")
        r = self._req("GET", f"/api/v1/vault/packs/{name}")
        if r.status_code == 404:
            raise VaultError(f"保險箱裡沒有 {name}")
        if r.status_code >= 400:
            raise VaultError(f"讀取保險箱失敗（HTTP {r.status_code}）")
        return open_pack(r.json()["data"], sk, name)

    def list(self) -> list[dict]:
        r = self._req("GET", "/api/v1/vault/packs")
        return r.json().get("items", []) if r.status_code == 200 else []

    def delete(self, name: str) -> None:
        self._req("DELETE", f"/api/v1/vault/packs/{name}")
