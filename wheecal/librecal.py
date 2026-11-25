"""
LibreCAL (https://github.com/jankae/LibreCAL) over its USB virtual COM port.

Command set from Documentation/SCPI_API.tex and Software/LibreCAL/src/SCPI.cpp (main @ 5ff26b5, 2026-10-04):
  *IDN?                       LibreCAL,LibreCAL,<serial>,<major.minor.patch>
  :FIRMWARE?                  <major.minor.patch>
  :PORTS?                     4
  :PORT <p> OPEN|SHORT|LOAD|NONE        set a port's standard ('' on success, 'ERROR' otherwise)
  :PORT <p> THROUGH <dest>              through between two ports (both are set; S21 cal)
  :PORT? <p>                  OPEN|SHORT|LOAD|NONE|THROUGH <dest>
  :TEMPerature?               float, degrees C;  :TEMPerature:STABLE?  TRUE|FALSE;  :HEATer:POWer?  watts
  :COEFFicient:LIST?          comma-separated set names (FACTORY first)
  :COEFFicient:NUMber? <set> <coeff>        points in a coefficient (P1_OPEN ... P34_THROUGH)
  :COEFFicient:GET? <set> <coeff> <index>   <f GHz>,<re>,<im>[,... S11 S21 S12 S22 for a through]
Every command answers exactly one CRLF-terminated line (empty for a successful event, 'ERROR...' on failure).
Standard names are case SENSITIVE (OPEN, not open); one command per line.
USB: VID 0x1209 PID 0x4122 (CDC VCP + vendor interface); in "Siglent mode" 0xF4EC:0x1600 (USBTMC, not used here).

These were read from the LibreCAL repository (not yet checked against hardware by this project); the mock below
implements the same replies.

Never used: :COEFFicient:CREATE/ADD/DELete/FINish, :FACTory:* , :BOOTloader, :TEMPerature <set> (all write the
device's memory or settings).
"""
from __future__ import annotations

import threading
import time

VID, PID = 0x1209, 0x4122
STANDARDS = ('OPEN', 'SHORT', 'LOAD', 'NONE')
REFLECTION = ('OPEN', 'SHORT', 'LOAD')


class LibreCALError(RuntimeError):
    pass


