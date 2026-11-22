# Luna — Modular AI VTuber & Multi-Platform Agent

An event-driven, real-time AI companion and streamer built in Python (`asyncio`) and C++ (`OpenVR`). Luna separates her **Core Brain** (`luna/`) from her **Platforms / Bodies** (`platforms/`), allowing the exact same sassy-smug personality, Hugging Face dataset retriever, persistent memory, and neural voice engine to drive a **full 6-DOF SteamVR VRChat avatar**, a Live2D web stage, Desktop Studio, Twitch/TikTok stream, or Minecraft bot.

---

## Project Structure

```text
AI_VirtualYoutuber/
├── luna/                         # CORE BRAIN (Reusable anywhere)
│   ├── __init__.py               # Exports AIHandler, VoiceHandler, persona config
│   ├── brain.py                  # LLM client (Groq ⇄ Ollama failover), action parser & rolling memory
│   ├── dataset_retriever.py      # Zero-VRAM Hugging Face Neuro-sama style retriever & PersistentMemory
│   ├── datasets/                 # 753-row Hugging Face (Neuro-sama-QnA + neuro.alpha) + Sassy/Smug dataset
│   │   ├── build_dataset.py      # Downloads, adapts & merges HF datasets into sassy_neuro_dataset.jsonl
│   │   └── sassy_neuro_dataset.jsonl
│   ├── voice.py                  # Neural TTS (Edge-TTS / ElevenLabs / GPT-SoVITS) + 48kHz stereo resampling
│   ├── filter.py                 # Output moderation & TTS sanitization
│   ├── persona.json              # Sassy & smug character bio, rules & [action:...] body tags
│   └── core/                     # StreamDirector, Priority+TTL SpeechQueue, Intent Router
│
├── platforms/                    # BODIES & INTEGRATIONS (Each powered by luna/)
│   ├── vrchat/                   # Autonomous 6-DOF SteamVR & OSC VRChat Companion
│   │   ├── vrchat_luna.py        # Main autonomous VRChat runner (Eyes, Ears, Mind & Body loops)
│   │   ├── osc.py                # 40Hz cosine-eased OSC locomotion, chatbox & Colibri Blind expressions
│   │   ├── vr_tracker.py         # 60Hz procedural 6-DOF VR Head + Left/Right Hands animator (UDP :27015)
│   │   ├── vision.py             # YOLOv8-nano real-time avatar tracking + Gemini/Qwen Vision + OpenCV
│   │   ├── world_watcher.py      # Live VRChat output_log tailer (world changes & player joins/leaves)
│   │   └── openvr_driver/        # Custom C++ SteamVR Virtual HMD + Left/Right Hand Controllers driver
│   │       ├── driver_luna.cpp   # C++17 OpenVR IServerTrackedDeviceProvider (LUNA-HMD-001, CTRL-L, CTRL-R)
│   │       ├── driver_luna.dll   # Compiled x64 SteamVR driver binary
│   │       ├── build_and_install.bat
│   │       └── install_driver.py # Installs driver_luna.dll into SteamVR & configures steamvr.vrsettings
│   ├── web_stage/                # Browser Live2D avatar stage + real-time WebAudio lip-sync
│   ├── desktop_studio/           # CustomTkinter desktop control center & PyInstaller spec
│   ├── minecraft/                # Autonomous Mineflayer Node.js bot & HTTP controller
│   ├── live_stream/              # Full Twitch + TikTok + Mic stream director
│   └── vercel_chat/              # Standalone serverless web chatbot export
│
├── scripts/                      # CLI utilities
│   ├── chat.py                   # Fast terminal text/voice chat loop
│   └── test_chat.py              # Interactive model & audio device tester
│
├── start_demo.bat                # 1-click Browser Live2D Showcase
├── start_studio.bat              # 1-click Desktop Studio GUI
├── start_my_vrchat.bat           # 1-click Launcher for YOUR Main VRChat (Profile 0, Desktop Mode)
├── start_vrchat.bat              # 1-click Launcher for LUNA's VRChat (Profile 1, SteamVR 6-DOF Mode + Brain)
├── .env.example
└── requirements.txt
```

