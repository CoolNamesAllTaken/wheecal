"""v0.2.0: host-side S11 calibration (wheecal.hostcal): the OSL solver, correction, interpolation, the error-terms
file, Touchstone export, the flows on the mock VNA/LibreCAL, the CLI, and the real-data result."""
import cmath
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from wheecal import calflow, cli, config, hostcal
from wheecal.drivers.base import VnaDriver
from wheecal.drivers.rsa5065n import RSA5065N
from wheecal.librecal import LibreCAL

from .helpers import IsolatedConfig

DATA = Path(__file__).parent / 'data' / 'librecal-capture-25.json'
BOX = (complex(0.08, -0.05), complex(0.12, 0.07), complex(0.62, -0.31))


def raw_of(g, box=BOX):
    e00, e11, t = box
    return e00 + t * g / (1 - e11 * g)


def quiet(*_a, **_k):
    pass


class SolverTests(unittest.TestCase):
    def setUp(self):
        n = 9
        self.true_o = [cmath.exp(-0.3j * k) for k in range(n)]
        self.true_s = [-cmath.exp(-0.25j * k) for k in range(n)]
        self.true_l = [complex(0.02, 0.01 * k) for k in range(n)]
        self.raw = {k: [raw_of(g) for g in v] for k, v in
                    (('o', self.true_o), ('s', self.true_s), ('l', self.true_l))}
        self.terms = hostcal.solve_osl(self.raw['o'], self.raw['s'], self.raw['l'],
                                       self.true_o, self.true_s, self.true_l)

    def test_reproduces_the_standards(self):
        for raw, true in ((self.raw['o'], self.true_o), (self.raw['s'], self.true_s), (self.raw['l'], self.true_l)):
            for a, b in zip(hostcal.apply(self.terms, raw), true):
                self.assertAlmostEqual(abs(a - b), 0.0, places=12)

    def test_terms_match_the_box(self):
        e00, e11, t = BOX
        for got in self.terms:
            self.assertAlmostEqual(abs(got[0] - e00), 0, places=12)
            self.assertAlmostEqual(abs(got[1] - e11), 0, places=12)
            self.assertAlmostEqual(abs(got[2] - (e00 * e11 - t)), 0, places=12)

    def test_apply_round_trips_any_dut(self):
        dut = [complex(0.3, -0.2), complex(-0.5, 0.1), 0j, complex(0.9, 0.4)]
        terms = self.terms[:len(dut)]
        back = hostcal.apply(terms, [raw_of(g) for g in dut])
        for a, b in zip(back, dut):
            self.assertAlmostEqual(abs(a - b), 0.0, places=12)

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            hostcal.apply(self.terms, [0j])
        with self.assertRaises(ValueError):
            hostcal.solve_osl([1], [2], [3], [1], [2], [])
        with self.assertRaises(ValueError):            # identical standards: singular
            hostcal.solve3(1, 1, 0, 1, 1, 0)


