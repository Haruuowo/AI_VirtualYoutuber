# -*- mode: python ; coding: utf-8 -*-
import os
import customtkinter

block_cipher = None

ctk_path = os.path.dirname(customtkinter.__file__)

datas = [
    (ctk_path, 'customtkinter'),
    ('demo/static', 'demo/static'),
    ('data', 'data'),
    ('docs/screenshots', 'docs/screenshots'),
    ('.env.example', '.'),
]

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'uvicorn',
        'fastapi',
        'websockets',
        'sounddevice',
        'soundfile',
        'edge_tts',
        'groq',
        'customtkinter',
        'core',
        'core.speech_queue',
        'core.intent',
        'core.world_state',
        'core.director',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test'],
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
    name='LunaStudio',
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LunaStudio',
)

