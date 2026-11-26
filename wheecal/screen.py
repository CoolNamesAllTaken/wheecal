"""
Screenshots, pure Python: decode the BMP a VNA sends (24- or 32-bit, uncompressed or BI_BITFIELDS), crop, write a
crop back as BMP, and read a correction indicator (a small badge on screen) by comparing it with reference crops.

Why the screen: some firmware reports its calibration state wrongly over SCPI (the RSA5065N's
:CALibration:S11:STATe? reads 0 while the panel shows "C ?", docs/rsa5065n.md), so the badge the operator sees is
the only trustworthy "is a correction on?" signal.

classify(frame, spec) looks at spec.box only:
  1. the box must be there: most of its inside is the badge's fill colour (else 'unknown': another screen, layout
     or resolution);
  2. 'none'  when it matches the blank reference ("---"): at most spec.blank_tolerance pixels differ;
  3. a reference's state (e.g. 'on' for "Cor", 'interpolated' for "C ?") when it matches that reference the same
     way (nearest reference first);
  4. 'on'    when it clearly differs from blank (another mark, e.g. "C !"): at least spec.min_glyph_pixels pixels
     that differ from the blank reference, spanning at least spec.min_glyph_rows rows (a letter, not a speck);
  5. else 'unknown'.
A pixel "differs" when a channel differs by more than spec.pixel_delta; the references are compared at the box's
position and 1 pixel around it, best match kept.
"""
from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass, field


@dataclass
class Bitmap:
    """RGB pixels, rows top to bottom, 3 bytes a pixel."""
    width: int
    height: int
    rgb: bytes

    def px(self, x: int, y: int) -> tuple:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]

    def crop(self, x0: int, y0: int, x1: int, y1: int) -> 'Bitmap':
        """Pixels x0 <= x < x1, y0 <= y < y1 (clipped to the image)."""
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.width, x1), min(self.height, y1)
        w = max(0, x1 - x0)
        rows = [self.rgb[(y * self.width + x0) * 3:(y * self.width + x0 + w) * 3] for y in range(y0, y1)]
        return Bitmap(w, max(0, y1 - y0), b''.join(rows))

    def paste(self, other: 'Bitmap', x0: int, y0: int) -> 'Bitmap':
        """A copy with other's pixels at (x0, y0)."""
        buf = bytearray(self.rgb)
        for y in range(other.height):
            if 0 <= y0 + y < self.height:
                w = min(other.width, self.width - x0)
                i = ((y0 + y) * self.width + x0) * 3
                buf[i:i + w * 3] = other.rgb[y * other.width * 3:(y * other.width + w) * 3]
        return Bitmap(self.width, self.height, bytes(buf))

    @classmethod
    def solid(cls, width: int, height: int, color=(0, 0, 0)) -> 'Bitmap':
        return cls(width, height, bytes(color) * (width * height))

    def pack(self) -> str:
        """The pixels as text (zlib + base64) for a reference kept in code."""
        return base64.b64encode(zlib.compress(self.rgb, 9)).decode()

    @classmethod
    def unpack(cls, width: int, height: int, text: str) -> 'Bitmap':
        return cls(width, height, zlib.decompress(base64.b64decode(text)))


def decode_bmp(data: bytes) -> Bitmap:
    if data[:2] != b'BM' or len(data) < 54:
        raise ValueError('not a BMP image')
    offset = struct.unpack_from('<I', data, 10)[0]
    width, height = struct.unpack_from('<ii', data, 18)
    bpp, comp = struct.unpack_from('<HI', data, 28)
    if bpp not in (24, 32) or comp not in (0, 3):
        raise ValueError(f'BMP {bpp}-bit compression {comp} is not supported')
    top_down, height = height < 0, abs(height)
    step = bpp // 8
    stride = (width * step + 3) & ~3
    if len(data) < offset + stride * height:
        raise ValueError('BMP is truncated')
    out = bytearray(width * height * 3)
    for row in range(height):
        y = row if top_down else height - 1 - row
        src = data[offset + row * stride:offset + row * stride + width * step]
        dst = y * width * 3
        out[dst + 0:dst + width * 3:3] = src[2::step]
        out[dst + 1:dst + width * 3:3] = src[1::step]
        out[dst + 2:dst + width * 3:3] = src[0::step]
    return Bitmap(width, height, bytes(out))


