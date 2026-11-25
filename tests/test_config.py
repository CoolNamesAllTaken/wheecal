import json
import os
import tempfile
import unittest
from pathlib import Path

from wheecal import config

from .helpers import IsolatedConfig


class ConfigTests(IsolatedConfig, unittest.TestCase):
    def test_first_run_creates_the_template_without_an_address(self):
        cfg = config.load()
        self.assertEqual(cfg, config.DEFAULTS)
        self.assertEqual(cfg['host'], '')
        with open(self.config_file) as fh:
            self.assertEqual(json.load(fh), config.DEFAULTS)

    def test_round_trip_and_bad_values(self):
        cfg = config.load()
        cfg.update(host='192.0.2.1', through_port=3, settle_s=5, mode='guided')
        config.save(cfg)
        self.assertEqual(config.load()['host'], '192.0.2.1')
        self.assertEqual(config.load()['settle_s'], 5.0)
        with open(self.config_file, 'w') as fh:
            json.dump({'host': 7, 'scpi_port': True, 'kit_check': 'yes', 'unknown': 1, 'through_port': 4}, fh)
        got = config.load()
        self.assertEqual((got['host'], got['scpi_port'], got['kit_check'], got['through_port']), ('', 5555, True, 4))
        self.assertNotIn('unknown', got)
        with open(self.config_file, 'w') as fh:
            fh.write('{not json')
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_platform_locations(self):
        home = Path('/home/u')
        self.assertEqual(config.config_dir('darwin', {}, home), home / 'Library/Application Support/wheecal')
        self.assertEqual(config.config_dir('linux', {}, home), home / '.config/wheecal')
        self.assertEqual(config.config_dir('linux', {'XDG_CONFIG_HOME': '/x'}, home), Path('/x/wheecal'))
        self.assertEqual(config.config_dir('win32', {'APPDATA': 'C:/Users/u/AppData/Roaming'}, home),
                         Path('C:/Users/u/AppData/Roaming/wheecal'))

    def test_example_file_matches_the_defaults(self):
        example = Path(__file__).resolve().parent.parent / 'config.example.json'
        self.assertEqual(json.loads(example.read_text()), config.DEFAULTS)

    def test_env_and_explicit_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'sub', 'c.json')
            config.save(dict(config.DEFAULTS, host='h'), p)
            self.assertEqual(config.load(p)['host'], 'h')
            self.assertEqual(config.config_path(p), Path(p))
