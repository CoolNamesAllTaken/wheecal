import inspect
import unittest

from wheecal import drivers
from wheecal.drivers.base import VnaDriver


class RegistryTests(unittest.TestCase):
    def test_default_and_lookup(self):
        self.assertEqual(drivers.DEFAULT, 'rsa5065n')
        self.assertIs(drivers.get(None), drivers.get('RSA5065N'))
        self.assertIn('rsa5065n', drivers.names())
        with self.assertRaises(KeyError):
            drivers.get('nope')

    def test_every_driver_implements_the_contract(self):
        for name in drivers.names():
            cls = drivers.get(name)
            self.assertTrue(issubclass(cls, VnaDriver))
            self.assertFalse(inspect.isabstract(cls), name)
            self.assertEqual(cls.NAME, name)
            self.assertTrue(cls.TITLE and cls.ALLOWED_WRITES and cls.CAL_STEPS)
            for meas, steps in cls.CAL_STEPS.items():
                self.assertIn(meas, ('S11', 'S21'))
                self.assertTrue(set(steps) <= {'OPEN', 'SHORT', 'LOAD', 'THRU', 'ISOLATION'})
            vna = cls.mock()
            self.assertEqual(vna.identify()['mode'], 'VNA')
            self.assertEqual(vna.writes, [])                     # identifying sends no setting

    def test_register(self):
        class Fake(drivers.get('rsa5065n')):
            NAME = 'fake-test'
        try:
            drivers.register(Fake)
            self.assertIs(drivers.get('fake-test'), Fake)
        finally:
            drivers.DRIVERS.pop('fake-test', None)
        with self.assertRaises(ValueError):
            drivers.register(type('NoName', (VnaDriver,), {}))
