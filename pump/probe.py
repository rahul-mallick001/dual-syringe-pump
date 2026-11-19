"""Protocol probe harness for finding Rack 2's run/flow command.

Why this exists
---------------
The original `Infusion Pump.exe` contains **no** second-axis flow code - that
was established by disassembly (see `DUAL-RACK-PLAN.md`).  Rack 2's *home*
command (`0x1B`) was found empirically, so the controller firmware clearly
knows about a second axis; whether it also exposes a flow command for that
axis is unknown and can only be settled against the hardware.

Two things made earlier attempts unreliable, and this harness fixes both:

1. **The pump silently leaves PC/remote mode** when its panel is touched.  Every
   command then goes unanswered, which looks exactly like "the byte does
   nothing".  `liveness_gate()` refuses to run a trial unless the controller
   answers, so that failure mode can no longer masquerade as a negative result.

2. **2 mm of rack travel is invisible.**  5 ml/min for 8 s moves ~2 mm.  The
   detector below removes the need to see anything at all.

The detector
------------
`Home()` in the original blocks until the controller answers `0x32` (HomeAck),
which happens when the axis *reaches* the home limit.  So the **round-trip time
of the home command is a position probe**: short when the rack is already
parked, long when it had to travel back.

Each trial therefore runs:

    measure home latency (park + baseline)
      -> send candidate
      -> measure home latency again

If the second measurement is clearly longer, the candidate moved the rack.

Usage
-----
    python -m pump.probe doctor
    python -m pump.probe calibrate --confirm-motion
    python -m pump.probe trial h1 --confirm-motion
    python -m pump.probe all --confirm-motion

Nothing moves without `--confirm-motion`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import protocol
from .device import PumpDevice, PumpError

# Trial motion profile: 15 ml/min on a 20 mm syringe for 12 s ~ 9.5 mm travel,
# which is big enough to be obvious if anyone is watching as well.
TRIAL_TDSP = 2237          # 0x08BD - 15 ml/min @ 20 mm (see tests/test_fidelity)
TRIAL_DURATION_S = 12
TRIAL_PRESCALER = 1

#: A latency difference below this is indistinguishable from jitter.
LATENCY_NOISE_S = 0.30


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def frame_bytes(
    opener: int,
    terminator: int,
    tdsp: float = TRIAL_TDSP,
    duration_s: int = TRIAL_DURATION_S,
    prescaler: int = TRIAL_PRESCALER,
) -> list[int]:
    """The SendValues payload, with caller-chosen delimiters."""
    hi = int(tdsp) // 0x100
    lo = int(tdsp) % 0x100
    packet = int(duration_s) * 100
    return [
        opener,
        hi,
        lo,
        (packet >> 24) & 0xFF,
        (packet >> 16) & 0xFF,
        (packet >> 8) & 0xFF,
        packet & 0xFF,
        int(prescaler) // 0x100,
        int(prescaler) % 0x100,
        terminator,
    ]


def axis_a_run_sequence() -> list[int]:
    """The proven Rack-1 run sequence, built from the real protocol layer."""
    seq = [
        protocol.CMD_DEVICE,
        protocol.CMD_PROGRAM_START,
        0x01,                                   # one step
        protocol.withdrawal_byte(False),        # infuse
    ]
    seq += [byte for byte, _ in protocol.send_values_frame(
        TRIAL_TDSP, TRIAL_DURATION_S, TRIAL_PRESCALER)]
    return seq


@dataclass
class Trial:
    key: str
    title: str
    rationale: str
    sequence: list[int]
    #: Which rack the hypothesis expects to move.
    target_axis: str = "B"
    #: How long to let the trial play out before probing position.  Any
    #: sequence carrying a SendValues frame must be allowed to finish, or the
    #: controller is still busy and the probe after it is meaningless.
    settle_s: float = 2.0

    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.sequence)


def build_trials() -> list[Trial]:
    """The ranked hypotheses from DUAL-RACK-PLAN.md."""
    frame_a = frame_bytes(protocol.CMD_ACK_OK, protocol.CMD_PROGRAM_GO)
    frame_b_full = frame_bytes(0x0B, 0x10)      # both delimiters shifted
    frame_b_open = frame_bytes(0x0B, 0x0F)      # only the opener shifted
    # Any sequence carrying a frame needs the full step duration, plus margin.
    after_frame = TRIAL_DURATION_S + 4.0

    return [
        Trial(
            key="h1",
            title="H1 - complete axis-B frame (low bit set on every command)",
            rationale=(
                "0x1A->0x1B proved axis B is A+1 for home.  Apply the same +1 to "
                "the whole sequence, never mixing A and B bytes."
            ),
            sequence=[
                0x05,            # device
                0x24,            # program start, axis B
                0x01,            # one step
                0x03,            # direction, axis B
            ] + frame_b_full,
            settle_s=after_frame,
        ),
        Trial(
            key="h2",
            title="H2 - generic Y axis: set speed, then move",
            rationale=(
                "DataType exposes MotorSpeedXy (0x51) and YMove (0x6F).  A bare "
                "0x6F with no speed set would do nothing, which may explain why "
                "the earlier 100x repeat showed no motion.  Send speed first."
            ),
            sequence=[
                0x05,
                0x51, 0x00, 0x64,   # MotorSpeedXy, speed 100
                0x6F,               # YMove
            ],
            settle_s=3.0,
        ),
        Trial(
            key="h3",
            title="H3 - axis-B start byte with only the opener shifted",
            rationale=(
                "0x24 program start; frame opener 0x0B but the original 0x0F "
                "terminator, in case only the leading delimiter is axis-encoded."
            ),
            sequence=[0x05, 0x24, 0x01, 0x02] + frame_b_open,
            settle_s=after_frame,
        ),
        Trial(
            key="h4",
            title="H4 - latch axis B first, then the unmodified A frame",
            rationale=(
                "0x1B demonstrably latches axis B to reach its home switch.  If it "
                "also selects the axis for subsequent commands, the ordinary A "
                "frame will flow Rack 2."
            ),
            sequence=[0x05, 0x1B, 0x23, 0x01, 0x02] + frame_a,
            settle_s=after_frame,
        ),
        Trial(
            key="h5",
            title="H5 - axis-A framing with axis-B direction bytes",
            rationale=(
                "Cheap last variant: maybe only the direction byte is "
                "axis-encoded and the rest is shared.  Low expectation."
            ),
            sequence=[0x05, 0x23, 0x01, 0x03] + frame_a,
            settle_s=after_frame,
        ),
        Trial(
            key="h6",
            title="H6 - axis-B start byte one below axis A (0x22)",
            rationale=(
                "0x24 produced no reply at all in H1/H3, so the B start byte is "
                "probably not A+1.  Try A-1 before giving up on the offset idea."
            ),
            sequence=[0x05, 0x22, 0x01, 0x02] + frame_a,
            settle_s=after_frame,
        ),
        Trial(
            key="h7",
            title="H7 - two steps on a shared frame, step-count high bit set",
            rationale=(
                "If one program drives both racks in lockstep, the step count "
                "0x81 may mean 'two axes, one step each'."
            ),
            sequence=[0x05, 0x23, 0x81, 0x02] + frame_a,
            settle_s=after_frame,
        ),
        # ------------------------------------------------------------------
        # Round 2: the even/odd parity idea.
        #
        # Every proven channel-1 opcode is even and the +1 odd neighbour is the
        # channel-2 counterpart: 0x1A->0x1B (home, verified on hardware).
        # H1/H3 tested 0x0B but paired it with 0x24, which the controller never
        # acknowledges - so the opener was rejected before it was ever reached.
        # These keep the *proven* 0x23 start byte and shift only the delimiters.
        # ------------------------------------------------------------------
        Trial(
            key="p1",
            title="P1 - proven 0x23 start, frame shifted to 0x0B..0x10",
            rationale=(
                "0x23 is the only proven start byte. 0x24 is rejected outright. "
                "Shift both delimiters by +1 to the channel-2 parity."
            ),
            sequence=[0x05, 0x23, 0x01, 0x02] + frame_b_full,
            settle_s=after_frame,
        ),
        Trial(
            key="p2",
            title="P2 - proven 0x23 start, opener 0x0B, terminator 0x0F",
            rationale=(
                "Same as P1 but keeps the original GO byte, in case only the "
                "opener carries the channel."
            ),
            sequence=[0x05, 0x23, 0x01, 0x02] + frame_b_open,
            settle_s=after_frame,
        ),
        Trial(
            key="p3",
            title="P3 - P1 plus the channel-2 stop byte 0x15 first",
            rationale=(
                "0x14/0x15 is the stop pair by the same parity rule. Select the "
                "channel with its stop, then send its frame."
            ),
            sequence=[0x05, 0x15, 0x23, 0x01, 0x02] + frame_b_full,
            settle_s=after_frame,
        ),
        Trial(
            key="t1",
            title="T1 - 0x24 select then the STANDARD 0x0A..0x0F frame",
            rationale=(
                "The gap: every earlier 0x24 test paired it with the bogus 0x0B "
                "opener, so 0x24 was never fairly tested. 0x24 is silent, which "
                "fits a 'select pump 2' directive that needs no ack rather than "
                "an invalid command. Standard frame after it."
            ),
            sequence=[0x05, 0x24, 0x01, 0x02] + frame_a,
            settle_s=after_frame,
        ),
        Trial(
            key="t2",
            title="T2 - queue pump 2 then pump 1, one GO fires both",
            rationale=(
                "The panel workflow: program both, press Start once. If 0x24 "
                "queues and the trailing 0x0F starts everything queued, both "
                "run together."
            ),
            sequence=[0x05, 0x24, 0x01, 0x02] + frame_a + [0x23, 0x01, 0x02] + frame_a,
            settle_s=after_frame,
        ),
    ]


# ---------------------------------------------------------------------------
# Device interaction
# ---------------------------------------------------------------------------
def open_device(port: str, trace=None) -> PumpDevice:
    dev = PumpDevice(port=port, trace=trace)
    dev.open()
    return dev


def liveness_gate(dev: PumpDevice, attempts: int = 5) -> bool:
    """Refuse to test unless the controller actually answers.

    Returns True if the device acked.  A 'no reply' here has a specific, common
    cause: the pump has dropped out of PC/remote mode because its panel was
    used.  Say so, rather than letting the caller record a fake negative.
    """
    dev.flush_input()
    for _ in range(attempts):
        reply = dev.send_and_wait(protocol.CMD_DEVICE, timeout_ms=250)
        if reply == protocol.CMD_ACK_OK:
            return True
    return False


def measure_home_latency(
    dev: PumpDevice, home_byte: int, timeout_s: float = 30.0
) -> float | None:
    """Send a home command and return seconds until the HomeAck.

    ``None`` means no acknowledgement arrived - the controller is not tracking
    that axis, or it is not listening.
    """
    dev.flush_input()
    started = time.monotonic()
    dev.write_byte(home_byte)
    deadline = started + timeout_s
    while time.monotonic() < deadline:
        value = dev.read_byte(50)
        if value is None:
            continue
        if value in (protocol.CMD_HOME_ACK, protocol.ACK_HOME_LIMIT):
            return time.monotonic() - started
    return None


def parked_latency(dev: PumpDevice, home_byte: int) -> float | None:
    """Latency of a home command issued to a rack that is *already* parked.

    Two homes in a row: the first brings the rack to the switch wherever it
    started (that timing is discarded, because it includes travel we did not
    ask for), the second measures the genuinely-parked response.
    """
    measure_home_latency(dev, home_byte)      # bring it home; timing discarded
    time.sleep(0.3)
    return measure_home_latency(dev, home_byte)


def send_sequence(dev: PumpDevice, sequence: list[int]) -> list[int | None]:
    """Send bytes one at a time, collecting whatever each one answers."""
    replies: list[int | None] = []
    for byte in sequence:
        dev.write_byte(byte)
        replies.append(dev.read_byte(40))
    return replies


def settle(dev: PumpDevice, seconds: float = 1.0) -> None:
    """Let the motion finish, then abort anything left half-sent.

    A rejected frame can leave the controller waiting for the rest of a packet,
    after which it stops answering *everything* - including home.  Repeated
    stops are what clear that state, so sending just one is not enough.
    """
    time.sleep(seconds)
    for _ in range(4):
        dev.write_byte(protocol.CMD_STOP)
        time.sleep(0.08)
        while dev.read_byte(20) is not None:
            pass
    time.sleep(0.2)


def recover(dev: PumpDevice) -> bool:
    """Clear any partial-frame state and confirm the controller is listening."""
    for _ in range(4):
        dev.write_byte(protocol.CMD_STOP)
        time.sleep(0.08)
        while dev.read_byte(20) is not None:
            pass
    time.sleep(0.2)
    return liveness_gate(dev, attempts=6)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
@dataclass
class TrialResult:
    trial: Trial
    baseline_s: float | None = None
    after_s: float | None = None
    replies: list[int | None] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        """True when the post-trial home clearly took longer than the baseline."""
        if self.baseline_s is None or self.after_s is None:
            return False
        return (self.after_s - self.baseline_s) > LATENCY_NOISE_S

    @property
    def inconclusive(self) -> bool:
        """Position could not be measured after the trial.

        This is *not* evidence of no motion - the controller may have been busy
        or not listening.  Reporting it as a negative would repeat exactly the
        mistake that wasted earlier rounds.
        """
        return self.baseline_s is not None and self.after_s is None

    def describe(self) -> str:
        base = "n/a" if self.baseline_s is None else f"{self.baseline_s:.2f}s"
        after = "n/a" if self.after_s is None else f"{self.after_s:.2f}s"
        if self.inconclusive:
            verdict = "INCONCLUSIVE - no home ack after trial"
        elif self.moved:
            verdict = "MOVED"
        else:
            verdict = "no motion"
        return f"{self.trial.key}: baseline {base} -> after {after}  => {verdict}"


def run_trial(dev: PumpDevice, trial: Trial) -> TrialResult:
    result = TrialResult(trial=trial)

    # Park axis B, then measure the genuinely-parked response as the baseline.
    result.baseline_s = parked_latency(dev, protocol.CMD_HOME_CH2)
    time.sleep(0.5)

    result.replies = send_sequence(dev, trial.sequence)
    settle(dev, trial.settle_s)

    # If it moved, this call has to wait for travel.  Crucially, do not trust a
    # missing ack as "no motion": the controller may simply still be busy.
    result.after_s = measure_home_latency(dev, protocol.CMD_HOME_CH2)
    return result


def calibrate(dev: PumpDevice) -> dict[str, float | None]:
    """Prove the latency detector discriminates, using Rack 1 as the control.

    Rack 1 is fully controllable, so we can park it, time the home command,
    drive it away with a known-good run, and time again.  If those two numbers
    do not separate, the detector is unusable for Rack 2 either.
    """
    at_home = measure_home_latency(dev, protocol.CMD_HOME)
    time.sleep(0.5)

    send_sequence(dev, axis_a_run_sequence())
    settle(dev, 2.0)

    away = measure_home_latency(dev, protocol.CMD_HOME)
    return {"at_home_s": at_home, "away_s": away}


def positive_control(dev: PumpDevice) -> TrialResult:
    """Run the *known-good* Rack-1 sequence against Rack 1's detector.

    Every negative result in `all` is only meaningful if the instrument can
    register a movement we already know happens.  This is that check: if the
    detector does not report MOVED here, the whole run is invalid.
    """
    control = Trial(
        key="ctrl",
        title="positive control - proven Rack-1 sequence against Rack 1",
        rationale="Closes the loop: the detector must see a move it knows about.",
        sequence=axis_a_run_sequence(),
        target_axis="A",
        settle_s=TRIAL_DURATION_S + 4.0,
    )
    result = TrialResult(trial=control)
    result.baseline_s = parked_latency(dev, protocol.CMD_HOME)
    time.sleep(0.5)
    result.replies = send_sequence(dev, control.sequence)
    settle(dev, control.settle_s)
    result.after_s = measure_home_latency(dev, protocol.CMD_HOME)
    return result


# ---------------------------------------------------------------------------
# Channel / cable identity
# ---------------------------------------------------------------------------
CHANNEL_MAP_PATH = Path(__file__).resolve().parent.parent / "channel-map.json"


def swap_identity(dev: PumpDevice) -> dict:
    """Measure which channel moves, for the current cable configuration.

    The software can time each channel independently, but it cannot know which
    *physical* rack a cable feeds - that is the operator's observation.  What it
    can do is prove unambiguously whether a given channel has flow, so results
    from swapped and unswapped sessions stay distinguishable.
    """
    report: dict = {}

    # Park both channels, then take parked baselines.
    report["ch1_parked_s"] = parked_latency(dev, protocol.CMD_HOME)
    report["ch2_parked_s"] = parked_latency(dev, protocol.CMD_HOME_CH2)
    time.sleep(0.5)

    # Drive channel 1 with the proven sequence.
    report["ch1_replies"] = send_sequence(dev, axis_a_run_sequence())
    settle(dev, TRIAL_DURATION_S + 4.0)

    # Which channel is now away from home?
    report["ch1_after_s"] = measure_home_latency(dev, protocol.CMD_HOME)
    report["ch2_after_s"] = measure_home_latency(dev, protocol.CMD_HOME_CH2)

    def moved(before, after):
        return (
            before is not None
            and after is not None
            and (after - before) > LATENCY_NOISE_S
        )

    report["ch1_moved"] = moved(report["ch1_parked_s"], report["ch1_after_s"])
    report["ch2_moved"] = moved(report["ch2_parked_s"], report["ch2_after_s"])
    return report


def save_channel_map(report: dict, note: str = "") -> str:
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        **{k: v for k, v in report.items() if not isinstance(v, list)},
    }
    CHANNEL_MAP_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(CHANNEL_MAP_PATH)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _report_ports() -> None:
    from . import device as device_mod

    ports = device_mod.list_ports()
    if not ports:
        print("  no serial ports detected")
    for entry in ports:
        print(f"  {entry['device']:<8} {entry['description']}  [{entry.get('hwid', '')}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pump.probe", description=__doc__)
    parser.add_argument(
        "action",
        choices=["doctor", "list", "calibrate", "control", "swap", "trial", "all"],
    )
    parser.add_argument("key", nargs="?", help="trial key (h1..h5)")
    parser.add_argument("--port", default="COM6")
    parser.add_argument("--confirm-motion", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the byte sequences without opening the port")
    args = parser.parse_args(argv)

    trials = build_trials()

    if args.action == "list":
        for trial in trials:
            print(f"\n[{trial.key}] {trial.title}")
            print(f"     {trial.rationale}")
            print(f"     bytes: {trial.hex()}")
        return 0

    if args.dry_run or args.action == "doctor":
        print("Rack-1 (proven) run sequence:")
        print("  " + " ".join(f"{b:02X}" for b in axis_a_run_sequence()))
        for trial in trials:
            print(f"\n[{trial.key}] {trial.title}")
            print("  " + trial.hex())
        if args.action == "doctor":
            print("\nserial ports:")
            _report_ports()
        return 0

    moves = args.action in ("calibrate", "control", "swap", "trial", "all")
    if moves and not args.confirm_motion:
        print("refusing to move hardware without --confirm-motion", file=sys.stderr)
        return 2

    dev = open_device(args.port, trace=lambda k, m: print(f"  trace {k}: {m}"))
    try:
        print(f"liveness gate on {args.port} ...")
        if not liveness_gate(dev):
            print(
                "  controller did NOT answer 0x05 -> 0x0A.\n"
                "  It has almost certainly dropped out of PC/remote mode.\n"
                "  Put the pump back into PC mode on its front panel and retry.",
                file=sys.stderr,
            )
            return 3
        print("  controller alive\n")

        if args.action == "calibrate":
            result = calibrate(dev)
            home, away = result["at_home_s"], result["away_s"]
            print(f"  Rack 1 home latency parked : {home}")
            print(f"  Rack 1 home latency away   : {away}")
            if home is not None and away is not None:
                delta = away - home
                verdict = "USABLE" if delta > LATENCY_NOISE_S else "TOO NOISY"
                print(f"  delta {delta:.2f}s -> detector {verdict}")
            return 0

        if args.action == "swap":
            print("[swap] channel identity for the current cable configuration\n")
            report = swap_identity(dev)
            print(f"  ch1 parked {report['ch1_parked_s']:.2f}s"
                  if report["ch1_parked_s"] is not None else "  ch1 parked n/a")
            print(f"  ch1 after  {report['ch1_after_s']:.2f}s"
                  if report["ch1_after_s"] is not None else "  ch1 after  n/a")
            print(f"  ch2 parked {report['ch2_parked_s']:.2f}s"
                  if report["ch2_parked_s"] is not None else "  ch2 parked n/a")
            print(f"  ch2 after  {report['ch2_after_s']:.2f}s"
                  if report["ch2_after_s"] is not None else "  ch2 after  n/a")
            print()
            print(f"  channel 1 moved : {report['ch1_moved']}")
            print(f"  channel 2 moved : {report['ch2_moved']}")
            print()
            if report["ch1_moved"] and not report["ch2_moved"]:
                print("  As expected: flow lives on channel 1 only.")
            elif report["ch1_moved"] and report["ch2_moved"]:
                print("  BOTH channels moved - this contradicts earlier results.")
                print("  Check whether the frames were queued then started together.")
            else:
                print("  Channel 1 did NOT move - investigate before trusting anything.")
            print()
            print("  Now record the physical side, which software cannot see:")
            print("    which rack is plugged into CONNECTOR 1 right now?")
            path = save_channel_map(report, note="record cable configuration")
            print(f"  saved -> {path}")
            return 0

        if args.action == "control":
            print("[ctrl] positive control - proven Rack-1 sequence")
            control = positive_control(dev)
            print(f"  {control.describe()}")
            if control.moved:
                print("\n  detector VALIDATED: it registers a known-good movement.")
                return 0
            print("\n  detector FAILED to see a movement that definitely happens.\n"
                  "  Every negative result from `all` must be treated as invalid.",
                  file=sys.stderr)
            return 4

        selected = trials if args.action == "all" else [
            t for t in trials if t.key == (args.key or "").lower()
        ]
        if not selected:
            keys = ", ".join(t.key for t in trials)
            print(f"unknown trial {args.key!r}; try one of {keys}", file=sys.stderr)
            return 2

        hits = []
        unclear = []
        for trial in selected:
            print(f"[{trial.key}] {trial.title}")
            print(f"  sending: {trial.hex()}")
            result = run_trial(dev, trial)
            replies = " ".join(
                "--" if r is None else f"{r:02X}" for r in result.replies)
            print(f"  replies: {replies}")
            print(f"  {result.describe()}\n")
            if result.moved:
                hits.append(trial.key)
            elif result.inconclusive:
                unclear.append(trial.key)

        if hits:
            print(f"*** candidates that moved rack {selected[0].target_axis}: "
                  f"{', '.join(hits)} ***")
        else:
            print("no candidate moved the target rack")
        if unclear:
            print(f"inconclusive (retest needed): {', '.join(unclear)}")
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    sys.exit(main())
