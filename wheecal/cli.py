"""
Command line. `wheecal` with no arguments starts the GUI; any argument runs this. Settings come from the local
config file (wheecal --show-config); flags override them for this run.

Examples (192.0.2.10 stands for your VNA's address)
  wheecal --list-ports
  wheecal --host 192.0.2.10 --load-kit 6650F03E-F --dry-run    print the commands, touch nothing
  wheecal --host 192.0.2.10 --backup kit.json                  save the VNA's user kit
  wheecal --device /dev/cu.usbmodem1101 --librecal-info
  wheecal --load-kit librecal                                  fit the LibreCAL standards and write that kit
  wheecal --calibrate --yes                                    S11 Open/Short/Load with the LibreCAL
  wheecal --calibrate s21 --librecal-port 1 --through-port 2   2-port through (S21) calibration
  wheecal --calibrate-thru --mode guided                       same, you press the VNA's buttons
  wheecal --dry-run --mock-librecal --load-kit librecal --calibrate s21 --yes     full simulation
  wheecal --discard-cal s11                                    discard the VNA's S11 calibration (asks first)

A calibration is never discarded automatically after Done: wheecal checks the VNA's correction badge on its screen
(the state query is unreliable on some firmware) and leaves the calibration in place either way.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, calflow, config, drivers, fit
from .calkit import BUILTIN_KITS, CalKit
from .librecal import LibreCAL, LibreCALError, find_librecal, list_ports
from .transport import ScpiError

KIT_CHOICES = (*BUILTIN_KITS, 'librecal')


def print_table(rows) -> bool:
    print(f"{'standard':8s} {'field':6s} {'unit':9s} {'wanted':>14s} {'read back':>14s}  ok")
    for r in rows:
        got = '' if r['got'] is None else f"{r['got']:14.6g}"
        print(f"{r['standard']:8s} {r['field']:6s} {r['unit']:9s} {r['want']:14.6g} {got:>14s}  "
              f"{'yes' if r['ok'] else 'NO'}")
    ok = all(r['ok'] for r in rows)
    print('RESULT', 'PASS' if ok else 'FAIL')
    return ok


def read_librecal_coeffs(cal: LibreCAL, port: int, thru_pair=None, set_name: str = 'FACTORY', log=print) -> dict:
    """The LibreCAL data the kit fit needs: P<port>_OPEN/SHORT/LOAD and, if it has one, the through thru_pair."""
    coeffs = {}
    for s in ('OPEN', 'SHORT', 'LOAD'):
        name = f'P{port}_{s}'
        log(f'Reading {name} from the LibreCAL ...')
        coeffs[name] = cal.coefficient(set_name, name)
    if thru_pair is not None:
        name = fit.thru_coefficient_name(*thru_pair)
        log(f'Reading {name} from the LibreCAL ...')
        try:
            coeffs[name] = cal.coefficient(set_name, name)
        except LibreCALError as exc:
            log(f'  {name} not available ({exc}): the thru length stays 0')
    return coeffs


def librecal_kit(cal: LibreCAL, vna, port: int, band: str, thru_pair=None, log=print, driver=None) -> tuple[CalKit, dict]:
    """Fit the LibreCAL standards over band ('span': the VNA sweep, default; 'full': all the LibreCAL data), never
    above the instrument's limit (vna or driver FREQ_MAX_HZ) or outside the LibreCAL data. Logs each standard's
    worst error and a warning when the open or short does not reduce to the model."""
    coeffs = read_librecal_coeffs(cal, port, thru_pair, log=log)
    freqs = [f for f, _ in coeffs[f'P{port}_OPEN'] if f > 0]
    span = vna.span() if band == 'span' and vna is not None else None
    f_max = getattr(vna if vna is not None else driver, 'FREQ_MAX_HZ', None)
    lo, hi, how = fit.fit_band_limits(band, min(freqs), max(freqs), span, f_max)
    log(f'Fit band: {lo / 1e9:.3f}-{hi / 1e9:.3f} GHz ({how})')
    kit, fits = fit.kit_from_librecal(coeffs, port, lo, hi, thru_pair)
    for k in ('OPEN', 'SHORT', 'LOAD'):
        log(f'  {k.lower()}: worst error {fits[k].residual_db:.1f} dB over {fits[k].points} points')
    for w in fit.residual_warnings(fits):
        log('WARNING: ' + w)
    return kit, fits


def build_parser(cfg: dict) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog='wheecal', description='LibreCAL companion: VNA cal kits and calibrations '
                                 'over SCPI', formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--version', action='version', version=f'wheecal {__version__}')
    ap.add_argument('--config', metavar='JSON', help='settings file (default: the per-user file, see --show-config)')
    ap.add_argument('--show-config', action='store_true', help='print the settings file path and contents')
    ap.add_argument('--list-drivers', action='store_true')
    ap.add_argument('--list-ports', action='store_true', help='serial ports (LibreCALs first)')
    ap.add_argument('--driver', help=f"VNA driver ({', '.join(drivers.names())})")
    ap.add_argument('--host', '--ip', dest='host', help='VNA IP address or hostname')
    ap.add_argument('--scpi-port', type=int, help='VNA SCPI TCP port')
    ap.add_argument('--device', help="LibreCAL serial port, or 'auto'")
    ap.add_argument('--mock-librecal', action='store_true', help='use a simulated LibreCAL')
    ap.add_argument('--dry-run', action='store_true', help='never talk to the VNA: simulate it and print its commands')
    ap.add_argument('--librecal-info', action='store_true')
    ap.add_argument('--load-kit', choices=KIT_CHOICES, help='write a kit into the VNA user kit')
    ap.add_argument('--verify', choices=KIT_CHOICES, help='read the VNA user kit back and compare with a kit')
    ap.add_argument('--backup', metavar='JSON', help='save the VNA user kit')
    ap.add_argument('--restore', metavar='JSON', help='write a saved user kit back')
    ap.add_argument('--calibrate', nargs='?', const='s11', choices=('s11', 's21'),
                    help='run a calibration: s11 (Open/Short/Load, default) or s21 (2-port through)')
    ap.add_argument('--calibrate-thru', action='store_true', help='same as --calibrate s21')
    ap.add_argument('--mode', choices=('auto', 'guided'))
    ap.add_argument('--librecal-port', type=int, help='LibreCAL port on the VNA test port (S11) / output (S21)')
    ap.add_argument('--through-port', type=int, help='LibreCAL port on the VNA input (S21)')
    ap.add_argument('--settle', type=float, help='seconds to wait per standard measurement')
    ap.add_argument('--fit-band', choices=('span', 'full'),
                    help='fit the LibreCAL standards over the VNA sweep (default) or their whole range; '
                         "never above the VNA's limit")
    ap.add_argument('--discard-cal', choices=('s11', 's21'),
                    help="abort / discard the VNA's calibration of that measurement (asks first)")
    ap.add_argument('--no-kit-check', action='store_true', help='calibrate without reading the VNA kit back')
    ap.add_argument('--snapshot', metavar='FILE', help='save a VNA screenshot')
    ap.add_argument('--yes', action='store_true', help='do not ask before calibrating')
    return ap


def main(argv=None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config')
    known, _ = pre.parse_known_args(argv)
    try:
        cfg = config.load(known.config)
    except OSError as exc:
        print(f'ERROR: cannot read or create the settings file: {exc}', file=sys.stderr)
        return 2
    a = build_parser(cfg).parse_args(argv)
    for key, flag in (('driver', 'driver'), ('host', 'host'), ('scpi_port', 'scpi_port'), ('mode', 'mode'),
                      ('librecal_device', 'device'), ('librecal_port', 'librecal_port'),
                      ('through_port', 'through_port'), ('settle_s', 'settle'), ('fit_band', 'fit_band')):
        if getattr(a, flag) is not None:
            cfg[key] = getattr(a, flag)
    meas = 'S21' if a.calibrate_thru else (a.calibrate.upper() if a.calibrate else None)

    if a.show_config:
        print(config.config_path(a.config))
        print(json.dumps(cfg, indent=2))
        return 0
    if a.list_drivers:
        for name in drivers.names():
            d = drivers.get(name)
            print(f"{name:12s} {d.TITLE}  (calibrations: {', '.join(d.CAL_STEPS)})")
        return 0
    if a.list_ports:
        for p in list_ports():
            print(f"{p['device']:30s} {'LibreCAL ' if p['librecal'] else ''}{p['description']}")
        return 0

    vna = cal = None
    try:
        driver = drivers.get(cfg['driver'])
        need_vna = any((a.load_kit, a.verify, a.backup, a.restore, meas, a.snapshot, a.discard_cal))
        if need_vna:
            vna = driver.mock(meas=meas or 'S11') if a.dry_run else driver.connect(cfg['host'], cfg['scpi_port'])
            info = vna.require_mode()
            print(f"VNA {info['idn']} | {info['mode']} {info['meas']}{'  (DRY RUN: simulated)' if a.dry_run else ''}")
        need_cal = a.librecal_info or meas or 'librecal' in (a.load_kit, a.verify)
        if need_cal:
            if a.mock_librecal:
                cal = LibreCAL.mock()
            else:
                dev = cfg['librecal_device']
                if dev in ('', 'auto'):
                    dev = find_librecal()
                    if dev is None:
                        raise LibreCALError('no LibreCAL found: plug it in, or give --device (see --list-ports)')
                cal = LibreCAL.open(dev)
            print('LibreCAL', json.dumps(cal.identify()))
            if a.librecal_info:
                print('coefficient sets:', cal.coefficient_sets())
                for p in range(1, 5):
                    print(f'port {p}:', cal.get_port(p))

        pair = (cfg['librecal_port'], cfg['through_port'])

        def kit_for(choice):
            if choice == 'librecal':
                kit, _ = librecal_kit(cal, vna, cfg['librecal_port'], cfg['fit_band'], pair, driver=driver)
                print(f'{kit.name}: {kit.note}')
                return kit
            return BUILTIN_KITS[choice]

        if a.backup:
            data = vna.backup_kit()
            with open(a.backup, 'w') as fh:
                json.dump(data, fh, indent=2)
            print(f'backup written to {a.backup}')
        if a.load_kit or a.restore:
            if a.load_kit:
                kit = kit_for(a.load_kit)
                print(f'Writing "{kit.name}" ({kit.note})')
                rows = vna.write_kit(kit)
            else:
                with open(a.restore) as fh:
                    rows = vna.restore_kit(json.load(fh))
            if a.dry_run:
                print('\n'.join(vna.writes))
            if not print_table(rows):
                return 1
        if a.verify:
            if not print_table(vna.verify_kit(kit_for(a.verify))):
                return 1
            print('User kit selected:', vna.user_kit_selected())
        if meas:
            if not vna.supports(meas):
                raise ScpiError(f'{driver.TITLE} has no {meas} calibration in wheecal')
            expected = None if a.no_kit_check or not cfg['kit_check'] else kit_for('librecal')
            what = 'S11 Open/Short/Load' if meas == 'S11' else (
                f"S21 through (LibreCAL {pair[0]} <-> {pair[1]})")
            if not a.yes and input(f'Run the {what} calibration on the VNA now? [y/N] ').strip().lower() != 'y':
                print('not started')
                return 1
            opts = calflow.CalOptions(mode=cfg['mode'], librecal_port=pair[0], through_port=pair[1],
                                      settle_s=cfg['settle_s'], switch_settle_s=cfg['switch_settle_s'],
                                      require_stable_temp=cfg['require_stable_temp'], expected_kit=expected,
                                      evidence_dir=str(config.evidence_dir(a.config)))
            res = calflow.run_cal(vna, cal, meas, opts, log=print,
                                  prompt=lambda m: input(m + '\n[Enter] continue, q quit: ').strip().lower() != 'q')
            if a.dry_run:
                print('VNA commands (simulated):', *vna.writes, sep='\n  ')
            print('CAL', 'OK' if res.ok else ('SAVED, NOT APPLIED' if res.saved else 'FAILED'), '-', res.message)
            if res.saved:
                print(f'To discard it: wheecal --discard-cal {meas.lower()}')
            if not res.ok:
                return 1
        if a.discard_cal:
            m = a.discard_cal.upper()
            if not vna.supports(m):
                raise ScpiError(f'{driver.TITLE} has no {m} calibration in wheecal')
            if not a.yes and input(f"Discard the VNA's {m} calibration (Abort)? [y/N] ").strip().lower() != 'y':
                print('not discarded')
                return 1
            vna.cal_abort(m)
            print(f'{m} calibration discarded (Abort sent)')
        if a.snapshot:
            img, _ext = vna.snapshot()
            with open(a.snapshot, 'wb') as fh:
                fh.write(img)
            print(f'screenshot written to {a.snapshot}')
        if not (need_vna or need_cal):
            build_parser(cfg).print_help()
        return 0
    except (ScpiError, LibreCALError, OSError, ValueError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    finally:
        for dev in (vna, cal):
            if dev is not None:
                dev.close()
