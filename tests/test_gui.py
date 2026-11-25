"""Headless GUI smoke test. Needs a display (CI runs it under xvfb-run); set WHEECAL_GUI_TESTS=1 to require it."""
import os
import time
import unittest

from .helpers import IsolatedConfig

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:            # Python without Tk
    tk = None


def _display_ok() -> bool:
    if tk is None:
        return False
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


REQUIRED = os.environ.get('WHEECAL_GUI_TESTS') == '1'


@unittest.skipUnless(REQUIRED or _display_ok(), 'no display for Tk')
class GuiSmokeTests(IsolatedConfig, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from wheecal import gui
        self.dialogs = []
        self._saved = {n: getattr(messagebox, n) for n in ('askokcancel', 'showinfo', 'showerror', 'showwarning')}
        messagebox.askokcancel = lambda *a, **k: (self.dialogs.append(('ask',) + a), True)[1]
        for n in ('showinfo', 'showerror', 'showwarning'):
            setattr(messagebox, n, lambda *a, _n=n, **k: self.dialogs.append((_n,) + a))
        self.root = tk.Tk()
        self.app = gui.App(self.root)

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(messagebox, n, f)
        self.root.destroy()
        super().tearDown()

    def wait(self):
        for _ in range(1500):
            self.root.update()
            time.sleep(0.01)
            if not self.app.busy and self.app.jobs.empty():
                for _ in range(5):
                    self.root.update()
                return
        self.fail('GUI stuck busy')

    def test_launch_then_thru_calibration(self):
        app = self.app
        self.root.update()
        self.assertIsNone(app.vna)                             # nothing runs on launch
        self.assertIsNone(app.cal)
        self.assertTrue(os.path.exists(self.config_file))      # settings template created
        self.assertEqual(app.host.get(), '')                   # no address built in
        self.assertEqual((app.lport.get(), app.tport.get()), (1, 2))

        app.dry.set(True)
        app.vna_connect(); self.wait()
        self.assertIn('MOCK0001', app.rinfo.get())
        app.portvar.set('<simulated LibreCAL>')
        app.librecal_connect(); self.wait()
        self.assertIn('MOCK1234', app.linfo.get())
        app.kitname.set('LibreCAL standards (fitted from its FACTORY data)')
        app.load_kit(); self.wait()
        self.assertTrue(any(w.startswith(':CALibration:CKIT:LENGth 4.4969') for w in app.vna.writes), app.vna.writes)
        app.settle.set(0.5)

        app.start_thru(); self.wait()                          # the simulated VNA measures S11: refused
        self.assertIn('select Meas > S21', self.dialogs[-1][-1])
        self.assertFalse(any(w.startswith(':CALibration:S21') for w in app.vna.writes))

        app.vna.t.meas = 'S21'                                 # the user selects S21 on the panel
        app.start_thru(); self.wait()
        self.assertEqual(self.dialogs[-1][:2], ('showinfo', 'Calibration'), self.dialogs[-1])
        self.assertEqual([w for w in app.vna.writes if w.startswith(':CALibration:S21')],
                         [':CALibration:S21:THROugh', ':CALibration:S21:SAVE'])
        self.assertTrue(app.vna.cal_state('S21'))
        log = app.logbox.get('1.0', 'end')
        self.assertIn('LibreCAL port 1 <-> port 2 THROUGH', log)
        self.assertIn('S21 calibration ON', log)
        self.assertIn('Settings saved locally', app.saved.get())


if __name__ == '__main__':
    unittest.main()
