# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

First release, planned as v0.1.0.

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
