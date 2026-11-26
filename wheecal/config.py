"""
Per-user settings, kept outside the program (nothing site-specific is built in):

  macOS    ~/Library/Application Support/wheecal/config.json
  Linux    $XDG_CONFIG_HOME/wheecal/config.json (default ~/.config/wheecal/config.json)
  Windows  %APPDATA%\\wheecal\\config.json

WHEECAL_CONFIG=<path> (or the CLI's --config) uses another file. The file is created with DEFAULTS on first run,
with no instrument address. The GUI saves it when settings change; CLI flags override it for one run only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    'driver': 'rsa5065n',          # VNA driver (wheecal.drivers)
    'host': '',                    # VNA IP address or hostname: set it before connecting
    'scpi_port': 5555,             # VNA raw SCPI TCP port
    'librecal_device': 'auto',     # LibreCAL serial port, or 'auto' (first USB 1209:4122 device)
    'librecal_port': 1,            # LibreCAL port on the VNA test port (S11) / output (S21)
    'through_port': 2,             # LibreCAL port on the VNA input (S21)
    'settle_s': 3.0,               # wait after each VNA measurement
    'switch_settle_s': 0.5,        # wait after switching the LibreCAL
    'mode': 'auto',                # auto | guided
    'fit_band': 'span',            # span | full: fit over the VNA sweep (default) or all the LibreCAL data
    'require_stable_temp': True,
    'kit_check': True,
    'settings_version': 2,         # 2 (v0.1.1): fit_band default span
}


def config_dir(platform: str | None = None, env=None, home: Path | None = None) -> Path:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == 'darwin':
        return home / 'Library' / 'Application Support' / 'wheecal'
    if platform.startswith('win'):
        base = env.get('APPDATA')
        return Path(base) / 'wheecal' if base else home / 'AppData' / 'Roaming' / 'wheecal'
    base = env.get('XDG_CONFIG_HOME')
    return (Path(base) if base else home / '.config') / 'wheecal'


def config_path(path: str | os.PathLike | None = None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get('WHEECAL_CONFIG')
    return Path(env) if env else config_dir() / 'config.json'


def evidence_dir(path=None) -> Path:
    """Where screenshots crops of the VNA's correction indicator are kept (next to the settings file)."""
    return config_path(path).parent / 'indicator'


def hostcal_path(path=None) -> Path:
    """The default host-side calibration (error terms) file, next to the settings file."""
    return config_path(path).parent / 'hostcal.json'


def _valid(key: str, value) -> bool:
    d = DEFAULTS[key]
    if isinstance(d, bool) or isinstance(value, bool):
        return isinstance(d, bool) and isinstance(value, bool)
    if isinstance(d, float):
        return isinstance(value, (int, float))
    return isinstance(value, type(d))


def _clean(data: dict) -> dict:
    if data.get('settings_version', 1) < 2 and data.get('fit_band') == 'full':
        data = dict(data, fit_band='span')       # v0.1.0 wrote its default 'full' into every file
    out = dict(DEFAULTS)
    for k, v in data.items():
        if k in DEFAULTS and _valid(k, v):
            out[k] = float(v) if isinstance(DEFAULTS[k], float) else v
    return out


def load(path=None) -> dict:
    """The settings (DEFAULTS for missing or malformed keys). Creates the file from DEFAULTS on first run."""
    p = config_path(path)
    if not p.exists():
        save(DEFAULTS, p)
        return dict(DEFAULTS)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return dict(DEFAULTS)
    return _clean(data) if isinstance(data, dict) else dict(DEFAULTS)


def save(cfg: dict, path=None) -> Path:
    p = config_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(_clean(cfg), indent=2) + '\n')
    tmp.replace(p)
    return p
