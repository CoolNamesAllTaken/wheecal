"""
Host-side one-port (S11) calibration: wheecal measures the LibreCAL's Open, Short and Load UNCORRECTED, solves the
VNA's three error terms per frequency against the LibreCAL's own measured standards (its FACTORY coefficients), and
corrects later traces in software.

Why: a VNA's on-board calibration solves against a parametric kit (offset line + lossless C/L polynomial + ideal
load). The LibreCAL's standards are measured data that this model does not fit exactly, so an on-board calibration
with a fitted kit carries the fit error, and where the fit is poor the corrected trace spikes above 0 dB. Solving on
the host against the measured standards themselves has no model error: correcting the raw Open, Short or Load
gives back exactly the LibreCAL's coefficient.

The one-port error model (per frequency), with m the raw reading and g the true reflection coefficient:

    m = e00 + e11 * m * g - De * g        (De = e00*e11 - e01*e10)

Three standards give three linear equations in (e00, e11, De) (solve3, Cramer's rule). A DUT is corrected with

    g = (m - e00) / (e11 * m - De)

The correction lives in software: the VNA's own screen keeps showing the raw (or on-board corrected) trace. Reading
raw traces needs the on-board S11 correction off, so run_host_cal, measure_dut and verify_host_cal abort the VNA's
S11 calibration first (and say so).

Independent check (verify_host_cal): the LibreCAL connects its test port to `through_port` with its internal
through. With the far port terminated in a match (the VNA's 50-ohm input), the reflection the test port sees is the
through's own S11 from the FACTORY data. That standard was not used to solve the terms, so a host-corrected reading
close to it shows the whole chain works. Pass: mean |error| < VERIFY_MAX_MEAN over f >= VERIFY_F_MIN_HZ (the
metric's floor on real hardware is about 0.06, set by the far port's match).
"""
from __future__ import annotations

import bisect
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .fit import thru_coefficient_name
from .librecal import LibreCAL, LibreCALError
from .transport import ScpiError

FORMAT = 'wheecal-host-cal'
FORMAT_VERSION = 1
#: verify_host_cal: pass when the mean |corrected - truth| over f >= VERIFY_F_MIN_HZ is below VERIFY_MAX_MEAN.
VERIFY_MAX_MEAN = 0.12
VERIFY_F_MIN_HZ = 10e6
#: Sweep frequencies of the terms and of a trace must agree to this fraction of the span to apply.
FREQ_MATCH_TOL = 1e-6
RAW_WARNING = ('WARNING: reading RAW S11: the VNA\'s on-board S11 calibration is aborted (cleared). Host-side '
               'correction does not use it; run an on-board calibration again if you want the VNA screen corrected.')

Terms = tuple[complex, complex, complex]


# ── math ─────────────────────────────────────────────────────────────────────
def _det3(m) -> complex:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def solve3(mo: complex, ms: complex, ml: complex, go: complex, gs: complex, gl: complex) -> Terms:
    """(e00, e11, De) at one frequency from raw readings m* of standards with true reflections g*."""
    rows = [[1, mo * go, -go], [1, ms * gs, -gs], [1, ml * gl, -gl]]
    rhs = [mo, ms, ml]
    dd = _det3(rows)
    if dd == 0:
        raise ValueError('the standards are not distinct at this frequency (singular OSL system)')
    sol = []
    for c in range(3):
        m = [r[:] for r in rows]
        for i in range(3):
            m[i][c] = rhs[i]
        sol.append(_det3(m) / dd)
    return sol[0], sol[1], sol[2]


def solve_osl(raw_o, raw_s, raw_l, true_o, true_s, true_l) -> list[Terms]:
    """Per-frequency error terms [(e00, e11, De)] from the raw Open/Short/Load traces and their true values."""
    n = len(raw_o)
    if not all(len(x) == n for x in (raw_s, raw_l, true_o, true_s, true_l)):
        raise ValueError('the raw traces and true standards must have the same number of points')
    return [solve3(raw_o[i], raw_s[i], raw_l[i], true_o[i], true_s[i], true_l[i]) for i in range(n)]


def correct(t: Terms, m: complex) -> complex:
    e00, e11, de = t
    return (m - e00) / (e11 * m - de)


def apply(terms, raw_trace) -> list[complex]:
    """The corrected trace. `terms` is an ErrorTerms or a list of (e00, e11, De), one per point of raw_trace."""
    tl = terms.terms if isinstance(terms, ErrorTerms) else terms
    if len(tl) != len(raw_trace):
        raise ValueError(f'the trace has {len(raw_trace)} points but the calibration {len(tl)}: the sweep changed, '
                         'calibrate again')
    return [correct(t, m) for t, m in zip(tl, raw_trace)]


