# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Joy4_Novel.
빌드: pyinstaller Joy4_Novel.spec --noconfirm
출력: dist\\Joy4_Novel\\Joy4_Novel.exe
"""

from PyInstaller.utils.hooks import collect_all

# tkinterdnd2 는 TCL 확장 DLL/tcl 파일들을 번들해야 함
dnd_datas, dnd_binaries, dnd_hidden = collect_all("tkinterdnd2")

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=dnd_binaries,
    datas=dnd_datas,
    hiddenimports=dnd_hidden + [
        "PIL._tkinter_finder",
        "bs4",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "pandas", "scipy",
        "pytest", "IPython", "notebook",
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
    name="Joy4_Novel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # 콘솔창 숨김
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,           # 아이콘 파일 있으면 "icon.ico" 로 교체
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Joy4_Novel",
)
