# Adding a VNA

wheecal splits its work in three:
- the **LibreCAL layer** (`wheecal/librecal.py`, shared by every VNA);
- the **calibration flow** (`wheecal/calflow.py`, generic);
- one **driver** per VNA model (`wheecal/drivers/`).

A new model needs only a driver.

## What the VNA must offer

- **SCPI over TCP.** `wheecal/transport.py` speaks raw SCPI over a socket. A model that needs VISA or USBTMC needs a
  transport with the same `query`/`write`/`close`/`lock` methods.
- **A user cal kit you can write over SCPI** that holds a model like `wheecal.calkit.CalKit`:
  - open: offset length + C0..C3;
  - short: offset length + L0..L3;
  - load: R;
  - thru: offset length.

  If the model takes fewer terms, write what it has. The fit's residuals tell the user how good that is.
- **Calibration steps over SCPI**, the same as the panel's buttons: measure a standard, apply ("Done"), abort, and
  read whether the calibration is on. Without these, only guided mode can work: the user presses the buttons and
  wheecal reads the state at the end.

## Write the driver

Subclass `wheecal.drivers.base.VnaDriver` in `wheecal/drivers/<model>.py`. `base.py` documents the contract. Set:

| Attribute | Meaning |
|---|---|
| `NAME`, `TITLE` | registry key (lowercase) and the name shown in the GUI |
| `DEFAULT_SCPI_PORT` | the instrument's SCPI socket |
| `CAL_STEPS` | e.g. `{'S11': ('OPEN', 'SHORT', 'LOAD'), 'S21': ('ISOLATION', 'THRU')}`, in the order the instrument wants them |
| `ALLOWED_WRITES` | command prefixes `write()` may send: kit fields and calibration steps only |
| `FIELD_UNITS` | units of your kit fields, for the read-back table |

And implement:

| Method | Must |
|---|---|
| `mock(**kw)` | return the driver on a simulated instrument that answers like the real one and logs every line (tests and `--dry-run`) |
| `identify()`, `require_mode(meas)` | report `{'idn', 'mode', 'meas'}`. If the VNA isn't in VNA mode or isn't measuring `meas`, raise with an instruction for the user. **Never change the mode or the measurement.** |
| `write_kit(kit)`, `verify_kit(kit, standards)`, `preview_kit(kit)` | convert the SI `CalKit` to your fields. Write, read back, re-send dropped writes, select the user kit. Return rows `{standard, field, unit, want, got, ok}`. |
| `user_kit_selected()`, `backup_kit()`, `restore_kit(data)` | backups are JSON with `'driver': NAME` |
| `cal_measure(meas, step, settle_s)` | measure one standard and return once it is done (wait out asynchronous measurements) |
| `cal_save(meas)`, `cal_abort(meas)`, `cal_state(meas)` | apply, abort, query |
| `panel_hint(meas, step)` | the panel path for guided mode, e.g. `S21 Meas Setup > Align > Through` (`step` `'DONE'` = apply) |
| `span()`, `snapshot()` | optional |

Step names wheecal knows, and what the LibreCAL does for each:

| Step | LibreCAL |
|---|---|
| `OPEN`, `SHORT`, `LOAD` | `librecal_port` set to that standard |
| `THRU` | `librecal_port` THROUGH `through_port` |
| `ISOLATION` | both ports LOAD |

Then register the driver in `wheecal/drivers/__init__.py` (`DRIVERS`). It shows up in the GUI's Model list, in
`--driver` and in `--list-drivers`.

## Rules

- **Read the commands from the instrument itself where you can.** Use its programming guide, SCPI tree and built-in
  help. Record where each one came from in a notes page like [rsa5065n.md](rsa5065n.md).
- **Don't change the user's setup.** `write()` must refuse anything outside `ALLOWED_WRITES`: span, points, IF
  bandwidth, source power, presets, mode, measurement, clear-calibration.
- **Pace writes if the instrument drops them.** Then read back every kit field.
- **Test the driver against its mock.** `tests/test_drivers.py` checks every registered driver against the
  contract. Add tests like `tests/test_calflow.py` that assert the exact SCPI sequence of each calibration.
- **Run the first real calibration on a bench, in guided mode.** Note in the notes page what you verified.
