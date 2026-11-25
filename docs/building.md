# Building

## Run from source

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m wheecal                 # GUI; any argument runs the command line
pip install -e .                   # optional: a `wheecal` command on your PATH
```

You need a Python with Tk:
- **python.org installers** include Tk.
- **Homebrew:** `brew install python python-tk`.
- **Debian/Ubuntu:** `apt install python3-tk`.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The GUI test needs a display: it runs under `xvfb-run` in CI and is skipped elsewhere unless `WHEECAL_GUI_TESTS=1`.
The tests use simulated instruments only, and a temporary settings file (never yours).

## Standalone app (PyInstaller)

```bash
pip install -r requirements-build.txt
pyinstaller --noconfirm packaging/wheecal.spec
```

The output is in `dist/`, and the bundle includes Tk:

| System | Output |
|---|---|
| macOS | `dist/wheecal.app`, plus the `dist/wheecal/` folder. `wheecal.app/Contents/MacOS/wheecal --help` runs the command line. |
| Windows | `dist\wheecal\wheecal.exe` (GUI) and `wheecal-cli.exe` (command line with a console) |
| Linux | `dist/wheecal/wheecal` (GUI without arguments, command line with) |

Quick check of a build:

```bash
WHEECAL_CONFIG=/tmp/wheecal-check/config.json dist/wheecal/wheecal --version
dist/wheecal/wheecal --dry-run --mock-librecal --load-kit librecal --calibrate s21 --yes --settle 0
```

The first command must print the version and create the settings file at that path.

PyInstaller builds for the system it runs on. To build for all three, use the release workflow.

## Releases

`.github/workflows/release.yml` runs on a pushed tag `v*`, e.g. `v0.1.0` (semantic versioning):
1. Builds the app with PyInstaller on macOS, Windows and Ubuntu.
2. Checks that each build starts.
3. Creates a GitHub Release for the tag with these files:
   - `wheecal-<tag>-macos.zip` (`wheecal.app`)
   - `wheecal-<tag>-windows.zip`
   - `wheecal-<tag>-linux.tar.gz`

It uses only the workflow's `GITHUB_TOKEN`. Pull requests that change `packaging/`, `requirements-build.txt` or
the workflow run the same builds and checks but don't publish anything.

To cut a release:
1. Set `__version__` in `wheecal/__init__.py`.
2. Move the CHANGELOG's *Unreleased* entries under the new version and merge to `main`.
3. Push the tag:
   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```

`.github/workflows/ci.yml` runs the tests on every push and pull request (Ubuntu with the GUI test, and macOS). It
also builds and starts the Linux app.
