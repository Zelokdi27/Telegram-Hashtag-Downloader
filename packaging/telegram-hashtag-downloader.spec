# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec · Сборка portable-папки с exe для Windows"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "locales"), "locales"),
        (str(ROOT / ".env.example"), "."),
    ],
    hiddenimports=[
        "socks",
        "cryptg",
        "qrcode",
        "PIL",
        "PIL.Image",
        "telethon",
        "telethon.errors",
        "telethon.tl.types",
        "telethon.tl.functions",
        "telethon.network",
        "telethon.crypto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "sphinx"],
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
    name="TelegramHashtagDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TelegramHashtagDownloader",
)
