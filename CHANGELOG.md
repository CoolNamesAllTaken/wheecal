# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-26

### Added
- **Host-side S11 calibration** (`wheecal.hostcal`, [docs/host-calibration.md](docs/host-calibration.md)).
  - wheecal measures the LibreCAL Open, Short and Load raw, and solves the 3-term one-port error model per sweep
    point against the LibreCAL's FACTORY coefficients, interpolated onto the sweep.
  - It corrects DUT traces in software, so there is no kit-model error. The VNA's parametric kit can't describe
    the LibreCAL standards, which is why on-board calibrations spiked above 0 dB.
  - Bench, RSA5065N 100 kHz-3 GHz: independent through check mean |error| 0.062, max 0.107. On-board, the
    fitted LibreCAL kit gave 0.146 / 7.32, and the ideal kit 0.109 / 0.232.
- CLI:
  - `--host-cal`: save the error terms (`hostcal.json` next to the settings, or `--host-cal-file`).
  - `--host-cal-verify`: the independent check. The LibreCAL through, far port matched, is compared with its
    FACTORY S11. Pass: mean < 0.12. Exit code 1 on a fail.
  - `--host-correct OUT.s1p`: correct the DUT on the test port and write Touchstone.
  - `--host-cal --export OUT.s1p`: calibrate, then measure and write the DUT.
  - All of these simulate with `--dry-run --mock-librecal`.
- Driver interface: optional `sweep_freqs()`. RSA5065N: `sweep_points()` (`:SWEep:POINts?`) and `sweep_freqs()`.
  The mock answers the points query, and `follow_raw()` makes its trace an uncorrected port behind an error box.
- Tests: solver, correction, interpolation, terms file, `.s1p`, flows, CLI. A trimmed real capture (25 points)
  locks in the bench result.

### Changed
- The host-side commands abort the VNA's on-board S11 calibration to read raw, with a warning; `--host-cal` asks
  first unless `--yes`. Nothing else changed: on-board calibrations are still never discarded automatically.

## [0.1.2] - 2026-11-26

### Fixed
- **Calibration waits scale to the sweep time.** At high point counts an S11 calibration read wildly above 0 dB
  after Done. The RSA5065N's sweep takes about 4.5 s at 1000 points, but wheecal waited a fixed 0.5 s after
  switching the LibreCAL and 3 s after each measure command, both shorter than one sweep. So each standard was
  captured before it was fully swept in, as a blend with the previous one, and the error terms were wrong.
  `*OPC?` does not help: on this firmware it answers in about 0.2 s and doesn't wait for the sweep.
  - Now wheecal reads `:SWEep:TIME?` and waits 2 × sweep time + 1 s after each LibreCAL switch (before measuring)
    and after each measure command. The settle times in the settings are the minimums.
  - Bench check with the fix: Open mean 0.00 dB, Short -0.02 dB, Load -24 dB, badge "Cor".
  - Applies to automatic and guided mode, S11 and S21.

### Added
- **`--verify-standards`**: after an S11 calibration (or on its own) the LibreCAL presents Open, Short and Load in
  turn, and wheecal reads the corrected S11 trace and logs |S11| min/max/mean in dB for each. Pass: Open and Short
  within 1 dB of 0 dB, Load below -15 dB. Exit code 1 on a fail. Skipped for drivers that can't read the trace.
- Driver interface: `measurement_settle(min_s)` (default: `min_s`) and the optional `read_s11()`.
- RSA5065N: `sweep_time()` (`:SWEep:TIME?`) and `read_s11()` (`:TRACe1:DATA?`). The mock answers both (sweep time
  0 by default, so tests stay fast) and can make the trace follow a mock LibreCAL.

## [0.1.1] - 2026-11-26

Fixes from the first bench calibration (RSA5065N firmware 00.03.08 with a LibreCAL).

### Fixed
- **A saved calibration is never discarded automatically.** v0.1.0 read `:CALibration:S11:STATe?` after Done, got
  0 and sent `:CALibration:S11:ABORt`, which discards the calibration. On this firmware `STATe?` reads 0 even while
  a correction is on (the panel showed "Cor" and "C ?" while it read 0), so a calibration that had applied was
  thrown away. Now `ABORt` is sent only on Cancel or an error *before* Done. After Done the calibration stays,
  whatever any check says. To discard it on purpose: **Abort / discard** in the GUI or `--discard-cal s11|s21`.
  This applies to S11 and S21, in automatic and guided mode.
- **Verified from the screen.** After Done, wheecal takes a screenshot (`:PRIV:SNAP?`) and reads the correction
  badge at the top left. It compares the badge with three references taken from this instrument: "---" (blank),
  "Cor" and "C ?".
  - "Cor", or any other clear mark such as "C !": **applied**.
  - "C ?": **applied, interpolated**.
  - "---": **saved but not applied**. The calibration is still left in place.
  - Anything it can't classify: **saved; could not auto-verify**. Check the VNA shows "Cor" or "C ?".

  `STATe?` is still read and logged, as advisory only. The badge crop is saved next to the settings file, in
  `indicator/`, for debugging. Pure Python: no new dependency.
- **Kit fit over the VNA sweep by default** (`fit_band` = `span`, was `full`). Every fit stops at the instrument's
  highest frequency (RSA5065N: 6.5 GHz) and within the LibreCAL data.
  - Each standard's worst error is logged.
  - A warning appears when the open or short does not reduce to the VNA's C0..C3 / L0..L3 model (worst error above
    -20 dB). v0.1.0 fitted 0-8.5 GHz with an open error of +5.3 dB.
  - **full** is still selectable.
  - Settings files written by v0.1.0, which stored `full` as the default, switch to `span` once.

### Added
- `screen` module: BMP decode/encode, crop, correction-badge classifier.
- Driver interface: `cal_indicator(meas)` and `FREQ_MAX_HZ`.
- The mock RSA5065N can lie about `STATe?` like the real one and returns screenshots with the badge.

## [0.1.0] - 2026-11-25

First release.

### Added
- **LibreCAL support.** Connect over USB serial, read identity and temperature, set port standards and throughs,
  and read the stored coefficients. Commands that write the LibreCAL's memory are never sent.
- **Kit fit.** The LibreCAL's FACTORY open, short and load are fitted to the VNA cal-kit model: offset length +
  C0..C3, offset length + L0..L3, and R. The through's electrical length comes from a linear fit of its unwrapped
  S21 phase.
- **VNA driver interface** (`wheecal.drivers`) with a registry. First driver: Rigol RSA5065N with the VNA option:
  - write its Custom kit, read it back, back it up and restore it;
  - S11 Open/Short/Load and S21 Through calibration steps.
- **Calibrations.** S11 (1-port) and S21 (2-port through), in automatic (SCPI) or guided mode. Before calibrating,
  the VNA kit is checked against the LibreCAL fit. A failure aborts the VNA's calibration and always releases the
  LibreCAL.
- **GUI** (Tk) and **command line**. Nothing runs on launch. The write whitelist keeps span, points, IF bandwidth,
  source power, mode and measurement unchanged, and no calibration is ever cleared.
- **Per-user settings file**, created on first run without an instrument address. CLI flags override it.
- **Standalone apps** from PyInstaller for macOS (`.app`), Windows and Linux. A tag `v*` builds them and publishes
  a GitHub Release.
- **Docs:** user guide, adding a VNA, building, and RSA5065N/LibreCAL command notes.