class InterpTests(unittest.TestCase):
    rows = [(1.0, 1 + 1j), (2.0, 3 + 0j), (4.0, [1j, 5])]   # a list: the first item is used (a through's S11)

    def test_nodes_midpoints_and_ends(self):
        got = hostcal.interp_complex(self.rows, [0.0, 1.0, 1.5, 2.0, 3.0, 4.0, 9.0])
        want = [1 + 1j, 1 + 1j, 2 + 0.5j, 3, 1.5 + 0.5j, 1j, 1j]
        for a, b in zip(got, want):
            self.assertAlmostEqual(abs(a - b), 0.0, places=12)

    def test_empty(self):
        with self.assertRaises(ValueError):
            hostcal.interp_complex([], [1.0])

    def test_matches_the_reference_harness(self):
        """The research harness' ic(): j advances while fs[j+1] < f; same result at every point."""
        def ic(rows, freqs):
            fs, out = [r[0] for r in rows], []
            for f in freqs:
                if f <= fs[0]:
                    out.append(rows[0][1]); continue
                if f >= fs[-1]:
                    out.append(rows[-1][1]); continue
                j = 0
                while fs[j + 1] < f:
                    j += 1
                t = (f - fs[j]) / (fs[j + 1] - fs[j])
                out.append(rows[j][1] + (rows[j + 1][1] - rows[j][1]) * t)
            return out
        rows = [(f * 7.0, complex(f % 3, -f)) for f in range(1, 40)]
        freqs = [x * 2.3 for x in range(130)]
        for a, b in zip(hostcal.interp_complex(rows, freqs), ic(rows, freqs)):
            self.assertAlmostEqual(abs(a - b), 0.0, places=12)


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.terms = hostcal.ErrorTerms([1e6, 2e6, 3e6], [(1 + 2j, 3 - 4j, 0.5j)] * 3, 1, 'FACTORY',
                                        '2026-11-26T10:00:00', 'VNA', 'LC')

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_round_trip(self):
        p = self.terms.save(self.dir / 'sub' / 'hc.json')
        back = hostcal.ErrorTerms.load(p)
        self.assertEqual(back, self.terms)
        d = json.loads(p.read_text())
        self.assertEqual(d['format'], 'wheecal-host-cal')
        self.assertEqual(d['e00'][0], [1.0, 2.0])

    def test_bad_files(self):
        with self.assertRaises(FileNotFoundError):
            hostcal.ErrorTerms.load(self.dir / 'missing.json')
        with self.assertRaises(ValueError):
            hostcal.ErrorTerms.from_json({'format': 'other'})
        d = self.terms.to_json()
        d['De'] = d['De'][:2]
        with self.assertRaises(ValueError):
            hostcal.ErrorTerms.from_json(d)
        d = self.terms.to_json()
        d['version'] = 99
        with self.assertRaises(ValueError):
            hostcal.ErrorTerms.from_json(d)

    def test_check_sweep(self):
        self.terms.check_sweep([1e6, 2e6, 3e6])
        with self.assertRaisesRegex(ValueError, 'points'):
            self.terms.check_sweep([1e6, 2e6])
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.terms.check_sweep([1e6, 2e6, 3.1e6])

    def test_s1p(self):
        p = hostcal.write_s1p(self.dir / 'x.s1p', [1e6, 2.5e9], [0.5 - 0.25j, -1 + 0j], 'a comment')
        lines = p.read_text().splitlines()
        self.assertTrue(lines[0].startswith('! wheecal '))
        self.assertIn('! a comment', lines)
        self.assertIn('# HZ S RI R 50', lines)
        data = lines[lines.index('# HZ S RI R 50') + 1:]
        self.assertEqual(len(data), 2)
        f, re_, im = data[1].split()
        self.assertEqual((float(f), float(re_), float(im)), (2.5e9, -1.0, 0.0))
        freqs, gam = hostcal.read_s1p(p)
        self.assertEqual(freqs, [1e6, 2.5e9])
        self.assertEqual(gam, [0.5 - 0.25j, -1 + 0j])
        with self.assertRaises(ValueError):
            hostcal.write_s1p(self.dir / 'y.s1p', [1.0], [])


class DriverTests(unittest.TestCase):
    def test_sweep_freqs(self):
        vna = RSA5065N.mock(points=8)
        f = vna.sweep_freqs()
        self.assertEqual(len(f), 8)
        self.assertAlmostEqual(f[0], 1.2e9)
        self.assertAlmostEqual(f[-1], 1.9e9)
        self.assertAlmostEqual(f[1] - f[0], 0.1e9)
        self.assertEqual(RSA5065N.mock(points=1).sweep_freqs(), [1.2e9])
        vna.t._answer = lambda q: None
        self.assertIsNone(vna.sweep_freqs())
        self.assertIsNone(VnaDriver.sweep_freqs(vna), 'the base driver has no sweep frequencies')

    def test_follow_raw_is_the_box(self):
        vna, cal = RSA5065N.mock(), LibreCAL.mock()
        vna.t.follow_raw(cal.link, 1, points=5)
        cal.set_port(1, 'LOAD')
        tr = vna.read_s11()
        self.assertEqual(len(tr), 5)
        g = (51 - 50) / (51 + 50)
        self.assertAlmostEqual(abs(tr[0] - raw_of(g, vna.t.RAW_BOX)), 0, places=9)


