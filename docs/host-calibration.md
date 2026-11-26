# Host-side S11 calibration

Since 0.2.0 wheecal can do the 1-port (S11) calibration itself instead of the VNA. It measures the LibreCAL's Open,
Short and Load **uncorrected**, solves the VNA's error terms in software, and corrects later measurements with them.
This is the accurate way to use a LibreCAL with a VNA whose on-board calibration only takes a parametric kit.

## Why

A VNA's on-board calibration solves against the kit selected on the instrument. The RSA5065N's kit model is
- an open: a lossless offset plus a capacitance polynomial C0..C3;
- a short: a lossless offset plus an inductance polynomial L0..L3;
- a load: an ideal resistor.

The LibreCAL doesn't contain such standards. It stores the *measured* reflection of each of its standards at
hundreds of frequencies (its FACTORY coefficients). `--load-kit librecal` fits those to the model, but the fit
can't be exact (wheecal prints the worst error of each standard). The calibration inherits the fit error, and
where the fit is poor the corrected trace jumps above 0 dB.

The host-side calibration solves against the measured standards themselves, so there is no model in between:
correcting the raw Open, Short or Load gives back exactly the LibreCAL's own coefficient.

Measured on an RSA5065N with a LibreCAL (100 kHz-3 GHz, 1000 points), checked independently with the LibreCAL
through (below). The table shows the mean and max |S11 error| over 10 MHz-3 GHz:

| method | mean | max | max \|S11\| of the through |
|---|---|---|---|
| **host-side, LibreCAL FACTORY standards** (this page) | **0.062** | **0.107** | |
| host-side, ideal standards (O = 1, S = -1, L = 0) | 0.109 | 0.233 | |
| on-board, ideal kit (`--load-kit 6650F03E-M`) | 0.109 | 0.232 | -11.8 dB |
| on-board, fitted LibreCAL kit (`--load-kit librecal`) | 0.146 | 7.32 | **+17.3 dB** |

The host-side FACTORY result sits at the check's own floor of about 0.06, set by how well the far port of the
through is matched. Averaging and drift were ruled out as the cause.

## The model

At each frequency the raw reading *m* of a reflection *g* is

    m = e00 + e11·m·g − Δe·g          (Δe = e00·e11 − e01·e10)

Three known standards give three linear equations in (e00, e11, Δe). wheecal solves them per sweep point with
Cramer's rule. A DUT is then corrected with

    g = (m − e00) / (e11·m − Δe)

The true standards are the LibreCAL's `FACTORY` `P<port>_OPEN/SHORT/LOAD` coefficients, linearly interpolated (real
and imaginary parts) onto the VNA's sweep frequencies and held at the end values outside the LibreCAL data.

## Using it

The VNA measures S11, with the LibreCAL port (`librecal_port`, default 1) on the test port.

```bash
wheecal --host-cal --host-cal-verify       # calibrate, save the terms, run the independent check
wheecal --host-correct dut.s1p             # later: DUT on the test port -> corrected Touchstone file
wheecal --host-cal --export dut.s1p        # calibrate, then swap in the DUT (wheecal waits) and export it
wheecal --host-cal-verify                  # re-check a saved calibration (LibreCAL back on the test port)
```

- `--host-cal` reads the FACTORY standards first. Then it aborts the VNA's on-board S11 calibration (see below),
  and measures Open, Short and Load. Each standard is read once it is fully swept in (two sweeps + 1 s on the
  RSA5065N). It saves the error terms as JSON, by default in `hostcal.json` next to the settings file; give
  `--host-cal-file PATH` for another file. It asks first unless you give `--yes`.
- `--host-correct OUT.s1p` reads the raw trace of whatever is on the test port, applies the saved terms and writes
  a Touchstone v1 file (`# HZ S RI R 50`, one `freq re im` line per point). It prints the |S11| min/max/mean in dB.
- `--export OUT.s1p` (with `--host-cal`) does the same right after the calibration. It asks you to connect the DUT
  first (without `--yes`).
- The terms belong to one sweep. If the span or the number of points changed, wheecal refuses to apply them and asks
  you to calibrate again. Changing IF bandwidth, power or the cables also calls for a new calibration, as with any
  calibration.

The flags combine. In one run they go in this order: calibrate, verify, export / correct. The on-board calibration
is aborted only once per run.

### The on-board calibration is cleared

Raw readings need the VNA's own S11 correction off, so `--host-cal`, `--host-cal-verify` and `--host-correct` send
`:CALibration:S11:ABORt` first, and print a warning saying so. The correction then lives only in wheecal:
**the VNA screen shows the raw trace**, not the corrected one. Look at the corrected data in the exported `.s1p`
(any Touchstone viewer, or scikit-rf). To get a corrected trace on the screen again, run an on-board calibration
(`wheecal --calibrate`). It is less accurate (see the table above). For an on-board calibration, the ideal kit
gave a bounded error on the bench (no spikes above 0 dB), while the fitted LibreCAL kit spiked where the fit is
poor.

## The independent check

`--host-cal-verify` measures something the calibration did not use. The LibreCAL connects its test port to
`through_port` (default 2) with its internal through. The far end must be terminated in a matched 50 Ω (on the bench:
the VNA's receiver input, or a load). Then the test port sees the through's own reflection, stored in the LibreCAL's
FACTORY `P12_THROUGH` as S11 (S22 when the test port is the higher-numbered port).

wheecal reads the through raw, corrects it, and compares it point by point with that S11 over f ≥ 10 MHz. It logs
the min, max and mean |error|. **PASS when the mean is below 0.12** (about twice the floor). Exit code 1 on a
fail. A fail usually means:
- the sweep or cabling changed since the calibration;
- the far port isn't matched;
- an on-board correction was applied while the terms were measured. wheecal aborts the on-board correction itself,
  so this only happens if someone calibrated on the panel during the run.

Bench output (RSA5065N firmware 00.03.08, LibreCAL firmware 0.3.0):

```
Sweep: 0.1-3000 MHz, 1000 points
host S11 calibration solved over 1000 points against LibreCAL FACTORY P1 (standards reproduced to 1.8e-14)
Verify: LibreCAL port 1 <-> port 2 THROUGH (port 2 must see a matched 50-ohm termination)
  |corrected - FACTORY through S11| over f >= 10 MHz (996 points): min 0.0181  max 0.1072  mean 0.0620
HOST VERIFY PASS - mean 0.0620, max 0.1072 (limit mean < 0.12)
```

## For drivers

Host-side calibration needs two optional driver methods: `sweep_freqs()` (the Hz of each sweep point) and
`read_s11()` (the trace as complex numbers). It also uses `cal_abort('S11')` and `measurement_settle()`. A driver
without `sweep_freqs()` gets a clear error. In Python the pieces are in `wheecal.hostcal`:
- `solve_osl`, `apply`, `interp_complex`;
- `ErrorTerms`, with JSON `save` / `load`;
- `write_s1p`;
- `run_host_cal`, `verify_host_cal` and `measure_dut`.
