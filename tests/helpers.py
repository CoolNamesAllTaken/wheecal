"""Shared test helpers: every test runs with its own settings file, never the user's."""
import os
import tempfile


class IsolatedConfig:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get('WHEECAL_CONFIG')
        self.config_file = os.path.join(self._tmp.name, 'config.json')
        os.environ['WHEECAL_CONFIG'] = self.config_file
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._old is None:
            os.environ.pop('WHEECAL_CONFIG', None)
        else:
            os.environ['WHEECAL_CONFIG'] = self._old
        self._tmp.cleanup()
