"""Wire protocol for the Holmarc dual syringe-pump / motion controller.

Reverse-engineered from `Dual Syringe Pump.exe` (Holmarc 2019, `DualPump.exe`)
and `Infusion Pump.exe`.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Serial line settings
# ---------------------------------------------------------------------------
BAUD_RATE = 19200
BYTE_SIZE = 8
PARITY = "N"
STOP_BITS = 1

# ---------------------------------------------------------------------------
# Command / acknowledgement bytes
# ---------------------------------------------------------------------------
CMD_DIR_MINUS = 0x00        # DirMinus
CMD_DIR_PLUS = 0x01         # DirPlus -> SyringeWithdrawal(true)
CMD_WITHDRAW_OFF = 0x02     # SyringeWithdrawal(false)
CMD_DEVICE = 0x05           # Connect probe -> expects CMD_ACK_OK (0x0A)
CMD_ACK_OK = 0x0A           # OkAck
CMD_STOP = 0x14             # Emergency / manual stop (20)
CMD_HOME = 0x1A             # Home, rack 1 (26)
CMD_HOME_CH2 = 0x1B         # Home, rack 2 (27)
CMD_ADC_READ = 0x1E         # AdcRead
CMD_LIMIT = 0x28            # Limit
CMD_HOME_ACK = 0x32         # HomeAck (50)
CMD_X_FAR = 0x43            # XFar
CMD_X_HOME = 0x46           # XHome
CMD_MOTOR_SPEED_XY = 0x51   # MotorSpeedXy
CMD_X_HOME_LIMIT = 0x54     # XHomeLimit
CMD_X_FAR_LIMIT = 0x55      # XFarLimit
CMD_X_MOVE = 0x65           # XMove

CMD_PROGRAM_START = 0x23    # program start (single-channel legacy)
CMD_PROGRAM_GO = 0x0F       # final byte of SendValues frame
ACK_STEP_DONE = 0x1E        # step finished normally
ACK_FAR_LIMIT = 0x23        # far limit switch hit
ACK_HOME_LIMIT = 0x32       # home limit switch hit

# Dual-pump commands (from Dual Syringe Pump.exe)
CMD_HOME_A = 0x1A           # Home Pump 1 / Rack A (26)
CMD_HOME_B = 0x1B           # Home Pump 2 / Rack B (27)
CMD_HOME_BOTH = 0x19        # Home BOTH pumps simultaneously (25)

OPENER_PUMP_A = 0x0A        # Opener for Pump A frame (10)
OPENER_PUMP_B = 0x28        # Opener for Pump B frame (40)

TRIGGER_FORWARD = 0x0F      # Start Forward (Infusion) for all queued frames (15)
TRIGGER_REVERSE = 0x37      # Start Reverse (Withdrawal) for all queued frames (55, '7')

# Real-time feedback emitted by the controller while running
ACK_PUMP_A_DONE_FWD = 0x1F  # Pump A Program Completed (31)
ACK_PUMP_B_DONE_FWD = 0x20  # Pump B Program Completed (32, ' ')
ACK_ALL_DONE = 0x1E         # All active programs / steps completed (30)
ACK_FAR_LIMIT_A = 0x23      # Pump A Far Limit Reached (35, '#')
ACK_FAR_LIMIT_B = 0x24      # Pump B Far Limit Reached (36, '$')

ACK_PUMP_A_DONE_REV = 0x3C  # Pump A Program Completed (60, '<')
ACK_PUMP_B_DONE_REV = 0x4B  # Pump B Program Completed (75, 'K')
ACK_HOME_LIMIT_A = 0x46     # Pump A Home Limit Reached (70, 'F')
ACK_HOME_LIMIT_B = 0x50     # Pump B Home Limit Reached (80, 'P')

CONNECT_ATTEMPTS = 20


# ---------------------------------------------------------------------------
# Axis Profiles
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AxisProfile:
    name: str
    device: int = CMD_DEVICE
    program_start: int = CMD_PROGRAM_START
    opener: int = OPENER_PUMP_A
    terminator: int = TRIGGER_FORWARD
    stop: int = CMD_STOP
    direction_plus: int = CMD_DIR_PLUS
    direction_minus: int = CMD_WITHDRAW_OFF
    home: int = CMD_HOME
    flow_verified: bool = True

    def summary(self) -> str:
        return f"axis {self.name}: opener=0x{self.opener:02X} home=0x{self.home:02X}"


AXIS_A = AxisProfile(
    name="A",
    opener=OPENER_PUMP_A,
    home=CMD_HOME_A,
    direction_plus=0x01,
    direction_minus=0x02,
    flow_verified=True,
)
AXIS_B = AxisProfile(
    name="B",
    program_start=0x24,
    opener=0x0B,
    terminator=0x10,
    stop=0x15,
    home=CMD_HOME_B,
    direction_plus=0x03,
    direction_minus=0x04,
    flow_verified=False,
)


def profile_for(axis: int | str) -> AxisProfile:
    """Resolve 1/'a'/'A' to AXIS_A and 2/'b'/'B' to AXIS_B."""
    if isinstance(axis, str):
        key = axis.strip().upper()
        if key in ("A", "1"):
            return AXIS_A
        if key in ("B", "2"):
            return AXIS_B
        raise ValueError(f"unknown axis {axis!r}")
    return AXIS_B if int(axis) == 2 else AXIS_A


# ---------------------------------------------------------------------------
# Frame encoding
# ---------------------------------------------------------------------------
def encode_u16(value: int) -> bytes:
    """Big-endian two-byte encoding, as sent for tdsp and prescaler."""
    v = int(value) & 0xFFFF
    return bytes(((v >> 8) & 0xFF, v & 0xFF))


def encode_u32(value: int) -> bytes:
    """Big-endian four-byte encoding, as sent for duration*100."""
    v = int(value) & 0xFFFFFFFF
    return bytes(
        ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    )


def build_pump_frame(
    axis: str | int,
    tdsp: float,
    duration_seconds: float,
    prescaler: int,
) -> list[int]:
    """Build the exact 9-byte frame sequence for a given pump.

    Frame format:
      [Opener, Tdsp_hi, Tdsp_lo, Dur_3, Dur_2, Dur_1, Dur_0, Presc_hi, Presc_lo]
    """
    key = str(axis).strip().upper()
    opener = OPENER_PUMP_B if key in ("B", "2") else OPENER_PUMP_A
    hi = (int(tdsp) >> 8) & 0xFF
    lo = int(tdsp) & 0xFF
    duration_packet = int(round(duration_seconds * 100))
    prescaler_hi = (int(prescaler) >> 8) & 0xFF
    prescaler_lo = int(prescaler) & 0xFF

    dur_bytes = encode_u32(duration_packet)
    return [
        opener,
        hi,
        lo,
        dur_bytes[0],
        dur_bytes[1],
        dur_bytes[2],
        dur_bytes[3],
        prescaler_hi,
        prescaler_lo,
    ]


def send_values_frame(
    tdsp: float,
    duration_seconds: int,
    prescaler: int,
    profile: AxisProfile | None = None,
):
    prof = profile or AXIS_A
    hi = int(tdsp) // 0x100
    lo = int(tdsp) % 0x100
    duration_packet = int(duration_seconds) * 100
    prescaler_hi = int(prescaler) // 0x100
    prescaler_lo = int(prescaler) % 0x100

    frame = [
        (prof.opener, True),
        (hi, True),
        (lo, True),
    ]
    for byte in encode_u32(duration_packet):
        frame.append((byte, True))
    frame.append((prescaler_hi, True))
    frame.append((prescaler_lo, True))
    frame.append((prof.terminator, False))
    return frame


def withdrawal_byte(withdrawal: bool, profile: AxisProfile | None = None) -> int:
    prof = profile or AXIS_A
    return prof.direction_plus if withdrawal else prof.direction_minus


def home_byte(channel: int | str = 1) -> int:
    """Return the home command byte for 'both', 1/'A', or 2/'B'."""
    if isinstance(channel, str):
        t = channel.strip().lower()
        if t in ("both", "all"):
            return CMD_HOME_BOTH
        if t in ("b", "2"):
            return CMD_HOME_CH2
        return CMD_HOME
    return CMD_HOME_CH2 if int(channel) == 2 else CMD_HOME


def program_start_byte(profile: AxisProfile | None = None) -> int:
    return (profile or AXIS_A).program_start


def hexbyte(value: int | None) -> str:
    return "None" if value is None else f"0x{value:02X}"