def interp_complex(rows, freqs) -> list[complex]:
    """Linear interpolation of [(f_Hz, z)] (z complex, or a list whose first item is used) onto freqs, holding the
    end values outside the data. rows must be in ascending frequency."""
    fs = [r[0] for r in rows]
    zs = [r[1][0] if isinstance(r[1], (list, tuple)) else r[1] for r in rows]
    if not fs:
        raise ValueError('no data to interpolate')
    out = []
    for f in freqs:
        if f <= fs[0]:
            out.append(complex(zs[0]))
            continue
        if f >= fs[-1]:
            out.append(complex(zs[-1]))
            continue
        j = bisect.bisect_left(fs, f) - 1          # fs[j] < f <= fs[j+1]
        t = (f - fs[j]) / (fs[j + 1] - fs[j])
        out.append(zs[j] + (zs[j + 1] - zs[j]) * t)
    return out


def residual(est, truth, freqs, f_min: float = VERIFY_F_MIN_HZ) -> dict:
    """|est - truth| over the points at f >= f_min: {'mean', 'max', 'min', 'points'}."""
    e = [abs(a - b) for a, b, f in zip(est, truth, freqs) if f >= f_min]
    if not e:
        raise ValueError(f'no points at or above {f_min / 1e6:g} MHz')
    return {'mean': sum(e) / len(e), 'max': max(e), 'min': min(e), 'points': len(e)}


def _db(z: complex) -> float:
    return 20 * math.log10(max(abs(z), 1e-12))


def summary_db(trace) -> dict:
    dbs = [_db(z) for z in trace]
    return {'min': min(dbs), 'max': max(dbs), 'mean': sum(dbs) / len(dbs), 'points': len(dbs)}


# ── the error terms on disk ──────────────────────────────────────────────────
def _pairs(zs) -> list[list[float]]:
    return [[z.real, z.imag] for z in zs]


def _cx(pairs) -> list[complex]:
    return [complex(a, b) for a, b in pairs]


@dataclass
class ErrorTerms:
    freqs: list[float]                     # Hz, one per sweep point
    terms: list[Terms]                     # (e00, e11, De) per point
    port: int = 1                          # the LibreCAL port the standards were on
    coefficient_set: str = 'FACTORY'
    created: str = ''
    vna: str = ''
    librecal: str = ''

    def to_json(self) -> dict:
        return {'format': FORMAT, 'version': FORMAT_VERSION, 'wheecal': __version__, 'created': self.created,
                'vna': self.vna, 'librecal': self.librecal, 'port': self.port,
                'coefficient_set': self.coefficient_set, 'freqs': list(self.freqs),
                'e00': _pairs(t[0] for t in self.terms), 'e11': _pairs(t[1] for t in self.terms),
                'De': _pairs(t[2] for t in self.terms)}

    @classmethod
    def from_json(cls, d: dict) -> 'ErrorTerms':
        if d.get('format') != FORMAT:
            raise ValueError('not a wheecal host calibration file')
        if d.get('version', 0) > FORMAT_VERSION:
            raise ValueError(f"host calibration file version {d.get('version')} is newer than this wheecal")
        e00, e11, de = _cx(d['e00']), _cx(d['e11']), _cx(d['De'])
        freqs = [float(f) for f in d['freqs']]
        if not len(freqs) == len(e00) == len(e11) == len(de):
            raise ValueError('host calibration file: the term lists differ in length')
        return cls(freqs, list(zip(e00, e11, de)), int(d.get('port', 1)), d.get('coefficient_set', 'FACTORY'),
                   d.get('created', ''), d.get('vna', ''), d.get('librecal', ''))

    def save(self, path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + '.tmp')
        tmp.write_text(json.dumps(self.to_json()) + '\n')
        tmp.replace(p)
        return p

    @classmethod
    def load(cls, path) -> 'ErrorTerms':
        try:
            return cls.from_json(json.loads(Path(path).read_text()))
        except FileNotFoundError:
            raise FileNotFoundError(f'no host calibration at {path}: run --host-cal first') from None

    def check_sweep(self, freqs) -> None:
        """Raise ValueError when freqs is not the sweep these terms were solved on."""
        if len(freqs) != len(self.freqs):
            raise ValueError(f'the VNA sweeps {len(freqs)} points, the host calibration has {len(self.freqs)}: '
                             'the sweep changed, run --host-cal again')
        span = max(abs(self.freqs[-1] - self.freqs[0]), 1.0)
        worst = max((abs(a - b) for a, b in zip(freqs, self.freqs)), default=0.0)
        if worst > FREQ_MATCH_TOL * span:
            raise ValueError(f'the VNA sweep ({freqs[0] / 1e6:g}-{freqs[-1] / 1e6:g} MHz) differs from the host '
                             f'calibration ({self.freqs[0] / 1e6:g}-{self.freqs[-1] / 1e6:g} MHz): run --host-cal '
                             'again')


