"""v0.1.2 behaviour: the calibration waits scale to the VNA's sweep time (the RSA5065N's *OPC? does not wait for a
sweep, and fixed waits shorter than one sweep measured blends of two standards), the S11 trace read and the
verify-standards check."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from wheecal import calflow, cli
from wheecal.drivers.base import VnaDriver
from wheecal.drivers.rsa5065n import RSA5065N, parse_trace
from wheecal.librecal import LibreCAL

from .helpers import IsolatedConfig
from .test_calflow import fitted


class MeasurementSettleTests(unittest.TestCase):
    def test_sweep_time_scaling(self):
        vna = RSA5065N.mock()
        for sweep, floor, want in ((None, 3.0, 3.0), (0.0, 3.0, 3.0), (-1.0, 0.5, 0.5),
                                   (4.5, 0.5, 10.0), (4.5, 3.0, 10.0), (4.5, 20.0, 20.0)):
            with mock.patch.object(vna, 'sweep_time', return_value=sweep):
                self.assertAlmostEqual(vna.measurement_settle(floor), want, msg=f'sweep {sweep}, floor {floor}')

    def test_sweep_time_query(self):
        self.assertEqual(RSA5065N.mock().sweep_time(), 0.0)
        self.assertAlmostEqual(RSA5065N.mock(sweep_s=4.5).sweep_time(), 4.5)
        vna = RSA5065N.mock()
        vna.t._answer = lambda q: 'error' if q.startswith(':SWE') else None
        self.assertIsNone(vna.sweep_time(), 'unparsable reply -> None')
        vna.t._answer = lambda q: None
        self.assertIsNone(vna.sweep_time(), 'no reply -> None')
        self.assertEqual(vna.measurement_settle(0.5), 0.5)

    def test_base_driver_default_is_the_floor(self):
        vna = RSA5065N.mock(sweep_s=4.5)
        self.assertEqual(VnaDriver.measurement_settle(vna, 2.5), 2.5, 'drivers without sweeps keep the floor')
        with self.assertRaises(NotImplementedError):
            VnaDriver.read_s11(vna)

    def test_cal_measure_waits_for_two_sweeps(self):
        vna = RSA5065N.mock(sweep_s=4.5)
        with mock.patch('wheecal.drivers.rsa5065n.time.sleep') as sleep:
            vna.cal_measure('S11', 'OPEN', settle_s=3.0)
        self.assertIn(mock.call(10.0), sleep.call_args_list)
        self.assertNotIn(mock.call(3.0), sleep.call_args_list)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.cal = LibreCAL.mock()
        self.kit = fitted(self.cal)

    def make(self, **kw):
        vna = RSA5065N.mock(**kw)
        vna.write_kit(self.kit)
        vna.writes.clear()
        return vna

    def opts(self, **kw):
        kw = {'settle_s': 0, 'switch_settle_s': 0, **kw}
        return calflow.CalOptions(verify_gap_s=0, expected_kit=self.kit, **kw)

    def test_auto_s11_end_to_end_with_instant_sweeps(self):
        vna = self.make()
        res = calflow.run_s11(vna, self.cal, self.opts(), log=lambda m: None)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.steps, ['OPEN', 'SHORT', 'LOAD', 'DONE'])
        self.assertEqual(vna.writes[-4:], [':CALibration:S11:OPEN', ':CALibration:S11:SHORt',
                                           ':CALibration:S11:LOAD', ':CALibration:S11:SAVE'])
        self.assertGreaterEqual(vna.t.log.count(':SWEep:TIME?'), 6, 'sweep time read before and after each step')

    def test_switch_wait_scales_before_each_acquire(self):
        vna = self.make(sweep_s=4.5)
        events = []
        measure = vna.cal_measure
        vna.cal_measure = lambda *a, **k: (events.append('measure'), measure(*a, **k))[1]
        with mock.patch('wheecal.calflow.time.sleep', side_effect=lambda s: events.append(s)), \
                mock.patch('wheecal.drivers.rsa5065n.time.sleep', side_effect=lambda s: events.append(s)):
            res = calflow.run_s11(vna, self.cal, self.opts(switch_settle_s=0.5, settle_s=3.0), log=lambda m: None)
        self.assertTrue(res.ok, res.message)
        for i, e in enumerate(events):
            if e == 'measure':
                self.assertEqual(events[i - 1], 10.0, 'a full 2 sweeps + 1 s after the switch, before measuring')
        self.assertEqual(events.count('measure'), 3)
        self.assertNotIn(0.5, events)
        self.assertNotIn(3.0, events)

    def test_guided_mode_also_waits_for_the_sweep(self):
        vna = self.make(sweep_s=1.0)
        waits = []

        def answer(m):
            if 'Done' in m:
                vna.t.cal['S11'] = '1'
            return True
        with mock.patch('wheecal.calflow.time.sleep', side_effect=waits.append):
            res = calflow.run_s11(vna, self.cal, self.opts(mode='guided'), log=lambda m: None, prompt=answer)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(waits.count(3.0), 3)


class TraceTests(unittest.TestCase):
    def test_parse_sample(self):
        self.assertEqual(parse_trace('(1.0,0.5)(0.9,-0.1)'), [complex(1.0, 0.5), complex(0.9, -0.1)])
        self.assertEqual(parse_trace(' (1.000000e+00, -2.5e-03) (-9.9e-01,1e-2)\n'),
                         [complex(1.0, -0.0025), complex(-0.99, 0.01)])

    def test_parse_rejects_junk(self):
        for bad in ('error', '', '(1.0)', '(1.0,0.5)junk', '(a,b)'):
            with self.assertRaises(ValueError, msg=bad):
                parse_trace(bad)

    def test_read_s11_query(self):
        vna = RSA5065N.mock(trace='(1.0,0.5)(0.9,-0.1)')
        self.assertEqual(vna.read_s11(), [complex(1.0, 0.5), complex(0.9, -0.1)])
        self.assertIn(':TRACe1:DATA?', vna.t.log)
        self.assertEqual(vna.writes, [], 'reading the trace writes nothing')


class VerifyStandardsTests(unittest.TestCase):
    def setUp(self):
        self.vna, self.cal = RSA5065N.mock(), LibreCAL.mock()
        self.logs = []

    def run_verify(self):
        return calflow.verify_standards(self.vna, self.cal, calflow.CalOptions(switch_settle_s=0),
                                        log=self.logs.append)

    def test_good_calibration_passes(self):
        self.vna.t.follow(self.cal.link, 1)
        out = self.run_verify()
        self.assertTrue(out['ok'], self.logs)
        self.assertAlmostEqual(out['OPEN']['mean'], 0.0, places=2)
        self.assertLess(out['LOAD']['mean'], -15)
        sets = [line for line, _ in self.cal.link.script if line.startswith(':PORT ')]
        self.assertEqual(sets[:3], [':PORT 1 OPEN', ':PORT 1 SHORT', ':PORT 1 LOAD'])
        self.assertEqual(self.cal.link.ports, ['NONE'] * 4, 'released')
        self.assertEqual(self.vna.writes, [])
        self.assertTrue(any('|S11| LOAD' in m and 'PASS' in m for m in self.logs))

    def test_corrupted_calibration_fails(self):
        self.vna.t.FOLLOW_GAMMA = {'OPEN': (2.0, 0.0), 'SHORT': (-1.0, 0.0), 'LOAD': (0.5, 0.0)}   # +6 dB open
        self.vna.t.follow(self.cal.link, 1)
        out = self.run_verify()
        self.assertFalse(out['ok'])
        self.assertFalse(out['OPEN']['ok'])
        self.assertTrue(out['SHORT']['ok'])
        self.assertFalse(out['LOAD']['ok'])

    def test_driver_without_trace_read_is_skipped(self):
        self.vna.read_s11 = None
        self.assertIsNone(self.run_verify())
        self.vna.read_s11 = mock.Mock(side_effect=NotImplementedError)
        self.assertIsNone(self.run_verify())
        self.assertTrue(all('skipped' in m for m in self.logs if m.startswith('Verify standards')))

    def test_waits_for_the_sweep_after_each_switch(self):
        self.vna.t.sweep_s = 2.0
        self.vna.t.follow(self.cal.link, 1)
        with mock.patch('wheecal.calflow.time.sleep') as sleep:
            self.run_verify()
        self.assertEqual(sleep.call_args_list, [mock.call(5.0)] * 3)


class CliTests(IsolatedConfig, unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_calibrate_and_verify(self):
        code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--load-kit', 'librecal', '--calibrate',
                                      '--verify-standards', '--yes', '--settle', '0')
        self.assertEqual(code, 0, out + err)
        self.assertIn('CAL OK', out)
        self.assertIn('VERIFY PASS', out)

    def test_verify_standards_alone(self):
        code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--verify-standards')
        self.assertEqual(code, 0, out + err)
        self.assertIn('VERIFY PASS', out)

    def test_verify_standards_refuses_s21(self):
        code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--calibrate', 's21', '--verify-standards',
                                      '--yes')
        self.assertEqual(code, 2)
        self.assertIn('S11', err)
        self.assertNotIn('CAL', out, 'refused before calibrating')


if __name__ == '__main__':
    unittest.main()
