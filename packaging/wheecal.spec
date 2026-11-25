# PyInstaller spec: pyinstaller packaging/wheecal.spec  (from the repository root; see docs/building.md)
#   dist/wheecal/        one folder with wheecal (GUI; also takes CLI arguments) and wheecal-cli (console)
#   dist/wheecal.app     macOS app bundle (macOS only)
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from wheecal import __version__  # noqa: E402

hidden = ['serial.tools.list_ports', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox']

a = Analysis([str(root / 'packaging' / 'launch.py')], pathex=[str(root)], hiddenimports=hidden,
             excludes=['numpy', 'PIL', 'matplotlib'], noarchive=False)
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='wheecal', console=sys.platform not in ('win32', 'darwin'),
          disable_windowed_traceback=False, argv_emulation=False)
exes = [gui]
if sys.platform == 'win32':
    # The windowed wheecal.exe has no console for the command line; ship a console twin.
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name='wheecal-cli', console=True))

coll = COLLECT(*exes, a.binaries, a.datas, name='wheecal')

if sys.platform == 'darwin':
    app = BUNDLE(coll, name='wheecal.app', bundle_identifier='io.github.coolnamesalltaken.wheecal',
                 version=__version__, info_plist={'NSHighResolutionCapable': True,
                                                  'CFBundleShortVersionString': __version__})
