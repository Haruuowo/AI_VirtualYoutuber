"""
Luna Interview Demo - one-command browser showcase.

Runs Luna's *real* StreamDirector (priority routing, intent detection,
speech queue) but swaps the hardware-bound adapters for web ones:

    VoiceHandler        -> WebVoiceHandler   (edge-tts audio streamed to the browser)
    MinecraftController -> WebMinecraftController (actions shown as overlay events)
    Twitch / TikTok     -> browser chat box + optional simulated viewers

Everything Luna "thinks" is emitted on an event bus and rendered live in the
Brain panel, so the pipeline is visible while you demo it.

Run:  python demo/server.py        (or double-click start_demo.bat)
"""
import asyncio
import base64
import json
import os
import random
import re
import socket
import sys
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai import AIHandler
from core import StreamDirector, WorldState, is_luna_mentioned, parse_minecraft_intent

def _find_open_port(preferred: int = 8000) -> int:
    for p in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return preferred


PORT = _find_open_port(int(os.getenv("DEMO_PORT", "8000")))
EDGE_VOICE = os.getenv("EDGE_VOICE", "en-US-AnaNeural")


# ── Event bus (thread-safe fan-out to every connected browser) ──────────────

class EventBus:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def emit(self, type_: str, **data):
        """Safe to call from any thread (LLM calls run in worker threads)."""
        msg = {"type": type_, "t": time.time(), **data}
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._broadcast(msg)))

    async def _broadcast(self, msg):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


bus = EventBus()
stats = {"messages": 0, "replies": 0, "llm_ms": [], "tts_ms": [], "started": time.time()}


# ── Adapters ────────────────────────────────────────────────────────────────

def _pick_provider():
    """Prefer a provider that will actually answer on an interview laptop."""
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    try:
        import ollama  # noqa: F401
        ollama.list()
        return "ollama"
    except Exception:
        return "offline"


OFFLINE_REPLIES = [
    "my cloud brain is napping, so you get the backup luna. still smarter than a creeper though.",
    "running on offline mode right now, but kai built me to keep the show going no matter what.",
    "no internet brain at the moment, but i am still here vibing with chat.",
]


class InstrumentedAI(AIHandler):
    """AIHandler that reports mood, latency and filtering to the Brain panel,
    and never crashes the demo (falls back to canned lines)."""

    def __init__(self, provider):
        self.offline = provider == "offline"
        super().__init__(provider="ollama" if self.offline else provider)
        if self.offline:
            self.model = "scripted fallback"
        self.last_mood = None

    def _build_system_prompt(self, mood):
        self.last_mood = mood
        return super()._build_system_prompt(mood)

    def _call_llm(self, messages, max_tokens=45, temperature=0.75):
        bus.emit("brain", stage="llm_start", provider=self.provider, model=self.model, mood=self.last_mood)
        t0 = time.perf_counter()
        try:
            if self.offline:
                raise RuntimeError("offline mode")
            raw = super()._call_llm(messages, max_tokens=max(max_tokens, 70), temperature=temperature)
            raw = re.sub(r"<think>.*?(</think>|$)", "", raw, flags=re.S).strip()
            ok = True
        except Exception as e:
            raw, ok = random.choice(OFFLINE_REPLIES), False
            bus.emit("brain", stage="llm_error", error=str(e)[:160])
        ms = round((time.perf_counter() - t0) * 1000)
        if ok:
            stats["llm_ms"].append(ms)
        bus.emit("brain", stage="llm_done", ms=ms, raw=raw, mood=self.last_mood, ok=ok)
        bus.emit("mood", mood=self.last_mood)
        return raw


class WebVoiceHandler:
    """Same interface as voice.VoiceHandler, but plays audio in the browser."""

    def __init__(self):
        self.is_speaking = False
        self.tts_enabled = True
        self._done = asyncio.Event()

    def set_audio_device(self):
        pass

    def speech_finished(self):
        self._done.set()

    async def _tts(self, text):
        import edge_tts
        audio = bytearray()
        async for chunk in edge_tts.Communicate(text, EDGE_VOICE, rate="+8%", pitch="+12Hz").stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    async def generate_and_play_audio(self, text):
        self.is_speaking = True
        try:
            t0 = time.perf_counter()
            audio_b64 = None
            try:
                audio_b64 = base64.b64encode(await self._tts(text)).decode()
            except Exception as e:
                bus.emit("brain", stage="tts_error", error=str(e)[:160])
            ms = round((time.perf_counter() - t0) * 1000)
            if audio_b64:
                stats["tts_ms"].append(ms)
            bus.emit("brain", stage="tts_done", ms=ms, voice=EDGE_VOICE, ok=bool(audio_b64))

            stats["replies"] += 1
            self._done.clear()
            bus.emit("speak", text=text, audio=audio_b64)
            bus.emit("stats", **_stats())
            if bus.clients:
                # Wait for the browser to finish playing (with a safety timeout).
                try:
                    await asyncio.wait_for(self._done.wait(), timeout=8 + len(text) * 0.12)
                except asyncio.TimeoutError:
                    pass
            await asyncio.sleep(0.6)
        finally:
            self.is_speaking = False