def encode_bmp(bm: Bitmap) -> bytes:
    """24-bit bottom-up BMP."""
    stride = (bm.width * 3 + 3) & ~3
    pad = b'\0' * (stride - bm.width * 3)
    rows = []
    for y in range(bm.height - 1, -1, -1):
        r = bm.rgb[y * bm.width * 3:(y + 1) * bm.width * 3]
        bgr = bytearray(len(r))
        bgr[0::3], bgr[1::3], bgr[2::3] = r[2::3], r[1::3], r[0::3]
        rows.append(bytes(bgr) + pad)
    pixels = b''.join(rows)
    header = struct.pack('<2sIHHI', b'BM', 54 + len(pixels), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, bm.width, bm.height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


# ── correction indicator ────────────────────────────────────────────────────

@dataclass
class IndicatorSpec:
    box: tuple                       # x0, y0, x1, y1 (end exclusive) of the badge in the full frame
    fill: tuple                      # the badge's background colour
    blank: Bitmap                    # the badge with no correction ("---"), box-sized
    references: dict = field(default_factory=dict)   # state -> box-sized Bitmap (e.g. 'interpolated': "C ?")
    inset: int = 2                   # border pixels left out
    pixel_delta: int = 30
    fill_delta: int = 8
    min_fill: float = 0.55
    blank_tolerance: int = 4
    min_glyph_pixels: int = 20
    min_glyph_rows: int = 5
    labels: dict = field(default_factory=dict)       # state -> what the panel shows


@dataclass
class Indicator:
    state: str                       # on | interpolated | none | unknown
    detail: str = ''
    crop: Bitmap | None = None
    scores: dict = field(default_factory=dict)

    @property
    def active(self) -> bool | None:
        """True: a correction is on; False: none; None: could not tell."""
        return {'on': True, 'interpolated': True, 'none': False}.get(self.state)


def _differs(a: tuple, b: tuple, delta: int) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) > delta


def _mismatch(frame: Bitmap, ref: Bitmap, x0: int, y0: int, inset: int, delta: int) -> int:
    n = 0
    for y in range(inset, ref.height - inset):
        for x in range(inset, ref.width - inset):
            if _differs(frame.px(x0 + x, y0 + y), ref.px(x, y), delta):
                n += 1
    return n


def _best_mismatch(frame: Bitmap, ref: Bitmap, spec: IndicatorSpec) -> int:
    x0, y0 = spec.box[0], spec.box[1]
    return min(_mismatch(frame, ref, x0 + dx, y0 + dy, spec.inset + 1, spec.pixel_delta)
               for dx in (-1, 0, 1) for dy in (-1, 0, 1))


def classify(frame: Bitmap, spec: IndicatorSpec) -> Indicator:
    x0, y0, x1, y1 = spec.box
    if frame.width < x1 + 1 or frame.height < y1 + 1 or x0 < 1 or y0 < 1:
        return Indicator('unknown', f'the screenshot ({frame.width}x{frame.height}) has no badge at {spec.box}')
    crop = frame.crop(x0, y0, x1, y1)
    inner = [(x, y) for y in range(spec.inset, crop.height - spec.inset) for x in range(spec.inset, crop.width - spec.inset)]
    fill = sum(not _differs(crop.px(x, y), spec.fill, spec.fill_delta) for x, y in inner) / max(1, len(inner))
    text = [(x, y) for x, y in inner if _differs(crop.px(x, y), spec.fill, spec.pixel_delta)]
    rows = len({y for _, y in text})
    scores = {'fill': round(fill, 3), 'text_pixels': len(text), 'text_rows': rows}
    if fill < spec.min_fill:
        return Indicator('unknown', f'no badge where expected (only {fill:.0%} of the box is badge colour)', crop, scores)
    scores['vs_blank'] = _best_mismatch(frame, spec.blank, spec)
    for state, ref in spec.references.items():
        scores[f'vs_{state}'] = _best_mismatch(frame, ref, spec)
    label = spec.labels.get
    if scores['vs_blank'] <= spec.blank_tolerance:
        return Indicator('none', f"the badge is blank ({label('none', 'no correction')})", crop, scores)
    for state in sorted(spec.references, key=lambda st: scores[f'vs_{st}']):
        if scores[f'vs_{state}'] <= spec.blank_tolerance + 2:
            return Indicator(state, f"the badge shows {label(state, state)}", crop, scores)
    new = [(x, y) for x, y in inner if _differs(crop.px(x, y), spec.blank.px(x, y), spec.pixel_delta)]
    scores['new_rows'] = len({y for _, y in new})
    if len(new) >= spec.min_glyph_pixels and scores['new_rows'] >= spec.min_glyph_rows:
        r, g, b = (sum(crop.px(x, y)[i] for x, y in new) // len(new) for i in range(3))
        return Indicator('on', f"the badge shows a correction ({label('on', 'correction on')}; text colour "
                               f'#{r:02x}{g:02x}{b:02x})', crop, scores)
    return Indicator('unknown', 'the badge is neither blank nor a clear correction mark', crop, scores)
