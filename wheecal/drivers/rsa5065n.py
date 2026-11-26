"""
Rigol RSA5065N with the VNA option (RSA5000 series VNA mode): custom cal kit, S11 and S21 calibration.

SCPI used (the instrument's own SCPI tree and built-in help; checked with queries on firmware 00.03.08; docs/rsa5065n.md):
  *IDN?  *OPC?  :INST:SEL? (VNA)  :CONF? (S11|S21|DTF)  :FREQ:STAR?  :FREQ:STOP?  :PRIV:SNAP?
  :SWEep:TIME?                                       one sweep, seconds (scales with points / IF bandwidth)
  :TRACe1:DATA?                                      the S11 trace, ASCII "(re,im)(re,im)..." (FORMat:DATA ASC,8)
  :CALibration:CKIT:TYPE OPEN|SHORt|LOAD|THRough   which standard the fields below edit
  :CALibration:CKIT:LENGth|Z0|LOSS|C0..C3|L0..L3     the standard's fields (units below)
  :CALibration:CKIT:LABel CUSTom                     select the Custom kit
  :CALibration:S11:OPEN|SHORt|LOAD                   measure one standard (panel: S11 Meas Setup > Align > ...)
  :CALibration:S11:SAVE | :ABORt | :STATe?           Done | Abort | 0/1 (STATe? is not trustworthy, see Quirks)
  :CALibration:S21:THROugh                           measure the through (panel: S21 Meas Setup > Align > Through)
  :CALibration:S21:SAVE | :ABORt | :STATe?           Done | Abort | 0/1
The S21 calibration is a through (transmission response) calibration only: the firmware's :CALibration:S21 subtree
has THROugh, ABORt, SAVE, STATe and NORState? (a state query), and no isolation, load or match step; the panel's S21
Align menu is Through, Abort, Done. The [:SENSe] prefix in the built-in help gets no reply.

Quirks:
  - Replies have no terminator; a setting written while busy is dropped silently, so every write is followed by
    *OPC? and every kit field is read back (and re-sent up to 3 times).
  - The Custom kit holds ONE open (C0..C3) and ONE short (L0..L3), shared by all standards; only the fields a
    standard enables may be written, and a greyed field's query answers garbage (LOAD LENGth? returns L3/1000).
  - Lengths are meters (the screen shows mm), C in fF/GHz^n, L in pH/GHz^n, LOSS is a flat dB ("Att").
  - The measure commands start an asynchronous measurement ("Open Calibrating..."), and *OPC? does NOT wait for a
    sweep (it answers in ~0.2 s while a 4.5 s sweep runs). One sweep takes :SWEep:TIME? seconds (about 4.5 s at 1000
    points), so after each LibreCAL switch and after each cal step wheecal waits 2 x sweep time + 1 s (at least the
    configured settle times; measurement_settle). With fixed 0.5 s / 3 s waits the OSL captured blends of two
    standards and the applied calibration read well above 0 dB (2026-11-26).
  - :TRACe:DATA? TRACE1 answers 'error': the trace is read with :TRACe1:DATA?.
  - :CALibration:S11:STATe? (and :STAT?) reads 0 even while a correction is on: on 2026-11-25 the panel showed
    "C ?" while it read 0. :NORState?, :CALC:CORR:STAT? and :SENS:CORR:STAT? get no reply. So STATe? is advisory
    only, and a calibration is verified from the screen: the correction badge at the top left of :PRIV:SNAP?
    ("---" none, "Cor" on, "C ?" on but interpolated, "C !" on with a warning), see cal_indicator().
  - :CALibration:<meas>:ABORt after SAVE discards the calibration: wheecal never sends it on its own after Done.
  - The instrument sweeps to 6.5 GHz (FREQ_MAX_HZ): kit fits stop there.
"""
from __future__ import annotations

import re
import threading
import time

from .. import screen
from ..calkit import CalKit
from ..transport import ScpiError
from . import rsa5065n_badge as badge
from .base import VnaDriver