def list_ports() -> list[dict]:
    """Serial ports, LibreCALs first: [{device, description, librecal}]."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:                       # pyserial missing
        return []
    out = []
    for p in lp.comports():
        out.append({'device': p.device, 'description': p.description or '',
                    'librecal': (p.vid, p.pid) == (VID, PID) or 'LibreCAL' in (p.product or p.description or '')})
    return sorted(out, key=lambda p: (not p['librecal'], p['device']))


def find_librecal() -> str | None:
    """The first serial port that looks like a LibreCAL (USB 1209:4122), or None."""
    for p in list_ports():
        if p['librecal']:
            return p['device']
    return None


class LibreCAL:
    def __init__(self, link, timeout: float = 2.0):
        self.link = link                     # a pyserial Serial or MockLibreCALSerial
        self.timeout = timeout
        self.lock = threading.RLock()
        self.log: list[tuple[str, str]] = []

    @classmethod
    def open(cls, device: str, timeout: float = 2.0) -> 'LibreCAL':
        try:
            import serial
        except ImportError as exc:
            raise LibreCALError('pyserial is not installed: pip install pyserial') from exc
        try:
            link = serial.Serial(device, 115200, timeout=timeout, write_timeout=timeout)
        except (serial.SerialException, OSError) as exc:
            raise LibreCALError(f'cannot open {device}: {exc}') from exc
        dev = cls(link, timeout)
        dev._drain()
        return dev

    @classmethod
    def mock(cls, **kw) -> 'LibreCAL':
        return cls(MockLibreCALSerial(**kw))

    def close(self) -> None:
        try:
            self.link.close()
        except Exception:                    # noqa: BLE001
            pass

    def _drain(self) -> None:
        time.sleep(0.05)
        try:
            self.link.reset_input_buffer()
        except Exception:                    # noqa: BLE001
            pass

    def command(self, line: str) -> str:
        """Send one line, return its one-line reply (stripped). Raises on timeout or an ERROR reply."""
        with self.lock:
            self.link.write((line + '\n').encode())
            reply = self.link.readline()
            if not reply.endswith(b'\n'):
                raise LibreCALError(f'no reply to {line!r} (is it a LibreCAL? is the port right?)')
            text = reply.decode(errors='replace').strip()
            self.log.append((line, text))
            if text.startswith('ERROR'):
                raise LibreCALError(f'{line!r} -> {text}')
            return text

    # ── info ────────────────────────────────────────────────────────────────
    def identify(self) -> dict:
        idn = self.command('*IDN?')
        parts = idn.split(',')
        if len(parts) < 4 or parts[0] != 'LibreCAL':
            raise LibreCALError(f'not a LibreCAL: {idn!r}')
        info = {'idn': idn, 'serial': parts[2], 'firmware': parts[3], 'ports': int(self.command(':PORTS?'))}
        try:
            info['temperature'] = float(self.command(':TEMPerature?'))
            info['stable'] = self.command(':TEMPerature:STABLE?') == 'TRUE'
        except (LibreCALError, ValueError):
            info['temperature'], info['stable'] = None, None
        return info

    def temperature(self) -> tuple[float, bool]:
        return float(self.command(':TEMPerature?')), self.command(':TEMPerature:STABLE?') == 'TRUE'

    # ── ports ───────────────────────────────────────────────────────────────
    def set_port(self, port: int, standard: str, dest: int | None = None) -> None:
        standard = standard.upper()
        if standard == 'THROUGH':
            if dest is None:
                raise LibreCALError('THROUGH needs a destination port')
            self.command(f':PORT {port} THROUGH {dest}')
            want = f'THROUGH {dest}'
        elif standard in STANDARDS:
            self.command(f':PORT {port} {standard}')
            want = standard
        else:
            raise LibreCALError(f'unknown standard {standard}')
        got = self.get_port(port)
        if got != want:
            raise LibreCALError(f'port {port} reads {got!r} after setting {want}')

    def get_port(self, port: int) -> str:
        return self.command(f':PORT? {port}')

    def release(self, ports: int = 4) -> None:
        for p in range(1, ports + 1):
            self.command(f':PORT {p} NONE')

    # ── coefficients ────────────────────────────────────────────────────────
    def coefficient_sets(self) -> list[str]:
        return [s for s in self.command(':COEFFicient:LIST?').split(',') if s]

    def coefficient(self, set_name: str, name: str, progress=None) -> list[tuple[float, list[complex]]]:
        """[(f_Hz, [S...])]; reflection standards have one S parameter."""
        n = int(self.command(f':COEFFicient:NUMber? {set_name} {name}'))
        out = []
        for i in range(n):
            vals = [float(v) for v in self.command(f':COEFFicient:GET? {set_name} {name} {i}').split(',')]
            s = [complex(vals[k], vals[k + 1]) for k in range(1, len(vals) - 1, 2)]
            out.append((vals[0] * 1e9, s))
            if progress:
                progress(i + 1, n)
        return out


class MockLibreCALSerial:
    """Pyserial stand-in that answers like LibreCAL firmware (SCPI.cpp). `script` records (line, reply).
    The FACTORY set holds synthetic P1 coefficients: an open of 50 fF behind 5 mm, a short of 10 pH behind 5 mm,
    a 51-ohm load (ideal lossless lines, 100 MHz .. 6 GHz, 60 points), and a P12_THROUGH of 150 ps."""

    def __init__(self, ports: int = 4, broken: bool = False):
        import cmath
        self.ports = ['NONE'] * ports
        self.broken = broken
        self.script: list[tuple[str, str]] = []
        self._out = b''
        c, l, z0, length = 50e-15, 10e-12, 50.0, 5e-3
        freqs = [100e6 + i * (5.9e9 / 59) for i in range(60)]

        def line(g, f):
            return g * cmath.exp(-2j * 2 * cmath.pi * f * length / 299792458.0)
        self.coeffs = {
            'P1_OPEN': [(f, line((1 - 1j * 2 * cmath.pi * f * c * z0) / (1 + 1j * 2 * cmath.pi * f * c * z0), f)) for f in freqs],
            'P1_SHORT': [(f, line((1j * 2 * cmath.pi * f * l - z0) / (1j * 2 * cmath.pi * f * l + z0), f)) for f in freqs],
            'P1_LOAD': [(f, complex((51 - 50) / (51 + 50), 0)) for f in freqs],
        }
        # A through between ports 1 and 2: 150 ps (44.969 mm in air), |S21| 0.97, matched (S11 = S22 = 0).
        self.thru_delay_s = 150e-12
        t = [0.97 * cmath.exp(-2j * cmath.pi * f * self.thru_delay_s) for f in freqs]
        self.coeffs['P12_THROUGH'] = [(f, [0j, s21, s21, 0j]) for f, s21 in zip(freqs, t)]

    def write(self, data: bytes) -> int:
        line = data.decode().strip()
        reply = '' if self.broken else self._answer(line)
        self.script.append((line, reply if reply is not None else '<no reply>'))
        if reply is not None:
            self._out += (reply + '\r\n').encode()
        return len(data)

    def readline(self) -> bytes:
        if b'\n' not in self._out:
            out, self._out = self._out, b''
            return out                      # a timeout: no newline
        line, self._out = self._out.split(b'\n', 1)
        return line + b'\n'

    def reset_input_buffer(self) -> None:
        self._out = b''

    def close(self) -> None:
        pass

    def _answer(self, line: str) -> str:
        cmd, *args = line.split(' ')
        c = cmd.upper()
        if c == '*IDN?':
            return 'LibreCAL,LibreCAL,MOCK1234,0.2.5'
        if c in (':FIRMWARE?', 'FIRMWARE?'):
            return '0.2.5'
        if c in (':PORTS?',):
            return str(len(self.ports))
        if c in (':TEMPERATURE?', ':TEMP?'):
            return '35.012000'
        if c in (':TEMPERATURE:STABLE?', ':TEMP:STABLE?'):
            return 'TRUE'
        if c == ':PORT':
            try:
                p = int(args[0]) - 1
                assert 0 <= p < len(self.ports)
            except (ValueError, IndexError, AssertionError):
                return 'ERROR'
            std = args[1] if len(args) > 1 else ''
            if std == 'THROUGH':
                try:
                    d = int(args[2]) - 1
                    assert 0 <= d < len(self.ports) and d != p
                except (ValueError, IndexError, AssertionError):
                    return 'ERROR'
                self._unthrough(p); self._unthrough(d)
                self.ports[p], self.ports[d] = f'THROUGH {d + 1}', f'THROUGH {p + 1}'
                return ''
            if std in ('OPEN', 'SHORT', 'LOAD', 'NONE'):     # case sensitive, like the firmware
                self._unthrough(p)
                self.ports[p] = std
                return ''
            return 'ERROR'
        if c == ':PORT?':
            try:
                return self.ports[int(args[0]) - 1]
            except (ValueError, IndexError):
                return 'ERROR'
        if c in (':COEFFICIENT:LIST?', ':COEFF:LIST?'):
            return 'FACTORY'
        if c in (':COEFFICIENT:NUMBER?', ':COEFF:NUM?'):
            return str(len(self.coeffs[args[1]])) if len(args) == 2 and args[1] in self.coeffs else 'ERROR'
        if c in (':COEFFICIENT:GET?', ':COEFF:GET?'):
            try:
                f, s = self.coeffs[args[1]][int(args[2])]
            except (KeyError, IndexError, ValueError):
                return 'ERROR'
            return f'{f / 1e9:.9f},' + ','.join(f'{v.real:.9f},{v.imag:.9f}' for v in (s if isinstance(s, list) else [s]))
        return 'ERROR'

    def _unthrough(self, p: int) -> None:
        if self.ports[p].startswith('THROUGH'):
            self.ports[int(self.ports[p].split()[1]) - 1] = 'NONE'
