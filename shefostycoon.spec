# -*- mode: python ; coding: utf-8 -*-
# Сборка: из папки проекта (лучше через .venv — см. build_portable.bat):
#   python -m PyInstaller --clean -y shefostycoon.spec
import glob
import os

block_cipher = None

spec_dir = os.path.dirname(os.path.abspath(SPEC))

datas = []
for pattern in ("*.png", "*.mp3", "*.jpg", "*.jpeg", "*.webp", "*.ogg", "*.wav"):
    for path in glob.glob(os.path.join(spec_dir, pattern)):
        if os.path.isfile(path):
            datas.append((path, "."))

# Шрифты (TTF/OTF) — добавляем из `fonts/` и из корня, если вдруг.
for pattern in ("*.ttf", "*.otf"):
    for base in (spec_dir, os.path.join(spec_dir, "fonts")):
        for path in glob.glob(os.path.join(base, pattern)):
            if os.path.isfile(path):
                datas.append((path, "."))

a = Analysis(
    ["Tycoon.py"],
    pathex=[spec_dir],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SHEFOS_Tycoon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
