"""
Raw SCPI over a TCP socket (no VISA), stdlib only.

Written for instruments that behave like the Rigol RSA5065N:
- Replies carry no terminator: a reply ends at a newline, after an IEEE block's length, or when the line goes quiet.
- A setting written while the instrument is busy can be dropped without an error, so drivers pace writes with *OPC?.
- Unknown queries get no reply at all (a timeout here).
"""
from __future__ import annotations

import socket
import threading
import time


class ScpiError(RuntimeError):
    pass


class SocketTransport:
    def __init__(self, host: str, port: int, timeout: float = 5.0, quiet: float = 0.06,
                 connect_timeout: float = 4.0):
        if not host:
            raise ScpiError('no VNA address: set the host in Settings (or pass --host)')
        try:
            self.sock = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError as exc:
            raise ScpiError(f'cannot connect to {host}:{port}: {exc}') from exc
        self.timeout, self.quiet = timeout, quiet
        self._buf = b''
        self.lock = threading.RLock()

    def write(self, line: str) -> None:
        try:
            self.sock.sendall(line.encode() + b'\n')
        except OSError as exc:
            raise ScpiError(f'send failed: {exc}') from exc

    def _recv(self, wait: float) -> bytes:
        self.sock.settimeout(wait)
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return b''
        except OSError as exc:
            raise ScpiError(f'receive failed: {exc}') from exc
        if not chunk:
            raise ScpiError('instrument closed the connection')
        return chunk

    def _block_len(self):
        if self._buf[:1] != b'#' or len(self._buf) < 2 or not self._buf[1:2].isdigit():
            return None
        n = int(self._buf[1:2])
        if n == 0 or len(self._buf) < 2 + n:
            return None
        return 2 + n + int(self._buf[2:2 + n])

    def read(self, timeout: float | None = None, raw: bool = False):
        deadline = time.monotonic() + (timeout or self.timeout)
        while not self._buf:
            if time.monotonic() > deadline:
                raise ScpiError('no reply (unknown command, or the instrument is busy)')
            self._buf += self._recv(min(0.5, max(0.01, deadline - time.monotonic())))
        while True:
            need = self._block_len()
            if need is not None and len(self._buf) >= need:
                out, self._buf = self._buf[:need], self._buf[need:]
                return out if raw else out.decode(errors='replace').strip()
            if self._buf[:1] == b'#' and self._buf[1:2].isdigit():
                if time.monotonic() > deadline:
                    raise ScpiError('block reply cut short')
                self._buf += self._recv(0.5)
                continue
            if b'\n' in self._buf:
                line, self._buf = self._buf.split(b'\n', 1)
                return line if raw else line.decode(errors='replace').strip()
            chunk = self._recv(self.quiet)
            if not chunk:
                out, self._buf = self._buf, b''
                return out if raw else out.decode(errors='replace').strip()
            self._buf += chunk

    def query(self, q: str, timeout: float | None = None, raw: bool = False):
        with self.lock:
            self._buf = b''
            self.write(q)
            return self.read(timeout, raw=raw)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
