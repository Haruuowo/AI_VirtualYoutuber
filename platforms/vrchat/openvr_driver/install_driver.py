"""
Installs `driver_luna.dll` into SteamVR's `drivers/luna/` directory and configures
`Steam/config/steamvr.vrsettings` so SteamVR boots straight into Luna's Virtual
6-DOF Headset + Left/Right Hand Controllers without needing a physical VR headset.
"""
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

STEAMVR_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR")
STEAM_CONFIG_DIR = Path(r"C:\Program Files (x86)\Steam\config")
HERE = Path(__file__).resolve().parent
DLL_SRC = HERE / "driver_luna.dll"


def ensure_steamvr_chaperone():
    """
    Ensures Steam/config/chaperone_info.vrchap exists with calibrated Standing + Seated
    floor coordinates for universeID '2' so SteamVR & VRChat know y=0 is the room floor.
    """
    STEAM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    chap_path = STEAM_CONFIG_DIR / "chaperone_info.vrchap"
    data = {}
    if chap_path.exists():
        try:
            data = json.loads(chap_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    universes = data.get("universes")
    if not isinstance(universes, list):
        universes = []

    has_u2 = any(isinstance(u, dict) and str(u.get("universeID")) == "2" for u in universes)
    if not has_u2:
        now_str = datetime.now(timezone.utc).strftime("%a %b %d %Y %H:%M:%S.%f")[:-3]
        universes.append({
            "collision_bounds": [
                [[-1.5, 0.0, -1.5], [-1.5, 2.43, -1.5], [1.5, 2.43, -1.5], [1.5, 0.0, -1.5]],
                [[1.5, 0.0, -1.5], [1.5, 2.43, -1.5], [1.5, 2.43, 1.5], [1.5, 0.0, 1.5]],
                [[1.5, 0.0, 1.5], [1.5, 2.43, 1.5], [-1.5, 2.43, 1.5], [-1.5, 0.0, 1.5]],
                [[-1.5, 0.0, 1.5], [-1.5, 2.43, 1.5], [-1.5, 2.43, -1.5], [-1.5, 0.0, -1.5]],
            ],
            "play_area": [3.0, 3.0],
            "seated": {
                "translation": [0.0, 1.68, 0.0],
                "yaw": 0.0,
            },
            "standing": {
                "translation": [0.0, 0.0, 0.0],
                "yaw": 0.0,
            },
            "time": now_str,
            "universeID": "2",
        })
        out = {
            "jsonid": "chaperone_info",
            "universes": universes,
            "version": 5,
        }
        chap_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"[install] Wrote calibrated Standing Chaperone -> {chap_path}")


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
    ensure_steamvr_chaperone()
    print(f"[install] Installed driver_luna.dll -> {bin_win64 / 'driver_luna.dll'}")
    print(f"[install] Configured SteamVR settings -> {vrsettings_path}")


if __name__ == "__main__":
    install()
