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
  - **fit full band** (recommended) fits over all of the LibreCAL's data.
  - **fit VNA span** fits over the current sweep only.

### Calibration options

- **Automatic:** wheecal sends the VNA's measure and apply commands.
- **Guided:** wheecal switches the LibreCAL, then tells you which VNA button to press. At the end it checks that
  the calibration is on.
- **settle s:** the wait after each measurement. Allow a few full sweeps; raise it for slow sweeps.
- **require stable LibreCAL temperature** and **check VNA kit = LibreCAL fit** are on by default.

### 3. Calibrate S11 (Open, Short, Load)

The VNA must measure S11. For each standard, wheecal switches the LibreCAL port to it, the VNA measures it, then
the next one. Then wheecal applies the calibration ("Done") and checks it is on.

### 4. Calibrate S21 (2-port Through)

The VNA must measure S21: select it on the panel. If it doesn't, wheecal stops and says so.
1. wheecal connects the two LibreCAL ports to each other (THROUGH).
2. The VNA measures the through.
3. wheecal applies the calibration and checks it is on.

On the RSA5065N this is a through (transmission response) calibration. The instrument has no isolation step.

**Cancel** stops between steps. If something fails after the first measurement, wheecal aborts the VNA's
calibration (in automatic mode) and always sets every LibreCAL port to NONE. The log records each step.

## Command line

Any argument runs the command line instead of the GUI. `wheecal --help` lists every flag. Exit codes: 0 OK,
1 read-back mismatch or failed or cancelled calibration, 2 connection or instrument error.

```bash
wheecal --show-config                          # where the settings are, and what they say
wheecal --list-ports                           # LibreCALs first
wheecal --host 192.0.2.10 --backup kit.json    # save the VNA user kit (192.0.2.10 stands for your VNA)
wheecal --load-kit librecal                    # fit the LibreCAL standards and write them (host from settings)
wheecal --verify librecal
wheecal --calibrate                            # S11 Open/Short/Load (asks first)
wheecal --calibrate s21                        # 2-port through; same as --calibrate-thru
wheecal --calibrate-thru --librecal-port 3 --through-port 4 --mode guided
wheecal --dry-run --mock-librecal --load-kit librecal --calibrate s21 --yes     # full simulation
```

Flags override the settings file for one run: `--driver`, `--host`, `--scpi-port`, `--device` (LibreCAL serial
port), `--librecal-port`, `--through-port`, `--mode`, `--settle`, `--fit-band`.

## After calibrating

Check the calibration with something known:
- **S11:** a load reads well below -30 dB, and an open or short sits on the edge of the Smith chart.
- **S21:** the through reads about 0 dB.

On the RSA5065N it's not yet known whether the through calibration uses the kit's through length or only
normalizes the through. Measure the LibreCAL through after calibrating:
- if its phase is flat, the calibration only normalizes;
- if it shows the fitted delay, the calibration used the kit's through length.