CKIT = ':CALibration:CKIT'
#: wheecal standard -> instrument TYPE, and what :TYPE? answers.
TYPE_OF = {'OPEN': 'OPEN', 'SHORT': 'SHORt', 'LOAD': 'LOAD', 'THRU': 'THRough'}
TYPE_REPLY = {'OPEN': 'OPEN', 'SHORt': 'SHOR', 'LOAD': 'LOAD', 'THRough': 'THRU'}
STANDARD_OF = {v: k for k, v in TYPE_OF.items()}
EDITABLE = {
    'OPEN': ('LENGth', 'LOSS', 'C0', 'C1', 'C2', 'C3'),
    'SHORt': ('LENGth', 'LOSS', 'L0', 'L1', 'L2', 'L3'),
    'LOAD': ('Z0', 'LOSS'),
    'THRough': ('LENGth', 'LOSS'),
}
UNITS = {'LENGth': 'm', 'Z0': 'ohm', 'LOSS': 'dB', 'C0': 'fF', 'C1': 'fF/GHz', 'C2': 'fF/GHz^2', 'C3': 'fF/GHz^3',
         'L0': 'pH', 'L1': 'pH/GHz', 'L2': 'pH/GHz^2', 'L3': 'pH/GHz^3'}
MEASURE = {('S11', 'OPEN'): ':CALibration:S11:OPEN', ('S11', 'SHORT'): ':CALibration:S11:SHORt',
           ('S11', 'LOAD'): ':CALibration:S11:LOAD', ('S21', 'THRU'): ':CALibration:S21:THROugh'}
PANEL = {('S11', 'OPEN'): 'S11 Meas Setup > Align > Open', ('S11', 'SHORT'): 'S11 Meas Setup > Align > Short',
         ('S11', 'LOAD'): 'S11 Meas Setup > Align > Load', ('S11', 'DONE'): 'S11 Meas Setup > Align > Done',
         ('S21', 'THRU'): 'S21 Meas Setup > Align > Through', ('S21', 'DONE'): 'S21 Meas Setup > Align > Done'}


def kit_fields(kit: CalKit) -> dict:
    """A CalKit in this instrument's fields and units: {TYPE: {field: value}} (EDITABLE fields only)."""
    c = {f'C{i}': v * 1e15 * (1e9 ** i) for i, v in enumerate(kit.open_c)}
    lv = {f'L{i}': v * 1e12 * (1e9 ** i) for i, v in enumerate(kit.short_l)}
    return {
        'OPEN': {'LENGth': float(kit.open_len_m), 'LOSS': 0.0, **c},
        'SHORt': {'LENGth': float(kit.short_len_m), 'LOSS': 0.0, **lv},
        'LOAD': {'Z0': float(kit.load_r), 'LOSS': 0.0},
        'THRough': {'LENGth': float(kit.thru_len_m), 'LOSS': 0.0},
    }


def same(a: float | None, b: float) -> bool:
    """Read-back tolerance: the instrument stores about 5 significant digits."""
    return a is not None and abs(a - b) <= max(1e-9, 2e-5 * max(abs(a), abs(b)))


def parse_trace(text: str) -> list[complex]:
    """'(1.0,0.5)(0.9,-0.1)' -> [1+0.5j, 0.9-0.1j]. Raises ValueError on anything else (e.g. 'error')."""
    pts = re.findall(r'\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)', text)
    if not pts or re.sub(r'\([^()]*\)|[\s,]', '', text):
        raise ValueError(f'not an (re,im) trace: {text[:60]!r}')
    return [complex(float(a), float(b)) for a, b in pts]