def write_s1p(path, freqs, gammas, comment: str = '') -> Path:
    """Touchstone v1 one-port: '# HZ S RI R 50', then 'freq re im' per point."""
    if len(freqs) != len(gammas):
        raise ValueError('freqs and gammas differ in length')
    lines = [f'! wheecal {__version__} host-corrected S11']
    lines += [f'! {c}' for c in comment.splitlines() if c]
    lines.append('# HZ S RI R 50')
    lines += [f'{f:.6f} {g.real:.9e} {g.imag:.9e}' for f, g in zip(freqs, gammas)]
    p = Path(path)
    p.write_text('\n'.join(lines) + '\n')
    return p


def read_s1p(path) -> tuple[list[float], list[complex]]:
    """Reads what write_s1p writes (HZ, RI)."""
    freqs, gammas = [], []
    for line in Path(path).read_text().splitlines():
        s = line.strip()
        if not s or s.startswith(('!', '#')):
            continue
        f, re_, im = (float(v) for v in s.split()[:3])
        freqs.append(f)
        gammas.append(complex(re_, im))
    return freqs, gammas


# ── LibreCAL standards ───────────────────────────────────────────────────────
def factory_standards(cal: LibreCAL, port: int, freqs, set_name: str = 'FACTORY', log=print) -> dict:
    """{'OPEN'|'SHORT'|'LOAD': [complex per freq]}: the LibreCAL's measured standards on `port`, interpolated onto
    the sweep."""
    out = {}
    for std in ('OPEN', 'SHORT', 'LOAD'):
        name = f'P{port}_{std}'
        log(f'Reading {set_name} {name} from the LibreCAL ...')
        out[std] = interp_complex(cal.coefficient(set_name, name), freqs)
    return out


def through_truth(cal: LibreCAL, a: int, b: int, freqs, set_name: str = 'FACTORY', log=print) -> list[complex]:
    """The reflection port a of the LibreCAL's a<->b through presents with port b matched: its S11 (a < b) or S22."""
    name = thru_coefficient_name(a, b)
    log(f'Reading {set_name} {name} from the LibreCAL ...')
    rows = cal.coefficient(set_name, name)
    idx = 0 if a < b else 3
    return interp_complex([(f, s[idx]) for f, s in rows], freqs)


# ── flows ────────────────────────────────────────────────────────────────────
@dataclass
class HostCalResult:
    ok: bool = False
    message: str = ''
    terms: ErrorTerms | None = None
    check: dict = field(default_factory=dict)       # corrected standards vs truth (self-check)


def _sweep(vna) -> list[float]:
    fn = getattr(vna, 'sweep_freqs', None)
    freqs = fn() if callable(fn) else None
    if not freqs:
        raise ScpiError(f'{vna.TITLE}: cannot read the sweep frequencies, so no host-side calibration')
    return freqs


def go_raw(vna, opts, log=print) -> None:
    """Abort the on-board S11 calibration so the trace reads raw, then wait for a full raw sweep."""
    log(RAW_WARNING)
    vna.cal_abort('S11')
    time.sleep(vna.measurement_settle(opts.switch_settle_s))


def read_raw(vna, freqs) -> list[complex]:
    tr = vna.read_s11()
    if len(tr) != len(freqs):
        raise ValueError(f'the trace has {len(tr)} points, the sweep {len(freqs)}')
    return tr


def _check_librecal(cal: LibreCAL, opts, log, two_port: bool = False) -> int:
    lc = cal.identify()
    ports = lc['ports']
    for p in ((opts.librecal_port, opts.through_port) if two_port else (opts.librecal_port,)):
        if not 1 <= p <= ports:
            raise LibreCALError(f'LibreCAL port {p} does not exist (it has {ports})')
    log(f"LibreCAL: {lc['idn']} | {ports} ports | {lc['temperature']} C, {'stable' if lc['stable'] else 'NOT stable'}")
    if getattr(opts, 'require_stable_temp', False) and lc['stable'] is False:
        raise LibreCALError('the LibreCAL is not at its temperature yet (wait for its LED, or turn off '
                            '"require stable temperature")')
    return ports


def _release(cal, ports, log):
    try:
        cal.release(ports)
        log('LibreCAL: all ports NONE')
    except (LibreCALError, OSError) as exc:
        log(f'LibreCAL release failed: {exc}')