@mock.patch('wheecal.hostcal.time.sleep')
class FlowTests(unittest.TestCase):
    def setUp(self):
        self.vna, self.cal = RSA5065N.mock(), LibreCAL.mock()
        self.vna.t.follow_raw(self.cal.link, 1, points=25)
        self.opts = calflow.CalOptions(switch_settle_s=0.5)
        self.log = []

    def host_cal(self):
        res = hostcal.run_host_cal(self.vna, self.cal, self.opts, log=self.log.append)
        self.assertTrue(res.ok, res.message)
        return res

    def test_cal_aborts_onboard_and_releases(self, sleep):
        res = self.host_cal()
        self.assertEqual(self.vna.writes, [':CALibration:S11:ABORt'])
        self.assertTrue(any('WARNING' in m and 'aborted' in m for m in self.log))
        self.assertEqual(self.cal.link.ports, ['NONE'] * 4)
        self.assertEqual(len(res.terms.freqs), 25)
        self.assertLess(max(res.check.values()), 1e-9)
        self.assertEqual(res.terms.vna.split(',')[1], 'RSA5065N')
        self.assertTrue(sleep.called)
        # the standards are read before the on-board cal is touched
        first_abort = next(i for i, m in enumerate(self.log) if 'WARNING' in m)
        self.assertLess(max(i for i, m in enumerate(self.log) if m.startswith('Reading')), first_abort)

    def test_cal_stops_cleanly(self, _sleep):
        vna = RSA5065N.mock(meas='S21')
        res = hostcal.run_host_cal(vna, self.cal, self.opts, log=self.log.append)
        self.assertFalse(res.ok)
        self.assertIn('S11', res.message)
        self.assertEqual(vna.writes, [], 'nothing aborted when the VNA is not on S11')
        self.vna.t._answer = (lambda orig: lambda q: None if 'POIN' in q else orig(q))(self.vna.t._answer)
        res = hostcal.run_host_cal(self.vna, self.cal, self.opts, log=self.log.append)
        self.assertFalse(res.ok)
        self.assertIn('sweep frequencies', res.message)
        self.assertEqual(self.cal.link.ports, ['NONE'] * 4)

    def test_verify_pass(self, _sleep):
        terms = self.host_cal().terms
        r = hostcal.verify_host_cal(self.vna, self.cal, terms, self.opts, log=self.log.append)
        self.assertTrue(r['ok'])
        self.assertLess(r['mean'], 1e-6)
        self.assertEqual(r['points'], 25)
        self.assertEqual(self.cal.link.ports, ['NONE'] * 4)
        self.assertTrue(any('PASS' in m for m in self.log))

    def test_verify_fail_with_onboard_corrected_trace(self, _sleep):
        """follow(): the trace is already corrected on board (constant per standard) -> host terms are wrong."""
        terms = self.host_cal().terms
        self.vna.t.follow(self.cal.link, 1, points=25)
        r = hostcal.verify_host_cal(self.vna, self.cal, terms, self.opts, log=self.log.append)
        self.assertFalse(r['ok'])
        self.assertGreater(r['mean'], hostcal.VERIFY_MAX_MEAN)
        self.assertTrue(any('FAIL' in m for m in self.log))

    def test_verify_fail_after_drift(self, _sleep):
        terms = self.host_cal().terms
        e00, e11, t = self.vna.t.RAW_BOX
        self.vna.t.follow_raw(self.cal.link, 1, box=(e00 + 0.15, e11, t * 0.8))
        self.assertFalse(hostcal.verify_host_cal(self.vna, self.cal, terms, self.opts, log=quiet)['ok'])

    def test_measure_dut(self, _sleep):
        terms = self.host_cal().terms
        self.vna.t.follow_raw(None)
        freqs, gam = hostcal.measure_dut(self.vna, terms, self.opts, log=quiet)
        self.assertEqual(freqs, terms.freqs)
        for g in gam:
            self.assertAlmostEqual(abs(g - self.vna.t.RAW_DUT), 0, places=9)
        self.vna.t.points = 24
        with self.assertRaisesRegex(ValueError, 'sweep changed'):
            hostcal.measure_dut(self.vna, terms, self.opts, log=quiet)


