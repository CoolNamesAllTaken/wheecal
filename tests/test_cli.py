import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from wheecal import cli, config

from .helpers import IsolatedConfig


class CliTests(IsolatedConfig, unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_load_and_verify(self):
        code, out, _ = self.run_cli('--dry-run', '--load-kit', '6650F03E-F')
        self.assertEqual(code, 0)
        self.assertIn(':CALibration:CKIT:C0 4.943300000e+01', out)
        self.assertIn('RESULT PASS', out)

    def test_dry_run_s11_with_librecal_kit(self):
        code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--load-kit', 'librecal', '--calibrate',
                                      '--yes', '--settle', '0')
        self.assertEqual(code, 0, out + err)
        self.assertIn('CAL OK', out)
        self.assertIn(':CALibration:S11:SAVE', out)

    def test_dry_run_s21(self):
        for flag in (['--calibrate', 's21'], ['--calibrate-thru']):
            code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--load-kit', 'librecal', *flag, '--yes',
                                          '--settle', '0')
            self.assertEqual(code, 0, out + err)
            self.assertIn('through 1-2: 150.00 ps = 44.969 mm', out)
            self.assertIn(':CALibration:S21:THROugh', out)
            self.assertIn(':CALibration:S21:SAVE', out)
            self.assertNotIn(':CALibration:S11:', out)
            self.assertIn('CAL OK', out)

    def test_s21_ports_and_mode_flags(self):
        code, out, err = self.run_cli('--dry-run', '--mock-librecal', '--calibrate-thru', '--yes', '--settle', '0',
                                      '--no-kit-check', '--librecal-port', '2', '--through-port', '4')
        self.assertEqual(code, 0, out + err)
        self.assertIn('LibreCAL port 2 <-> port 4 THROUGH', out)

    def test_backup_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'b.json')
            code, _, _ = self.run_cli('--dry-run', '--backup', path)
            self.assertEqual(code, 0)
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data['OPEN']['C0'], 13.381)
            self.assertEqual(data['driver'], 'rsa5065n')

    def test_no_host_is_a_clear_error(self):
        code, _, err = self.run_cli('--verify', '6650F03E-F')
        self.assertEqual(code, 2)
        self.assertIn('no VNA address', err)

    def test_unreachable_vna_is_a_clear_error(self):
        code, _, err = self.run_cli('--host', '127.0.0.1', '--scpi-port', '9', '--verify', '6650F03E-F')
        self.assertEqual(code, 2)
        self.assertIn('cannot connect', err)

    def test_config_is_created_and_flags_override_it_for_one_run(self):
        code, out, _ = self.run_cli('--show-config')
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.config_file))
        self.assertIn(self.config_file, out)
        cfg = config.load()
        cfg['host'] = '198.51.100.7'
        config.save(cfg)
        code, out, _ = self.run_cli('--show-config', '--host', '203.0.113.5', '--through-port', '3')
        self.assertIn('"host": "203.0.113.5"', out)
        self.assertIn('"through_port": 3', out)
        self.assertEqual(config.load()['host'], '198.51.100.7')       # not saved by the CLI

    def test_list_drivers(self):
        code, out, _ = self.run_cli('--list-drivers')
        self.assertEqual(code, 0)
        self.assertIn('rsa5065n', out)
        self.assertIn('S11, S21', out)

    def test_unknown_driver(self):
        code, _, err = self.run_cli('--driver', 'nope', '--dry-run', '--verify', '6650F03E-F')
        self.assertEqual(code, 2)
        self.assertIn('unknown VNA driver', err)
