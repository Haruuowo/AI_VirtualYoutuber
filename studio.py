"""
studio.py — Luna Web Studio (FastAPI)

Serves the browser front-end at /web and a WebSocket at /ws that bridges
the chat input, Luna's responses, TTS audio bytes, live stats, and a
settings/config API. Designed to run alongside the existing main.py loop
handlers (AIHandler + VoiceHandler) without modifying their behavior when
WEB_STUDIO is off.

Run via:
    python main.py --web
or:
    WEB_STUDIO=true python main.py
"""
import asyncio
import json
import os
import time
import random
import struct
import threading
from pathlib import Path
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Local imports (same handlers as main.py)
from ai import AIHandler
from voice import VoiceHandler


# ── Path setup ───────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
WEB_DIR = ROOT / "web"
ENV_PATH = ROOT / ".env"

# ── App state ────────────────────────────────────────
# Singletons shared across the WS connection(s)
ai_handler: AIHandler = None         # type: ignore
voice_handler: VoiceHandler = None   # type: ignore
pending_messages: deque = deque()
stats = {
    "provider": "",
    "model": "",
    "tts": "",
    "is_speaking": False,
    "queue": 0,
    "last_latency_ms": None,
    "total_responded": 0,
    "idle_countdown_s": None,
    "vram_used_mb": None,
}
last_response_time = time.time()
last_incoming_time = time.time()
connected_clients: set[WebSocket] = set()


# ── FastAPI app ──────────────────────────────────────
app = FastAPI(title="Luna Studio")
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")


@app.get("/")
async def root():
    return RedirectResponse(url="/web/index.html")


@app.get("/api/assets/list")
async def list_assets():
    """Probe web/assets/ for a Live2D model3.json."""
    assets = WEB_DIR / "assets"
    if not assets.exists():
        return {"model3_url": None}
    for name in ("Luna.model3.json", "luna.model3.json", "model.model3.json"):
        p = assets / name
        if p.exists():
            return {"model3_url": f"/web/assets/{name}"}
    # Fall back to a manifest file
    idx = assets / "index.json"
    if idx.exists():
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            url = data.get("url")
            if url:
                return {"model3_url": f"/web/assets/{url}"}
        except Exception:
            pass
    return {"model3_url": None}


@app.get("/api/models")
async def api_models():
    """Return the list of LLM models the current provider exposes."""
    if ai_handler is None:
        return {"list": []}
    return {"list": ai_handler.list_models()}


@app.get("/api/config")
async def api_config():
    """Return current env config. Sensitive keys are masked."""
    return _build_config_snapshot()