### Reusing Luna's Brain in Any Script

```python
from luna import AIHandler, VoiceHandler

ai = AIHandler()
voice = VoiceHandler()

reply = ai.generate_response("Why are you staring at me like that?", platform="VRChat", username="Kai")
print(f"Luna [{ai.last_action}]: {reply}")
```

---

## Visual Showcase

### 1. Interactive Browser Stage & Live2D Demo
A self-contained browser showcase with real-time WebGL Live2D avatar animation, audio-driven RMS lip-sync, and a PictoChat-inspired live chat interface.

![Luna Browser Demo Showcase](docs/screenshots/demo_showcase.png)

### 2. AI VTuber Studio Desktop App
A lightweight desktop control center (`CustomTkinter`) allowing you to customize VTuber personality, switch AI models (Groq / Ollama), configure voice routing (Edge-TTS / ElevenLabs / Virtual Audio Cable), test speech in a built-in sandbox, and launch services with one click.

![AI VTuber Studio Desktop App](docs/screenshots/studio_app.png)

---

## Running Modes

| Mode | One-Click Launcher | Command | Description |
| :--- | :--- | :--- | :--- |
| **Browser Live2D Stage** | `start_demo.bat` | `python platforms/web_stage/server.py` | Full visual stage + Live2D avatar + real-time lip-sync in your browser at `http://localhost:8000`. |
| **Desktop Studio App** | `start_studio.bat` | `python platforms/desktop_studio/app.py` | All-in-one GUI to customize persona, voice, audio cable, API keys, and launch services. |
| **VRChat 6-DOF Companion** | `start_vrchat.bat` | `python platforms/vrchat/vrchat_luna.py --vr --listen` | Launches SteamVR with `driver_luna.dll` (Virtual 6-DOF Headset + Left/Right Hands), Luna's VRChat client (Profile 1, OSC `9002`), YOLOv8 vision, Whisper STT, and VB-Cable voice. |
| **Your Main VRChat** | `start_my_vrchat.bat` | `launch.exe --profile=0 --no-vr` | Launches your personal VRChat account in Desktop Mouse & Keyboard mode alongside Luna on the same PC. |
| **Full Stream Director** | — | `python platforms/live_stream/main.py` | Connects to Twitch, TikTok, Mic, VTube Studio audio cable, and the Minecraft bridge. |
| **Minecraft Standalone** | — | `python platforms/minecraft/play_minecraft.py` | Launches the Node.js Mineflayer bridge and autonomous Minecraft companion loop. |
| **Terminal Chat** | — | `python scripts/chat.py` | Lightweight CLI loop for testing prompts and persona responses (`--voice` optional). |

---

## Autonomous 6-DOF VRChat Companion (`platforms/vrchat/`)

Luna runs as an **independent player in her own VRChat window (`--profile=1`, OSC port `9002`)** right alongside your main VRChat window (`--profile=0 --no-vr`, OSC port `9000`) on a single PC—no physical VR headset required.

### 1. Custom SteamVR OpenVR 6-DOF Virtual Headset & Hands Driver (`driver_luna.dll`)
- Located in [`platforms/vrchat/openvr_driver/`](platforms/vrchat/openvr_driver/), `driver_luna.cpp` compiles into `SteamVR/drivers/luna/bin/win64/driver_luna.dll`.
- Registers **3 Virtual Tracked Devices** in SteamVR:
  - **`LUNA-HMD-001` (Virtual VR Headset):** Full 3D head tilting (cutesy-smug head cock), nodding, leaning forward/back, and neck rotation independent of her feet.
  - **`LUNA-CTRL-L` & `LUNA-CTRL-R` (Virtual Left & Right Hand Controllers):** Full 6-DOF 3D arm and hand IK control.
