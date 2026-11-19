"""Serial transport for the dual syringe-pump controller.

Every call is bounded and consults a stop event so the UI and controller
can never lock up.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import serial

from . import protocol


class PumpError(RuntimeError):
    """Raised for protocol / transport failures that the UI should show."""


class StopRequested(Exception):
    """Raised internally when a stop is requested mid-operation."""


TraceFn = Callable[[str, str], None]


class PumpDevice:
    def __init__(
        self,
        port: str,
        ack_timeout_ms: int = 300,
        connect_timeout_ms: int = 200,
        trace: TraceFn | None = None,
        serial_factory: Callable[..., serial.Serial] | None = None,
    ) -> None:
        self.port = port
        self.ack_timeout_ms = ack_timeout_ms
        self.connect_timeout_ms = connect_timeout_ms
        self.trace = trace
        self._serial: serial.Serial | None = None
        self._lock = threading.RLock()
        self._serial_factory = serial_factory or serial.Serial

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                return
            self._serial = self._serial_factory(
                port=self.port,
                baudrate=protocol.BAUD_RATE,
                bytesize=protocol.BYTE_SIZE,
                parity=protocol.PARITY,
                stopbits=protocol.STOP_BITS,
                timeout=None,
                write_timeout=1.0,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False,
            )
            self._serial.dtr = True
            self._serial.rts = True
            self._serial.reset_input_buffer()
            self._emit("open", f"{self.port} @ {protocol.BAUD_RATE} 8N1")

    def close(self) -> None:
        with self._lock:
            if self._serial:
                try:
                    if self._serial.is_open:
                        self._serial.close()
                finally:
                    self._serial = None
                    self._emit("close", self.port)

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def _require_open(self) -> serial.Serial:
        if not self._serial or not self._serial.is_open:
            raise PumpError("serial port is not open")
        return self._serial

    def _emit(self, kind: str, message: str) -> None:
        if self.trace:
            try:
                self.trace(kind, message)
            except Exception:
                pass

    # -- raw I/O -----------------------------------------------------------
    def write_byte(self, value: int) -> None:
        with self._lock:
            ser = self._require_open()
            data = bytes((int(value) & 0xFF,))
            ser.write(data)
            ser.flush()
            self._emit("tx", protocol.hexbyte(value))

    def read_byte(self, timeout_ms: int) -> int | None:
        with self._lock:
            ser = self._require_open()
            ser.timeout = max(0.0, timeout_ms / 1000.0)
            data = ser.read(1)
            if data:
                value = data[0]
                self._emit("rx", protocol.hexbyte(value))
                return value
            return None

    def flush_input(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.reset_input_buffer()

    # -- primitives --------------------------------------------------------
    def send_and_wait(
        self,
        value: int,
        timeout_ms: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> int | None:
        """Send one byte and wait (bounded) for one reply byte."""
        self.write_byte(value)
        return self.wait_reply(timeout_ms, stop_event)

    def wait_reply(
        self,
        timeout_ms: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> int | None:
        """Wait for a reply byte, honouring both a deadline and a stop event."""
        timeout_ms = self.ack_timeout_ms if timeout_ms is None else timeout_ms
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            if stop_event is not None and stop_event.is_set():
                raise StopRequested()
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return None
            value = self.read_byte(min(remaining_ms, 50))
            if value is not None:
                return value

    # -- high level --------------------------------------------------------
    def connect(self, attempts: int = protocol.CONNECT_ATTEMPTS) -> None:
        """Device probe: send 0x05 until the controller answers."""
        self.open()
        self.flush_input()
        for _ in range(attempts):
            reply = self.send_and_wait(
                protocol.CMD_DEVICE, timeout_ms=self.connect_timeout_ms
            )
            if reply is not None:
                self._emit("connect", f"device acknowledged (0x{reply:02X})")
                return
        raise PumpError("device not connected")

    def home(
        self,
        target: str | int = 1,
        timeout_s: float = 60.0,
        stop_event: threading.Event | None = None,
        channel: int | None = None,
    ) -> int | None:
        """Home the requested target ('both', 'A'/1, or 'B'/2) and wait for 0x32."""
        actual_target = channel if channel is not None else target
        cmd = protocol.home_byte(actual_target)
        self.write_byte(cmd)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                self.stop()
                raise StopRequested()
            value = self.read_byte(50)
            if value is None:
                continue
            if value in (protocol.CMD_HOME_ACK, protocol.ACK_HOME_LIMIT):
                return value
            if value in (protocol.ACK_FAR_LIMIT_A, protocol.ACK_FAR_LIMIT_B):
                raise PumpError("far limit reached while homing")
        return None

    def stop(self) -> None:
        """Best-effort stop; sends 0x14 and drains reply."""
        try:
            self.write_byte(protocol.CMD_STOP)
            self.wait_reply(timeout_ms=200)
        except Exception:
            pass

    def send_frame_with_ack(
        self,
        frame_bytes: list[int],
        stop_event: threading.Event | None = None,
        max_misses: int = 3,
    ) -> None:
        """Send a 9-byte frame waiting for the controller's per-byte acknowledgement."""
        misses = 0
        open_loop = False
        for b in frame_bytes:
            if stop_event is not None and stop_event.is_set():
                raise StopRequested()
            if not open_loop:
                reply = self.send_and_wait(b, timeout_ms=self.ack_timeout_ms, stop_event=stop_event)
                if reply is None:
                    misses += 1
                    if misses >= max_misses:
                        open_loop = True
                        self._emit("warning", "frame ack missing; continuing open-loop")
                else:
                    misses = 0
            else:
                self.write_byte(b)

    def trigger_run(self, withdrawal: bool = False) -> None:
        """Trigger the queued frames to start moving (0x0F forward, 0x37 reverse)."""
        cmd = protocol.TRIGGER_REVERSE if withdrawal else protocol.TRIGGER_FORWARD
        self.write_byte(cmd)

    def wait_dual_outcome(
        self,
        expected_s: float,
        withdrawal: bool = False,
        stop_event: threading.Event | None = None,
        grace_s: float = 3.0,
        on_event: Callable[[int], None] | None = None,
        active_a: bool = True,
        active_b: bool = True,
    ) -> dict[str, bool]:
        """Monitor running motion for completion or limit bytes."""
        deadline = time.monotonic() + max(0.0, expected_s) + max(0.0, grace_s)
        outcomes = {
            "a_done": False,
            "b_done": False,
            "all_done": False,
            "far_limit_a": False,
            "far_limit_b": False,
            "home_limit_a": False,
            "home_limit_b": False,
            "timeout": False,
        }

        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                self.stop()
                raise StopRequested()

            b = self.read_byte(50)
            if b is None:
                continue

            if on_event:
                try:
                    on_event(b)
                except Exception:
                    pass

            if not withdrawal:
                if b == protocol.ACK_PUMP_A_DONE_FWD:
                    outcomes["a_done"] = True
                    if not active_b:
                        return outcomes
                elif b == protocol.ACK_PUMP_B_DONE_FWD:
                    outcomes["b_done"] = True
                    if not active_a:
                        return outcomes
                elif b == protocol.ACK_ALL_DONE:
                    outcomes["all_done"] = True
                    return outcomes
                elif b == protocol.ACK_FAR_LIMIT_A:
                    outcomes["far_limit_a"] = True
                elif b == protocol.ACK_FAR_LIMIT_B:
                    outcomes["far_limit_b"] = True
            else:
                if b == protocol.ACK_PUMP_A_DONE_REV:
                    outcomes["a_done"] = True
                    if not active_b:
                        return outcomes
                elif b == protocol.ACK_PUMP_B_DONE_REV:
                    outcomes["b_done"] = True
                    if not active_a:
                        return outcomes
                elif b == protocol.ACK_ALL_DONE:
                    outcomes["all_done"] = True
                    return outcomes
                elif b == protocol.ACK_HOME_LIMIT_A:
                    outcomes["home_limit_a"] = True
                elif b == protocol.ACK_HOME_LIMIT_B:
                    outcomes["home_limit_b"] = True

        outcomes["timeout"] = True
        return outcomes

    # -- Legacy methods for single-pump test suite compatibility -----------
    def send_program_start(
        self, stop_event: threading.Event | None = None
    ) -> int | None:
        return self.send_and_wait(protocol.CMD_PROGRAM_START, stop_event=stop_event)

    def send_step_count(
        self, count: int, stop_event: threading.Event | None = None
    ) -> int | None:
        return self.send_and_wait(int(count) & 0xFF, stop_event=stop_event)

    def send_withdrawal(self, withdrawal: bool) -> None:
        self.write_byte(protocol.withdrawal_byte(withdrawal))

    def send_values(
        self,
        tdsp: float,
        duration_seconds: int,
        prescaler: int,
        stop_event: threading.Event | None = None,
        max_consecutive_misses: int = 3,
    ) -> None:
        frame = protocol.send_values_frame(tdsp, duration_seconds, prescaler)
        misses = 0
        open_loop = False
        pending_ack = False

        for value, expects_reply in frame:
            if stop_event is not None and stop_event.is_set():
                raise StopRequested()
            if expects_reply and not open_loop:
                reply = self.send_and_wait(value, stop_event=stop_event)
                if reply is None:
                    misses += 1
                    if misses >= max_consecutive_misses:
                        open_loop = True
                        self._emit(
                            "warning",
                            "controller is not acknowledging the frame; continuing open-loop",
                        )
                else:
                    misses = 0
            else:
                self.write_byte(value)
            pending_ack = expects_reply

        if pending_ack and not open_loop:
            self.wait_reply(timeout_ms=100, stop_event=stop_event)

    def wait_step_outcome(
        self,
        expected_s: float,
        stop_event: threading.Event | None = None,
        grace_s: float = 2.0,
    ) -> str:
        deadline = time.monotonic() + max(0.0, expected_s) + max(0.0, grace_s)
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise StopRequested()
            value = self.read_byte(50)
            if value is None:
                continue
            if value in (protocol.ACK_STEP_DONE, protocol.ACK_ALL_DONE):
                return "done"
            if value in (protocol.ACK_FAR_LIMIT, protocol.ACK_FAR_LIMIT_A, protocol.ACK_FAR_LIMIT_B):
                return "far_limit"
            if value in (protocol.ACK_HOME_LIMIT, protocol.ACK_HOME_LIMIT_A, protocol.ACK_HOME_LIMIT_B):
                return "home_limit"
        return "elapsed"


def list_ports() -> list[dict]:
    from serial.tools import list_ports as _list_ports

    found = []
    for info in _list_ports.comports():
        found.append(
            {
                "device": info.device,
                "description": info.description or "",
                "hwid": info.hwid or "",
            }
        )
    return sorted(found, key=lambda item: item["device"])