@app.post("/api/upload_ref")
async def upload_ref(file: UploadFile = File(...)):
    """Save a voice-clone reference audio file to web/assets/ or a temp path."""
    assets = WEB_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    out_path = assets / f"ref_audio_{int(time.time())}_{file.filename}"
    try:
        content = await file.read()
        out_path.write_bytes(content)
        return {"ok": True, "path": str(out_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── WebSocket ────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    try:
        # Send initial snapshot
        await ws.send_json({"type": "config", **_build_config_snapshot()})
        await ws.send_json({"type": "memory", **_build_memory_snapshot()})
        await ws.send_json({"type": "stats", **stats})

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            await _handle_ws_message(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        connected_clients.discard(ws)


async def _handle_ws_message(ws: WebSocket, msg: dict):
    mtype = msg.get("type")
    if mtype == "hello":
        await ws.send_json({"type": "config", **_build_config_snapshot()})
        await ws.send_json({"type": "memory", **_build_memory_snapshot()})
    elif mtype == "chat":
        username = (msg.get("username") or "You").strip()[:24]
        text = (msg.get("message") or "").strip()[:500]
        if not text:
            return
        pending_messages.append({
            "platform": "Web",
            "username": username,
            "message": text,
        })
        global last_incoming_time
        last_incoming_time = time.time()
        # Echo to all clients so everyone sees the user's message
        await _broadcast_json({"type": "chat_msg", "platform": "Web",
                               "username": username, "message": text})
    elif mtype == "settings_save":
        ok, err = _apply_settings(msg)
        if ok:
            await _broadcast_json({"type": "settings_saved"})
        else:
            await ws.send_json({"type": "error", "text": err})
    elif mtype == "fetch_models":
        models = ai_handler.list_models() if ai_handler else []
        await ws.send_json({"type": "models", "list": models})
    elif mtype == "clear_memory":
        scope = msg.get("scope", "short")
        _clear_memory(scope)
        await ws.send_json({"type": "memory", **_build_memory_snapshot()})


# ── Broadcast helpers ───────────────────────────────
async def _broadcast_json(obj: dict):
    """Send a JSON message to every connected client."""
    dead = []
    payload = json.dumps(obj)
    for ws in connected_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.discard(ws)


async def _broadcast_binary(wav_bytes: bytes, engine_id: str = "edge"):
    """Send a binary frame: 4-byte ASCII engine_id + WAV bytes."""
    prefix = engine_id.ljust(4)[:4].encode("ascii")
    frame = prefix + wav_bytes
    dead = []
    for ws in connected_clients:
        try:
            await ws.send_bytes(frame)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.discard(ws)


# Voice relay hook — passed to VoiceHandler.web_relay so its _play_* methods
# can stream WAV bytes to the browser instead of playing locally.
async def voice_relay(wav_bytes: bytes, engine_id: str):
    await _broadcast_binary(wav_bytes, engine_id)


# ── Config snapshot ─────────────────────────────────
def _build_config_snapshot() -> dict:
    """Current config + masked API key presence flags."""
    def is_set(name):
        v = os.getenv(name, "").strip()
        return bool(v) and not v.startswith("your_")
    return {
        "llm_provider": os.getenv("LLM_PROVIDER", "groq"),
        "llm_model": os.getenv("LLM_MODEL", ""),
        "tts_engine": os.getenv("TTS_ENGINE", "edge"),
        "qwen_tts_voice": os.getenv("QWEN_TTS_VOICE", "Ryan"),
        "qwen_tts_language": os.getenv("QWEN_TTS_LANGUAGE", "English"),
        "japanese_mode": os.getenv("JAPANESE_MODE", "").lower() == "true",
        "qwen_tts_ref_audio": os.getenv("QWEN_TTS_REF_AUDIO", ""),
        "qwen_tts_ref_text": os.getenv("QWEN_TTS_REF_TEXT", ""),
        "openrouter_key_set": is_set("OPENROUTER_API_KEY"),
        "deepseek_key_set": is_set("DEEPSEEK_API_KEY"),
        "groq_key_set": is_set("GROQ_API_KEY"),
        "memory": _build_memory_snapshot(),
    }


def _build_memory_snapshot() -> dict:
    if ai_handler is None:
        return {"short_term": [], "long_term": []}
    return {
        "short_term": list(ai_handler.memory),
        "long_term": ai_handler.longterm,
    }


def _clear_memory(scope: str):
    if ai_handler is None:
        return
    if scope in ("short", "short_term"):
        ai_handler.memory.clear()
        ai_handler._save_short_term()
    elif scope in ("long", "long_term"):
        ai_handler.longterm.clear()
        ai_handler._save_long_term()


# ── Settings save ────────────────────────────────────
def _apply_settings(msg: dict) -> tuple[bool, str]:
    """Update .env on disk and rebuild the handlers in place."""
    try:
        # Load existing .env lines (preserve unknown keys)
        existing: dict[str, str] = {}
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                existing[k.strip()] = v

        # Apply updates from the client
        def up(key, value, leave_blank_if_unset=False):
            if value is None:
                if leave_blank_if_unset: return
                return
            if isinstance(value, bool):
                existing[key] = "true" if value else "false"
            else:
                v = str(value).strip()
                if v:
                    existing[key] = v
                elif key in existing and not leave_blank_if_unset:
                    existing[key] = v

        up("LLM_PROVIDER", msg.get("llm_provider"))
        up("LLM_MODEL", msg.get("llm_model"))
        up("TTS_ENGINE", msg.get("tts_engine"))
        up("QWEN_TTS_VOICE", msg.get("qwen_tts_voice"))
        up("JAPANESE_MODE", msg.get("japanese_mode"))
        up("QWEN_TTS_REF_TEXT", msg.get("qwen_tts_ref_text"))

        # Only update keys the user touched (app.js skips unchanged masked keys)
        if msg.get("openrouter_key"):
            existing["OPENROUTER_API_KEY"] = msg["openrouter_key"]
        if msg.get("deepseek_key"):
            existing["DEEPSEEK_API_KEY"] = msg["deepseek_key"]
        if msg.get("groq_key"):
            existing["GROQ_API_KEY"] = msg["groq_key"]

        # Atomic rewrite
        tmp = ENV_PATH.with_suffix(".env.tmp")
        lines = [f"{k}={v}" for k, v in existing.items()]
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(ENV_PATH))

        # Reload env into the process
        load_dotenv(dotenv_path=str(ENV_PATH), override=True)

        # Rebuild handlers in place. The main loop holds references to these
        # objects; we mutate their attributes so the loop sees the new state.
        _rebuild_handlers()
        return True, ""
    except Exception as e:
        return False, str(e)


def _rebuild_handlers():
    """Re-initialize AIHandler and VoiceHandler without restarting the loop."""
    global ai_handler, voice_handler
    # Save memory state from the old handler so it survives the swap
    saved_memory = list(ai_handler.memory) if ai_handler else []
    saved_longterm = ai_handler.longterm if ai_handler else []

    new_ai = AIHandler()
    new_ai.memory = deque(saved_memory, maxlen=30)
    new_ai.longterm = saved_longterm
    ai_handler = new_ai

    new_voice = VoiceHandler()
    new_voice.device_id = voice_handler.device_id if voice_handler else None
    new_voice.web_relay = voice_relay
    new_voice.is_speaking = False
    voice_handler = new_voice

    # Stats reflect new config
    stats["provider"] = os.getenv("LLM_PROVIDER", "groq")
    stats["model"] = new_ai.model
    stats["tts"] = os.getenv("TTS_ENGINE", "edge")

    # Wire the relay on the existing instance too (belt + suspenders)
    if voice_handler is not None:
        voice_handler.web_relay = voice_relay


# ── Main loop (idle banter + chat dispatch) ─────────
async def _main_loop():
    """Run alongside main.py's main() — processes pending_messages and idle banter."""
    global last_response_time, last_incoming_time
    loop = asyncio.get_event_loop()
    last_response_time = loop.time()
    last_incoming_time = loop.time()

    while True:
        current_time = loop.time()
        stats["queue"] = len(pending_messages)
        stats["idle_countdown_s"] = max(0, int(45 - (current_time - last_incoming_time))) if not pending_messages else None

        if pending_messages and not voice_handler.is_speaking:
            last_incoming_time = current_time
            await asyncio.sleep(0.5)
            batch = []
            while pending_messages and len(batch) < 5:
                batch.append(pending_messages.popleft())

            if len(batch) == 1:
                words = batch[0]["message"].strip().split()
                if len(words) <= 1 and random.random() < 0.35:
                    await asyncio.sleep(0.1)
                    continue
                t0 = time.time()
                response = ai_handler.generate_response(
                    batch[0]["message"], batch[0]["platform"], batch[0]["username"]
                )
            else:
                t0 = time.time()
                response = ai_handler.generate_batch_response(batch)

            if response:
                latency = int((time.time() - t0) * 1000)
                stats["last_latency_ms"] = latency
                stats["total_responded"] += 1
                display, speech = ai_handler.split_response(response)
                await _broadcast_json({
                    "type": "luna_response",
                    "en": display,
                    "ja": speech if speech != display else None,
                    "platform": "Luna",
                    "latency_ms": latency,
                    "banter": False,
                })
                if speech is not None:
                    await voice_handler.generate_and_play_audio(speech)
                last_response_time = current_time

        elif (current_time - last_incoming_time >= 45
              and current_time - last_response_time >= 45):
            if not voice_handler.is_speaking:
                t0 = time.time()
                if random.random() < 0.3:
                    comment = ai_handler.continue_last_thought()
                else:
                    comment = ai_handler.get_random_comment()
                if comment:
                    latency = int((time.time() - t0) * 1000)
                    stats["last_latency_ms"] = latency
                    stats["total_responded"] += 1
                    display, speech = ai_handler.split_response(comment)
                    await _broadcast_json({
                        "type": "luna_response",
                        "en": display,
                        "ja": speech if speech != display else None,
                        "platform": "Luna",
                        "latency_ms": latency,
                        "banter": True,
                    })
                    if speech is not None:
                        await voice_handler.generate_and_play_audio(speech)
                last_response_time = current_time

        await asyncio.sleep(0.1)


# ── Stats broadcaster ───────────────────────────────
async def _stats_broadcaster():
    """Push the stats dict to every client once per second."""
    while True:
        stats["provider"] = os.getenv("LLM_PROVIDER", "groq")
        stats["model"] = ai_handler.model if ai_handler else ""
        stats["tts"] = os.getenv("TTS_ENGINE", "edge")
        stats["is_speaking"] = voice_handler.is_speaking if voice_handler else False
        stats["vram_used_mb"] = _vram_used_mb()
        await _broadcast_json({"type": "stats", **stats})
        await asyncio.sleep(1.0)


def _vram_used_mb() -> int | None:
    """Best-effort VRAM report. Returns None if unavailable."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            timeout=2, text=True
        ).strip()
        return int(out.splitlines()[0]) if out else None
    except Exception:
        return None


# ── Entry point ─────────────────────────────────────
def run_web_studio():
    """Initialize handlers and start uvicorn with the main loop + stats broadcaster."""
    global ai_handler, voice_handler

    # Load env (main.py has already loaded it, but be defensive)
    load_dotenv(dotenv_path=str(ENV_PATH), override=True)

    ai_handler = AIHandler()
    voice_handler = VoiceHandler()
    voice_handler.set_audio_device()
    voice_handler.web_relay = voice_relay  # wire the WS broadcast as the audio sink

    stats["provider"] = os.getenv("LLM_PROVIDER", "groq")
    stats["model"] = ai_handler.model
    stats["tts"] = os.getenv("TTS_ENGINE", "edge")

    print(f"[Web Studio] Luna awake on http://127.0.0.1:{os.getenv('WEB_STUDIO_PORT','8000')}")
    print(f"[LLM] provider={stats['provider']} model={stats['model']}")
    print(f"[TTS] engine={stats['tts']} (web relay: streaming WAV bytes to browser)")

    import uvicorn

    # Start background tasks on the same event loop
    @app.on_event("startup")
    async def _start_bg():
        asyncio.create_task(_main_loop())
        asyncio.create_task(_stats_broadcaster())

    host = os.getenv("WEB_STUDIO_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_STUDIO_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="warning")