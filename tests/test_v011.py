"""v0.1.1 behaviour: no calibration is ever aborted after Done (SAVE), the result comes from the screen (the
RSA5065N's STATe? reads 0 with a correction on), and the kit fit runs over the VNA sweep up to the instrument's
limit by default."""
import io
import json
import os
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from wheecal import calflow, cli, config, fit
from wheecal.drivers.rsa5065n import RSA5065N
from wheecal.librecal import LibreCAL

from .helpers import IsolatedConfig
from .test_calflow import fitted


def aborts(vna):
    return [w for w in vna.writes if 'ABOR' in w.upper()]


class NoAutoAbortTests(IsolatedConfig, unittest.TestCase):
    def make(self, meas='S11', **mock):
        self.vna = RSA5065N.mock(meas=meas, state_lies=True, **mock)
        self.cal = LibreCAL.mock()
        pair = (1, 2) if meas == 'S21' else None
        self.kit = fitted(self.cal, pair)
        self.vna.write_kit(self.kit)
        self.vna.writes.clear()
        self.logs = []
        self.evidence = os.path.join(os.path.dirname(self.config_file), 'indicator')

    def run_flow(self, meas='S11', **kw):
        opts = calflow.CalOptions(settle_s=0, switch_settle_s=0, verify_gap_s=0, expected_kit=self.kit,
                                  evidence_dir=self.evidence, **kw)
        return calflow.run_cal(self.vna, self.cal, meas, opts, log=self.logs.append)

    def test_lying_state_query_with_cor_on_screen_is_success_without_abort(self):
        self.make()
        res = self.run_flow()
        self.assertTrue(res.ok, res.message)
        self.assertEqual((res.saved, res.indicator, res.state_query), (True, 'on', False))
        self.assertEqual(self.vna.writes[-1], ':CALibration:S11:SAVE')
        self.assertEqual(aborts(self.vna), [])
        self.assertIn(':CALibration:S11:STATe?', self.vna.t.log, 'STATe? is still read (advisory)')
        self.assertTrue(any('advisory' in m for m in self.logs))
        self.assertTrue(Path(res.evidence).is_file() and res.evidence.endswith('-on.bmp'))

    def test_interpolated_is_success_and_says_so(self):
        self.make(screen_badge='interpolated')
        res = self.run_flow()
        self.assertTrue(res.ok)
        self.assertIn('interpolated', res.message)
        self.assertEqual(aborts(self.vna), [])

    def test_blank_screen_after_save_is_reported_but_never_aborted(self):
        self.make(screen_badge='none')
        res = self.run_flow()
        self.assertFalse(res.ok)
        self.assertTrue(res.saved)
        self.assertEqual(res.indicator, 'none')
        self.assertEqual(aborts(self.vna), [])
        self.assertIn('left in place', res.message)
        self.assertEqual(len([q for q in self.vna.t.log if q.startswith(':PRIV:SNAP')]), 4, 'polled the badge')

    def test_unclassifiable_screen_leaves_the_cal_and_asks_to_check(self):
        self.make(screen_badge='missing')
        res = self.run_flow()
        self.assertTrue(res.ok)
        self.assertEqual(res.indicator, 'unknown')
        self.assertIn('could not auto-verify', res.message)
        self.assertEqual(aborts(self.vna), [])

    def test_screenshot_failure_after_save_never_aborts(self):
        self.make()
        self.vna.snapshot = lambda timeout=20.0: (_ for _ in ()).throw(OSError('link lost'))
        res = self.run_flow()
        self.assertTrue(res.saved)
        self.assertIn('could not auto-verify', res.message)
        self.assertEqual(aborts(self.vna), [])

    def test_error_inside_save_never_aborts(self):
        self.make()

        def bad_save(meas, wait_s=3.0):
            self.vna.write(f':CALibration:{meas}:SAVE')
            raise OSError('timeout after SAVE')
        self.vna.cal_save = bad_save
        res = self.run_flow()
        self.assertFalse(res.ok)
        self.assertTrue(res.saved)
        self.assertIn('left in place', res.message)
        self.assertEqual(aborts(self.vna), [])

    def test_s21_same_rules(self):
        self.make('S21', screen_badge='none')
        res = self.run_flow('S21')
        self.assertTrue(res.saved)
        self.assertEqual(aborts(self.vna), [])
        self.make('S21')
        res = self.run_flow('S21')
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.vna.writes[-1], ':CALibration:S21:SAVE')
        self.assertEqual(aborts(self.vna), [])

    def test_cancel_before_save_aborts(self):
        self.make()
        cancel = threading.Event()
        orig = self.vna.cal_measure

        def measure(meas, step, settle_s=3.0):
            orig(meas, step, settle_s)
            if step == 'SHORT':
                cancel.set()
        self.vna.cal_measure = measure
        opts = calflow.CalOptions(settle_s=0, switch_settle_s=0, expected_kit=self.kit)
        res = calflow.run_cal(self.vna, self.cal, 'S11', opts, log=self.logs.append, cancel=cancel)
        self.assertFalse(res.ok or res.saved)
        self.assertEqual(aborts(self.vna), [':CALibration:S11:ABORt'])
        self.assertNotIn(':CALibration:S11:SAVE', self.vna.writes)

    def test_no_abort_path_after_save_in_the_code(self):
        """The only cal_abort() calls: calflow before Done, the GUI's and CLI's explicit discard."""
        root = Path(__file__).resolve().parent.parent / 'wheecal'
        calls = {p.name: p.read_text().count('cal_abort(') for p in root.glob('*.py')}
        self.assertEqual({k: v for k, v in calls.items() if v}, {'calflow.py': 1, 'gui.py': 1, 'cli.py': 1})
        src = (root / 'calflow.py').read_text()
        self.assertLess(src.index('elif started and opts.mode == \'auto\':'), src.index('vna.cal_abort(meas)'))


