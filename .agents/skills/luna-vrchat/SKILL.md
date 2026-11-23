---
name: luna-vrchat
description: >-
  Runbook and architecture guide for developing, debugging, testing, and
  launching Luna (AI Virtual YouTuber) in VRChat 6-DOF SteamVR mode, tuning her
  Gawr Gura RVC v2 voice, managing her Vedal & Neuro-sama persona/datasets, and
  building the custom OpenVR driver (`driver_luna.dll`). Use whenever working on
  Luna's VRChat agent, voice pipeline, 6-DOF body/gaze tracking, or relaunching
  her in VRChat.
---

# Luna AI Virtual YouTuber (VRChat 6-DOF Runbook)

## Dual-Account VRChat Setup (Single Laptop)

- **Main User (`DumAlcoholicPa3`)**: `--profile=0 --no-vr`, OSC `9000 -> 9001`. **NEVER** close, mute, or modify `--profile=0`.
- **Luna (`kairu!`)**: `--profile=1`, SteamVR 6-DOF 3-point mode, `854x480`, OSC `9002 -> 9003`, avatar `Colibri Blind` (`avtr_9b48413e-07f7-493e-9e34-85a3ce1f4079`).
- **Auto-Mute**: `vrchat_luna.py` automatically mutes ONLY `--profile=1` in Windows Volume Mixer via `pycaw` (`comtypes.CoInitialize()`).
- **World-Ears & Chatbox OCR**: Uses non-blocking Windows WASAPI Loopback (`pyaudiowpatch`) on default audio output with `1.6s` self-echo suppression, plus `RapidOCR` (`rapidocr-onnxruntime`) for reading floating VRChat chatbox bubbles.
- **EAC Note**: Easy Anti-Cheat blocks scripts from force-closing `VRChat.exe`. If Luna's VRChat window needs a full client restart (e.g., after registry changes), ask the user to click **X** on Luna's `854x480` window first.

## Launching & Verifying Luna

```powershell
# Launch Luna in VRChat 6-DOF VR mode with mic + world-ears + chatbox OCR
python -u platforms/vrchat/vrchat_luna.py --listen --vr
```

- Default autonomy mode is **`roam`**; in-game voice/chatbox commands switch modes:
  - `"chill"` / `"stay here"` -> `chill` (stationary gaze + expressions)
  - `"follow"` / `"follow me"` / `"come here"` -> `follow` (closed-loop curved following via `steer_walk`)
  - `"roam"` / `"wander"` -> `roam` (follows recent speaker, otherwise explores & avoids walls)
- Always verify modified Python modules compile cleanly before relaunching:
  ```powershell
  python -m py_compile luna/brain.py platforms/vrchat/vr_tracker.py platforms/vrchat/vision.py platforms/vrchat/osc.py platforms/vrchat/vrchat_luna.py
  ```

## Key Architecture & Guardrails (Do Not Regress!)

### 1. SteamVR 6-DOF Driver (`platforms/vrchat/openvr_driver/`)
- **3-Point VR Default**: `driver_luna.cpp` exposes Oculus Rift HMD (`/proximity` = `true`, standing eye height `1.68m`) + Oculus Quest 2 Touch controllers.
- **DO NOT enable virtual FBT trackers unconditionally**: `LUNA-TRK-WAIST/LFOOT/RFOOT` are opt-in via `LUNA_ENABLE_FBT=1` only; unconditional 6-point tracking freezes VRChat waiting for manual FBT calibration.
- **Smooth Turning**: `_configure_vrchat_registry_for_luna()` sets `VRC_INPUT_COMFORT_TURNING_h2182102264 = 0` so right-stick smooth turning works alongside left-stick walking.
- **Rebuild Driver** (only when editing `driver_luna.cpp`): run `platforms/vrchat/openvr_driver/build_and_install.bat` (Visual Studio 2022 x64).

### 2. Natural 6-DOF Motion, Gaze & Facial Expressions
- **`platforms/vrchat/vr_tracker.py`**: 60 Hz UDP streamer (`127.0.0.1:27015`, 46 floats). Uses `_organic()` 3-frequency non-harmonic idle waves, 2D head gaze (`set_gaze_target(offset_x, offset_y)`), and per-frame exponential smoothing across all 18 head/hand channels so poses never snap in 1 frame.
- **`platforms/vrchat/vision.py`**: Local RTX 4060 CUDA YOLOv8-nano (`conf=0.12`, `imgsz=480`) + VRChat glowing nametag pill fallback returning `(found, offset_x, offset_y, height_ratio)` via `track_target_person_2d()`.
- **`platforms/vrchat/osc.py`**: Sends **both** SteamVR Touch thumbsticks and OSC `/input/*` axes/buttons. `trigger_colibri_blink()` and `apply_cutesy_smug_face()` preserve `_last_eye_pitch` / `_last_eye_yaw` and avoid random facial parameter scrambling.

### 3. Voice Pipeline (`luna/voice.py`, `luna/rvc_infer/converter.py`)
- Active `.env`: `TTS_ENGINE=gura` (`luna/models/rvc/GuraTalkV2.pth`), `EDGE_VOICE=en-US-AnaNeural`, `RVC_PITCH_SHIFT=0.0`, `RVC_PROTECT=0.33`.
- Uses parallel per-clause Edge TTS (`+2Hz..+13Hz`) concatenated in memory into **one single continuous RVC pass** on CUDA (`torch==2.10.0+cu128`), with 3-frame `medfilt` on voiced F0 and `155–410 Hz` pitch clamp to prevent voice cracks.
- **Do not** push F0 multipliers or Edge prosody above `+13Hz` (causes voice cracks), and keep VRChat registry noise suppression (`0`) and noise gate (`0.0` double) disabled.

### 4. Brain, Personality & Datasets (`luna/brain.py`, `luna/persona.json`, `luna/dataset_retriever.py`)
- **Persona**: Strictly platonic **Vedal & Neuro-sama** creator/creation banter (no flirting, no pet names).
- **Datasets**:
  - `luna/datasets/sassy_neuro_dataset.jsonl` (766 rows): CPU TF-IDF/BM25 retrieval injects top 2 matched + 1 curated few-shot examples into every prompt.
  - `luna/datasets/long_term_memory.json`: Auto-saves personal facts (`PersistentMemory`) across sessions.
- **LLM Failover**: Rotates across 4 separate Groq daily token pools (`qwen/qwen3.8-27b`, `openai/gpt-oss-safeguard-20b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`) before falling back to local Ollama. Rolling memory sent per request is capped at the last 6 messages to conserve daily tokens.