def run_host_cal(vna, cal: LibreCAL, opts, log=print, set_name: str = 'FACTORY') -> HostCalResult:
    """Measure the LibreCAL Open/Short/Load raw on opts.librecal_port and solve the error terms against its
    `set_name` coefficients. The VNA's on-board S11 calibration is aborted (raw reads). Releases the LibreCAL."""
    from .calflow import set_librecal          # calflow imports nothing from here; keep the import local anyway
    res, ports = HostCalResult(), 4
    try:
        info = vna.require_mode('S11')
        log(f"VNA: {info['idn']} | {info['mode']} {info['meas']}")
        ports = _check_librecal(cal, opts, log)
        freqs = _sweep(vna)
        log(f'Sweep: {freqs[0] / 1e6:g}-{freqs[-1] / 1e6:g} MHz, {len(freqs)} points')
        true = factory_standards(cal, opts.librecal_port, freqs, set_name, log)    # before touching the VNA
        go_raw(vna, opts, log)
        raw = {}
        for std in ('OPEN', 'SHORT', 'LOAD'):
            log(f'— {std}: ' + set_librecal(cal, std, opts))
            time.sleep(vna.measurement_settle(opts.switch_settle_s))
            raw[std] = read_raw(vna, freqs)
            log(f'  raw {std} read ({len(raw[std])} points)')
        terms = solve_osl(raw['OPEN'], raw['SHORT'], raw['LOAD'], true['OPEN'], true['SHORT'], true['LOAD'])
        lc = cal.identify()
        res.terms = ErrorTerms(freqs, terms, opts.librecal_port, set_name, time.strftime('%Y-%m-%dT%H:%M:%S'),
                               info['idn'], lc['idn'])
        for std in ('OPEN', 'SHORT', 'LOAD'):      # the solver reproduces each standard (sanity: ~1e-15)
            res.check[std] = max(abs(a - b) for a, b in zip(apply(terms, raw[std]), true[std]))
        worst = max(res.check.values())
        if not worst < 1e-6:
            raise ValueError(f'the solved terms do not reproduce the standards (worst {worst:.3g})')
        res.ok = True
        res.message = (f'host S11 calibration solved over {len(freqs)} points against LibreCAL {set_name} '
                       f'P{opts.librecal_port} (standards reproduced to {worst:.1e})')
        log(res.message)
    except (ScpiError, LibreCALError, OSError, ValueError, KeyError) as exc:
        res.ok, res.message = False, str(exc)
        log(f'STOPPED: {exc}')
    finally:
        _release(cal, ports, log)
    return res


def measure_dut(vna, terms: ErrorTerms, opts, log=print, raw_already: bool = False) -> tuple[list[float], list[complex]]:
    """The DUT on the VNA test port, host-corrected: (freqs, gammas). Aborts the on-board S11 calibration first
    unless raw_already (the flow already did)."""
    vna.require_mode('S11')
    freqs = _sweep(vna)
    terms.check_sweep(freqs)
    if raw_already:
        time.sleep(vna.measurement_settle(opts.switch_settle_s))
    else:
        go_raw(vna, opts, log)
    return freqs, apply(terms, read_raw(vna, freqs))


def verify_host_cal(vna, cal: LibreCAL, terms: ErrorTerms, opts, log=print, set_name: str = 'FACTORY',
                    raw_already: bool = False) -> dict:
    """The independent check: LibreCAL THROUGH librecal_port <-> through_port (far port matched), raw read, host
    correction, compared with the through's FACTORY S11. Returns residual() plus 'ok' and 'limit'."""
    ports = 4
    try:
        vna.require_mode('S11')
        ports = _check_librecal(cal, opts, log, two_port=True)
        freqs = _sweep(vna)
        terms.check_sweep(freqs)
        truth = through_truth(cal, opts.librecal_port, opts.through_port, freqs, set_name, log)
        if not raw_already:
            go_raw(vna, opts, log)
        cal.set_port(opts.librecal_port, 'THROUGH', opts.through_port)
        log(f'Verify: LibreCAL port {opts.librecal_port} <-> port {opts.through_port} THROUGH (port '
            f'{opts.through_port} must see a matched 50-ohm termination)')
        time.sleep(vna.measurement_settle(opts.switch_settle_s))
        est = apply(terms, read_raw(vna, freqs))
        r = residual(est, truth, freqs)
        r['limit'] = VERIFY_MAX_MEAN
        r['ok'] = r['mean'] < VERIFY_MAX_MEAN
        log(f"  |corrected - FACTORY through S11| over f >= {VERIFY_F_MIN_HZ / 1e6:g} MHz ({r['points']} points): "
            f"min {r['min']:.4f}  max {r['max']:.4f}  mean {r['mean']:.4f}")
        log(f"Host-cal verify: {'PASS' if r['ok'] else 'FAIL'} (mean < {VERIFY_MAX_MEAN:g}; ~0.06 is the floor)")
        return r
    finally:
        _release(cal, ports, log)
