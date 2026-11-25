import cmath
import math
import unittest

from wheecal import fit
from wheecal.calkit import C_LIGHT
from wheecal.librecal import LibreCAL


def _coeffs(dev, *names):
    return {n: dev.coefficient('FACTORY', n) for n in names}


class ReflectionFitTests(unittest.TestCase):
    def test_recovers_the_synthetic_standards(self):
        kit, fits = fit.kit_from_librecal(_coeffs(LibreCAL.mock(), 'P1_OPEN', 'P1_SHORT', 'P1_LOAD'), 1)
        self.assertAlmostEqual(kit.open_len_m, 5e-3, delta=2e-5)
        self.assertAlmostEqual(kit.open_c[0], 50e-15, delta=0.5e-15)
        self.assertAlmostEqual(kit.short_len_m, 5e-3, delta=2e-5)
        self.assertAlmostEqual(kit.short_l[0], 10e-12, delta=0.5e-12)
        self.assertEqual(kit.load_r, 51.0)
        self.assertEqual(kit.thru_len_m, 0.0)
        self.assertLess(fits['OPEN'].residual_db, -60)
        self.assertLess(fits['SHORT'].residual_db, -60)

    def test_band_limited_fit_matches_in_band(self):
        _, fits = fit.kit_from_librecal(_coeffs(LibreCAL.mock(), 'P1_OPEN', 'P1_SHORT', 'P1_LOAD'), 1, 1.2e9, 1.9e9)
        self.assertLess(fits['OPEN'].residual_db, -60)
        self.assertLessEqual(fits['OPEN'].f_lo, 1.2e9)
        self.assertGreaterEqual(fits['OPEN'].f_hi, 1.9e9)


def _thru(delay_s, f_lo=10e6, f_hi=6e9, n=401, phi0=0.3, mag=0.9, ripple_deg=0.0):
    out = []
    for i in range(n):
        f = f_lo + (f_hi - f_lo) * i / (n - 1)
        ph = phi0 - 2 * math.pi * f * delay_s + math.radians(ripple_deg) * math.sin(i)
        s21 = mag * cmath.exp(1j * ph)
        out.append((f, [0.01 + 0j, s21, s21, 0.02 + 0j]))
    return out


class ThruFitTests(unittest.TestCase):
    def test_known_delay_in_known_delay_out(self):
        for delay in (0.0, 37.5e-12, 150e-12, 1.234e-9, 5e-9):        # up to ~180 phase wraps
            ft = fit.fit_thru(_thru(delay))
            self.assertAlmostEqual(ft.delay_s, delay, delta=1e-15, msg=delay)
            self.assertAlmostEqual(ft.length_m, delay * C_LIGHT, delta=max(1e-6, 5e-5 * delay * C_LIGHT))   # rounded to 5 significant digits
            self.assertLess(ft.residual_deg, 1e-6)
            self.assertAlmostEqual(ft.loss_db, -20 * math.log10(0.9), places=6)

    def test_reverse_direction_uses_s12(self):
        data = [(f, [s[0], 1 + 0j, s[1], s[3]]) for f, s in _thru(200e-12)]   # S21 flat, S12 delayed
        self.assertAlmostEqual(fit.fit_thru(data, 2, 1).delay_s, 200e-12, delta=1e-15)
        self.assertAlmostEqual(fit.fit_thru(data, 1, 2).delay_s, 0.0, delta=1e-15)

    def test_ripple_is_reported(self):
        ft = fit.fit_thru(_thru(100e-12, ripple_deg=2.0))
        self.assertAlmostEqual(ft.delay_s, 100e-12, delta=0.2e-12)
        self.assertGreater(ft.residual_deg, 1.0)

    def test_coefficient_names_and_bad_input(self):
        self.assertEqual(fit.thru_coefficient_name(2, 1), 'P12_THROUGH')
        self.assertEqual(fit.thru_coefficient_name(3, 4), 'P34_THROUGH')
        with self.assertRaises(ValueError):
            fit.thru_coefficient_name(2, 2)
        with self.assertRaises(ValueError):
            fit.fit_thru(_thru(1e-10)[:1])

    def test_kit_gets_the_librecal_thru_length(self):
        dev = LibreCAL.mock()
        co = _coeffs(dev, 'P1_OPEN', 'P1_SHORT', 'P1_LOAD', 'P12_THROUGH')
        kit, fits = fit.kit_from_librecal(co, 1, thru_pair=(1, 2))
        self.assertAlmostEqual(fits['THRU'].delay_s, dev.link.thru_delay_s, delta=1e-14)
        self.assertAlmostEqual(kit.thru_len_m, 150e-12 * C_LIGHT, delta=1e-6)    # 44.969 mm
        self.assertIn('through 1-2', kit.note)
        kit2, _ = fit.kit_from_librecal(co, 1, thru_pair=(1, 3))
        self.assertEqual(kit2.thru_len_m, 0.0)
        self.assertIn('no P13_THROUGH', kit2.note)
