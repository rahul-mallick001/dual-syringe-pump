"""Run controller: replicates MainForm.MotorRun / DeviceControl.SendValues.

Supports both dual-rack simultaneous execution (the official Dual Syringe Pump
protocol) and multi-step sequential programs, with bounded operations and
instant emergency stop handling.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from . import device as device_mod
from . import protocol
from . import safety
from .program import (
    DurationType,
    DurationUnit,
    FlowRateUnit,
    ProgramData,
    StepCommand,
    VolumeUnit,
    calculate_values,
    load_program,
    save_program,
)


def format_hms(total_seconds: float | None) -> str:
    if total_seconds is None:
        return "00 : 00 : 00"
    seconds = max(0, int(round(total_seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d} : {minutes:02d} : {secs:02d}"


class PumpController:
    """Thread-safe façade over PumpDevice plus program state."""

    def __init__(
        self, device_factory: Callable[[str], device_mod.PumpDevice] | None = None
    ) -> None:
        self._lock = threading.RLock()
        self._device: device_mod.PumpDevice | None = None
        self._device_factory = device_factory or (
            lambda port: device_mod.PumpDevice(port=port, trace=self._trace_fn)
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._trace: list[str] = []

        self.port = ""
        self.connected = False
        self.status = "Ready"
        self.running = False
        self.homing = False
        self.error: str | None = None
        self.fault: str | None = None

        self.at_home = False
        self.at_home2 = False

        # Dual channel configurations
        self.pump_a = ProgramData(step=1, pumb_status=True)
        self.pump_b = ProgramData(step=2, pumb_status=True)
        self.pump_a.cal_duration = self.pump_a.compute_cal_duration()
        self.pump_b.cal_duration = self.pump_b.compute_cal_duration()
        self.status_a = "Ready"
        self.status_b = "Ready"

        # Mode flag: True = dual pump mode (default for UI), False = legacy step list
        self.is_dual_mode = True

        # Multi-step sequential program (steps 1..5 initialized by default)
        self.steps: list[ProgramData] = [
            ProgramData(
                step=i + 1,
                pumb_status=(i == 0),
                diameter=14.5,
                flow_rate=1.0,
                flow_rate_unit=FlowRateUnit.ML_MIN,
                duration_type=DurationType.DURATION,
                duration=60.0,
                duration_unit=DurationUnit.SECOND,
            )
            for i in range(5)
        ]
        for s in self.steps:
            s.cal_duration = s.compute_cal_duration()
        self.selected: list[bool] = [s.pumb_status for s in self.steps]
        self.withdrawal = False

        self.current_step = 0
        self.step_remaining = 0.0
        self.total_remaining = 0.0
        self.step_total = 0.0
        self.total_total = 0.0

        self._plan: list[tuple[int, ProgramData, StepCommand]] = []
        self._plan_index = 0
        self._step_started = 0.0

        self._step_started_a = 0.0
        self._step_started_b = 0.0
        self._dur_a = 0.0
        self._dur_b = 0.0

    # -- tracing -----------------------------------------------------------
    def _trace_fn(self, kind: str, message: str) -> None:
        with self._lock:
            self._trace.append(f"{time.strftime('%H:%M:%S')} {kind:>7} {message}")
            if len(self._trace) > 400:
                del self._trace[: len(self._trace) - 400]

    def recent_trace(self) -> list[str]:
        with self._lock:
            return list(self._trace[-40:])

    # -- dual channel parameters -------------------------------------------
    def update_pump(self, axis: str, payload: dict) -> None:
        with self._lock:
            self.is_dual_mode = True
            key = str(axis).strip().upper()
            target = self.pump_a if key in ("A", "1") else self.pump_b
            if "diameter" in payload:
                target.diameter = float(payload["diameter"])
            if "flowRate" in payload:
                target.flow_rate = float(payload["flowRate"])
            if "flowRateUnit" in payload:
                target.flow_rate_unit = FlowRateUnit(int(payload["flowRateUnit"]))
            if "durationType" in payload:
                target.duration_type = DurationType(int(payload["durationType"]))
            if "duration" in payload:
                target.duration = float(payload["duration"])
            if "durationUnit" in payload:
                target.duration_unit = DurationUnit(int(payload["durationUnit"]))
            if "volumeUnit" in payload:
                target.volume_unit = VolumeUnit(int(payload["volumeUnit"]))
            if "enabled" in payload:
                target.pumb_status = bool(payload["enabled"])
            elif "pumbStatus" in payload:
                target.pumb_status = bool(payload["pumbStatus"])
            if "withdrawal" in payload:
                target.withdrawal = bool(payload["withdrawal"])
            target.cal_duration = target.compute_cal_duration()
            self._recompute_preview()

    # -- program / steps (legacy and multi-step) ---------------------------
    def set_steps(self, steps: list[ProgramData]) -> None:
        with self._lock:
            self.steps = steps
            self.selected = [step.pumb_status for step in steps]
            self.is_dual_mode = False
            for index, step in enumerate(steps):
                step.pumb_status = self.selected[index]
            if len(steps) >= 1:
                self.pump_a = steps[0]
            if len(steps) >= 2:
                self.pump_b = steps[1]
            self._recompute_preview()

    def set_selected(self, selected: list[bool]) -> None:
        with self._lock:
            self.selected = list(selected)
            for index, step in enumerate(self.steps):
                if index < len(self.selected):
                    step.pumb_status = self.selected[index]
            if len(self.selected) >= 1 and self.pump_a:
                self.pump_a.pumb_status = self.selected[0]
            if len(self.selected) >= 2 and self.pump_b:
                self.pump_b.pumb_status = self.selected[1]
            self._recompute_preview()

    def set_withdrawal(self, value: bool) -> None:
        with self._lock:
            self.withdrawal = bool(value)
            self.pump_a.withdrawal = bool(value)
            self.pump_b.withdrawal = bool(value)
            for step in self.steps:
                step.withdrawal = bool(value)

    def load_file(self, path: str) -> None:
        steps = load_program(path)
        with self._lock:
            self.steps = steps
            self.selected = [step.pumb_status for step in steps]
            self.is_dual_mode = False
            if steps:
                self.withdrawal = bool(steps[0].withdrawal)
            if len(steps) >= 1:
                self.pump_a = steps[0]
            if len(steps) >= 2:
                self.pump_b = steps[1]
            self.status = "Successfully Loaded"
            self.error = None
        self._recompute_preview()

    def save_file(self, path: str) -> None:
        with self._lock:
            steps = list(self.steps) if self.steps else [self.pump_a, self.pump_b]
        save_program(path, steps)
        with self._lock:
            self.status = "Successfully Saved"

    def set_mode(self, mode: str) -> None:
        with self._lock:
            m = str(mode).strip().lower()
            if m in ("dual", "simultaneous"):
                self.is_dual_mode = True
            elif m in ("steps", "step", "sequential"):
                self.is_dual_mode = False
            self._recompute_preview()

    def update_step(self, index: int, payload: dict) -> None:
        with self._lock:
            self.is_dual_mode = False
            if not self.steps or not (0 <= index < len(self.steps)):
                raise ValueError("invalid step index")

            step = self.steps[index]
            if "diameter" in payload:
                step.diameter = float(payload["diameter"])
            if "flowRate" in payload:
                step.flow_rate = float(payload["flowRate"])
            if "flowRateUnit" in payload:
                step.flow_rate_unit = FlowRateUnit(int(payload["flowRateUnit"]))
            if "durationType" in payload:
                step.duration_type = DurationType(int(payload["durationType"]))
            if "duration" in payload:
                step.duration = float(payload["duration"])
            if "durationUnit" in payload:
                step.duration_unit = DurationUnit(int(payload["durationUnit"]))
            if "volumeUnit" in payload:
                step.volume_unit = VolumeUnit(int(payload["volumeUnit"]))
            if "withdrawal" in payload:
                step.withdrawal = bool(payload["withdrawal"])
            if "enabled" in payload:
                step.pumb_status = bool(payload["enabled"])
                if index < len(self.selected):
                    self.selected[index] = step.pumb_status
            elif "pumbStatus" in payload:
                step.pumb_status = bool(payload["pumbStatus"])
                if index < len(self.selected):
                    self.selected[index] = step.pumb_status
            step.cal_duration = step.compute_cal_duration()
            self._recompute_preview()

    def _recompute_preview(self) -> None:
        if self.running or self.homing:
            return
        if not self.is_dual_mode and self.steps:
            chosen = [s for s, on in zip(self.steps, self.selected) if on]
            if not chosen:
                self.total_total = 0.0
                self.total_remaining = 0.0
                self.step_total = 0.0
                self.step_remaining = 0.0
                return
            total = 0.0
            for step in chosen:
                step.cal_duration = step.compute_cal_duration()
                total += step.cal_duration
            self.total_total = total
            self.total_remaining = total
            self.step_total = chosen[0].cal_duration
            self.step_remaining = chosen[0].cal_duration
            self.current_step = 1
        else:
            self.pump_a.cal_duration = self.pump_a.compute_cal_duration()
            self.pump_b.cal_duration = self.pump_b.compute_cal_duration()
            dur_a = self.pump_a.cal_duration if self.pump_a.pumb_status else 0.0
            dur_b = self.pump_b.cal_duration if self.pump_b.pumb_status else 0.0
            total = max(dur_a, dur_b)
            self.total_total = total
            self.total_remaining = total
            self.step_total = total
            self.step_remaining = total
            self.current_step = 1

    # -- connection --------------------------------------------------------
    def connect(self, port: str) -> None:
        self.disconnect()
        dev = self._device_factory(port)
        if getattr(dev, "trace", None) is None:
            dev.trace = self._trace_fn
        dev.open()
        dev.connect()
        with self._lock:
            self._device = dev
            self.port = port
            self.connected = True
            self.status = "Ready"
            self.error = None

    def disconnect(self) -> None:
        self.stop(wait=True)
        with self._lock:
            dev = self._device
            self._device = None
            self.connected = False
            self.status = "Ready"
        if dev:
            try:
                dev.close()
            except Exception:
                pass

    @property
    def device(self) -> device_mod.PumpDevice:
        if not self._device:
            raise device_mod.PumpError("device not connected")
        return self._device

    # -- motion: homing ----------------------------------------------------
    def home(self, channel: int | str = 1) -> None:
        with self._lock:
            if not self._device:
                raise RuntimeError("device not connected")
            if self.running or self.homing:
                raise RuntimeError("process already in progress")
            self._stop_event = threading.Event()
            self.homing = True
            ch_str = str(channel).strip().lower()
            if ch_str in ("both", "all"):
                self.status = "Moving both racks to Home..."
            else:
                self.status = f"Moving rack {channel} to Home..."
            self.error = None
        threading.Thread(
            target=self._home_worker, args=(channel,), name="MotorHome", daemon=True
        ).start()

    def home_all(self) -> None:
        """Home both racks simultaneously using CMD_HOME_BOTH (0x19)."""
        self.home("both")

    def _home_worker(self, channel: int | str = 1) -> None:
        try:
            dev = self.device
            dev.connect()
            reply = dev.home(
                target=channel,
                timeout_s=safety.HOME_TIMEOUT_S,
                stop_event=self._stop_event,
            )
            with self._lock:
                ch_str = str(channel).strip().lower()
                if reply is None:
                    self.status = "Error"
                    self.error = f"homing rack {channel} timed out"
                    self.fault = "homing timed out - check the rack before running"
                else:
                    self.error = None
                    self.fault = None
                    if ch_str in ("both", "all"):
                        self.status = "Home Limit Reached (both racks)"
                        self.at_home = True
                        self.at_home2 = True
                        self.status_a = "Home Limit Reached"
                        self.status_b = "Home Limit Reached"
                    elif ch_str in ("2", "b"):
                        self.status = "Home Limit Reached (rack 2)"
                        self.at_home2 = True
                        self.status_b = "Home Limit Reached"
                    else:
                        self.status = "Home Limit Reached (rack 1)"
                        self.at_home = True
                        self.status_a = "Home Limit Reached"
        except device_mod.StopRequested:
            with self._lock:
                self.status = "Program Terminated"
        except Exception as exc:
            with self._lock:
                self.status = "Error"
                self.error = str(exc)
        finally:
            with self._lock:
                self.homing = False

    # -- motion: run -------------------------------------------------------
    def run(self, mode: str | None = None) -> None:
        with self._lock:
            if not self._device:
                raise RuntimeError("device not connected")
            if self.fault:
                raise RuntimeError(
                    f"run blocked: {self.fault} - clear the fault first"
                )
            if self.running or self.homing:
                raise RuntimeError("process already in progress")

            target_mode = mode or ("dual" if self.is_dual_mode else "steps")

            if target_mode == "dual":
                self._prepare_dual_run()
            else:
                self._prepare_steps_run()

        self._thread = threading.Thread(
            target=self._run_dual_worker if target_mode == "dual" else self._run_steps_worker,
            name="MotorRun",
            daemon=True,
        )
        self._thread.start()

    def _prepare_dual_run(self) -> None:
        active_a = bool(self.pump_a.pumb_status)
        active_b = bool(self.pump_b.pumb_status)
        if not active_a and not active_b:
            raise RuntimeError("Please Select a Syringe Pump (Pump 1 and/or Pump 2)")

        cmd_a: StepCommand | None = None
        cmd_b: StepCommand | None = None

        if active_a:
            err_a = self.pump_a.validate()
            if err_a:
                raise RuntimeError(f"Pump 1: {err_a}")
            cmd_a = calculate_values(self.pump_a)
            if cmd_a.tdsp > 65535:
                raise RuntimeError("Pump 1: Flow rate too low for 16-bit speed")

        if active_b:
            err_b = self.pump_b.validate()
            if err_b:
                raise RuntimeError(f"Pump 2: {err_b}")
            cmd_b = calculate_values(self.pump_b)
            if cmd_b.tdsp > 65535:
                raise RuntimeError("Pump 2: Flow rate too low for 16-bit speed")

        if self.withdrawal:
            if active_a and self.at_home:
                raise RuntimeError("rack is at the home stop: withdrawal blocked.")
            if active_b and self.at_home2:
                raise RuntimeError("rack 2 is at the home stop: withdrawal blocked.")

        self._stop_event = threading.Event()
        self.running = True
        self.error = None
        self.status = "Moving..."

        dur_a = float(cmd_a.duration_step) if cmd_a else 0.0
        dur_b = float(cmd_b.duration_step) if cmd_b else 0.0
        max_dur = max(dur_a, dur_b)

        self._dur_a = dur_a
        self._dur_b = dur_b
        self.total_total = max_dur
        self.total_remaining = max_dur
        self.step_total = max_dur
        self.step_remaining = max_dur
        now = time.monotonic()
        self._step_started_a = now
        self._step_started_b = now

        if active_a:
            self.at_home = False
            self.status_a = "Moving Reverse" if self.withdrawal else "Moving Forward"
        if active_b:
            self.at_home2 = False
            self.status_b = "Moving Reverse" if self.withdrawal else "Moving Forward"

        self._dual_active_a = active_a
        self._dual_cmd_a = cmd_a
        self._dual_active_b = active_b
        self._dual_cmd_b = cmd_b

    def _prepare_steps_run(self) -> None:
        chosen = [
            (index, step)
            for index, (step, on) in enumerate(zip(self.steps, self.selected))
            if on
        ]
        if not chosen:
            raise RuntimeError("Select Program")

        plan: list[tuple[int, ProgramData, StepCommand]] = []
        for index, step in chosen:
            error = step.validate()
            if error:
                raise RuntimeError(f"Step {step.step}: {error}")
            safety_error = safety.check_step(step)
            if safety_error:
                raise RuntimeError(f"Step {step.step}: {safety_error}")
            command = calculate_values(step)
            if command.tdsp > 65535:
                raise RuntimeError(
                    f"Step {step.step}: flow rate too low for a 16-bit speed value"
                )
            plan.append((index, step, command))

        program_error = safety.check_program([step for _, step in chosen])
        if program_error:
            raise RuntimeError(program_error)

        if self.at_home:
            withdraw_steps = [s.step for _, s in chosen if s.withdrawal]
            if withdraw_steps:
                raise RuntimeError(
                    "rack is at the home stop: a withdrawal step would drive it "
                    "into the stop. Run an infuse step (WITHDRAWAL off) first to "
                    "move it away, or jog it off home from the panel."
                )

        self._plan = plan
        self._plan_index = 0
        self._stop_event = threading.Event()
        self.running = True
        self.error = None
        self.status = "Ready"
        self.total_total = float(sum(cmd.duration_step for _, _, cmd in plan))
        self.total_remaining = self.total_total
        self.current_step = plan[0][1].step
        self.step_total = float(plan[0][2].duration_step)
        self.step_remaining = self.step_total
        self._step_started = time.monotonic()

    def _run_dual_worker(self) -> None:
        dev = self._device
        active_a = self._dual_active_a
        cmd_a = self._dual_cmd_a
        active_b = self._dual_active_b
        cmd_b = self._dual_cmd_b
        withdrawal = self.withdrawal
        try:
            if dev is None:
                raise device_mod.PumpError("device not connected")
            dev.connect()

            if active_a and cmd_a:
                frame_a = protocol.build_pump_frame(
                    axis="A",
                    tdsp=cmd_a.tdsp,
                    duration_seconds=cmd_a.duration_step,
                    prescaler=cmd_a.prescaler,
                )
                dev.send_frame_with_ack(frame_a, stop_event=self._stop_event)

            if active_b and cmd_b:
                frame_b = protocol.build_pump_frame(
                    axis="B",
                    tdsp=cmd_b.tdsp,
                    duration_seconds=cmd_b.duration_step,
                    prescaler=cmd_b.prescaler,
                )
                dev.send_frame_with_ack(frame_b, stop_event=self._stop_event)

            dev.trigger_run(withdrawal=withdrawal)

            max_dur = max(
                (cmd_a.duration_step if cmd_a else 0),
                (cmd_b.duration_step if cmd_b else 0),
            )

            def on_event(b: int) -> None:
                with self._lock:
                    if not withdrawal:
                        if b == protocol.ACK_PUMP_A_DONE_FWD:
                            self.status_a = "Program Completed"
                        elif b == protocol.ACK_PUMP_B_DONE_FWD:
                            self.status_b = "Program Completed"
                        elif b == protocol.ACK_FAR_LIMIT_A:
                            self.status_a = "Far Limit Reached"
                            self.fault = "Pump 1 far limit reached"
                        elif b == protocol.ACK_FAR_LIMIT_B:
                            self.status_b = "Far Limit Reached"
                            self.fault = "Pump 2 far limit reached"
                    else:
                        if b == protocol.ACK_PUMP_A_DONE_REV:
                            self.status_a = "Program Completed"
                        elif b == protocol.ACK_PUMP_B_DONE_REV:
                            self.status_b = "Program Completed"
                        elif b == protocol.ACK_HOME_LIMIT_A:
                            self.status_a = "Home Limit Reached"
                            self.at_home = True
                        elif b == protocol.ACK_HOME_LIMIT_B:
                            self.status_b = "Home Limit Reached"
                            self.at_home2 = True

            outcomes = dev.wait_dual_outcome(
                expected_s=max_dur,
                withdrawal=withdrawal,
                stop_event=self._stop_event,
                on_event=on_event,
                active_a=active_a,
                active_b=active_b,
            )

            with self._lock:
                if outcomes.get("far_limit_a") or outcomes.get("far_limit_b"):
                    self.status = "Far Limit Reached"
                    self.fault = "Far limit reached"
                elif outcomes.get("home_limit_a") or outcomes.get("home_limit_b"):
                    self.status = "Home Limit Reached"
                else:
                    self.status = "Program completed"
                    if active_a:
                        self.status_a = "Program Completed"
                    if active_b:
                        self.status_b = "Program Completed"
                self.step_remaining = 0.0
                self.total_remaining = 0.0

        except device_mod.StopRequested:
            try:
                if dev:
                    dev.stop()
            except Exception:
                pass
            with self._lock:
                self.status = "Program Terminated"
                if active_a:
                    self.status_a = "Program Terminated"
                if active_b:
                    self.status_b = "Program Terminated"
        except Exception as exc:
            try:
                if dev:
                    dev.stop()
            except Exception:
                pass
            with self._lock:
                self.status = "Error"
                self.error = str(exc)
                self._trace.append(traceback.format_exc(limit=2))
        finally:
            with self._lock:
                self.running = False
                if self.status != "Program completed":
                    self._recompute_preview()

    def _run_steps_worker(self) -> None:
        dev = self._device
        try:
            if dev is None:
                raise device_mod.PumpError("device not connected")
            dev.connect()
            dev.send_program_start(stop_event=self._stop_event)
            dev.send_step_count(len(self._plan), stop_event=self._stop_event)

            with self._lock:
                directions = {bool(step.withdrawal) for _, step, _ in self._plan}
                per_step = len(directions) > 1
                global_withdrawal = bool(self.withdrawal)

            if per_step:
                self._trace_fn("note", "alternating direction per step (dual syringe)")
            else:
                dev.send_withdrawal(global_withdrawal)
                time.sleep(0.15)

            for position, (index, step, command) in enumerate(self._plan):
                if self._stop_event.is_set():
                    raise device_mod.StopRequested()

                with self._lock:
                    self._plan_index = position
                    self.current_step = step.step
                    self.step_total = float(command.duration_step)
                    self.step_remaining = self.step_total
                    self.total_remaining = float(
                        sum(c.duration_step for _, _, c in self._plan[position:])
                    )
                    self._step_started = time.monotonic()
                    self.status = f"Step {step.step} Program Started"

                if per_step:
                    dev.send_withdrawal(bool(step.withdrawal))
                    time.sleep(0.15)

                with self._lock:
                    self.at_home = False

                dev.send_values(
                    command.tdsp,
                    command.duration_step,
                    command.prescaler,
                    stop_event=self._stop_event,
                )

                outcome = dev.wait_step_outcome(
                    expected_s=command.duration_step,
                    stop_event=self._stop_event,
                )
                if outcome == "far_limit":
                    try:
                        dev.stop()
                    except Exception:
                        pass
                    with self._lock:
                        self.status = "Far Limit Reached"
                        self.error = "Far Limit Reached"
                        self.fault = "far limit reached - check the rack before running"
                    return
                if outcome == "home_limit":
                    try:
                        dev.stop()
                    except Exception:
                        pass
                    with self._lock:
                        self.status = "Home Limit Reached"
                        self.error = "Home Limit Reached"
                        self.fault = "home limit reached - check the rack before running"
                    return

            with self._lock:
                self.status = "Program completed"
                self.step_remaining = 0.0
                self.total_remaining = 0.0
        except device_mod.StopRequested:
            try:
                if dev:
                    dev.stop()
            except Exception:
                pass
            with self._lock:
                self.status = "Program Terminated"
        except Exception as exc:
            try:
                if dev:
                    dev.stop()
            except Exception:
                pass
            with self._lock:
                self.status = "Error"
                self.error = str(exc)
                self._trace.append(traceback.format_exc(limit=2))
        finally:
            with self._lock:
                self.running = False
                if self.status != "Program completed":
                    self._recompute_preview()

    def stop(self, wait: bool = False) -> None:
        with self._lock:
            was_running = self.running or self.homing
            self._stop_event.set()
            dev = self._device
        if dev:
            try:
                dev.stop()
            except Exception:
                pass
        if wait and self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if was_running:
            with self._lock:
                if self.status in ("Ready", "Moving..."):
                    self.status = "Program Terminated"

    def emergency_stop(self, reason: str = "emergency stop") -> None:
        with self._lock:
            self._stop_event.set()
            self.fault = reason
            dev = self._device
            self.status = "Program Terminated"
            self.status_a = "Program Terminated"
            self.status_b = "Program Terminated"
        if dev:
            for _ in range(3):
                try:
                    dev.stop()
                except Exception:
                    break
                time.sleep(0.05)

    def clear_fault(self) -> None:
        with self._lock:
            self.fault = None
            self.error = None
            if not self.running and not self.homing:
                self.status = "Ready"

    def mark_at_home(self, value: bool = True, axis: str = "both") -> None:
        with self._lock:
            t = str(axis).lower()
            if t in ("both", "all"):
                self.at_home = bool(value)
                self.at_home2 = bool(value)
            elif t in ("b", "2"):
                self.at_home2 = bool(value)
            else:
                self.at_home = bool(value)

    def state(self) -> dict:
        with self._lock:
            step_remaining = self.step_remaining
            total_remaining = self.total_remaining
            rem_a = self._dur_a
            rem_b = self._dur_b
            if self.running:
                now = time.monotonic()
                if not self.is_dual_mode:
                    elapsed = max(0.0, now - self._step_started)
                    step_remaining = max(0.0, self.step_total - elapsed)
                    total_remaining = max(0.0, self.total_remaining - elapsed)
                else:
                    el_a = max(0.0, now - self._step_started_a)
                    el_b = max(0.0, now - self._step_started_b)
                    rem_a = max(0.0, self._dur_a - el_a) if self.pump_a.pumb_status else 0.0
                    rem_b = max(0.0, self._dur_b - el_b) if self.pump_b.pumb_status else 0.0
                    step_remaining = max(rem_a, rem_b)
                    total_remaining = step_remaining

            return {
                "port": self.port,
                "connected": self.connected,
                "status": self.status,
                "running": self.running,
                "homing": self.homing,
                "error": self.error,
                "fault": self.fault,
                "atHome": self.at_home,
                "atHome2": self.at_home2,
                "racks": {
                    "A": {
                        "name": "PUMP 1",
                        "home": True,
                        "flow": True,
                        "enabled": bool(self.pump_a.pumb_status),
                        "status": self.status_a,
                        "atHome": self.at_home,
                        "diameter": self.pump_a.diameter,
                        "flowRate": self.pump_a.flow_rate,
                        "flowRateUnit": int(self.pump_a.flow_rate_unit),
                        "flowRateUnitLabel": self.pump_a.flow_rate_unit.label,
                        "durationType": int(self.pump_a.duration_type),
                        "durationTypeLabel": self.pump_a.duration_type.label,
                        "duration": self.pump_a.duration,
                        "durationUnit": int(self.pump_a.duration_unit),
                        "durationUnitLabel": self.pump_a.duration_unit.label,
                        "volumeUnit": int(self.pump_a.volume_unit),
                        "volumeUnitLabel": self.pump_a.volume_unit.label,
                        "calDuration": round(self.pump_a.cal_duration, 2),
                        "remainingSeconds": round(rem_a, 1),
                        "remainingHMS": format_hms(rem_a),
                    },
                    "B": {
                        "name": "PUMP 2",
                        "home": True,
                        "flow": True,
                        "enabled": bool(self.pump_b.pumb_status),
                        "status": self.status_b,
                        "atHome": self.at_home2,
                        "diameter": self.pump_b.diameter,
                        "flowRate": self.pump_b.flow_rate,
                        "flowRateUnit": int(self.pump_b.flow_rate_unit),
                        "flowRateUnitLabel": self.pump_b.flow_rate_unit.label,
                        "durationType": int(self.pump_b.duration_type),
                        "durationTypeLabel": self.pump_b.duration_type.label,
                        "duration": self.pump_b.duration,
                        "durationUnit": int(self.pump_b.duration_unit),
                        "durationUnitLabel": self.pump_b.duration_unit.label,
                        "volumeUnit": int(self.pump_b.volume_unit),
                        "volumeUnitLabel": self.pump_b.volume_unit.label,
                        "calDuration": round(self.pump_b.cal_duration, 2),
                        "remainingSeconds": round(rem_b, 1),
                        "remainingHMS": format_hms(rem_b),
                    },
                },
                "mode": "dual" if self.is_dual_mode else "steps",
                "isDualMode": self.is_dual_mode,
                "currentStep": self.current_step,
                "stepRemaining": format_hms(step_remaining),
                "totalRemaining": format_hms(total_remaining),
                "stepRemainingSeconds": round(step_remaining, 1),
                "totalRemainingSeconds": round(total_remaining, 1),
                "steps": [step.to_dict() for step in self.steps],
                "selected": list(self.selected),
                "withdrawal": self.withdrawal,
                "limits": {
                    "maxStepMl": safety.MAX_STEP_VOLUME_ML,
                    "maxTotalMl": safety.MAX_TOTAL_VOLUME_ML,
                    "maxStepSeconds": safety.MAX_STEP_DURATION_S,
                },
                "trace": list(self._trace[-40:]),
            }


controller = PumpController()
