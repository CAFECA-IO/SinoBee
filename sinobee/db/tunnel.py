"""SSH tunnels to databases behind a bastion (jump host), e.g. an Amazon RDS / Aurora instance without public
access reached through an EC2 instance in the same VPC.

The tunnel listens on 127.0.0.1 only and forwards each accepted connection over one SSH transport
("direct-tcpip" channels). Tunnels are shared per connection and closed after a few idle minutes.

The bastion's host key is pinned on first use (TOFU): the SHA256 fingerprint is saved with the connection and a
different key later is refused (possible man-in-the-middle) until the user re-trusts it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import select
import socket
import threading
import time

log = logging.getLogger("sinobee.db")
logging.getLogger("paramiko").setLevel(logging.CRITICAL)   # errors are reported as TunnelError
IDLE_SECONDS = 600


class TunnelError(Exception):
    pass


def fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def load_private_key(text: str, passphrase: str | None):
    import paramiko
    errors = []
    classes = [getattr(paramiko, n) for n in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey") if hasattr(paramiko, n)]
    for cls in classes:
        try:
            return cls.from_private_key(io.StringIO(text.strip() + "\n"), password=passphrase or None)
        except paramiko.PasswordRequiredException:
            raise TunnelError("SSH 私鑰有加密，請填入私鑰密碼") from None
        except Exception as e:  # noqa: BLE001 - try the next key type
            errors.append(f"{cls.__name__}: {e}")
    if "PuTTY" in text[:40]:
        raise TunnelError("不支援 PuTTY (.ppk) 格式；請用 PuTTYgen 匯出成 OpenSSH 格式") from None
    raise TunnelError("無法讀取 SSH 私鑰（支援 OpenSSH / PEM 格式的 Ed25519、ECDSA、RSA）")


class Tunnel:
    def __init__(self, ssh_host: str, ssh_port: int, ssh_user: str, auth: str, secret: str | None,
                 passphrase: str | None, remote_host: str, remote_port: int, pinned: str = ""):
        self.ssh = (ssh_host, int(ssh_port or 22))
        self.user, self.auth, self.secret, self.passphrase = ssh_user, auth, secret, passphrase
        self.remote = (remote_host, int(remote_port))
        self.pinned = pinned
        self.fingerprint = ""
        self.transport = None
        self.server: socket.socket | None = None
        self.port = 0
        self.last_used = time.time()
        self._closed = False

    # ---------- lifecycle ----------
    def start(self) -> "Tunnel":
        import paramiko
        if not self.secret:
            raise TunnelError("還沒有設定 SSH 私鑰或密碼")
        try:
            sock = socket.create_connection(self.ssh, timeout=10)
        except OSError as e:
            raise TunnelError(f"連不到 SSH 跳板機 {self.ssh[0]}:{self.ssh[1]}（{e.strerror or e}）") from None
        t = paramiko.Transport(sock)
        t.set_keepalive(30)
        try:
            t.start_client(timeout=15)
            self.fingerprint = fingerprint(t.get_remote_server_key())
            if self.pinned and self.fingerprint != self.pinned:
                raise TunnelError(f"SSH 跳板機的主機金鑰改變了（預期 {self.pinned}，實際 {self.fingerprint}）。"
                                  "可能是重建了主機，也可能遭到攔截；確認無誤後在連線設定按「重新信任主機金鑰」。")
            if self.auth == "password":
                t.auth_password(self.user, self.secret)
            else:
                t.auth_publickey(self.user, load_private_key(self.secret, self.passphrase))
            if not t.is_authenticated():
                raise TunnelError("SSH 驗證失敗")
        except TunnelError:
            t.close()
            raise
        except paramiko.AuthenticationException:
            t.close()
            raise TunnelError(f"SSH 驗證失敗：帳號 {self.user} 的私鑰或密碼不正確") from None
        except Exception as e:  # noqa: BLE001
            t.close()
            raise TunnelError(f"SSH 連線失敗：{e}") from None
        self.transport = t
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(16)
        self.server, self.port = srv, srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, name="sinobee-ssh-tunnel", daemon=True).start()
        return self

    def alive(self) -> bool:
        return not self._closed and self.transport is not None and self.transport.is_active()

    def close(self) -> None:
        self._closed = True
        for x in (self.server, self.transport):
            try:
                if x is not None:
                    x.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- forwarding ----------
    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                client, addr = self.server.accept()
            except OSError:
                break
            self.last_used = time.time()
            threading.Thread(target=self._forward, args=(client, addr), daemon=True).start()

    def _forward(self, client: socket.socket, addr) -> None:
        try:
            chan = self.transport.open_channel("direct-tcpip", self.remote, addr, timeout=15)
        except Exception as e:  # noqa: BLE001
            log.warning("ssh tunnel: cannot open channel to %s:%s: %s", *self.remote, e)
            client.close()
            return
        try:
            while True:
                r, _, _ = select.select([client, chan], [], [], 60)
                if client in r:
                    data = client.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    client.sendall(data)
                self.last_used = time.time()
        except Exception:  # noqa: BLE001 - a closed socket ends the forward
            pass
        finally:
            for x in (chan, client):
                try:
                    x.close()
                except Exception:  # noqa: BLE001
                    pass


class TunnelPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._tunnels: dict[tuple, Tunnel] = {}
        threading.Thread(target=self._reaper, name="sinobee-ssh-reaper", daemon=True).start()

    def get(self, c, secret: str | None, passphrase: str | None) -> Tunnel:
        key = (c.id, c.ssh_host, int(c.ssh_port or 22), c.ssh_user, c.host, int(c.port or 0), c.ssh_auth,
               hashlib.sha256((secret or "").encode()).hexdigest())
        with self._lock:
            t = self._tunnels.get(key)
            if t and t.alive():
                t.last_used = time.time()
                return t
            if t:
                t.close()
            from .store import KINDS
            t = Tunnel(c.ssh_host, c.ssh_port, c.ssh_user, c.ssh_auth, secret, passphrase, c.host,
                       c.port or KINDS[c.kind], c.ssh_fingerprint).start()
            self._tunnels[key] = t
            return t

    def close(self, cid: str | None = None) -> None:
        with self._lock:
            for k in [k for k in self._tunnels if cid is None or k[0] == cid]:
                self._tunnels.pop(k).close()

    def _reaper(self) -> None:
        while True:
            time.sleep(30)
            with self._lock:
                for k, t in list(self._tunnels.items()):
                    if not t.alive() or time.time() - t.last_used > IDLE_SECONDS:
                        t.close()
                        self._tunnels.pop(k, None)


POOL = TunnelPool()