class RealDataTests(unittest.TestCase):
    """A trimmed real capture (25 points, 10 MHz-3 GHz): the host FACTORY correction of the LibreCAL through (port 2
    matched) lands on its FACTORY S11, at the metric's floor (~0.06), well inside the 0.12 pass limit; ideal
    standards are clearly worse."""

    @classmethod
    def setUpClass(cls):
        d = json.loads(DATA.read_text())
        cx = lambda p: [complex(a, b) for a, b in p]
        cls.freqs = d['freqs']
        cls.raw = {k: cx(d[k]) for k in ('raw_open', 'raw_short', 'raw_load', 'raw_dut')}
        cls.fac = {k: [(r[0], [complex(r[i], r[i + 1]) for i in range(1, len(r), 2)]) for r in rows]
                   for k, rows in d['factory'].items()}

    def host(self, o, s, l):
        terms = hostcal.solve_osl(self.raw['raw_open'], self.raw['raw_short'], self.raw['raw_load'], o, s, l)
        return terms, hostcal.apply(terms, self.raw['raw_dut'])

    def test_host_factory_matches_the_through(self):
        std = {k: hostcal.interp_complex(self.fac[f'P1_{k}'], self.freqs) for k in ('OPEN', 'SHORT', 'LOAD')}
        terms, dut = self.host(std['OPEN'], std['SHORT'], std['LOAD'])
        for k, raw in (('OPEN', 'raw_open'), ('SHORT', 'raw_short'), ('LOAD', 'raw_load')):
            err = max(abs(a - b) for a, b in zip(hostcal.apply(terms, self.raw[raw]), std[k]))
            self.assertLess(err, 1e-9, f'corr(raw {k}) == FACTORY {k}')
        truth = hostcal.interp_complex([(f, s[0]) for f, s in self.fac['P12_THROUGH']], self.freqs)
        r = hostcal.residual(dut, truth, self.freqs)
        self.assertEqual(r['points'], 25)
        self.assertLess(r['mean'], hostcal.VERIFY_MAX_MEAN)
        self.assertLess(r['mean'], 0.08)
        self.assertLess(r['max'], 0.15)
        n = len(self.freqs)
        _, ideal = self.host([1 + 0j] * n, [-1 + 0j] * n, [0j] * n)
        self.assertGreater(hostcal.residual(ideal, truth, self.freqs)['mean'], r['mean'] + 0.02)


@mock.patch('wheecal.hostcal.time.sleep')
class CliHostTests(IsolatedConfig, unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_cal_verify_export_then_correct(self, _sleep):
        with tempfile.TemporaryDirectory() as d:
            s1p = os.path.join(d, 'dut.s1p')
            code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--host-cal', '--host-cal-verify',
                                          '--export', s1p, '--yes')
            self.assertEqual(code, 0, out + err)
            self.assertIn('host calibration saved to', out)
            self.assertIn('HOST VERIFY PASS', out)
            self.assertIn(':CALibration:S11:ABORt', out)
            self.assertEqual(out.count('WARNING: reading RAW S11'), 1, 'aborted once per run')
            self.assertTrue(config.hostcal_path().exists())
            freqs, gam = hostcal.read_s1p(s1p)
            self.assertEqual(len(freqs), 21)
            self.assertAlmostEqual(abs(gam[0] - RSA5065N.mock().t.RAW_DUT), 0, places=6)

            s1p2 = os.path.join(d, 'again.s1p')
            code, out, err = self.run_cli('--dry-run', '--host-correct', s1p2, '--yes')
            self.assertEqual(code, 0, out + err)
            self.assertIn('|S11| host-corrected', out)
            self.assertIn('-13.01', out)                 # |0.2-0.1j| = -13.01 dB
            self.assertTrue(os.path.exists(s1p2))

            code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--host-cal-verify', '--yes')
            self.assertEqual(code, 0, out + err)
            self.assertIn('HOST VERIFY PASS', out)

    def test_custom_file_and_errors(self, _sleep):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'terms.json')
            code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--host-cal', '--host-cal-file', f,
                                          '--yes')
            self.assertEqual(code, 0, out + err)
            self.assertTrue(os.path.exists(f))
            code, _, err = self.run_cli('--dry-run', '--host-correct', os.path.join(d, 'x.s1p'), '--host-cal-file',
                                        os.path.join(d, 'none.json'))
            self.assertEqual(code, 2)
            self.assertIn('run --host-cal first', err)
        code, _, err = self.run_cli('--dry-run', '--export', 'x.s1p')
        self.assertEqual(code, 2)
        self.assertIn('--export goes with --host-cal', err)
        code, _, err = self.run_cli('--dry-run', '--mock-librecal', '--host-cal', '--calibrate', '--yes')
        self.assertEqual(code, 2)
        self.assertIn('separately', err)

    def test_asks_before_aborting(self, _sleep):
        with mock.patch('builtins.input', return_value='n'):
            code, out, _ = self.run_cli('--dry-run', '--mock-librecal', '--host-cal')
        self.assertEqual(code, 1)
        self.assertIn('not started', out)
        self.assertNotIn('ABORt', out)


if __name__ == '__main__':
    unittest.main()