class RSA5065N(VnaDriver):
    NAME = 'rsa5065n'
    TITLE = 'Rigol RSA5065N (VNA option)'
    DEFAULT_SCPI_PORT = 5555
    CAL_STEPS = {'S11': ('OPEN', 'SHORT', 'LOAD'), 'S21': ('THRU',)}
    ALLOWED_WRITES = (
        ':CALibration:CKIT:',
        ':CALibration:S11:OPEN', ':CALibration:S11:SHORt', ':CALibration:S11:LOAD',
        ':CALibration:S11:SAVE', ':CALibration:S11:ABORt',
        ':CALibration:S21:THROugh', ':CALibration:S21:SAVE', ':CALibration:S21:ABORt',
    )
    FIELD_UNITS = UNITS
    FREQ_MAX_HZ = 6.5e9
    #: The correction badge (top left of the 1024x600 screenshot) and its references.
    INDICATOR = screen.IndicatorSpec(box=badge.BOX, fill=badge.FILL, blank=badge.BLANK,
                                     references={'on': badge.ON, 'interpolated': badge.INTERPOLATED},
                                     labels={'none': '"---"', 'interpolated': '"C ?"', 'on': '"Cor"'})
    #: Settle after a switch or cal step: SWEEP_SETTLE_FACTOR sweeps + SWEEP_SETTLE_MARGIN_S (measurement_settle).
    SWEEP_SETTLE_FACTOR = 2.0
    SWEEP_SETTLE_MARGIN_S = 1.0
    #: Pause after each paced write (the instrument drops settings that arrive too quickly).
    write_gap_s = 0.03

    @classmethod
    def mock(cls, **kw) -> 'RSA5065N':
        vna = cls(MockRSA5065N(**kw))
        vna.write_gap_s = 0.0
        return vna

    # ── basics ──────────────────────────────────────────────────────────────
    def opc(self, timeout: float = 15.0) -> None:
        ans = self.t.query('*OPC?', timeout=timeout)
        if ans.strip() != '1':
            raise ScpiError(f'*OPC? answered {ans!r}')

    def write(self, cmd: str, opc_timeout: float = 15.0) -> None:
        with self.t.lock:
            super().write(cmd)
            self.opc(opc_timeout)
        time.sleep(self.write_gap_s)

    def identify(self) -> dict:
        info = {'idn': self.query('*IDN?')}
        try:
            info['mode'] = self.query(':INST:SEL?')
            info['meas'] = self.query(':CONF?') if info['mode'] == 'VNA' else ''
        except ScpiError:
            info.setdefault('mode', '?')
            info.setdefault('meas', '?')
        return info

    def require_mode(self, meas: str | None = None) -> dict:
        info = self.identify()
        if info['mode'] != 'VNA':
            raise ScpiError(f"the instrument is in {info['mode']} mode, not VNA: switch it to VNA on the panel first")
        if meas and info['meas'] != meas:
            raise ScpiError(f"the VNA measures {info['meas']}, not {meas}: select Meas > {meas} on the panel first "
                            '(wheecal never changes the measurement)')
        return info

    def span(self) -> tuple[float, float] | None:
        try:
            return float(self.query(':FREQ:STAR?')), float(self.query(':FREQ:STOP?'))
        except (ScpiError, ValueError):
            return None

    def sweep_points(self) -> int | None:
        try:
            return int(float(self.query(':SWEep:POINts?')))
        except (ScpiError, ValueError):
            return None

    def sweep_freqs(self) -> list[float] | None:
        """The sweep points in Hz: a linear sweep from span() with :SWEep:POINts? points, or None."""
        sp, n = self.span(), self.sweep_points()
        if sp is None or not n or n < 1:
            return None
        f0, f1 = sp
        return [f0] if n == 1 else [f0 + i * (f1 - f0) / (n - 1) for i in range(n)]

    def sweep_time(self) -> float | None:
        """One sweep in seconds (:SWEep:TIME?), or None when it can't be read."""
        try:
            return float(self.query(':SWEep:TIME?'))
        except (ScpiError, ValueError):
            return None

    def measurement_settle(self, min_s: float) -> float:
        """*OPC? doesn't wait for sweeps here, so wait by time: two full sweeps plus a margin, at least min_s."""
        sw = self.sweep_time()
        if sw is None or sw <= 0:
            return min_s
        return max(min_s, self.SWEEP_SETTLE_FACTOR * sw + self.SWEEP_SETTLE_MARGIN_S)

    def read_s11(self) -> list[complex]:
        """The S11 trace (:TRACe1:DATA?, ASCII "(re,im)(re,im)...") as complex reflection coefficients."""
        return parse_trace(self.query(':TRACe1:DATA?', timeout=10.0))

    def snapshot(self, timeout: float = 20.0) -> tuple[bytes, str]:
        raw = self.t.query(':PRIV:SNAP?', timeout=timeout, raw=True)
        if raw[:1] != b'#':
            raise ScpiError('no screenshot block')
        n = int(raw[1:2])
        return raw[2 + n:2 + n + int(raw[2:2 + n])], 'bmp'

    # ── custom kit ──────────────────────────────────────────────────────────
    def set_type(self, typ: str) -> None:
        want = TYPE_REPLY[typ]
        for _ in range(3):
            self.write(f'{CKIT}:TYPE {typ}')
            if self.query(f'{CKIT}:TYPE?').upper()[:4] == want:
                return
        raise ScpiError(f'Parts Type did not change to {typ}')

    def selected_kit(self) -> str:
        return self.query(f'{CKIT}:LABel?')

    def user_kit_selected(self) -> bool:
        return self.selected_kit().upper().startswith('CUST')

    def select_custom(self) -> None:
        for _ in range(3):
            if self.user_kit_selected():
                return
            self.write(f'{CKIT}:LABel CUSTom')
        raise ScpiError('Selected Kit did not change to Custom')

    def read_fields(self, types=tuple(EDITABLE)) -> dict:
        """The Custom kit's editable fields of the given TYPEs (switches Parts Type to read each, then restores
        it)."""
        before = self.query(f'{CKIT}:TYPE?').upper()[:4]
        out = {}
        for typ in types:
            self.set_type(typ)
            out[typ] = {f: float(self.query(f'{CKIT}:{f}?')) for f in EDITABLE[typ]}
        back = next((t for t, r in TYPE_REPLY.items() if r == before), 'OPEN')
        self.set_type(back)
        return out

    def _write_fields(self, values: dict, progress=None, select: bool = True) -> list[dict]:
        rows, total = [], sum(len(v) for v in values.values())
        for typ in EDITABLE:
            if typ not in values:
                continue
            self.set_type(typ)
            for f, v in values[typ].items():
                if f not in EDITABLE[typ]:
                    continue
                got = None
                for _ in range(3):
                    self.write(f'{CKIT}:{f} {v:.9e}')
                    got = float(self.query(f'{CKIT}:{f}?'))
                    if same(got, v):
                        break
                rows.append(self._row(typ, f, v, got))
                if progress:
                    progress(len(rows), total, rows[-1])
        if select:
            self.select_custom()
        self.set_type('OPEN')
        return rows

    @staticmethod
    def _row(typ, f, want, got) -> dict:
        return {'standard': STANDARD_OF[typ], 'field': f, 'unit': UNITS[f], 'want': want, 'got': got,
                'ok': same(got, want)}

    def write_kit(self, kit: CalKit, progress=None) -> list[dict]:
        return self._write_fields(kit_fields(kit), progress)

    def preview_kit(self, kit: CalKit) -> list[dict]:
        return [dict(self._row(t, f, v, None), ok=True) for t, fs in kit_fields(kit).items() for f, v in fs.items()]

    def verify_kit(self, kit: CalKit, standards=None) -> list[dict]:
        want = kit_fields(kit)
        types = [TYPE_OF[s] for s in (standards or TYPE_OF)]
        got = self.read_fields(types)
        return [self._row(t, f, v, got[t][f]) for t in types for f, v in want[t].items()]

    def backup_kit(self) -> dict:
        data = {'driver': self.NAME, 'selected_kit': self.selected_kit()}
        data.update(self.read_fields())
        return data

    def restore_kit(self, data: dict, progress=None) -> list[dict]:
        if data.get('driver', self.NAME) != self.NAME:
            raise ScpiError(f"this backup is for the {data['driver']} driver, not {self.NAME}")
        values = {t: {f: float(v) for f, v in data[t].items() if f in EDITABLE[t]} for t in EDITABLE if t in data}
        return self._write_fields(values, progress)

    # ── calibration ─────────────────────────────────────────────────────────
    def _check(self, meas: str, step: str | None = None):
        if meas not in self.CAL_STEPS or (step is not None and step not in self.CAL_STEPS[meas]):
            raise ScpiError(f'{self.TITLE} has no {meas} {step or ""} calibration step')

    def cal_measure(self, meas: str, step: str, settle_s: float = 3.0) -> None:
        self._check(meas, step)
        self.write(MEASURE[(meas, step)], opc_timeout=60)
        time.sleep(self.measurement_settle(settle_s))
        self.opc(60)

    def cal_state(self, meas: str) -> bool:
        self._check(meas)
        return self.query(f':CALibration:{meas}:STATe?').strip() == '1'

    def cal_save(self, meas: str, wait_s: float = 3.0) -> bool:
        """Done. Returns STATe? (advisory: this firmware reads 0 with a correction on; see cal_indicator)."""
        self._check(meas)
        self.write(f':CALibration:{meas}:SAVE', opc_timeout=60)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.cal_state(meas):
                return True
            time.sleep(0.5)
        return self.cal_state(meas)

    def cal_abort(self, meas: str) -> None:
        self._check(meas)
        self.write(f':CALibration:{meas}:ABORt')

    def cal_indicator(self, meas: str) -> screen.Indicator:
        """The correction badge on a fresh screenshot (the badge shows the displayed measurement's correction)."""
        self._check(meas)
        img, _ext = self.snapshot()
        return screen.classify(screen.decode_bmp(img), self.INDICATOR)

    def panel_hint(self, meas: str, step: str) -> str:
        return PANEL.get((meas, step), super().panel_hint(meas, step))


