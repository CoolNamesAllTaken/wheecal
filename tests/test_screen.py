"""The screenshot BMP codec and the RSA5065N correction-badge classifier, on crops of real screenshots of the
instrument (tests/data, 160x40 from x 200, y 0 of the 1024x600 frame):

  rsa5065n-top-blank.bmp                 "---"  no correction (2026-11-25)
  rsa5065n-top-c-interpolated.bmp        "C ?"  correction on, interpolated (2026-11-25, live; STATe? read 0)
  rsa5065n-top-c-interpolated-after.bmp  "C ?"  a second capture
  rsa5065n-top-cor.bmp                   "Cor"  correction on (2026-11-26, live; STATe? read 0)
"""
import struct
import unittest
from pathlib import Path

from wheecal import screen
from wheecal.drivers.rsa5065n import RSA5065N

DATA = Path(__file__).resolve().parent / 'data'
SPEC = RSA5065N.INDICATOR


def frame_from(name: str) -> screen.Bitmap:
    """A 1024x600 frame with the saved crop back at its place."""
    crop = screen.decode_bmp((DATA / f'rsa5065n-top-{name}.bmp').read_bytes())
    return screen.Bitmap.solid(1024, 600).paste(crop, 200, 0)


class BmpTests(unittest.TestCase):
    def test_round_trip_24_bit(self):
        bm = screen.Bitmap(3, 2, bytes(range(18)))
        self.assertEqual(screen.decode_bmp(screen.encode_bmp(bm)), bm)

    def test_32_bit_top_down(self):
        px = [(10, 20, 30), (40, 50, 60)]                     # RGB, one row
        body = b''.join(bytes((b, g, r, 255)) for r, g, b in px)
        head = struct.pack('<2sIHHI', b'BM', 54 + len(body), 0, 0, 54)
        info = struct.pack('<IiiHHIIiiII', 40, 2, -1, 1, 32, 0, len(body), 0, 0, 0, 0)
        bm = screen.decode_bmp(head + info + body)
        self.assertEqual((bm.px(0, 0), bm.px(1, 0)), tuple(px))

    def test_not_a_bmp(self):
        with self.assertRaises(ValueError):
            screen.decode_bmp(b'\x89PNG....')

    def test_crop_and_pack(self):
        bm = frame_from('cor').crop(*SPEC.box)
        self.assertEqual((bm.width, bm.height), (45, 24))
        self.assertEqual(screen.Bitmap.unpack(45, 24, bm.pack()), bm)


class IndicatorTests(unittest.TestCase):
    def test_blank_is_not_applied(self):
        ind = screen.classify(frame_from('blank'), SPEC)
        self.assertEqual(ind.state, 'none', ind.scores)
        self.assertIs(ind.active, False)
        self.assertEqual(ind.scores['vs_blank'], 0)

    def test_c_question_is_applied_interpolated(self):
        for name in ('c-interpolated', 'c-interpolated-after'):
            ind = screen.classify(frame_from(name), SPEC)
            self.assertEqual(ind.state, 'interpolated', (name, ind.scores))
            self.assertIs(ind.active, True)
            self.assertIn('C ?', ind.detail)

    def test_cor_is_applied(self):
        ind = screen.classify(frame_from('cor'), SPEC)
        self.assertEqual(ind.state, 'on', ind.scores)
        self.assertIs(ind.active, True)

    def test_margins_between_the_references(self):
        """Each real capture is far from the other states (pixels that differ, vs a tolerance of 4-6)."""
        for name, near in (('blank', 'vs_blank'), ('c-interpolated', 'vs_interpolated'), ('cor', 'vs_on')):
            sc = screen.classify(frame_from(name), SPEC).scores
            others = [v for k, v in sc.items() if k.startswith('vs_') and k != near]
            self.assertEqual(sc[near], 0, name)
            self.assertGreater(min(others), 40, (name, sc))

    def test_another_mark_counts_as_on(self):
        """A mark that is no reference (e.g. "C !") but clearly a glyph: a correction is on."""
        frame = screen.Bitmap.solid(1024, 600).paste(SPEC.blank, *SPEC.box[:2])
        frame = frame.paste(screen.Bitmap.solid(9, 11, (230, 120, 40)), SPEC.box[0] + 16, SPEC.box[1] + 7)
        ind = screen.classify(frame, SPEC)
        self.assertEqual(ind.state, 'on')
        self.assertIn('#', ind.detail)

    def test_no_badge_or_other_screen_is_unknown(self):
        self.assertEqual(screen.classify(screen.Bitmap.solid(1024, 600), SPEC).state, 'unknown')
        self.assertEqual(screen.classify(screen.Bitmap.solid(200, 20), SPEC).state, 'unknown')
        self.assertIsNone(screen.classify(screen.Bitmap.solid(1024, 600), SPEC).active)

    def test_a_faint_smudge_is_not_called_on(self):
        frame = screen.Bitmap.solid(1024, 600).paste(SPEC.blank, *SPEC.box[:2])
        frame = frame.paste(screen.Bitmap.solid(3, 3, (200, 200, 200)), SPEC.box[0] + 30, SPEC.box[1] + 10)
        self.assertEqual(screen.classify(frame, SPEC).state, 'unknown')

    def test_shifted_one_pixel_still_matches(self):
        crop = screen.decode_bmp((DATA / 'rsa5065n-top-cor.bmp').read_bytes())
        frame = screen.Bitmap.solid(1024, 600).paste(crop, 201, 1)
        self.assertEqual(screen.classify(frame, SPEC).state, 'on')

    def test_driver_reads_the_mock_screen(self):
        vna = RSA5065N.mock(state_lies=True, screen_badge='interpolated')
        self.assertEqual(vna.cal_indicator('S11').state, 'interpolated')
        self.assertFalse(vna.cal_state('S11'), 'the mock lies like firmware 00.03.08')


if __name__ == '__main__':
    unittest.main()
