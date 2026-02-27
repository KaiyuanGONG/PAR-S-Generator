# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PAR-S Generator (Windows)
# Usage: pyinstaller build_windows.spec

import sys
from pathlib import Path

block_cipher = None

# Collect all source files
src_path = str(Path('src').resolve())

a = Analysis(
    ['main.py'],
    pathex=[src_path],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('simind', 'simind'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtOpenGL',
        'pyqtgraph',
        'pyqtgraph.graphicsItems',
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_agg',
        'scipy.ndimage',
        'scipy.ndimage._ni_support',
        'skimage.measure',
        'skimage.measure._marching_cubes_lewiner',
        'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'IPython',
        'jupyter',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PAR-S-Generator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # No console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PAR-S-Generator',
)