class DiscardTests(IsolatedConfig, unittest.TestCase):
    def test_cli_discard_is_explicit(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(['--dry-run', '--discard-cal', 's11', '--yes'])
        self.assertEqual(code, 0)
        self.assertIn('S11 calibration discarded', out.getvalue())


class FitBandTests(IsolatedConfig, unittest.TestCase):
    def test_default_is_span(self):
        self.assertEqual(config.DEFAULTS['fit_band'], 'span')
        self.assertEqual(config.load()['fit_band'], 'span')

    def test_v010_file_default_full_moves_to_span(self):
        Path(self.config_file).write_text(json.dumps({'fit_band': 'full', 'host': 'x'}))
        self.assertEqual(config.load()['fit_band'], 'span')
        Path(self.config_file).write_text(json.dumps({'fit_band': 'full', 'settings_version': 2}))
        self.assertEqual(config.load()['fit_band'], 'full', 'a choice made in v0.1.1 stays')

    def test_limits(self):
        lim = fit.fit_band_limits
        self.assertEqual(lim('span', 0.1e9, 8.5e9, (1.2e9, 1.9e9), 6.5e9)[:2], (1.2e9, 1.9e9))
        self.assertEqual(lim('full', 0.0, 8.5e9, (1.2e9, 1.9e9), 6.5e9)[:2], (0.0, 6.5e9))
        self.assertEqual(lim('span', 0.1e9, 8.5e9, None, 6.5e9)[:2], (0.1e9, 6.5e9))
        self.assertEqual(lim('span', 0.1e9, 8.5e9, (5e9, 9e9), 6.5e9)[:2], (5e9, 6.5e9))
        self.assertEqual(lim('span', 0.1e9, 6.0e9, (5e9, 6.5e9), 6.5e9)[:2], (5e9, 6.0e9), 'within the data')
        self.assertEqual(RSA5065N.FREQ_MAX_HZ, 6.5e9)

    def test_librecal_kit_uses_the_sweep_and_the_limit(self):
        cal, vna, logs = LibreCAL.mock(), RSA5065N.mock(), []
        kit, fits = cli.librecal_kit(cal, vna, 1, 'span', log=logs.append)
        self.assertLessEqual(fits['OPEN'].f_hi, 2.0e9)
        self.assertGreaterEqual(fits['OPEN'].f_lo, 1.0e9)
        self.assertTrue(any('the VNA sweep' in m for m in logs))
        self.assertTrue(any(m.strip().startswith('open: worst error') for m in logs))

        class Small(RSA5065N):
            FREQ_MAX_HZ = 3e9
        kit, fits = cli.librecal_kit(LibreCAL.mock(), None, 1, 'full', log=logs.append, driver=Small)
        self.assertLessEqual(fits['OPEN'].f_hi, 3.1e9)

    def test_bad_open_fit_warns(self):
        f = fit.StandardFit('OPEN', residual_db=5.3, f_lo=0, f_hi=8.5e9)
        w = fit.residual_warnings({'OPEN': f, 'SHORT': fit.StandardFit('SHORT', residual_db=-40)})
        self.assertEqual(len(w), 1)
        self.assertIn('C0..C3 open', w[0])


if __name__ == '__main__':
    unittest.main()