class MockRSA5065N:
    """An RSA5065N VNA stand-in for tests and dry runs: keeps the Custom kit, answers the queries wheecal uses and
    records every line written (`log`). Reproduces the greyed-field quirk (LOAD LENGth? = L3/1000) and, with
    `drop_every`, a dropped write. `meas` is what :CONF? answers; `mode` what :INST:SEL? answers."""

    def __init__(self, drop_every: int = 0, cal_state: str = '0', meas: str = 'S11', mode: str = 'VNA',
                 state_lies: bool = False, screen_badge: str | None = None, sweep_s: float = 0.0,
                 trace: str = '(1.0,0.0)(0.99,-0.05)(0.98,-0.1)', points: int = 3):
        self.log: list[str] = []
        self.kit = {'LENGth': {'OPEN': 0.01, 'SHOR': 0.01, 'LOAD': 0.0, 'THRU': 0.0},
                    'Z0': {t: 50.0 for t in ('OPEN', 'SHOR', 'LOAD', 'THRU')},
                    'LOSS': {t: 0.0 for t in ('OPEN', 'SHOR', 'LOAD', 'THRU')}}
        self.poly = {'C0': 13.381, 'C1': -3.0403, 'C2': 0.60794, 'C3': -0.012839,
                     'L0': 41.512, 'L1': -10.695, 'L2': 1.7328, 'L3': -0.12534}
        self.type, self.label = 'OPEN', 'CUST'
        self.meas, self.mode = meas, mode
        self.cal = {'S11': cal_state, 'S21': '0'}
        self.acquired = {'S11': [], 'S21': []}
        self.drop_every, self._writes = drop_every, 0
        self.lock = threading.RLock()
        self.span = {'FREQ:STAR': 1.2e9, 'FREQ:STOP': 1.9e9}
        self._pending = None
        self.state_lies = state_lies         # STATe? always '0', like firmware 00.03.08
        self.screen_badge = screen_badge     # None: from the cal state; none | on | interpolated | other | missing
        self.sweep_s = sweep_s               # :SWEep:TIME? (0: instant sweeps, so waits stay at their floors)
        self.trace = trace                   # :TRACe1:DATA? reply, or a callable returning it (see follow())
        self.points = points                 # :SWEep:POINts?

    #: A corrected S11 reading per LibreCAL state (follow()): open/short near 0 dB, load near -40 dB.
    FOLLOW_GAMMA = {'OPEN': (1.0, 0.0), 'SHORT': (-0.998, 0.01), 'LOAD': (0.01, -0.002)}

    def follow(self, librecal_link, port: int = 1, points: int = 5) -> None:
        """Make :TRACe1:DATA? answer what a well-calibrated VNA reads from a mock LibreCAL's port (its .ports)."""
        def trace():
            re_, im = self.FOLLOW_GAMMA.get(librecal_link.ports[port - 1].split()[0], (0.3, 0.0))
            return ''.join(f'({re_:.6e},{im:.6e})' for _ in range(points))
        self.trace = trace

    #: follow_raw's error box (e00, e11, e01*e10): a plausible uncorrected test port.
    RAW_BOX = (complex(0.08, -0.05), complex(0.12, 0.07), complex(0.62, -0.31))
    #: follow_raw: what the port reads with no LibreCAL standard on it (NONE) or no LibreCAL at all (a DUT).
    RAW_DUT = complex(0.2, -0.1)

    def gamma_at(self, librecal_link, port: int, freqs) -> list[complex]:
        """The true reflection a mock LibreCAL's `port` presents at freqs (its FACTORY data; a through's far port is
        matched, so the through's S11/S22). RAW_DUT when there is no LibreCAL or the port is NONE."""
        from ..hostcal import interp_complex
        state = librecal_link.ports[port - 1].split() if librecal_link is not None else ['NONE']
        if state[0] in ('OPEN', 'SHORT', 'LOAD'):
            return interp_complex(librecal_link.coeffs[f'P{port}_{state[0]}'], freqs)
        if state[0] == 'THROUGH':
            other = int(state[1])
            lo, hi = sorted((port, other))
            rows = librecal_link.coeffs[f'P{lo}{hi}_THROUGH']
            idx = 0 if port < other else 3
            return interp_complex([(f, s[idx]) for f, s in rows], freqs)
        return [self.RAW_DUT] * len(freqs)

    def follow_raw(self, librecal_link=None, port: int = 1, box=None, points: int | None = None) -> None:
        """Make :TRACe1:DATA? answer the UNCORRECTED reading of the mock LibreCAL's port (or of RAW_DUT without
        one) through the error box (e00, e11, e01e10): m = e00 + e01e10*g / (1 - e11*g). For host-cal tests."""
        if points is not None:
            self.points = points

        def trace():
            e00, e11, t = box or self.RAW_BOX
            n = self.points
            f0, f1 = self.span['FREQ:STAR'], self.span['FREQ:STOP']
            freqs = [f0] if n == 1 else [f0 + i * (f1 - f0) / (n - 1) for i in range(n)]
            ms = [e00 + t * g / (1 - e11 * g) for g in self.gamma_at(librecal_link, port, freqs)]
            return ''.join(f'({m.real:.12e},{m.imag:.12e})' for m in ms)
        self.trace = trace

    def screenshot(self) -> bytes:
        """A 1024x600 BMP: black, with the correction badge drawn for the shown measurement."""
        kind = self.screen_badge or ('on' if self.cal.get(self.meas) == '1' else 'none')
        frame = screen.Bitmap.solid(1024, 600)
        if kind != 'missing':
            ref = {'none': badge.BLANK, 'on': badge.ON, 'interpolated': badge.INTERPOLATED}.get(kind)
            frame = frame.paste(ref or badge.BLANK, badge.BOX[0], badge.BOX[1])
            if ref is None:                  # another mark (e.g. "C !"): a glyph that is not a reference
                frame = frame.paste(screen.Bitmap.solid(9, 11, (230, 120, 40)), badge.BOX[0] + 16, badge.BOX[1] + 7)
        return screen.encode_bmp(frame)

    _NAMES = {'TYPE': 'TYPE', 'LENG': 'LENGth', 'LENGTH': 'LENGth', 'LAB': 'LABel', 'LABEL': 'LABel', 'Z0': 'Z0',
              'LOSS': 'LOSS'}

    def _leaf(self, cmd: str) -> str:
        leaf = cmd.split(':')[-1].upper()
        return self._NAMES.get(leaf, leaf)

    @staticmethod
    def _cal_path(up: str):
        """(meas, leaf) of a :CAL[ibration]:S11|S21:<leaf> command, or None."""
        for pre in (':CALIBRATION:', ':CAL:'):
            if up.startswith(pre):
                parts = up[len(pre):].split(':')
                if len(parts) == 2 and parts[0] in ('S11', 'S21'):
                    return parts[0], parts[1]
        return None

    def write(self, line: str) -> None:
        self.log.append(line)
        cmd, _, arg = line.partition(' ')
        up = cmd.upper()
        if up.endswith('?'):
            self._pending = self._answer(up[:-1])
            return
        self._writes += 1
        if self.drop_every and self._writes % self.drop_every == 0:
            return                                      # silently dropped, like the real one when busy
        if up.startswith(':CALIBRATION:CKIT:') or up.startswith(':CAL:CKIT:'):
            leaf = self._leaf(up)
            if leaf == 'TYPE':
                a = arg.upper()
                self.type = 'THRU' if a.startswith('THR') else a[:4]
            elif leaf == 'LABel':
                self.label = arg.upper()[:4]
            elif leaf in self.poly:
                self.poly[leaf] = float(arg)
            elif leaf in self.kit:
                self.kit[leaf][self.type] = float(arg)
            return
        path = self._cal_path(up)
        if path is None:
            return
        meas, leaf = path
        if meas != self.meas:
            return                                      # the panel only calibrates the shown measurement
        if leaf in ('OPEN', 'SHORT', 'SHOR', 'LOAD', 'THROUGH', 'THRU'):
            self.acquired[meas].append(leaf[:4])
        elif leaf == 'SAVE':
            need = {'OPEN', 'SHOR', 'LOAD'} if meas == 'S11' else {'THRO', 'THRU'}
            if (meas == 'S11' and need <= set(self.acquired[meas])) or (meas == 'S21' and need & set(self.acquired[meas])):
                self.cal[meas] = '1'
        elif leaf in ('ABORT', 'ABOR'):
            self.acquired[meas].clear()

    def _answer(self, q: str) -> str | None:
        if q == '*IDN':
            return 'Rigol Technologies,RSA5065N,MOCK0001,00.03.08'
        if q == '*OPC':
            return '1'
        if q in (':INST:SEL', ':INSTRUMENT:SELECT'):
            return self.mode
        if q in (':CONF', ':CONFIGURE'):
            return self.meas
        path = self._cal_path(q)
        if path and path[1] in ('STAT', 'STATE'):
            return '0' if self.state_lies else self.cal[path[0]]
        if q in (':PRIV:SNAP', ':PRIVATE:SNAP'):
            img = self.screenshot()
            n = str(len(img))
            return b'#' + str(len(n)).encode() + n.encode() + img
        if path and path[0] == 'S21' and path[1] in ('NORS', 'NORSTATE'):
            return '0'
        if q in (':SWE:TIME', ':SWEEP:TIME'):
            return f'{self.sweep_s:.9e}'
        if q in (':SWE:POIN', ':SWEEP:POINTS'):
            return str(self.points)
        if q in (':TRAC1:DATA', ':TRACE1:DATA'):
            return self.trace() if callable(self.trace) else self.trace
        if q in (':SYST:ERR', ':SYSTEM:ERROR'):
            return '0,"No error"'
        if q in (':FREQ:STAR', ':FREQ:STOP'):
            return f'{self.span[q[1:]]:.9e}'
        if q.startswith(':CALIBRATION:CKIT:') or q.startswith(':CAL:CKIT:'):
            leaf = self._leaf(q)
            if leaf == 'TYPE':
                return self.type
            if leaf == 'LABel':
                return self.label
            if leaf in self.poly:
                return f'{self.poly[leaf]:.9e}'
            if leaf in self.kit:
                if leaf == 'LENGth' and self.type == 'LOAD':
                    return f'{self.poly["L3"] / 1000:.9e}'      # the real instrument's greyed-field garbage
                return f'{self.kit[leaf][self.type]:.9e}'
        return None                                      # unknown: no reply, like the real one

    def read(self, timeout=None, raw=False):
        ans, self._pending = self._pending, None
        if ans is None:
            raise ScpiError('no reply (unknown command, or the instrument is busy)')
        if isinstance(ans, bytes):
            return ans if raw else ans.decode('latin-1')
        return ans.encode() if raw else ans

    def query(self, q: str, timeout=None, raw=False):
        with self.lock:
            self.write(q)
            return self.read(timeout, raw)

    def close(self) -> None:
        pass
