import unittest

from wheecal.librecal import LibreCAL, LibreCALError, MockLibreCALSerial


class LibreCALTests(unittest.TestCase):
    def test_commands_and_replies(self):
        dev = LibreCAL.mock()
        info = dev.identify()
        self.assertEqual((info['serial'], info['firmware'], info['ports'], info['stable']), ('MOCK1234', '0.2.5', 4, True))
        dev.set_port(1, 'open')
        dev.set_port(2, 'THROUGH', 3)
        self.assertEqual(dev.get_port(3), 'THROUGH 2')
        dev.set_port(2, 'SHORT')
        self.assertEqual(dev.get_port(3), 'NONE')
        dev.release()
        sent = [line for line, _ in dev.link.script]
        self.assertEqual(sent[:6], ['*IDN?', ':PORTS?', ':TEMPerature?', ':TEMPerature:STABLE?', ':PORT 1 OPEN',
                                    ':PORT? 1'])
        self.assertIn(':PORT 2 THROUGH 3', sent)
        self.assertEqual(sent[-4:], [':PORT 1 NONE', ':PORT 2 NONE', ':PORT 3 NONE', ':PORT 4 NONE'])

    def test_errors_and_silence(self):
        dev = LibreCAL.mock()
        with self.assertRaises(LibreCALError):
            dev.set_port(9, 'OPEN')
        with self.assertRaises(LibreCALError):
            dev.command(':PORT 1 open')                 # case sensitive on the device
        with self.assertRaises(LibreCALError):
            dev.set_port(1, 'THROUGH')                  # no destination
        dead = LibreCAL(MockLibreCALSerial(broken=True))
        with self.assertRaises(LibreCALError):
            dead.identify()

    def test_coefficients(self):
        dev = LibreCAL.mock()
        self.assertEqual(dev.coefficient_sets(), ['FACTORY'])
        pts = dev.coefficient('FACTORY', 'P1_OPEN')
        self.assertEqual(len(pts), 60)
        self.assertAlmostEqual(pts[0][0], 100e6)
        self.assertEqual(len(pts[0][1]), 1)
        thru = dev.coefficient('FACTORY', 'P12_THROUGH')
        self.assertEqual(len(thru[0][1]), 4)            # S11, S21, S12, S22
        with self.assertRaises(LibreCALError):
            dev.coefficient('FACTORY', 'P34_THROUGH')