class WebMinecraftController:
    """Stand-in for minecraft_controller.MinecraftController (no server needed)."""

    async def send_action(self, action, params=None):
        if action == "chat":
            return
        bus.emit("mc_action", action=action, params=params or {})


# ── Simulated viewers ───────────────────────────────────────────────────────

VIEWERS = ["pixelpanda", "creeper_hugger", "nightowl_dev", "mochi_bun", "steve_irl",
           "lofi_luke", "byte_sized", "sakura_rain", "diamond_dan", "glitchqueen"]
VIEWER_LINES = [
    "luna what are you up to today?", "first time here, this is so cool", "LUNA mine some diamonds",
    "is luna actually an AI??", "luna who made you", "what's your favorite minecraft mob luna",
    "the vibes are immaculate", "luna come here", "hi chat", "luna rate my setup out of 10",
    "can you sing something luna", "luna build me a house", "lol", "luna protect me from the creepers",
    "what game should we play next", "luna tell us a fun fact", "luna do you dream?",
    "w stream", "luna explore with me", "how fast is your brain luna",
]
PLATFORMS = ["Twitch", "TikTok"]

sim = {"enabled": False, "viewers": random.randint(38, 64)}


async def simulator(director):
    while True:
        await asyncio.sleep(random.uniform(7, 13))
        sim["viewers"] = max(12, sim["viewers"] + random.randint(-3, 5))
        bus.emit("viewers", count=sim["viewers"])
        if sim["enabled"] and bus.clients:
            ingest(director, random.choice(VIEWERS), random.choice(VIEWER_LINES), random.choice(PLATFORMS))


def ingest(director, user, text, platform):
    """Entry point for every chat message - mirrors what bot.py does for Twitch/TikTok."""
    stats["messages"] += 1
    direct = platform in ("Mic", "Console", "Minecraft") or is_luna_mentioned(text)
    cmd = parse_minecraft_intent(text, username=user) if direct else None
    bus.emit("chat", user=user, text=text, platform=platform)
    bus.emit("brain", stage="route", user=user, text=text, platform=platform,
             priority="P1 direct" if direct else "P3 batch",
             reason=("typed by host" if platform == "Console" else "mentions luna") if direct else "background chatter")
    bus.emit("brain", stage="intent",
             intent=cmd["action"] if cmd else "conversation",
             params=cmd["params"] if cmd else {})
    director.pending_messages.append({"username": user, "message": text, "platform": platform})


def _stats():
    avg = lambda xs: round(sum(xs[-20:]) / len(xs[-20:])) if xs else None
    return {"messages": stats["messages"], "replies": stats["replies"],
            "llm_avg": avg(stats["llm_ms"]), "tts_avg": avg(stats["tts_ms"])}


# ── App ─────────────────────────────────────────────────────────────────────

state = {}


@asynccontextmanager
async def lifespan(_app):
    await startup()
    yield


async def startup():
    bus.loop = asyncio.get_running_loop()
    provider = _pick_provider()
    ai = InstrumentedAI(provider)
    voice = WebVoiceHandler()
    director = StreamDirector(ai, voice, WorldState(), WebMinecraftController())
    director.start()
    state.update(ai=ai, voice=voice, director=director, provider=provider)

    async def director_loop():
        while True:
            try:
                await director.process_chat_cycle()
            except Exception as e:
                bus.emit("brain", stage="error", error=str(e)[:160])
            await asyncio.sleep(0.1)

    asyncio.create_task(director_loop())
    asyncio.create_task(simulator(director))
    print(f"\n  Luna demo running ->  http://localhost:{PORT}   (brain: {provider} / {ai.model})\n")
    if "--no-browser" not in sys.argv:
        bus.loop.call_later(1.0, webbrowser.open, f"http://localhost:{PORT}")


app = FastAPI(title="Luna Demo", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    bus.clients.add(ws)
    ai = state["ai"]
    await ws.send_text(json.dumps({
        "type": "hello",
        "name": ai.character_name, "creator": ai.creator_name,
        "provider": state["provider"], "model": ai.model, "voice": EDGE_VOICE,
        "sim": sim["enabled"], "viewers": sim["viewers"], **_stats(),
    }))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "chat" and msg.get("text", "").strip():
                ingest(state["director"], msg.get("user") or "Interviewer", msg["text"].strip()[:300], "Console")
            elif kind == "speech_done":
                state["voice"].speech_finished()
            elif kind == "sim":
                sim["enabled"] = bool(msg.get("enabled"))
                bus.emit("sim", enabled=sim["enabled"])
            elif kind == "reset":
                ai.memory.clear()
                state["director"].pending_messages.clear()
                bus.emit("brain", stage="reset")
    except WebSocketDisconnect:
        pass
    finally:
        bus.clients.discard(ws)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
