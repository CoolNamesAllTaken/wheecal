"""
Fit a LibreCAL's stored standards (its FACTORY coefficient set: the measured S-parameters of each standard) to the
CalKit model, so a VNA calibration with the LibreCAL uses what its standards really are.

Why: a VNA solves a calibration from its selected kit definition, and most VNAs only take a model, not tabulated
data:
  open  = lossless offset of electrical length l (in air) + C(f) = C0 + C1 f + C2 f^2 + C3 f^3
  short = lossless offset of length l + L(f) = L0 + L1 f + L2 f^2 + L3 f^3
  load  = a resistor R
  thru  = a lossless offset of length l

Open/short (fit_reactive), stdlib only: for each offset length on a grid, de-embed the offset, convert to C(f) (or
L(f)), least-squares a constant + linear term in f[GHz] to Re C, keep the length with the smallest error back in
the reflection domain (length and polynomial trade off; this keeps the length physical), then fit the cubic at that
length. Values are rounded to 5 significant digits. `residual_db` is the worst |Gamma_model - Gamma_data| in dB
(-40 dB = 1 %).

Through (fit_thru): the LibreCAL's P<ab>_THROUGH coefficient holds S11, S21, S12, S22 of the through between ports a
and b. The phase of S21 (S12 for a > b) is unwrapped and fitted with a straight line, phase = phi0 - 2 pi f tau
(least squares). tau is the delay and c * tau the length in air. `residual_deg` is the worst deviation from the
line; `loss_db` is the mean insertion loss (reported only).

Band (fit_band): by default the fit uses the VNA's current sweep ('span'); 'full' uses the LibreCAL's whole data.
Both stop at the instrument's highest frequency and stay within the LibreCAL data (fit_band_limits). A fit over more
than the VNA sweeps only makes the in-band error worse (the RSA5065N sweeps to 6.5 GHz; the LibreCAL data goes to
8.5 GHz or more).
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass

from .calkit import C_LIGHT, CalKit

C0_LIGHT = C_LIGHT
Z0 = 50.0


@dataclass
class StandardFit:
    kind: str                 # OPEN | SHORT | LOAD
    length_m: float = 0.0
    coeffs: tuple = ()        # fF / fF/GHz^n (open) or pH / pH/GHz^n (short)
    r_ohm: float = 50.0
    residual_db: float = -200.0
    points: int = 0
    f_lo: float = 0.0
    f_hi: float = 0.0


def _round(x: float, sig: int = 5) -> float:
    return 0.0 if x == 0 else float(f'{x:.{sig - 1}e}')


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-300:
            raise ValueError('singular fit')
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                k = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= k * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _polyfit(x: list[float], y: list[float], deg: int) -> list[float]:
    deg = min(deg, len(x) - 1)
    a = [[sum(xi ** (i + j) for xi in x) for j in range(deg + 1)] for i in range(deg + 1)]
    b = [sum(yi * xi ** i for xi, yi in zip(x, y)) for i in range(deg + 1)]
    return _solve(a, b) + [0.0] * (3 - deg)


def _poly(c, x):
    return c[0] + c[1] * x + c[2] * x * x + c[3] * x ** 3


def _offset(f: float, length: float) -> complex:
    return cmath.exp(-2j * 2 * math.pi * f * length / C0_LIGHT)


def gamma_open(f: float, length: float, c_ff: list[float]) -> complex:
    c = _poly(c_ff, f / 1e9) * 1e-15
    zc = 1 / (1j * 2 * math.pi * f * c) if c else complex(1e300)
    return (zc - Z0) / (zc + Z0) * _offset(f, length)


def gamma_short(f: float, length: float, l_ph: list[float]) -> complex:
    zl = 1j * 2 * math.pi * f * _poly(l_ph, f / 1e9) * 1e-12
    return (zl - Z0) / (zl + Z0) * _offset(f, length)


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-10))


def fit_reactive(kind: str, data, deg: int = 3, max_len_m: float = 0.05, step_m: float = 1e-4) -> StandardFit:
    """data: [(f_Hz, Gamma)]. kind OPEN or SHORT."""
    data = [(f, g) for f, g in data if f > 0]
    if len(data) < 2:
        raise ValueError(f'{kind}: not enough points to fit')
    xs = [f / 1e9 for f, _ in data]

    def attempt(length, deg=deg):
        ys = []
        for f, g in data:
            gt = g / _offset(f, length)
            w = 2 * math.pi * f
            if kind == 'OPEN':
                val = (1 - gt) / (1j * w * Z0 * (1 + gt)) / 1e-15 if abs(1 + gt) > 1e-9 else complex(1e6)
            else:
                val = Z0 * (1 + gt) / (1j * w * (1 - gt)) / 1e-12 if abs(1 - gt) > 1e-9 else complex(1e6)
            ys.append(val.real)
        c = _polyfit(xs, ys, deg)
        model = gamma_open if kind == 'OPEN' else gamma_short
        err = max(abs(model(f, length, c) - g) for f, g in data)
        return err, c

    # The offset length and the polynomial trade off (both make phase). The length is chosen with a constant +
    # linear C (or L), which keeps it physical; the cubic is then fitted at that length.
    pick = min(1, deg)
    best = None
    n = int(max_len_m / step_m)
    for i in range(n + 1):                              # coarse grid
        err, c = attempt(i * step_m, pick)
        if best is None or err < best[0]:
            best = (err, c, i * step_m)
    lo, hi = max(0.0, best[2] - step_m), best[2] + step_m
    for _ in range(40):                                 # refine (golden section)
        a, b = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if attempt(a, pick)[0] < attempt(b, pick)[0]:
            hi = b
        else:
            lo = a
    err, c = attempt((lo + hi) / 2, pick)
    if err < best[0]:
        best = (err, c, (lo + hi) / 2)
    length = best[2]
    err, c = attempt(length, deg)
    c = [_round(v) for v in c]
    length = _round(length)
    model = gamma_open if kind == 'OPEN' else gamma_short
    err = max(abs(model(f, length, c) - g) for f, g in data)
    return StandardFit(kind, length, tuple(c), residual_db=_db(err), points=len(data),
                       f_lo=data[0][0], f_hi=data[-1][0])


def fit_load(data) -> StandardFit:
    zs = [Z0 * (1 + g) / (1 - g) for _, g in data if abs(1 - g) > 1e-9]
    r = _round(sum(z.real for z in zs) / len(zs))
    gm = (r - Z0) / (r + Z0)
    err = max(abs(gm - g) for _, g in data)
    return StandardFit('LOAD', r_ohm=r, residual_db=_db(err), points=len(data), f_lo=data[0][0], f_hi=data[-1][0])


def in_band(data, f_lo: float | None, f_hi: float | None):
    """Points within [f_lo, f_hi], plus one neighbour each side so a narrow span still has points."""
    if f_lo is None or f_hi is None:
        return data
    idx = [i for i, (f, _) in enumerate(data) if f_lo <= f <= f_hi]
    if not idx:
        near = min(range(len(data)), key=lambda i: abs(data[i][0] - (f_lo + f_hi) / 2))
        idx = [near]
    lo, hi = max(0, idx[0] - 1), min(len(data) - 1, idx[-1] + 1)
    return data[lo:hi + 1]


#: A worst open/short error above this (dB, |Gamma_model - Gamma_data|) gets a warning: the standard does not reduce
#: to the VNA's model well in that band (-20 dB = 10 %).
WARN_RESIDUAL_DB = -20.0


def fit_band_limits(band: str, data_lo: float, data_hi: float, span=None, f_max=None) -> tuple[float, float, str]:
    """(f_lo, f_hi, how) of a fit: band 'span' uses span (the VNA sweep) when known, else the data; 'full' the
    data. Clamped to [data_lo, data_hi] and to f_max (the instrument's limit)."""
    lo, hi, how = data_lo, data_hi, 'the LibreCAL data range'
    if band == 'span' and span:
        lo, hi, how = span[0], span[1], 'the VNA sweep'
    elif band == 'span':
        how = 'the LibreCAL data range (VNA sweep unknown)'
    if f_max is not None and hi > f_max:
        hi, how = f_max, how + f', up to the instrument limit {f_max / 1e9:g} GHz'
    lo, hi = max(lo, data_lo), min(hi, data_hi)
    if lo > hi:
        lo, hi = data_lo, min(data_hi, f_max or data_hi)
        how = 'the LibreCAL data range (the sweep is outside it)'
    return lo, hi, how


def residual_warnings(fits: dict, limit_db: float = WARN_RESIDUAL_DB) -> list[str]:
    out = []
    for kind in ('OPEN', 'SHORT'):
        f = fits.get(kind)
        if f is not None and f.residual_db > limit_db:
            model = 'C0..C3 open' if kind == 'OPEN' else 'L0..L3 short'
            out.append(f'{kind.lower()} fit worst error {f.residual_db:.1f} dB over {f.f_lo / 1e9:.3f}-'
                       f'{f.f_hi / 1e9:.3f} GHz: the LibreCAL {kind.lower()} may not reduce to the VNA\'s {model} '
                       'model in this band (a limit of the model, not of the fit); expect a less exact calibration')
    return out


def _si(coeffs, unit):
    """fF/GHz^n (or pH/GHz^n) -> F/Hz^n (H/Hz^n)."""
    return tuple(v * unit / (1e9 ** i) for i, v in enumerate(coeffs))


def kit_from_librecal(coeffs: dict, port: int = 1, f_lo=None, f_hi=None, thru_pair=None) -> tuple[CalKit, dict]:
    """coeffs: {'P1_OPEN': [(f, [S11])], 'P1_SHORT': ..., 'P1_LOAD': ..., optionally 'P12_THROUGH':
    [(f, [S11, S21, S12, S22])]}. thru_pair (a, b) fits the thru length from that through when its coefficient is
    in coeffs (else it stays 0). Returns the CalKit and the fits ({'OPEN', 'SHORT', 'LOAD'[, 'THRU']})."""
    def series(name):
        return in_band([(f, s[0]) for f, s in coeffs[f'P{port}_{name}']], f_lo, f_hi)
    fo, fs, fl = fit_reactive('OPEN', series('OPEN')), fit_reactive('SHORT', series('SHORT')), fit_load(series('LOAD'))
    fits = {'OPEN': fo, 'SHORT': fs, 'LOAD': fl}
    note = (f'fit {fo.f_lo / 1e9:.3f}-{fo.f_hi / 1e9:.3f} GHz, worst error open {fo.residual_db:.1f} dB, '
            f'short {fs.residual_db:.1f} dB, load {fl.residual_db:.1f} dB')
    warns = residual_warnings(fits)
    if warns:
        note += '; WARNING: ' + '; '.join(warns)
    name = f'LibreCAL port {port} (fitted)'
    thru_len = 0.0
    if thru_pair is not None:
        key = thru_coefficient_name(*thru_pair)
        if key in coeffs:
            ft = fit_thru(in_band(coeffs[key], f_lo, f_hi), *thru_pair)
            thru_len = ft.length_m
            fits['THRU'] = ft
            name = f'LibreCAL port {port}, through {thru_pair[0]}-{thru_pair[1]} (fitted)'
            note += '; ' + ft.describe()
        else:
            note += f'; no {key} in the LibreCAL data: thru length left 0'
    kit = CalKit(name, open_c=_si(fo.coeffs, 1e-15), open_len_m=fo.length_m, short_l=_si(fs.coeffs, 1e-12),
                 short_len_m=fs.length_m, load_r=fl.r_ohm, thru_len_m=thru_len, note=note)
    return kit, fits


@dataclass
class ThruFit:
    pair: tuple = (1, 2)
    delay_s: float = 0.0
    length_m: float = 0.0
    intercept_deg: float = 0.0
    residual_deg: float = 0.0
    loss_db: float = 0.0
    points: int = 0
    f_lo: float = 0.0
    f_hi: float = 0.0

    def describe(self) -> str:
        return (f'through {self.pair[0]}-{self.pair[1]}: {self.delay_s * 1e12:.2f} ps = {self.length_m * 1e3:.3f} mm '
                f'in air, phase residual {self.residual_deg:.2f} deg, mean loss {self.loss_db:.2f} dB')


def thru_coefficient_name(a: int, b: int) -> str:
    """The LibreCAL coefficient that holds the through between ports a and b (stored low port first)."""
    if a == b:
        raise ValueError('the through needs two different LibreCAL ports')
    lo, hi = sorted((a, b))
    return f'P{lo}{hi}_THROUGH'


def unwrap(phases: list[float]) -> list[float]:
    out, k, prev = [], 0.0, None
    for p in phases:
        if prev is not None:
            d = p - prev
            if d > math.pi:
                k -= 2 * math.pi
            elif d < -math.pi:
                k += 2 * math.pi
        out.append(p + k)
        prev = p
    return out


def fit_thru(data, a: int = 1, b: int = 2) -> ThruFit:
    """data: [(f_Hz, [S11, S21, S12, S22])] from the LibreCAL, frequency ascending. Uses S21 for a<b, S12 for a>b
    (the direction from port a to port b)."""
    idx = 1 if a < b else 2
    pts = sorted((f, s[idx]) for f, s in data if f > 0)
    if len(pts) < 2:
        raise ValueError('through: not enough points to fit')
    fs = [f for f, _ in pts]
    ph = unwrap([cmath.phase(s) for _, s in pts])
    n, mf, mp = len(fs), sum(fs) / len(fs), sum(ph) / len(ph)
    sxx = sum((f - mf) ** 2 for f in fs)
    if sxx <= 0:
        raise ValueError('through: all points at one frequency')
    slope = sum((f - mf) * (p - mp) for f, p in zip(fs, ph)) / sxx
    icpt = mp - slope * mf
    tau = -slope / (2 * math.pi)
    resid = max(abs(p - (icpt + slope * f)) for f, p in zip(fs, ph))
    loss = -sum(_db(abs(s)) for _, s in pts) / n
    return ThruFit((a, b), tau, _round(tau * C0_LIGHT), math.degrees(icpt), math.degrees(resid), loss, n,
                   fs[0], fs[-1])
