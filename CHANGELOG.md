# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

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
