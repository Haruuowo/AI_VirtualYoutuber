# -*- mode: python ; coding: utf-8 -*-
import os
import customtkinter

block_cipher = None

ctk_path = os.path.dirname(customtkinter.__file__)
ROOT = os.path.abspath(os.path.join(SPECPATH, '..', '..'))

datas = [
    (ctk_path, 'customtkinter'),
    (os.path.join(ROOT, 'platforms', 'web_stage', 'static'), 'platforms/web_stage/static'),
    (os.path.join(ROOT, 'luna', 'persona.json'), 'luna'),
    (os.path.join(ROOT, 'docs', 'screenshots'), 'docs/screenshots'),
    (os.path.join(ROOT, '.env.example'), '.'),
]

a = Analysis(
    [os.path.join(SPECPATH, 'app.py')],
    pathex=[ROOT],
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
        'luna',
        'luna.brain',
        'luna.voice',
        'luna.filter',
        'luna.core',
        'luna.core.speech_queue',
        'luna.core.intent',
        'luna.core.world_state',
        'luna.core.director',
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

