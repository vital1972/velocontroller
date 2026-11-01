# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

python_base = sys.base_prefix
tcl_root = os.path.join(python_base, "tcl")
dll_root = os.path.join(python_base, "DLLs")

hiddenimports = [
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'tkinter.simpledialog',
    'win32api',
    'win32con',
    'win32gui',
]
hiddenimports += collect_submodules('serial')
hiddenimports += collect_submodules('keyboard')

datas = [
    (os.path.join(tcl_root, 'tcl8.6'), os.path.join('tcl', 'tcl8.6')),
    (os.path.join(tcl_root, 'tk8.6'), os.path.join('tcl', 'tk8.6')),
]

binaries = [
    (os.path.join(dll_root, 'tcl86t.dll'), '.'),
    (os.path.join(dll_root, 'tk86t.dll'), '.'),
]


a = Analysis(
    ['pedal_controller_windows.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['pyinstaller_hooks'],
    hooksconfig={
        "tkinter": {
            "module_collection_mode": "py"
        }
    },
    runtime_hooks=['pyi_rth_tkinter_fix.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VeloController',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['velocontroller.ico'],
)
