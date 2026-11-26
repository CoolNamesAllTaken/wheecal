import threading
import unittest

from wheecal import calflow, fit
from wheecal.calkit import KIT_6650F03E_F
from wheecal.drivers.rsa5065n import RSA5065N
from wheecal.librecal import LibreCAL, LibreCALError


def fitted(dev, pair=None):
    names = ['P1_OPEN', 'P1_SHORT', 'P1_LOAD'] + ([fit.thru_coefficient_name(*pair)] if pair else [])
    co = {n: dev.coefficient('FACTORY', n) for n in names}
    return fit.kit_from_librecal(co, 1, thru_pair=pair)[0]


def trace(vna, cal):
    """One time-ordered log of both instruments' lines: ('VNA', line) / ('LC', line)."""
    events = []
    vw, lw = vna.t.write, cal.link.write
    vna.t.write = lambda line: (events.append(('VNA', line)), vw(line))[1]
    cal.link.write = lambda data: (events.append(('LC', data.decode().strip())), lw(data))[1]
    return events


class S11FlowTests(unittest.TestCase):
    def setUp(self):
        self.vna, self.cal = RSA5065N.mock(), LibreCAL.mock()
        self.kit = fitted(self.cal)
        self.vna.write_kit(self.kit)
        self.vna.writes.clear()
        self.cal.link.script.clear()

    def opts(self, **kw):
        return calflow.CalOptions(settle_s=0, switch_settle_s=0, verify_gap_s=0, expected_kit=self.kit, **kw)

    def test_auto_sequence(self):
        res = calflow.run_s11(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.vna.writes, [':CALibration:CKIT:TYPE OPEN', ':CALibration:CKIT:TYPE SHORt',
                                           ':CALibration:CKIT:TYPE LOAD', ':CALibration:CKIT:TYPE OPEN',
                                           ':CALibration:S11:OPEN', ':CALibration:S11:SHORt',
                                           ':CALibration:S11:LOAD', ':CALibration:S11:SAVE'])
        sets = [line for line, _ in self.cal.link.script if line.startswith(':PORT ')]
        self.assertEqual(sets, [':PORT 1 OPEN', ':PORT 1 SHORT', ':PORT 1 LOAD', ':PORT 1 NONE', ':PORT 2 NONE',
                                ':PORT 3 NONE', ':PORT 4 NONE'])
        self.assertTrue(self.vna.cal_state('S11'))

    def test_guided_sequence_prompts_and_never_measures(self):
        prompts = []

        def answer(m):
            prompts.append(m)
            if 'Done' in m:
                self.vna.t.cal['S11'] = '1'
            return True
        res = calflow.run_s11(self.vna, self.cal, self.opts(mode='guided'), log=lambda m: None, prompt=answer)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(len(prompts), 4)
        self.assertIn('S11 Meas Setup > Align > Open', prompts[0])
        self.assertFalse(any(w.startswith(':CALibration:S11') for w in self.vna.writes))

    def test_wrong_kit_stops_before_anything(self):
        self.vna.write_kit(KIT_6650F03E_F)
        self.vna.writes.clear()
        res = calflow.run_s11(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertIn('differs', res.message)
        self.assertFalse(any(w.startswith(':CALibration:S11') for w in self.vna.writes))

    def test_failure_mid_way_aborts_and_releases(self):
        def boom(port, std, dest=None):
            if std == 'LOAD':
                raise LibreCALError('switch failed')
            LibreCAL.set_port(self.cal, port, std, dest)
        self.cal.set_port = boom
        res = calflow.run_s11(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertEqual(self.vna.writes[-1], ':CALibration:S11:ABORt')
        self.assertEqual(self.cal.link.ports, ['NONE'] * 4)
        self.assertFalse(self.vna.cal_state('S11'))

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        res = calflow.run_s11(self.vna, self.cal, self.opts(), log=lambda m: None, cancel=ev)
        self.assertFalse(res.ok)
        self.assertEqual(res.message, 'cancelled')

    def test_not_vna(self):
        self.vna.t.mode = 'SA'
        res = calflow.run_s11(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertIn('not VNA', res.message)


class S21FlowTests(unittest.TestCase):
    def setUp(self):
        self.vna, self.cal = RSA5065N.mock(meas='S21'), LibreCAL.mock()
        self.kit = fitted(self.cal, (1, 2))
        self.vna.write_kit(self.kit)
        self.vna.writes.clear()
        self.cal.link.script.clear()

    def opts(self, **kw):
        kw.setdefault('expected_kit', self.kit)
        return calflow.CalOptions(settle_s=0, switch_settle_s=0, verify_gap_s=0, **kw)

    def test_exact_auto_sequence(self):
        events = trace(self.vna, self.cal)
        res = calflow.run_s21(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.steps, ['THRU', 'DONE'])
        # Only the cal-relevant lines, in time order across both instruments.
        keep = [e for e in events if e[0] == 'LC' and e[1].startswith(':PORT ')
                or e[0] == 'VNA' and e[1].startswith((':CALibration:S21', ':CONF'))]
        self.assertEqual(keep, [
            ('VNA', ':CONF?'),                              # the VNA must already measure S21
            ('LC', ':PORT 1 THROUGH 2'),
            ('VNA', ':CALibration:S21:THROugh'),
            ('VNA', ':CALibration:S21:SAVE'),
            ('VNA', ':CALibration:S21:STATe?'),
            ('LC', ':PORT 1 NONE'), ('LC', ':PORT 2 NONE'), ('LC', ':PORT 3 NONE'), ('LC', ':PORT 4 NONE'),
        ])
        self.assertEqual(self.vna.writes, [':CALibration:CKIT:TYPE THRough', ':CALibration:CKIT:TYPE OPEN',
                                           ':CALibration:S21:THROugh', ':CALibration:S21:SAVE'])
        self.assertTrue(self.vna.cal_state('S21'))
        self.assertFalse(self.vna.cal_state('S11'))
        self.assertNotIn(':PORT 1 LOAD', [e[1] for e in events])     # no isolation step on this VNA

    def test_other_ports(self):
        events = trace(self.vna, self.cal)
        res = calflow.run_s21(self.vna, self.cal, self.opts(librecal_port=3, through_port=4, expected_kit=None),
                              log=lambda m: None)
        self.assertTrue(res.ok, res.message)
        self.assertIn(('LC', ':PORT 3 THROUGH 4'), events)

    def test_needs_s21_on_the_panel(self):
        self.vna.t.meas = 'S11'
        res = calflow.run_s21(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertIn('select Meas > S21', res.message)
        self.assertEqual(self.vna.writes, [])
        self.assertFalse(any(line.startswith(':PORT 1 THROUGH') for line, _ in self.cal.link.script))

    def test_same_port_twice_is_refused(self):
        res = calflow.run_s21(self.vna, self.cal, self.opts(through_port=1), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertIn('two different', res.message)
        self.assertEqual(self.vna.writes, [])

    def test_thru_length_mismatch_stops(self):
        self.vna.write_kit(fitted(self.cal))                  # thru length 0, not the LibreCAL's 44.97 mm
        self.vna.writes.clear()
        res = calflow.run_s21(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertIn('THRU LENGth', res.message)
        self.assertFalse(any(w.startswith(':CALibration:S21') for w in self.vna.writes))

    def test_guided(self):
        prompts = []

        def answer(m):
            prompts.append(m)
            if 'Done' in m:
                self.vna.t.cal['S21'] = '1'
            return True
        res = calflow.run_s21(self.vna, self.cal, self.opts(mode='guided'), log=lambda m: None, prompt=answer)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(len(prompts), 2)
        self.assertIn('S21 Meas Setup > Align > Through', prompts[0])
        self.assertIn('S21 Meas Setup > Align > Done', prompts[1])
        self.assertFalse(any(w.startswith(':CALibration:S21') for w in self.vna.writes))

    def test_failure_aborts_s21(self):
        orig = self.vna.cal_measure

        def boom(meas, step, settle_s=3.0):
            orig(meas, step, settle_s)
            raise OSError('link lost')
        self.vna.cal_measure = boom
        res = calflow.run_s21(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertEqual(self.vna.writes[-1], ':CALibration:S21:ABORt')
        self.assertEqual(self.cal.link.ports, ['NONE'] * 4)

    def test_save_that_does_not_apply_is_reported_and_left_in_place(self):
        self.vna.t.screen_badge = 'none'
        res = calflow.run_s21(self.vna, self.cal, self.opts(), log=lambda m: None)
        self.assertFalse(res.ok)
        self.assertTrue(res.saved)
        self.assertEqual(res.indicator, 'none')
        self.assertIn('left in place', res.message)
        self.assertFalse(any('ABOR' in w.upper() for w in self.vna.writes), 'never aborted after Done')


class IsolationStepTests(unittest.TestCase):
    """A driver that lists ISOLATION gets both LibreCAL ports on LOAD for it."""

    def test_isolation_terminates_both_ports(self):
        class WithIsolation(RSA5065N):
            CAL_STEPS = {'S21': ('ISOLATION', 'THRU')}
            measured = []

            def cal_measure(self, meas, step, settle_s=3.0):
                self.measured.append((step, tuple(self_cal.link.ports)))

            def cal_save(self, meas, wait_s=15.0):
                return True

        self_cal = LibreCAL.mock()
        vna = WithIsolation.mock(meas='S21', screen_badge='on')
        res = calflow.run_s21(vna, self_cal, calflow.CalOptions(settle_s=0, switch_settle_s=0, verify_gap_s=0),
                              log=lambda m: None)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(vna.measured, [('ISOLATION', ('LOAD', 'LOAD', 'NONE', 'NONE')),
                                        ('THRU', ('THROUGH 2', 'THROUGH 1', 'NONE', 'NONE'))])
