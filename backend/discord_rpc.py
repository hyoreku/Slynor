"""Direct Rich Presence over Discord Desktop's documented local RPC protocol."""

import json
import math
import os
import struct
import time
from uuid import uuid4


_HEADER = struct.Struct("<II")
_MAX_FRAME_SIZE = 64 * 1024
_IO_TIMEOUT = 1.5
_HANDSHAKE, _FRAME, _CLOSE, _PING, _PONG = range(5)


class _WindowsPipe:
    def __init__(self):
        import _winapi

        self._api = _winapi
        security_sqos_present = 0x00100000
        security_identification = 0x00010000
        flags = _winapi.FILE_FLAG_OVERLAPPED | security_sqos_present | security_identification
        for index in range(10):
            try:
                self._handle = _winapi.CreateFile(
                    rf"\\?\pipe\discord-ipc-{index}",
                    _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                    0, 0, _winapi.OPEN_EXISTING, flags, 0,
                )
                return
            except OSError:
                continue
        raise OSError("Discord IPC pipe is unavailable")

    def close(self):
        self._api.CloseHandle(self._handle)

    def readable(self):
        return self._api.PeekNamedPipe(self._handle)[0] > 0

    def _complete(self, operation, error, deadline):
        if error == self._api.ERROR_IO_PENDING:
            wait_ms = max(0, int(1000 * (deadline - time.monotonic())))
            try:
                if self._api.WaitForSingleObject(operation.event, wait_ms) != self._api.WAIT_OBJECT_0:
                    raise TimeoutError("Discord IPC timed out")
            except OSError:
                operation.cancel()
                operation.GetOverlappedResult(True)
                raise
        size, error = operation.GetOverlappedResult(True)
        if error:
            raise OSError("Discord IPC transfer failed")
        return size

    def write(self, data, deadline):
        operation, error = self._api.WriteFile(self._handle, data, overlapped=True)
        if self._complete(operation, error, deadline) != len(data):
            raise OSError("Incomplete Discord IPC write")

    def read_exact(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            if time.monotonic() >= deadline:
                raise TimeoutError("Discord IPC timed out")
            operation, error = self._api.ReadFile(self._handle, size - len(data), overlapped=True)
            if not self._complete(operation, error, deadline):
                raise OSError("Discord IPC pipe closed")
            data.extend(operation.getbuffer())
        return bytes(data)


class DiscordRpcRejected(OSError):
    """Discord responded with an RPC error on a live connection."""

    def __init__(self, message, *, rate_limited=False, retry_after=None, code=None):
        super().__init__(message)
        self.rate_limited = rate_limited
        self.retry_after = retry_after
        self.code = code


class DiscordRpc:
    def __init__(self, application_id, open_pipe=_WindowsPipe):
        self._pipe = open_pipe()
        deadline = time.monotonic() + _IO_TIMEOUT
        try:
            self._send(_HANDSHAKE, {"v": 1, "client_id": application_id}, deadline)
            self._wait_for(lambda reply: reply.get("cmd") == "DISPATCH" and reply.get("evt") == "READY", deadline)
        except (OSError, ValueError):
            self.close()
            raise

    def close(self):
        self._pipe.close()

    def _send(self, opcode, payload, deadline):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._pipe.write(_HEADER.pack(opcode, len(data)) + data, deadline)

    def _receive(self, deadline):
        opcode, size = _HEADER.unpack(self._pipe.read_exact(_HEADER.size, deadline))
        if size > _MAX_FRAME_SIZE:
            raise OSError("Discord IPC frame is too large")
        data = self._pipe.read_exact(size, deadline)
        if opcode == _CLOSE:
            raise OSError("Discord closed the RPC connection")
        if opcode == _PING:
            self._pipe.write(_HEADER.pack(_PONG, size) + data, deadline)
        if opcode != _FRAME:
            return {}
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise OSError("Invalid Discord IPC frame")
        return payload

    def _wait_for(self, matches, deadline, nonce=None):
        while time.monotonic() < deadline:
            payload = self._receive(deadline)
            if payload.get("evt") == "ERROR" and (nonce is None or payload.get("nonce") == nonce):
                data = payload.get("data")
                data = data if isinstance(data, dict) else {}
                message = str(data.get("message", "")).casefold()
                delay = data.get("retry_after")
                delay = delay if type(delay) in (float, int) and 0 < delay <= 3600 and math.isfinite(delay) else None
                limited = bool(delay or data.get("code") == 429 or "rate limit" in message or "too many" in message)
                code = data.get("code")
                raise DiscordRpcRejected("Discord rejected the RPC request", rate_limited=limited,
                                         retry_after=delay, code=code if type(code) is int else None)
            if matches(payload):
                return
        raise TimeoutError("Discord RPC response timed out")

    def poll(self):
        # Also detects a broken pipe when no new activity needs publishing.
        deadline = time.monotonic() + _IO_TIMEOUT
        while time.monotonic() < deadline and self._pipe.readable():
            self._receive(deadline)

    def set_activity(self, activity):
        nonce = uuid4().hex
        deadline = time.monotonic() + _IO_TIMEOUT
        self._send(_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": nonce,
        }, deadline)
        self._wait_for(lambda reply: reply.get("cmd") == "SET_ACTIVITY" and reply.get("nonce") == nonce, deadline, nonce)
