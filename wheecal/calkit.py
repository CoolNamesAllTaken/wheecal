"""
A cal-kit definition in SI units, independent of any VNA. Drivers convert it to their instrument's fields.

The model is the common one VNAs use for coaxial kits:
  open   lossless offset of electrical length open_len_m (in air) + C(f) = C0 + C1 f + C2 f^2 + C3 f^3
  short  lossless offset of short_len_m + L(f) = L0 + L1 f + L2 f^2 + L3 f^3
  load   a resistor load_r
  thru   a lossless offset of thru_len_m
C in F, F/Hz, F/Hz^2, F/Hz^3; L in H, H/Hz, H/Hz^2, H/Hz^3; lengths in meters (electrical length in air).
"""
from __future__ import annotations

from dataclasses import dataclass, field

C_LIGHT = 299792458.0
STANDARDS = ('OPEN', 'SHORT', 'LOAD', 'THRU')


@dataclass
class CalKit:
    name: str
    open_c: tuple = (0.0, 0.0, 0.0, 0.0)
    open_len_m: float = 0.0
    short_l: tuple = (0.0, 0.0, 0.0, 0.0)
    short_len_m: float = 0.0
    load_r: float = 50.0
    thru_len_m: float = 0.0
    note: str = ''
    extra: dict = field(default_factory=dict)


#: 6650F03E 3.5 mm, DC-3 GHz kit, female standards: the values printed on its card. Offset delays 29.243 / 31.785 ps
#: are these lengths in air. Offset loss is not modeled (0).
KIT_6650F03E_F = CalKit(
    '6650F03E female', open_c=(49.433e-15, -310.13e-27, 23.168e-36, -0.15966e-45), open_len_m=8.767e-3,
    short_l=(2.0765e-12, -108.54e-24, 2.1705e-33, -0.01e-42), short_len_m=9.529e-3,
    note='Card values; offset loss left at 0.')
#: The same kit's male standards are ideal.
KIT_6650F03E_M = CalKit('6650F03E male (ideal)', note='Ideal standards.')

BUILTIN_KITS = {'6650F03E-F': KIT_6650F03E_F, '6650F03E-M': KIT_6650F03E_M}
