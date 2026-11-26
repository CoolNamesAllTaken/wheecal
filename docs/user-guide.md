# User guide

## Before you start

1. Connect the LibreCAL by USB and wait until it is at temperature (its LED says when).
2. Put the VNA on the same network as your computer. Select VNA mode and the measurement (S11 or S21) on its panel.
3. Cable the LibreCAL to the VNA. See [Wiring](../README.md#wiring).

## GUI

Start `wheecal` (or `python3 -m wheecal`). The window has four sections.

### 1. VNA

- **Connect:**
  1. Choose the **Model**, and enter the **Host** (IP address or hostname) and **SCPI port**.
  2. Press **Connect**. The panel then shows the instrument's identity, mode, measurement, whether the user kit is
     selected, and whether the calibration is on.
  - **Dry run** simulates the VNA, so nothing is sent to it.
- **Load kit into VNA:** writes the chosen **Kit** into the VNA's user (Custom) kit, reads every field back and
  selects that kit. The table shows each field's wanted and read-back values. Kits:
  - **LibreCAL standards**: fitted from the LibreCAL's FACTORY data (open, short and load of the selected port).
    If the LibreCAL has data for the through between the two selected ports, the kit gets its length too. Use this
    kit for LibreCAL calibrations.
  - **6650F03E female / male**: the mechanical 3.5 mm kit, for calibrating by hand.
- **Verify:** reads the user kit back and compares it with the chosen kit.
- **Backup… / Restore…:** save the user kit to JSON, or write a saved one back.
- **Screenshot…:** saves the VNA's screen.

The VNA keeps one user kit, so loading one kit replaces the other. Back up first if you need what's there.

### 2. LibreCAL

- Pick the serial port and press **Connect**. LibreCALs are listed first; *auto* takes the first one found, and
  *simulated LibreCAL* lets you try the app without one.
- **Port on the VNA test port / output** is the LibreCAL port cabled to VNA port 1. **On the VNA input** is the one
  cabled to VNA port 2 (S21 only).
- **Fit LibreCAL standards (preview)** shows the fitted kit without writing it.
  - **fit VNA sweep** (default) fits over the VNA's current sweep: the band the calibration is used in.
  - **fit full band** fits over all of the LibreCAL's data.

  Either way the fit stops at the VNA's highest frequency (RSA5065N: 6.5 GHz). The log shows the band and each
  standard's worst error. A **WARNING** means the LibreCAL open (or short) doesn't reduce well to the VNA's
  C0..C3 (L0..L3) model in that band. That is a limit of the model; a narrower sweep helps.

### Calibration options

- **Automatic:** wheecal sends the VNA's measure and apply commands.
- **Guided:** wheecal switches the LibreCAL, then tells you which VNA button to press. At the end it checks the
  VNA's screen, as in automatic mode.
- **settle s:** the shortest wait after each measurement. On an instrument that reports its sweep time (the
  RSA5065N) wheecal waits at least 2 sweeps + 1 s after switching a standard and after each measurement, whatever
  this says, so a slow sweep (many points, narrow IF bandwidth) is handled automatically.
- **require stable LibreCAL temperature** and **check VNA kit = LibreCAL fit** are on by default.

### 3. Calibrate S11 (Open, Short, Load)

The VNA must measure S11. For each standard, wheecal switches the LibreCAL port to it, the VNA measures it, then
the next one. Then wheecal applies the calibration ("Done") and checks the VNA's screen (see below).

### 4. Calibrate S21 (2-port Through)

The VNA must measure S21: select it on the panel. If it doesn't, wheecal stops and says so.
1. wheecal connects the two LibreCAL ports to each other (THROUGH).
2. The VNA measures the through.
3. wheecal applies the calibration and checks the VNA's screen.

On the RSA5065N this is a through (transmission response) calibration. The instrument has no isolation step.

**Cancel** stops between steps. If something fails after the first measurement but before Done, wheecal aborts
the VNA's partial calibration (in automatic mode). It always sets every LibreCAL port to NONE. The log records
each step.

### After Done: how wheecal checks, and what it never does

**wheecal never discards a calibration after Done.** The RSA5065N's state query (`STATe?`) reads 0 even while a
correction is on, so it can't be trusted. wheecal logs it, but it is never a reason to abort.

Instead, wheecal takes a screenshot and reads the correction badge at the top left of the VNA screen:

| The VNA shows | wheecal says |
|---|---|
| **Cor** | applied |
| **C ?** | applied, interpolated (the cal was taken at other points or conditions) |
| **---** | saved, but no correction on screen. Check the VNA. The calibration is left in place |
| something else | saved, could not auto-verify. Check that the VNA shows Cor or C ? |

Each check saves the badge crop in the `indicator` folder next to the settings file.

To throw a calibration away, use **Abort / discard &lt;meas&gt; cal** (next to Cancel; it asks first), or the VNA's
own Abort. From the command line: `wheecal --discard-cal s11`.

## Command line

Any argument runs the command line instead of the GUI. `wheecal --help` lists every flag. Exit codes: 0 OK,
1 read-back mismatch, a failed or cancelled calibration, or one saved but not shown as on by the VNA, 2 connection or instrument error.

```bash
wheecal --show-config                          # where the settings are, and what they say
wheecal --list-ports                           # LibreCALs first
wheecal --host 192.0.2.10 --backup kit.json    # save the VNA user kit (192.0.2.10 stands for your VNA)
wheecal --load-kit librecal                    # fit the LibreCAL standards and write them (host from settings)
wheecal --verify librecal
wheecal --calibrate                            # S11 Open/Short/Load (asks first)
wheecal --calibrate --verify-standards         # then read |S11| of the LibreCAL open, short and load
wheecal --verify-standards                     # just the check, on the current calibration
wheecal --calibrate s21                        # 2-port through; same as --calibrate-thru
wheecal --calibrate-thru --librecal-port 3 --through-port 4 --mode guided
wheecal --discard-cal s11                      # discard the VNA's S11 calibration (asks first; never automatic)
wheecal --dry-run --mock-librecal --load-kit librecal --calibrate s21 --yes     # full simulation
wheecal --host-cal --host-cal-verify           # host-side S11 cal + independent check (aborts the on-board cal)
wheecal --host-correct dut.s1p                 # DUT on the test port -> host-corrected Touchstone file
wheecal --host-cal --export dut.s1p            # host-side cal, then measure and export the DUT
```

Flags override the settings file for one run: `--driver`, `--host`, `--scpi-port`, `--device` (LibreCAL serial
port), `--librecal-port`, `--through-port`, `--mode`, `--settle`, `--fit-band`.

## Host-side S11 calibration (most accurate)

The VNA's on-board calibration solves against a parametric kit that the LibreCAL's standards don't fit exactly.
`--host-cal` solves the calibration in wheecal instead, against the LibreCAL's measured standards, and corrects
measurements in software. On the bench it was about twice as accurate as the on-board calibration, and it had none
of the on-board calibration's spikes above 0 dB. It clears the VNA's on-board S11 calibration (raw reads), so the
VNA screen shows the raw trace. Read the corrected data from the `.s1p` that `--host-correct` / `--export` writes.
`--host-cal-verify` checks the calibration against the LibreCAL through, which the calibration doesn't use.
Command line only for now. See [Host-side S11 calibration](host-calibration.md).

## After calibrating

`--verify-standards` (S11, LibreCAL still on the test port) does the S11 check for you: it switches the LibreCAL
to Open, Short and Load, waits until each is swept in, reads the corrected trace (`:TRACe1:DATA?` on the RSA5065N)
and logs |S11| min/max/mean in dB. It passes when Open and Short read within 1 dB of 0 dB and Load below -15 dB
(exit code 1 otherwise). Drivers that can't read the trace skip it.

Check the calibration with something known:
- **S11:** a load reads well below -30 dB, and an open or short sits on the edge of the Smith chart.
- **S21:** the through reads about 0 dB.

On the RSA5065N it's not yet known whether the through calibration uses the kit's through length or only
normalizes the through. Measure the LibreCAL through after calibrating:
- if its phase is flat, the calibration only normalizes;
- if it shows the fitted delay, the calibration used the kit's through length.