- [`platforms/vrchat/vr_tracker.py`](platforms/vrchat/vr_tracker.py) streams 60 Hz 6-DOF poses (21 floats / 84-byte UDP packet on `127.0.0.1:27015`), driving:
  - **2-Stage 3D VR Hug (`hug`):** Opens both arms wide (`0..0.85s`) while stepping closer, then leans in, wraps both arms around you, blushes (`BlushLock=True`), and gently sways side-to-side.
  - **Conversational Hand-Talking:** Automatically raises and gestures with her hands and nods her head while her TTS voice is playing.
  - **Expressive 3D VR Poses & `Colibri Blind` Reactions:** Sassy hands-on-hips (`stare_smug`), dramatic two-hand shrug (`sassy_react`), high 3D hand wave (`wave`), forward point & handheld stick prop (`point` / `stick` $\rightarrow$ `Sticc`), 3D chest clap (`clap`), overhead cheer (`cheer`), covering blushing cheeks (`blush` / `headpat`), nose boop (`boop`), and upper-body idol dance (`dance`).

### 2. AI Eyes (YOLOv8-Nano Real-Time Tracking + Multimodal Vision)
- [`platforms/vrchat/vision.py`](platforms/vrchat/vision.py) captures Luna's VRChat window in the background via Win32 `PrintWindow` (`PW_RENDERFULLCONTENT`).
- **Local YOLOv8-nano (`yolov8n.pt`):** Detects nearby avatars in `~25 ms` on CPU (0 API tokens), filters out her own 3rd-person head, and smoothly turns her gaze and 3D VR head to face whoever is talking to her.
- **OpenCV Wall Avoidance & Cloud Scene Vision:** Automatically turns away from blank walls while roaming, and uses **Gemini 2.5 Flash / Groq Qwen3.8 Vision** to inspect avatars, outfits, mirrors, and room details when asked (*"what do you see?"*, *"look at my avatar"*).

### 3. Quick Start (Two VRChat Windows on One PC)
1. **Enable OSC in Luna's VRChat:** Open the in-game Action Menu (`R`) $\rightarrow$ **Options** $\rightarrow$ **OSC** $\rightarrow$ **Enabled**.
2. **Audio Routing (VB-Cable):**
   - Set Luna's VRChat microphone to **`CABLE Output (VB-Audio Virtual Cable)`** (`PushToTalk` mode; Luna holds `/input/Voice` automatically while speaking).
   - Luna resamples her neural TTS to `48000 Hz` stereo and plays it into **`CABLE Input`**.
3. **Launch Both Windows:**
   ```bat
   :: 1. Open YOUR main VRChat account in Desktop Mode (Profile 0)
   start_my_vrchat.bat

   :: 2. Open LUNA in SteamVR 6-DOF Mode + AI Brain + Whisper STT (Profile 1)
   start_vrchat.bat
   ```

---

## Key Engineering Highlights

- **Zero-VRAM Hugging Face Dataset Retriever & Long-Term Memory ([`luna/dataset_retriever.py`](luna/dataset_retriever.py)):** Searches **753 rows** from Hugging Face's **`neifuisan/Neuro-sama-QnA`** and **`Braite/neuro.alpha`** merged with curated Sassy/Smug VRChat banter ([`sassy_neuro_dataset.jsonl`](luna/datasets/sassy_neuro_dataset.jsonl)) in `~1 ms` on CPU using BM25/TF-IDF token scoring. Also persists personal facts about users across sessions in `long_term_memory.json`.
- **Brain-to-Body `[action:...]` Tagging ([`luna/brain.py`](luna/brain.py)):** Every LLM generation chooses both her spoken comeback and a physical VRChat action (`[action:stare_smug]`, `[action:hug]`, `[action:point]`, `[action:sassy_react]`, `[action:wave]`, `[action:dance]`, etc.), stripping the tag cleanly before TTS and executing the 6-DOF VR pose + OSC animation simultaneously.
- **Priority + TTL Speech Queue ([`luna/core/speech_queue.py`](luna/core/speech_queue.py)):** Prevents audio overlap and stale responses. Critical events (`P0`) and direct mentions (`P1`) jump ahead of background chat batches (`P3`) and idle banter (`P4`).
- **Automatic LLM Failover ([`luna/brain.py`](luna/brain.py)):** Seamlessly fails over across Groq model pools (`openai/gpt-oss-120b` $\leftrightarrow$ `openai/gpt-oss-20b`) and local `Ollama` inference.
