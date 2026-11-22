"""
Installs `driver_luna.dll` into SteamVR's `drivers/luna/` directory and configures
`Steam/config/steamvr.vrsettings` so SteamVR boots straight into Luna's Virtual
6-DOF Headset + Left/Right Hand Controllers without needing a physical VR headset.
"""
import json
import os
import shutil
from pathlib import Path

STEAMVR_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR")
STEAM_CONFIG_DIR = Path(r"C:\Program Files (x86)\Steam\config")
HERE = Path(__file__).resolve().parent
DLL_SRC = HERE / "driver_luna.dll"


def install():
    if not DLL_SRC.exists():
        raise FileNotFoundError(f"Compiled DLL not found at {DLL_SRC}")

    driver_root = STEAMVR_DIR / "drivers" / "luna"
    bin_win64 = driver_root / "bin" / "win64"
    settings_dir = driver_root / "resources" / "settings"
    bin_win64.mkdir(parents=True, exist_ok=True)
    settings_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(DLL_SRC, bin_win64 / "driver_luna.dll")

    manifest = {
        "alwaysActivate": True,
        "name": "luna",
        "directory": "",
        "resourceOnly": False,
        "hmd_presence": ["*.*"],
    }
    (driver_root / "driver.vrdrivermanifest").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    default_settings = {
        "driver_luna": {
            "enable": True,
            "loadPriority": 999,
        }
    }
    (settings_dir / "default.vrsettings").write_text(
        json.dumps(default_settings, indent=2), encoding="utf-8"
    )

    # Merge into Steam\config\steamvr.vrsettings
    STEAM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    vrsettings_path = STEAM_CONFIG_DIR / "steamvr.vrsettings"
    cfg = {}
    if vrsettings_path.exists():
        try:
            cfg = json.loads(vrsettings_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    steamvr_sec = cfg.setdefault("steamvr", {})
    steamvr_sec["activateMultipleDrivers"] = True
    steamvr_sec["forcedDriver"] = "luna"
    steamvr_sec["requireHmd"] = False
    steamvr_sec["neverKillProcesses"] = True

    luna_sec = cfg.setdefault("driver_luna", {})
    luna_sec["enable"] = True

    vrsettings_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"[install] Installed driver_luna.dll -> {bin_win64 / 'driver_luna.dll'}")
    print(f"[install] Configured SteamVR settings -> {vrsettings_path}")


if __name__ == "__main__":
    install()
