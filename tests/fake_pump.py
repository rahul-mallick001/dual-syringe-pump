"""A fake serial port that mimics the Holmarc controller.

Lets the engine be tested end-to-end (byte framing, acks, completion, stop)
without touching real hardware.
"""

from __future__ import annotations

import collections
import threading
import time

from pump import protocol

_REPLIES = {
    protocol.CMD_DEVICE: [protocol.CMD_ACK_OK],
    protocol.CMD_HOME_BOTH: [protocol.CMD_HOME_ACK],
    protocol.CMD_HOME_A: [protocol.CMD_HOME_ACK],
    protocol.CMD_HOME_B: [protocol.CMD_HOME_ACK],
    protocol.CMD_PROGRAM_START: [0x46],
    protocol.CMD_PROGRAM_GO: [protocol.ACK_STEP_DONE],
    protocol.TRIGGER_FORWARD: [
        protocol.ACK_PUMP_A_DONE_FWD,
        protocol.ACK_PUMP_B_DONE_FWD,
        protocol.ACK_ALL_DONE,
    ],
    protocol.TRIGGER_REVERSE: [
        protocol.ACK_PUMP_A_DONE_REV,
        protocol.ACK_PUMP_B_DONE_REV,
        protocol.ACK_ALL_DONE,
    ],
    protocol.CMD_STOP: [protocol.ACK_STEP_DONE],
    protocol.CMD_DIR_PLUS: [],
    protocol.CMD_WITHDRAW_OFF: [],
}
_GENERIC_ACK = 0x11


class FakeSerial:
    def __init__(self, silent: bool = False, **kwargs) -> None:
        self.kwargs = kwargs
        self.silent = silent
        self.is_open = True
        self.dtr = False
        self.rts = False
        self.timeout = None
        self.written: list[int] = []
        self._rx: collections.deque[int] = collections.deque()
        self._lock = threading.Lock()

    # -- serial-like API ---------------------------------------------------
    def write(self, data) -> int:
        with self._lock:
            for byte in data:
                value = int(byte)
                self.written.append(value)
                for reply in self._respond(value):
                    self._rx.append(reply)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        deadline = time.monotonic() + (self.timeout or 0.0)
        while True:
            with self._lock:
                if self._rx:
                    return bytes((self._rx.popleft(),))
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.001)

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._rx)

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._rx.clear()

    def close(self) -> None:
        self.is_open = False

    # -- behaviour ---------------------------------------------------------
    def _respond(self, value: int) -> list[int]:
        if self.silent:
            if value == protocol.CMD_DEVICE:
                return [protocol.CMD_ACK_OK]
            if value in (protocol.CMD_HOME_BOTH, protocol.CMD_HOME_A, protocol.CMD_HOME_B):
                return [protocol.CMD_HOME_ACK]
            return []
        return _REPLIES.get(value, [_GENERIC_ACK])


class FakeFactory:
    """Callable serial factory that remembers the ports it created."""

    def __init__(self, silent: bool = False) -> None:
        self.created: list[FakeSerial] = []
        self.silent = silent

    def __call__(self, **kwargs) -> FakeSerial:
        port = FakeSerial(silent=self.silent, **kwargs)
        self.created.append(port)
        return port

    @property
    def last(self) -> FakeSerial:
        return self.created[-1]
