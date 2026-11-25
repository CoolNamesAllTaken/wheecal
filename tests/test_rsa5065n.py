import json
import unittest

from wheecal.calkit import KIT_6650F03E_F, KIT_6650F03E_M, CalKit
from wheecal.drivers.rsa5065n import EDITABLE, RSA5065N, kit_fields
from wheecal.transport import ScpiError


class KitFieldTests(unittest.TestCase):
    def test_card_values_in_instrument_units(self):
        k = kit_fields(KIT_6650F03E_F)
        self.assertAlmostEqual(k['OPEN']['C0'], 49.433)
        self.assertAlmostEqual(k['OPEN']['C1'], -0.31013)
        self.assertAlmostEqual(k['OPEN']['C2'], 0.023168)
        self.assertAlmostEqual(k['OPEN']['C3'], -0.00015966)
        self.assertAlmostEqual(k['OPEN']['LENGth'], 0.008767)
        self.assertAlmostEqual(k['SHORt']['L0'], 2.0765)
        self.assertAlmostEqual(k['SHORt']['L1'], -0.10854)
        self.assertAlmostEqual(k['SHORt']['L2'], 0.0021705)
        self.assertAlmostEqual(k['SHORt']['L3'], -1e-05)
        self.assertAlmostEqual(k['SHORt']['LENGth'], 0.009529)
        self.assertEqual(k['LOAD'], {'Z0': 50.0, 'LOSS': 0.0})
        self.assertEqual(k['THRough'], {'LENGth': 0.0, 'LOSS': 0.0})

    def test_only_editable_fields(self):
        for kit in (KIT_6650F03E_F, KIT_6650F03E_M, CalKit('x', thru_len_m=0.01)):
            for typ, fields in kit_fields(kit).items():
                self.assertTrue(set(fields) <= set(EDITABLE[typ]), typ)

    def test_male_is_ideal(self):
        m = kit_fields(KIT_6650F03E_M)
        self.assertTrue(all(v == 0 for t in ('OPEN', 'SHORt') for v in m[t].values()))

    def test_thru_length_is_written(self):
        self.assertEqual(kit_fields(CalKit('t', thru_len_m=0.044969))['THRough']['LENGth'], 0.044969)


class DriverTests(unittest.TestCase):
    def test_write_reads_back_and_selects_custom(self):
        r = RSA5065N.mock()
        r.t.label = 'CK10'
        rows = r.write_kit(KIT_6650F03E_F)
        self.assertTrue(all(x['ok'] for x in rows))
        self.assertEqual(len(rows), 16)
        self.assertTrue(r.user_kit_selected())
        self.assertTrue(all(x['ok'] for x in r.verify_kit(KIT_6650F03E_F)))
        self.assertEqual(r.query(':CALibration:CKIT:TYPE?'), 'OPEN')
        self.assertEqual({x['standard'] for x in rows}, {'OPEN', 'SHORT', 'LOAD', 'THRU'})

    def test_every_write_is_paced_with_opc(self):
        r = RSA5065N.mock()
        r.write_kit(KIT_6650F03E_F)
        log = r.t.log
        for i, line in enumerate(log):
            if not line.endswith('?'):
                self.assertEqual(log[i + 1], '*OPC?', line)

    def test_a_dropped_write_is_resent(self):
        r = RSA5065N.mock(drop_every=5)
        self.assertTrue(all(x['ok'] for x in r.write_kit(KIT_6650F03E_F)))

    def test_greyed_load_length_is_never_checked(self):
        r = RSA5065N.mock()
        r.write_kit(KIT_6650F03E_F)
        r.set_type('LOAD')
        self.assertNotEqual(float(r.query(':CALibration:CKIT:LENGth?')), 0.0)     # the quirk
        self.assertTrue(all(x['ok'] for x in r.verify_kit(KIT_6650F03E_F)))

    def test_verify_a_subset(self):
        r = RSA5065N.mock()
        r.write_kit(CalKit('t', thru_len_m=0.03))
        r.writes.clear()
        rows = r.verify_kit(CalKit('t', thru_len_m=0.03), ('THRU',))
        self.assertEqual([(x['standard'], x['field']) for x in rows], [('THRU', 'LENGth'), ('THRU', 'LOSS')])
        self.assertTrue(all(x['ok'] for x in rows))
        self.assertEqual(r.writes, [':CALibration:CKIT:TYPE THRough', ':CALibration:CKIT:TYPE OPEN'])

    def test_backup_restore_round_trip(self):
        r = RSA5065N.mock()
        before = r.backup_kit()
        self.assertEqual(before['driver'], 'rsa5065n')
        r.write_kit(KIT_6650F03E_F)
        rows = r.restore_kit(json.loads(json.dumps(before)))
        self.assertTrue(all(x['ok'] for x in rows))
        after = r.backup_kit()
        self.assertEqual({t: before[t] for t in EDITABLE}, {t: after[t] for t in EDITABLE})

    def test_backup_of_another_driver_is_refused(self):
        with self.assertRaises(ScpiError):
            RSA5065N.mock().restore_kit({'driver': 'other', 'OPEN': {}})

    def test_measurement_settings_are_refused(self):
        r = RSA5065N.mock()
        for cmd in (':FREQ:SPAN 1e9', ':SWE:POIN 201', ':SENS:BAND 1e3', ':SOUR:POW -5', '*RST', ':SYST:PRES',
                    ':CALibration:CLEAr', ':CALibration:S21:STATe 0', ':CALibration:DTF:CLEAr', ':INST:SEL SA',
                    ':CONF S21'):
            with self.assertRaises(ScpiError, msg=cmd):
                r.write(cmd)
        self.assertEqual(r.writes, [])

    def test_s21_steps_are_whitelisted_and_thru_only(self):
        self.assertEqual(RSA5065N.CAL_STEPS['S21'], ('THRU',))
        for cmd in (':CALibration:S21:THROugh', ':CALibration:S21:SAVE', ':CALibration:S21:ABORt'):
            self.assertTrue(cmd.startswith(RSA5065N.ALLOWED_WRITES), cmd)

    def test_require_mode_never_writes(self):
        r = RSA5065N.mock(meas='S11')
        with self.assertRaises(ScpiError) as cm:
            r.require_mode('S21')
        self.assertIn('select Meas > S21', str(cm.exception))
        self.assertEqual(r.writes, [])
        with self.assertRaises(ScpiError):
            RSA5065N.mock(mode='SA').require_mode()
