"""
Tkinter GUI: VNA (driver, connect, kit load/verify/backup/restore, screenshot), LibreCAL (port, connect, fit),
Calibrate S11 (Open/Short/Load) and Calibrate S21 (2-port through), automatic or guided.

All instrument I/O runs on one worker thread; the UI thread only draws (a queue polled with after()). Nothing runs
on launch. Settings are read from and saved to the local config file (wheecal.config).
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, calflow, config, drivers
from .calkit import BUILTIN_KITS, CalKit
from .cli import librecal_kit
from .librecal import LibreCAL, LibreCALError, list_ports
from .transport import ScpiError

MOCK_PORT = '<simulated LibreCAL>'
AUTO_PORT = 'auto (first LibreCAL found)'
KIT_CHOICES = {'6650F03E female (card values)': '6650F03E-F', '6650F03E male (ideal, all zero)': '6650F03E-M',
               'LibreCAL standards (fitted from its FACTORY data)': 'librecal'}


class App:
    def __init__(self, root: tk.Tk, config_file=None):
        self.root = root
        root.title(f'wheecal {__version__}: LibreCAL + VNA calibration')
        root.minsize(980, 700)
        self.config_file = config_file
        self.cfg = config.load(config_file)
        self.vna = None
        self.cal: LibreCAL | None = None
        self.jobs: queue.Queue = queue.Queue()
        self.ui: queue.Queue = queue.Queue()
        self.busy = False
        self.cancel = threading.Event()
        self.fitted: CalKit | None = None
        self._fit_key = None
        threading.Thread(target=self._worker, daemon=True).start()
        self._build()
        self.root.after(50, self._pump)
        self.refresh_ports()
        root.protocol('WM_DELETE_WINDOW', self.on_close)

    # ── settings ────────────────────────────────────────────────────────────
    def _driver_name(self) -> str:
        title = self.driver.get()
        return next((n for n in drivers.names() if drivers.get(n).TITLE == title), drivers.DEFAULT)

    def save_settings(self):
        try:
            self.cfg.update({
                'driver': self._driver_name(), 'host': self.host.get().strip(), 'scpi_port': int(self.scpi_port.get()),
                'librecal_device': self._device_setting(), 'librecal_port': int(self.lport.get()),
                'through_port': int(self.tport.get()), 'settle_s': float(self.settle.get()),
                'mode': self.mode.get(), 'fit_band': self.fitband.get(),
                'require_stable_temp': bool(self.stable.get()), 'kit_check': bool(self.kitcheck.get()),
            })
            path = config.save(self.cfg, self.config_file)
            self.saved.set(f'Settings saved locally: {path}')
        except (OSError, ValueError, tk.TclError) as exc:
            self.saved.set(f'Settings not saved: {exc}')

    def _device_setting(self) -> str:
        choice = self.portvar.get()
        if choice in ('', AUTO_PORT):
            return 'auto'
        if choice == MOCK_PORT:
            return self.cfg.get('librecal_device', 'auto')
        return choice.split()[0]

    def on_close(self):
        self.save_settings()
        self.root.destroy()

    # ── threading ───────────────────────────────────────────────────────────
    def _worker(self):
        while True:
            fn, done = self.jobs.get()
            try:
                result, error = fn(), None
            except Exception as exc:          # noqa: BLE001 -- shown to the user
                result, error = None, exc
            self.ui.put(('done', done, result, error))

    def run(self, label: str, fn, done=None):
        if self.busy:
            messagebox.showinfo('Busy', 'Wait for the current step to finish.')
            return
        self.busy = True
        self.status.set(label + ' ...')
        self._set_buttons('disabled')
        self.jobs.put((fn, done))

    def _pump(self):
        try:
            while True:
                item = self.ui.get_nowait()
                kind = item[0]
                if kind == 'done':
                    _, done, result, error = item
                    self.busy = False
                    self._set_buttons('normal')
                    if error is not None:
                        self.status.set(f'Error: {error}')
                        self._log_now(f'ERROR: {error}')
                        messagebox.showerror('Error', str(error))
                    else:
                        self.status.set('Ready')
                        if done:
                            done(result)
                elif kind == 'log':
                    self._log_now(item[1])
                elif kind == 'progress':
                    _, n, total, label = item
                    self.progress['maximum'] = total
                    self.progress['value'] = n
                    self.step.set(label)
                elif kind == 'prompt':
                    _, msg, holder, ev = item
                    holder.append(messagebox.askokcancel('Calibration step', msg, parent=self.root))
                    ev.set()
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    def log(self, text: str):
        self.ui.put(('log', text))

    def _log_now(self, text: str):
        self.logbox.configure(state='normal')
        self.logbox.insert('end', time.strftime('%H:%M:%S  ') + text + '\n')
        self.logbox.see('end')
        self.logbox.configure(state='disabled')

    def _prompt_from_worker(self, msg: str) -> bool:
        holder, ev = [], threading.Event()
        self.ui.put(('prompt', msg, holder, ev))
        while not ev.wait(0.2):
            if self.cancel.is_set():
                return False
        return bool(holder and holder[0])

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self):
        pad = {'padx': 6, 'pady': 3}
        c = self.cfg
        top = ttk.Frame(self.root)
        top.pack(fill='x', **pad)

        # VNA
        rf = ttk.LabelFrame(top, text='1  VNA')
        rf.pack(side='left', fill='both', expand=True, padx=4)
        row = ttk.Frame(rf); row.pack(fill='x', **pad)
        ttk.Label(row, text='Model').pack(side='left')
        titles = [drivers.get(n).TITLE for n in drivers.names()]
        try:
            current = drivers.get(c['driver']).TITLE
        except KeyError:
            current = drivers.get(drivers.DEFAULT).TITLE
        self.driver = tk.StringVar(value=current)
        ttk.Combobox(row, textvariable=self.driver, values=titles, state='readonly', width=30).pack(side='left', padx=4)
        row = ttk.Frame(rf); row.pack(fill='x', **pad)
        ttk.Label(row, text='Host').pack(side='left')
        self.host = tk.StringVar(value=c['host'])
        ttk.Entry(row, textvariable=self.host, width=18).pack(side='left', padx=4)
        ttk.Label(row, text='SCPI port').pack(side='left')
        self.scpi_port = tk.IntVar(value=c['scpi_port'])
        ttk.Entry(row, textvariable=self.scpi_port, width=6).pack(side='left', padx=4)
        self.b_rconn = ttk.Button(row, text='Connect', command=self.vna_connect)
        self.b_rconn.pack(side='left')
        self.dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text='Dry run (simulated VNA)', variable=self.dry).pack(side='left', padx=6)
        self.rinfo = tk.StringVar(value='not connected')
        ttk.Label(rf, textvariable=self.rinfo, wraplength=460).pack(anchor='w', **pad)
        row = ttk.Frame(rf); row.pack(fill='x', **pad)
        ttk.Label(row, text='Kit').pack(side='left')
        self.kitname = tk.StringVar(value=list(KIT_CHOICES)[-1])
        ttk.Combobox(row, textvariable=self.kitname, values=list(KIT_CHOICES), state='readonly',
                     width=46).pack(side='left', padx=4)
        row = ttk.Frame(rf); row.pack(fill='x', **pad)
        self.b_load = ttk.Button(row, text='Load kit into VNA', command=self.load_kit)
        self.b_verify = ttk.Button(row, text='Verify', command=self.verify_kit)
        self.b_backup = ttk.Button(row, text='Backup…', command=self.backup)
        self.b_restore = ttk.Button(row, text='Restore…', command=self.restore)
        self.b_snap = ttk.Button(row, text='Screenshot…', command=self.snapshot)
        for b in (self.b_load, self.b_verify, self.b_backup, self.b_restore, self.b_snap):
            b.pack(side='left', padx=2)

        # LibreCAL
        lf = ttk.LabelFrame(top, text='2  LibreCAL (USB)')
        lf.pack(side='left', fill='both', expand=True, padx=4)
        row = ttk.Frame(lf); row.pack(fill='x', **pad)
        self.portvar = tk.StringVar()
        self.portbox = ttk.Combobox(row, textvariable=self.portvar, width=34, state='readonly')
        self.portbox.pack(side='left')
        ttk.Button(row, text='Refresh', command=self.refresh_ports).pack(side='left', padx=2)
        self.b_lconn = ttk.Button(row, text='Connect', command=self.librecal_connect)
        self.b_lconn.pack(side='left')
        self.linfo = tk.StringVar(value='not connected')
        ttk.Label(lf, textvariable=self.linfo, wraplength=420).pack(anchor='w', **pad)
        self.lport = tk.IntVar(value=c['librecal_port'])
        self.tport = tk.IntVar(value=c['through_port'])
        row = ttk.Frame(lf); row.pack(fill='x', **pad)
        ttk.Label(row, text='Port on the VNA test port / output').pack(side='left')
        ttk.Spinbox(row, from_=1, to=4, textvariable=self.lport, width=3).pack(side='left', padx=4)
        ttk.Label(row, text='on the VNA input').pack(side='left')
        ttk.Spinbox(row, from_=1, to=4, textvariable=self.tport, width=3).pack(side='left', padx=4)
        row = ttk.Frame(lf); row.pack(fill='x', **pad)
        self.fitband = tk.StringVar(value=c['fit_band'])
        ttk.Radiobutton(row, text='fit full band', value='full', variable=self.fitband).pack(side='left')
        ttk.Radiobutton(row, text='fit VNA span', value='span', variable=self.fitband).pack(side='left', padx=4)
        self.b_fit = ttk.Button(row, text='Fit LibreCAL standards (preview)', command=self.fit_preview)
        self.b_fit.pack(side='left', padx=6)

        # common calibration options
        opt = ttk.Frame(self.root)
        opt.pack(fill='x', **pad)
        self.mode = tk.StringVar(value=c['mode'])
        ttk.Label(opt, text='Calibration:').pack(side='left')
        ttk.Radiobutton(opt, text='Automatic (SCPI)', value='auto', variable=self.mode).pack(side='left', padx=4)
        ttk.Radiobutton(opt, text='Guided (press the VNA buttons)', value='guided',
                        variable=self.mode).pack(side='left', padx=4)
        ttk.Label(opt, text='settle s').pack(side='left', padx=(12, 2))
        self.settle = tk.DoubleVar(value=c['settle_s'])
        ttk.Spinbox(opt, from_=0.5, to=60, increment=0.5, textvariable=self.settle, width=5).pack(side='left')
        self.stable = tk.BooleanVar(value=c['require_stable_temp'])
        ttk.Checkbutton(opt, text='require stable LibreCAL temperature', variable=self.stable).pack(side='left', padx=8)
        self.kitcheck = tk.BooleanVar(value=c['kit_check'])
        ttk.Checkbutton(opt, text='check VNA kit = LibreCAL fit', variable=self.kitcheck).pack(side='left')

        cals = ttk.Frame(self.root)
        cals.pack(fill='x', **pad)
        cf = ttk.LabelFrame(cals, text='3  Calibrate S11 (1-port: Open, Short, Load)')
        cf.pack(side='left', fill='both', expand=True, padx=4)
        ttk.Label(cf, text='VNA in S11. LibreCAL "test port / output" port cabled to the VNA test port.',
                  wraplength=420).pack(anchor='w', **pad)
        self.b_start = ttk.Button(cf, text='Start S11 calibration', command=self.start_cal)
        self.b_start.pack(anchor='w', **pad)
        tf2 = ttk.LabelFrame(cals, text='4  Calibrate S21 (2-port Through)')
        tf2.pack(side='left', fill='both', expand=True, padx=4)
        row = ttk.Frame(tf2); row.pack(fill='x', **pad)
        ttk.Label(row, text='LibreCAL port').pack(side='left')
        ttk.Spinbox(row, from_=1, to=4, textvariable=self.lport, width=3).pack(side='left', padx=4)
        ttk.Label(row, text='-> VNA output,  port').pack(side='left')
        ttk.Spinbox(row, from_=1, to=4, textvariable=self.tport, width=3).pack(side='left', padx=4)
        ttk.Label(row, text='-> VNA input').pack(side='left')
        row = ttk.Frame(tf2); row.pack(fill='x', **pad)
        self.b_thru = ttk.Button(row, text='Start Through (S21) calibration', command=self.start_thru)
        self.b_thru.pack(side='left')
        ttk.Label(row, text='VNA in S21 (select it on the panel).').pack(side='left', padx=6)

        row = ttk.Frame(self.root); row.pack(fill='x', **pad)
        ttk.Button(row, text='Cancel', command=self.cancel.set).pack(side='left', padx=4)
        self.progress = ttk.Progressbar(row, length=300, mode='determinate')
        self.progress.pack(side='left', padx=8)
        self.step = tk.StringVar(value='')
        ttk.Label(row, textvariable=self.step).pack(side='left')

        # table + log
        bottom = ttk.PanedWindow(self.root, orient='horizontal')
        bottom.pack(fill='both', expand=True, **pad)
        tf = ttk.LabelFrame(bottom, text='Kit read-back')
        self.table = ttk.Treeview(tf, columns=('standard', 'field', 'unit', 'want', 'got', 'ok'), show='headings',
                                  height=14)
        for col, w in (('standard', 70), ('field', 60), ('unit', 70), ('want', 100), ('got', 100), ('ok', 40)):
            self.table.heading(col, text=col)
            self.table.column(col, width=w, anchor='e' if col in ('want', 'got') else 'w')
        self.table.tag_configure('bad', foreground='#b00020')
        self.table.pack(fill='both', expand=True)
        bottom.add(tf, weight=1)
        lg = ttk.LabelFrame(bottom, text='Log')
        self.logbox = tk.Text(lg, height=14, wrap='word', state='disabled')
        self.logbox.pack(fill='both', expand=True)
        bottom.add(lg, weight=1)
        bar = ttk.Frame(self.root)
        bar.pack(fill='x', side='bottom')
        self.status = tk.StringVar(value='Ready. Nothing runs until you press a button.')
        ttk.Label(bar, textvariable=self.status, relief='sunken', anchor='w').pack(fill='x', side='left', expand=True)
        self.saved = tk.StringVar(value=f'Settings: {config.config_path(self.config_file)}')
        ttk.Label(bar, textvariable=self.saved, relief='sunken', anchor='e').pack(side='right')
        self.buttons = [self.b_rconn, self.b_load, self.b_verify, self.b_backup, self.b_restore, self.b_snap,
                        self.b_lconn, self.b_fit, self.b_start, self.b_thru]

    def _set_buttons(self, state):
        for b in self.buttons:
            b.configure(state=state)

    def show_rows(self, rows):
        self.table.delete(*self.table.get_children())
        for r in rows:
            got = '' if r['got'] is None else f"{r['got']:.6g}"
            self.table.insert('', 'end', values=(r['standard'], r['field'], r['unit'], f"{r['want']:.6g}",
                                                 got, 'yes' if r['ok'] else 'NO'), tags=() if r['ok'] else ('bad',))
        ok = all(r['ok'] for r in rows)
        self._log_now(f"Read-back: {'PASS' if ok else 'FAIL'} ({sum(r['ok'] for r in rows)}/{len(rows)} fields)")
        return ok

    # ── VNA ─────────────────────────────────────────────────────────────────
    def need_vna(self):
        if self.vna is None:
            raise ScpiError('connect the VNA first')
        return self.vna

    def vna_connect(self):
        host, dry, name = self.host.get().strip(), self.dry.get(), self._driver_name()
        try:
            port = int(self.scpi_port.get())
        except (ValueError, tk.TclError):
            messagebox.showerror('VNA', 'The SCPI port must be a number.')
            return
        if not host and not dry:
            messagebox.showerror('VNA', 'Enter the VNA\'s IP address or hostname first (or tick Dry run).')
            return
        self.save_settings()

        def job():
            if self.vna is not None:
                self.vna.close()
                self.vna = None
            driver = drivers.get(name)
            v = driver.mock() if dry else driver.connect(host, port)
            info = v.identify()
            info['kit'] = ('user kit' if v.user_kit_selected() else 'not the user kit') if info.get('mode') == 'VNA' else '?'
            info['cal'] = ('ON' if v.cal_state(info['meas']) else 'off') if v.supports(info.get('meas', '')) else '?'
            self.vna = v
            return info

        def done(info):
            self.rinfo.set(f"{info['idn']}\nmode {info['mode']}  meas {info['meas']}  kit: {info['kit']}  "
                           f"{info['meas']} cal {info['cal']}{'   [DRY RUN]' if dry else ''}")
            self._log_now(f"VNA connected: {info['idn']} ({info['mode']} {info['meas']})")
            if info['mode'] != 'VNA':
                messagebox.showwarning('VNA', 'The instrument is not in VNA mode. Switch to VNA on the panel.')
        self.run('Connecting to the VNA', job, done)

    def fit_args(self) -> tuple:
        """UI values a job needs, read on the UI thread."""
        return int(self.lport.get()), self.fitband.get(), int(self.tport.get())

    def selected_kit(self, choice: str, fit_args: tuple) -> CalKit:
        if choice == 'librecal':
            return self.fit_now(*fit_args)
        return BUILTIN_KITS[choice]

    def load_kit(self):
        if not messagebox.askokcancel('Load kit', f'Write "{self.kitname.get()}" into the VNA user kit and '
                                      'select it?\n(Overwrites the current user kit: back it up first if needed.)'):
            return
        choice, fa = KIT_CHOICES[self.kitname.get()], self.fit_args()

        def job():
            v = self.need_vna()
            v.require_mode()
            kit = self.selected_kit(choice, fa)
            self.log(f'Writing "{kit.name}" ({kit.note})')
            return v.write_kit(kit, progress=lambda n, t, row: self.ui.put(
                ('progress', n, t, f"{row['standard']} {row['field']}")))
        self.run('Writing the kit', job, self.show_rows)

    def verify_kit(self):
        choice, fa = KIT_CHOICES[self.kitname.get()], self.fit_args()

        def job():
            v = self.need_vna()
            v.require_mode()
            rows = v.verify_kit(self.selected_kit(choice, fa))
            self.log(f'User kit selected: {v.user_kit_selected()}')
            return rows
        self.run('Reading the kit back', job, self.show_rows)

    def backup(self):
        path = filedialog.asksaveasfilename(defaultextension='.json', initialfile='vna-user-kit-backup.json')
        if not path:
            return

        def job():
            data = self.need_vna().backup_kit()
            data['saved'] = time.strftime('%Y-%m-%d %H:%M:%S')
            with open(path, 'w') as fh:
                json.dump(data, fh, indent=2)
            return path
        self.run('Backing up', job, lambda p: self._log_now(f'Backup written: {p}'))

    def restore(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path or not messagebox.askokcancel('Restore', f'Write the kit in {path} into the VNA user kit?'):
            return

        def job():
            with open(path) as fh:
                return self.need_vna().restore_kit(json.load(fh))
        self.run('Restoring', job, self.show_rows)

    def snapshot(self):
        path = filedialog.asksaveasfilename(defaultextension='.bmp', initialfile='vna-screen.bmp')
        if not path:
            return

        def job():
            img, _ext = self.need_vna().snapshot()
            with open(path, 'wb') as fh:
                fh.write(img)
            return path
        self.run('Screenshot', job, lambda p: self._log_now(f'Screenshot written: {p}'))

    # ── LibreCAL ────────────────────────────────────────────────────────────
    def refresh_ports(self):
        ports = list_ports()
        values = [AUTO_PORT] + [f"{p['device']}  {'(LibreCAL) ' if p['librecal'] else ''}{p['description']}"
                                for p in ports] + [MOCK_PORT]
        self.portbox['values'] = values
        want = self.cfg.get('librecal_device', 'auto')
        if not self.portvar.get() or self.portvar.get() not in values:
            match = next((v for v in values if v.split()[0] == want), None)
            self.portvar.set(match or AUTO_PORT)

    def librecal_connect(self):
        choice = self.portvar.get()
        self.save_settings()

        def job():
            if self.cal is not None:
                self.cal.close()
                self.cal = None
            if choice == MOCK_PORT:
                dev = LibreCAL.mock()
            else:
                device = choice.split()[0] if choice != AUTO_PORT else None
                if device is None:
                    from .librecal import find_librecal
                    device = find_librecal()
                    if device is None:
                        raise LibreCALError('no LibreCAL found: plug it in and press Refresh, or pick its port')
                dev = LibreCAL.open(device)
            info = dev.identify()
            info['sets'] = dev.coefficient_sets()
            self.cal = dev
            self.fitted = None
            return info

        def done(info):
            t = info['temperature']
            self.linfo.set(f"{info['idn']}\n{info['ports']} ports, "
                           f"{'?' if t is None else f'{t:.1f} °C'} {'stable' if info['stable'] else 'NOT stable'}, "
                           f"coefficient sets: {', '.join(info['sets'])}")
            self._log_now(f"LibreCAL connected: {info['idn']}")
        self.run('Connecting to the LibreCAL', job, done)

    def fit_now(self, port: int, band: str, tport: int) -> CalKit:
        if self.cal is None:
            raise LibreCALError('connect the LibreCAL first (its standards are read from it)')
        key = (port, band, tport)
        if self.fitted is None or self._fit_key != key:
            pair = (port, tport) if port != tport else None
            self.fitted, _ = librecal_kit(self.cal, self.vna, port, band, pair, log=self.log)
            self._fit_key = key
            self.log(f'Fitted: {self.fitted.note}')
        return self.fitted

    def fit_preview(self):
        fa = self.fit_args()

        def job():
            self.fitted = None
            kit = self.fit_now(*fa)
            return (self.vna or drivers.get(self._driver_name()).mock()).preview_kit(kit)
        self.run('Fitting the LibreCAL standards', job, lambda rows: (self.show_rows(rows), self._log_now(
            'Preview only: choose "LibreCAL standards" as the kit and press Load kit to write it.')))

    # ── calibration ─────────────────────────────────────────────────────────
    def _options(self) -> calflow.CalOptions:
        return calflow.CalOptions(mode=self.mode.get(), librecal_port=int(self.lport.get()),
                                  through_port=int(self.tport.get()), settle_s=float(self.settle.get()),
                                  switch_settle_s=float(self.cfg['switch_settle_s']),
                                  require_stable_temp=bool(self.stable.get()))

    def _start(self, meas: str, question: str, ok_text: str):
        if not messagebox.askokcancel(f'Calibrate {meas}', question):
            return
        self.save_settings()
        self.cancel.clear()
        opts = self._options()
        kitcheck, fa = self.kitcheck.get(), self.fit_args()

        def job():
            v = self.need_vna()
            if self.cal is None:
                raise LibreCALError('connect the LibreCAL first')
            if kitcheck:
                opts.expected_kit = self.fit_now(*fa)
            return calflow.run_cal(v, self.cal, meas, opts, log=self.log,
                                   progress=lambda n, t, lab: self.ui.put(('progress', n, t, lab)),
                                   prompt=self._prompt_from_worker, cancel=self.cancel)

        def done(res):
            (messagebox.showinfo if res.ok else messagebox.showerror)(
                'Calibration', ok_text if res.ok else f'Calibration stopped: {res.message}')
        self.run(f'Calibrating {meas}', job, done)

    def start_cal(self):
        self._start('S11', f"Run an S11 Open/Short/Load calibration on the VNA now ({self.mode.get()} mode)?\n\n"
                    "This replaces the VNA's current S11 calibration. The VNA must be in VNA mode measuring S11, "
                    f'with LibreCAL port {self.lport.get()} cabled to its test port.', 'S11 calibration applied.')

    def start_thru(self):
        if int(self.lport.get()) == int(self.tport.get()):
            messagebox.showerror('Calibrate S21', 'The through needs two different LibreCAL ports.')
            return
        self._start('S21', f"Run a 2-port Through (S21) calibration on the VNA now ({self.mode.get()} mode)?\n\n"
                    "This replaces the VNA's current S21 calibration. The VNA must be in VNA mode measuring S21 "
                    '(select it on the panel: wheecal never changes the measurement), with LibreCAL port '
                    f'{self.lport.get()} cabled to the VNA output and port {self.tport.get()} to the VNA input.',
                    'S21 through calibration applied.')


def main(config_file=None) -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use('aqua')
    except tk.TclError:
        pass
    App(root, config_file)
    root.mainloop()
    return 0
