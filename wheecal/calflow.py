"""
The calibration sequences: the LibreCAL presents a standard, the VNA measures it, next; then the VNA applies
the calibration ("Done").

  run_s11  1-port Open/Short/Load on the VNA's test port (LibreCAL port `librecal_port`).
  run_s21  2-port transmission cal: the LibreCAL connects `librecal_port` to `through_port` (THROUGH); a driver
           with an ISOLATION step also gets both ports terminated in LOAD for it.

Modes:
  auto    wheecal sends the VNA's measure and apply commands itself (the driver's cal_measure/cal_save). Default.
  guided  wheecal switches the LibreCAL and asks the operator to press the panel buttons; it checks the VNA's
          calibration state at the end. Use it for the first run on a new instrument or firmware.

The VNA solves the calibration with its selected kit, which must describe the LibreCAL's standards
(fit.kit_from_librecal). With `expected_kit` the flow reads the VNA's kit back first and stops when it differs.

Callbacks: log(str), progress(done, total, label), prompt(message) -> bool (False cancels). `cancel` is a
threading.Event checked between steps. A failure after the first measurement aborts the VNA's calibration (auto)
and the LibreCAL is always released (all ports NONE).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .calkit import CalKit
from .librecal import LibreCAL, LibreCALError
from .transport import ScpiError

#: Kit standards each measurement's calibration depends on (checked with expected_kit).
KIT_STANDARDS = {'S11': ('OPEN', 'SHORT', 'LOAD'), 'S21': ('THRU',)}


class Cancelled(RuntimeError):
    pass


@dataclass
class CalOptions:
    mode: str = 'auto'                 # auto | guided
    librecal_port: int = 1             # S11: the port on the VNA test port; S21: the port on the VNA output
    through_port: int = 2              # S21: the LibreCAL port on the VNA input
    settle_s: float = 3.0              # wait after each VNA measurement (several sweeps)
    switch_settle_s: float = 0.5       # wait after switching the LibreCAL
    require_stable_temp: bool = True
    expected_kit: CalKit | None = None


@dataclass
class CalResult:
    ok: bool = False
    steps: list = field(default_factory=list)
    message: str = ''


def _noop(*_a, **_k):
    return True


def set_librecal(cal: LibreCAL, step: str, opts: CalOptions) -> str:
    """Put the LibreCAL on a calibration step; returns a description for the log."""
    a, b = opts.librecal_port, opts.through_port
    if step in ('OPEN', 'SHORT', 'LOAD'):
        cal.set_port(a, step)
        return f'LibreCAL port {a} -> {step}'
    if step == 'THRU':
        cal.set_port(a, 'THROUGH', b)
        return f'LibreCAL port {a} <-> port {b} THROUGH'
    if step == 'ISOLATION':
        cal.set_port(a, 'LOAD')
        cal.set_port(b, 'LOAD')
        return f'LibreCAL ports {a} and {b} -> LOAD'
    raise LibreCALError(f'no LibreCAL setting for step {step}')


def run_cal(vna, cal: LibreCAL, meas: str, opts: CalOptions, *, log=print, progress=_noop, prompt=None,
            cancel: threading.Event | None = None) -> CalResult:
    cancel = cancel or threading.Event()
    prompt = prompt or (lambda msg: True)
    res = CalResult()
    steps = vna.CAL_STEPS.get(meas)
    total = len(steps or ()) + 2
    ports = 4

    def check():
        if cancel.is_set():
            raise Cancelled('cancelled')

    started = False
    try:
        if not steps:
            raise ScpiError(f'{vna.TITLE} has no {meas} calibration in wheecal')
        if meas == 'S21' and opts.librecal_port == opts.through_port:
            raise LibreCALError('the through needs two different LibreCAL ports')
        progress(0, total, 'checks')
        info = vna.require_mode(meas)
        log(f"VNA: {info['idn']} | {info['mode']} {info['meas']}")
        lc = cal.identify()
        ports = lc['ports']
        for p in ((opts.librecal_port, opts.through_port) if meas == 'S21' else (opts.librecal_port,)):
            if not 1 <= p <= ports:
                raise LibreCALError(f'LibreCAL port {p} does not exist (it has {ports})')
        log(f"LibreCAL: {lc['idn']} | {lc['ports']} ports | {lc['temperature']} C, "
            f"{'stable' if lc['stable'] else 'NOT stable'}")
        if opts.require_stable_temp and lc['stable'] is False:
            raise LibreCALError('the LibreCAL is not at its temperature yet (wait for its LED, or turn off '
                                '"require stable temperature")')
        if opts.expected_kit is not None:
            log(f'Checking the VNA kit against "{opts.expected_kit.name}" ...')
            rows = vna.verify_kit(opts.expected_kit, KIT_STANDARDS[meas])
            bad = [r for r in rows if not r['ok']]
            if bad:
                raise ScpiError('the VNA kit differs from the expected kit (' + ', '.join(
                    f"{r['standard']} {r['field']} {r['got']:g} != {r['want']:g}" for r in bad[:4]) +
                    '): load the kit first')
        if not vna.user_kit_selected():
            raise ScpiError('the VNA does not have the user (Custom) kit selected: load the kit first')
        progress(1, total, 'ready')
        check()

        for n, step in enumerate(steps, start=2):
            log(f'— {step}: ' + set_librecal(cal, step, opts))
            time.sleep(opts.switch_settle_s)
            check()
            if opts.mode == 'auto':
                log(f'  VNA: measuring {step} ...')
                started = True
                vna.cal_measure(meas, step, opts.settle_s)
            else:
                started = True
                if not prompt(f'LibreCAL is on {step}.\n\nOn the VNA press: {vna.panel_hint(meas, step)}.\n'
                              'Wait for it to finish, then press Continue.'):
                    raise Cancelled('cancelled at ' + step)
            res.steps.append(step)
            progress(n, total, step)
            check()

        if opts.mode == 'auto':
            log(f'— Done: apply the {meas} calibration')
            on = vna.cal_save(meas)
        else:
            if not prompt(f"On the VNA press: {vna.panel_hint(meas, 'DONE')}.\nThen press Continue."):
                raise Cancelled('cancelled before Done')
            deadline = time.monotonic() + 10
            on = vna.cal_state(meas)
            while not on and time.monotonic() < deadline:
                time.sleep(0.5)
                on = vna.cal_state(meas)
        if not on:
            raise ScpiError(f'the {meas} calibration state is still off after Done: the calibration did not apply')
        log(f'{meas} calibration ON')
        res.steps.append('DONE')
        progress(total, total, 'done')
        res.ok, res.message = True, f'{meas} calibration applied'
    except (Cancelled, ScpiError, LibreCALError, OSError, ValueError) as exc:
        res.ok, res.message = False, str(exc)
        log(f'STOPPED: {exc}')
        if started and opts.mode == 'auto':
            try:
                vna.cal_abort(meas)
                log(f'VNA: {meas} calibration aborted')
            except (ScpiError, OSError) as exc2:
                log(f'VNA abort failed: {exc2}')
        elif started:
            log(f'Press Abort in the VNA\'s {meas} calibration menu to discard the partial calibration.')
    finally:
        try:
            cal.release(ports)
            log('LibreCAL: all ports NONE')
        except (LibreCALError, OSError) as exc:
            log(f'LibreCAL release failed: {exc}')
    return res


def run_s11(vna, cal: LibreCAL, opts: CalOptions, **kw) -> CalResult:
    return run_cal(vna, cal, 'S11', opts, **kw)


def run_s21(vna, cal: LibreCAL, opts: CalOptions, **kw) -> CalResult:
    return run_cal(vna, cal, 'S21', opts, **kw)
