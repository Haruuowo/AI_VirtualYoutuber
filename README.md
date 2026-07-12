# AI VTuber Chatbot

A chaotic, funny AI VTuber that responds to live chat on Twitch and TikTok simultaneously.

**Repository:** [https://github.com/Haruuowo/AI_VirtualYoutuber](https://github.com/Haruuowo/AI_VirtualYoutuber)

## Setup

1. **Clone or download this repository.**
   - If using Git: `git clone https://github.com/Haruuowo/AI_VirtualYoutuber.git`

2. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

3. **Get API Keys:**

   - **Twitch:**
     - Go to [Twitch Developer Console](https://dev.twitch.tv/console/apps)
     - Create an app to get Client ID
     - Get OAuth token: Use [Twitch Token Generator](https://twitchtokengenerator.com/) or similar

   - **TikTok Live:**
     - Username: Your TikTok username
     - Session ID: Use browser dev tools to get sessionid from TikTok cookies

   - **Anthropic Claude:**
     - Sign up at [Anthropic](https://www.anthropic.com/)
     - Get API key from dashboard

   - **ElevenLabs:**
     - Sign up at [ElevenLabs](https://elevenlabs.io/)
     - Get API key and choose a voice ID

4. **Virtual Audio Cable (for VTube Studio lip sync):**
   - Install [VB-Audio Virtual Cable](https://www.vb-audio.com/Cable/)
   - Set AUDIO_DEVICE in .env to the virtual cable name (e.g., "CABLE Input")

5. **Configure environment:**
   - Copy `.env.example` to `.env`
   - Fill in all the API keys and settings

6. **Run:**
   ```
   python main.py
   ```

## Features

- Listens to Twitch and TikTok chat simultaneously
- AI responds every 10 seconds to the latest message
- TTS audio playback through specified device
- Ignores bots and short messages
- Fills silence with random comments

# Luna Minecraft Setup

## 1. Install Node.js
Download from https://nodejs.org (LTS version)

## 2. Install bot dependencies
```
cd minecraft
npm install
```

## 3. Add to your .env
```
MINECRAFT_ENABLED=true
MC_HOST=your-server.aternos.me
MC_PORT=25565
MC_USERNAME=Luna
MC_VERSION=1.20.1
```

## 4. Start your Aternos server
- Go to aternos.org, start your server
- Copy the server address into MC_HOST

## 5. Run the Minecraft bot (separate terminal)
```
cd minecraft
node minecraft_bot.js
```

## 6. Run Luna as normal
```
python main.py
```

Luna will automatically connect to Minecraft and start playing.
She reacts to: dying, taking damage, mobs nearby, nightfall, mining, picking up items.
She decides what to do every 8 seconds: mine, explore, flee, attack, look around, or idle.

---

# `labubuIMAI` branch additions

This branch extends the original repo **additively** — nothing is removed. New features:

| Layer | What changed |
|-------|-------------|
| LLM   | Hardcoded Groq → configurable provider menu (OpenRouter / DeepSeek / Groq) via `LLM_PROVIDER` env var |
| TTS   | New `qwen3tts` engine alongside existing `gptsovits` / `elevenlabs` / `edge` |
| Voice | Optional `JAPANESE_MODE` — Luna returns `{"en":"...","ja":"..."}`, English is displayed, Japanese is spoken |
| Web   | Optional **Web Studio** — browser-based streamer overlay with avatar + chat + stats + settings |
| Config | `.env.example` added (was missing from `main`), documenting all new vars |

## Setup (labubuIMAI branch)

1. `git checkout labubuIMAI`
2. `python -m venv .venv && .venv\Scripts\activate` (or `source .venv/bin/activate` on macOS/Linux)
3. `python -m pip install -r requirements.txt`
4. `cp .env.example .env` (or copy on Windows: `copy .env.example .env`)
5. Fill in `.env` — at minimum `LLM_PROVIDER`, the matching API key, and `TTS_ENGINE`
6. `python main.py` for live mode, or `python main.py --web` for the Web Studio

## LLM providers

| Provider | Env config | Default model |
|----------|-----------|---------------|
| OpenRouter | `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY=…` | `deepseek/deepseek-v4-flash` |
| DeepSeek (direct) | `LLM_PROVIDER=deepseek`, `DEEPSEEK_API_KEY=…` | `deepseek-chat` |
| Groq (legacy) | `LLM_PROVIDER=groq`, `GROQ_API_KEY=…` | `llama-3.3-70b-versatile` |

The SDK swap is `from groq import Groq` → `from openai import OpenAI` — all three providers are OpenAI-compatible, so call sites are unchanged.

## TTS engines

| Engine | Local? | Setup |
|--------|--------|-------|
| `edge` (cloud, free) | no | none |
| `qwen3tts` (local GPU) | yes — needs RTX 8 GB+ for 0.6B | see [Qwen3-TTS server setup](#qwen3-tts-server-setup) below |
| `gptsovits` (local :9880) | yes | already supported |
| `elevenlabs` (cloud BYOK) | no | already supported |

Fallback chain: primary engine → `edge-tts`. Set `EDGE_VOICE=ja-JP-NanamiNeural` if you want a Japanese fallback voice for the JP dub pipeline.

## Qwen3-TTS server setup

> Heavy install — keep it in a **separate venv** from the bot.

```bash
python -m venv C:\qwen3-tts-server
C:\qwen3-tts-server\Scripts\activate
python -m pip install torch --index-url https://download.pytorch.org/whl/cu121
python -m pip install qwen-tts
git clone https://github.com/pasky/Qwen3-TTS-Openai-Fastapi C:\qwen3-server
cd C:\qwen3-server
python -m pip install -e ".[api]"
python -m api.main          # serves http://localhost:8880
```

Smoke test:

```
curl http://localhost:8880/health
curl -X POST http://localhost:8880/v1/audio/speech -H "Content-Type: application/json" ^
  -d "{\"model\":\"qwen3-tts\",\"voice\":\"Ono_Anna\",\"input\":\"こんにちは\",\"response_format\":\"wav\"}" ^
  --output test.wav
```

Preset voices: `Ryan` (EN male), `Vivian` (ZH female), `Serena`, `Dylan`, `Eric`, `Aiden`, `Ono_Anna` (JP female), `Sohee`, `Uncle_Fu`.

### Voice cloning (optional)

For a custom voice instead of a preset:
1. Capture 3–10 seconds of clean reference audio (WAV, 22 kHz mono, no music/noise)
2. Set `QWEN_TTS_REF_AUDIO=/path/to/ref.wav` and `QWEN_TTS_REF_TEXT=exact transcript of the clip`
3. The client calls `/v1/audio/speech/upload` automatically when both are set
4. Requires the **0.6B-Base** model (not CustomVoice)

## Japanese pipeline (`JAPANESE_MODE=true`)

When enabled, Luna's system prompt asks the LLM to respond with `{"en":"...","ja":"..."}`. The bot:

1. Prints the English text in the console / Twitch chat
2. Sends the Japanese text to the TTS engine for speech

Recommended Japanese TTS:
- `QWEN_TTS_VOICE=Ono_Anna`, `QWEN_TTS_LANGUAGE=Japanese` (local, requires Qwen3-TTS server)
- `EDGE_VOICE=ja-JP-NanamiNeural` (cloud fallback)

## Web Studio (`--web` / `WEB_STUDIO=true`)

A browser-based streamer overlay at `http://127.0.0.1:8000`. Replaces the live Twitch/TikTok/Mic input with a chat box in the browser — chat with Luna directly. Audio plays in the browser, no system speaker needed.

```
python main.py --web
# → opens FastAPI on 127.0.0.1:8000
# → visit http://127.0.0.1:8000 in a browser
```

If you want Luna to actually stream, set `WEB_STUDIO=false` and use the live mode (`python main.py` with Twitch/TikTok creds).

### Layout

```
┌── AVATAR (60%) ──────┐ ┌── CHAT (40%) ────┐
│  Live2D via pixi-    │ │  scrollback      │
│  live2d-display      │ │  + input box     │
└──────────────────────┘ └──────────────────┘
┌── STATS / DEBUG BAR (bottom, full width) ──────┐
│ provider | model | tts | speaking | queue |    │
│ latency | total | idle | vram | [⚙ Settings]   │
└────────────────────────────────────────────────┘
```

### Adding your own Live2D model

1. Drop your `.model3.json` + `.moc3` + texture `.png` files + (optional) `.physics3.json` / `.motion3.json` / `.exp3.json` into `web/assets/`
2. Rename the .model3.json to `Luna.model3.json` (or create `web/assets/index.json` with `{"url":"your-model.model3.json"}`)
3. Refresh the browser — the avatar canvas replaces the placeholder

Live2D model binaries are gitignored — only `web/assets/.gitkeep` is tracked. Pixi v7 and `pixi-live2d-display` load from jsDelivr CDN; no npm install required.

### Settings modal (full feature set)

- API keys (OpenRouter / DeepSeek / Groq) — password-type with show/hide
- LLM provider radio + LLM model free-text + "Fetch models" button → dropdown populated from `list_models()`
- TTS engine dropdown + Qwen3 voice preset dropdown
- `JAPANESE_MODE` toggle
- Voice-clone reference audio file upload + transcript field
- Memory viewer (short-term deque + long-term facts) + clear buttons
- "Save & reload handlers" — atomically rewrites `.env`, reinstantiates AIHandler / VoiceHandler in place, no restart

### WebSocket protocol (for custom clients)

Connect to `ws://127.0.0.1:8000/ws`. JSON messages in / out:

| Direction | Type | Purpose |
|-----------|------|---------|
| in | `{type:"hello"}` | request config + memory snapshot |
| in | `{type:"chat", username, message}` | send a chat message |
| in | `{type:"settings_save", ...}` | update `.env` + rebuild handlers |
| in | `{type:"clear_memory", scope:"short"\|"long"}` | wipe memory |
| out | `{type:"chat_msg", platform, username, message}` | incoming chat (echo) |
| out | `{type:"luna_response", en, ja, latency_ms, banter}` | Luna's reply |
| out | `{type:"stats", provider, model, tts, is_speaking, queue, ...}` | 1/sec stats push |
| out | `{type:"memory", short_term[], long_term[]}` | memory snapshot |
| out | `{type:"config", ...}` | current `.env` state (keys masked) |
| out | `{type:"models", list[]}` | `list_models()` results |
| out | **binary**: 4-byte ASCII engine_id + WAV/MP3 bytes | TTS audio for browser playback (Web Audio) |