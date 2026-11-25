"""
The contract every VNA driver implements (docs/adding-a-vna.md explains how to add one).

A driver wraps one instrument model's SCPI for exactly what wheecal needs:
  - identify the instrument and check it is in the right mode and measurement (never change them),
  - write, read back, back up and restore its user cal-kit definition (from a CalKit, SI units),
  - run the steps of a calibration the instrument already offers on its panel: measure each standard, apply
    ("Done"), abort, and read whether the calibration is on.

Calibrations are named by measurement: 'S11' (1-port Open/Short/Load) and 'S21' (2-port transmission). Each driver
lists its steps in CAL_STEPS, e.g. {'S11': ('OPEN', 'SHORT', 'LOAD'), 'S21': ('THRU',)}. Step names wheecal knows
(and sets the LibreCAL for): OPEN, SHORT, LOAD (one port), THRU (a through between two ports), ISOLATION (both
ports terminated in LOAD).

Safety rules for every driver:
  - nothing is sent from __init__ or connect() except identification queries;
  - writes go through write(), which refuses anything outside ALLOWED_WRITES (kit fields and cal steps only):
    never span, points, IF bandwidth, source power, mode, measurement, preset or "clear calibration";
  - every write is recorded in self.writes (tests and dry runs check it).
"""
from __future__ import annotations

import abc

from ..calkit import CalKit
from ..transport import ScpiError, SocketTransport


class VnaDriver(abc.ABC):
    #: Registry key (lowercase, no spaces) and the name shown in the GUI.
    NAME = ''
    TITLE = ''
    #: TCP port of the instrument's raw SCPI socket.
    DEFAULT_SCPI_PORT = 5555
    #: Calibration steps per measurement, in the order they run. Leave a measurement out if unsupported.
    CAL_STEPS: dict = {}
    #: Command prefixes write() accepts. Everything else raises ScpiError before it is sent.
    ALLOWED_WRITES: tuple = ()
    #: Units of the kit fields in result rows (field -> unit string).
    FIELD_UNITS: dict = {}

    def __init__(self, transport):
        self.t = transport
        self.writes: list[str] = []

    # ── connection ──────────────────────────────────────────────────────────
    @classmethod
    def connect(cls, host: str, port: int | None = None, timeout: float = 5.0) -> 'VnaDriver':
        return cls(SocketTransport(host, port or cls.DEFAULT_SCPI_PORT, timeout=timeout))

    @classmethod
    @abc.abstractmethod
    def mock(cls, **kw) -> 'VnaDriver':
        """A simulated instrument (tests and dry runs) that answers like the real one and logs every line."""

    def close(self) -> None:
        self.t.close()

    def query(self, q: str, timeout: float = 3.0) -> str:
        return self.t.query(q, timeout=timeout)

    def write(self, cmd: str) -> None:
        """Send one setting. Subclasses add pacing (e.g. *OPC?) but must keep the whitelist check."""
        if not cmd.startswith(self.ALLOWED_WRITES):
            raise ScpiError(f'refused (not a cal-kit or calibration command): {cmd}')
        self.t.write(cmd)
        self.writes.append(cmd)

    # ── state ───────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def identify(self) -> dict:
        """{'idn': *IDN? reply, 'mode': e.g. 'VNA', 'meas': e.g. 'S11'|'S21'|''}."""

    @abc.abstractmethod
    def require_mode(self, meas: str | None = None) -> dict:
        """identify(), raising ScpiError with an instruction for the user when the instrument is not in VNA mode
        or (with meas) not measuring meas. Never changes the mode."""

    def span(self) -> tuple[float, float] | None:
        """The sweep (Hz), read only, or None."""
        return None

    def snapshot(self) -> tuple[bytes, str]:
        """(image bytes, file extension) of the screen."""
        raise ScpiError(f'{self.TITLE} has no screenshot support in wheecal')

    def supports(self, meas: str) -> bool:
        return meas in self.CAL_STEPS

    # ── cal kit ─────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def write_kit(self, kit: CalKit, progress=None) -> list[dict]:
        """Write the kit into the instrument's user kit, read every field back (re-send dropped writes), select
        that kit. Returns rows {standard, field, unit, want, got, ok}; progress(done, total, row) if given."""

    def preview_kit(self, kit: CalKit) -> list[dict]:
        """Rows like write_kit() (got None) for what write_kit(kit) would write; sends nothing."""
        rows = [('OPEN', 'length', 'm', kit.open_len_m), ('SHORT', 'length', 'm', kit.short_len_m),
                ('LOAD', 'R', 'ohm', kit.load_r), ('THRU', 'length', 'm', kit.thru_len_m)]
        rows += [('OPEN', f'C{i}', f'F/Hz^{i}', v) for i, v in enumerate(kit.open_c)]
        rows += [('SHORT', f'L{i}', f'H/Hz^{i}', v) for i, v in enumerate(kit.short_l)]
        return [{'standard': s, 'field': f, 'unit': u, 'want': v, 'got': None, 'ok': True} for s, f, u, v in rows]

    @abc.abstractmethod
    def verify_kit(self, kit: CalKit, standards=None) -> list[dict]:
        """Read back and compare (no field writes). standards: a subset of OPEN/SHORT/LOAD/THRU, default all."""

    @abc.abstractmethod
    def user_kit_selected(self) -> bool:
        """True when the instrument's selected kit is the user kit that write_kit() fills."""

    @abc.abstractmethod
    def backup_kit(self) -> dict:
        """The user kit as JSON-able data, including {'driver': NAME}."""

    @abc.abstractmethod
    def restore_kit(self, data: dict, progress=None) -> list[dict]:
        """Write a backup_kit() result back. Returns rows like write_kit()."""

    # ── calibration steps ───────────────────────────────────────────────────
    @abc.abstractmethod
    def cal_measure(self, meas: str, step: str, settle_s: float = 3.0) -> None:
        """Measure one standard (as the panel's button), and wait until it is done."""

    @abc.abstractmethod
    def cal_save(self, meas: str, wait_s: float = 15.0) -> bool:
        """Apply the calibration ("Done"). True once cal_state(meas) reads on."""

    @abc.abstractmethod
    def cal_abort(self, meas: str) -> None:
        """Abort a calibration in progress."""

    @abc.abstractmethod
    def cal_state(self, meas: str) -> bool:
        """Whether the meas calibration is on."""

    def panel_hint(self, meas: str, step: str) -> str:
        """What the operator presses on the instrument for a step in guided mode (step 'DONE' = apply)."""
        return f'{meas} calibration: {step.capitalize()}'
