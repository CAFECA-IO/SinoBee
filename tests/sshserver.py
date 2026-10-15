"""A tiny in-process SSH server for tests: public-key / password auth and direct-tcpip forwarding."""
from __future__ import annotations

import select
import socket
import threading

import paramiko


class _Server(paramiko.ServerInterface):
    def __init__(self, user: str, pubkey, password: str | None):
        self.user, self.pubkey, self.password = user, pubkey, password

    def get_allowed_auths(self, username):
        return "publickey,password"

    def check_auth_publickey(self, username, key):
        if username == self.user and self.pubkey is not None and key.asbytes() == self.pubkey.asbytes():
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        if username == self.user and self.password and password == self.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "direct-tcpip" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class SSHServer:
    def __init__(self, user="ec2-user", password=None, client_key=None):
        self.host_key = paramiko.ECDSAKey.generate()
        self.user, self.password = user, password
        self.client_key = client_key
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.forwards = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        t = paramiko.Transport(client)
        t.add_server_key(self.host_key)
        server = _Server(self.user, self.client_key.public_key() if hasattr(self.client_key, "public_key") else self.client_key,
                         self.password)
        orig = server.check_channel_direct_tcpip_request

        def direct(chanid, origin, destination):
            self._dest = destination
            return orig(chanid, origin, destination)
        server.check_channel_direct_tcpip_request = direct
        try:
            t.start_server(server=server)
        except Exception:  # noqa: BLE001
            return
        while t.is_active():
            chan = t.accept(1)
            if chan is None:
                continue
            dest = getattr(self, "_dest", None)
            threading.Thread(target=self._pump, args=(chan, dest), daemon=True).start()

    def _pump(self, chan, dest):
        self.forwards += 1
        try:
            remote = socket.create_connection(dest, timeout=10)
        except OSError:
            chan.close()
            return
        try:
            while True:
                r, _, _ = select.select([chan, remote], [], [], 30)
                if chan in r:
                    d = chan.recv(65536)
                    if not d:
                        break
                    remote.sendall(d)
                if remote in r:
                    d = remote.recv(65536)
                    if not d:
                        break
                    chan.sendall(d)
        except Exception:  # noqa: BLE001
            pass
        finally:
            chan.close()
            remote.close()

    def close(self):
        self.sock.close()


def private_key_text(key) -> str:
    import io
    buf = io.StringIO()
    key.write_private_key(buf)
    return buf.getvalue()
