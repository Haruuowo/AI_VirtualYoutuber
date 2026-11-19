"""
Luna in VRChat.

Uses the exact same brain as every other platform (luna.brain.AIHandler) and
gives her a body in VRChat:

    Luna's brain ──► reply text ──► VRChat OSC  /chatbox/input   (speech bubble over her head)
                              └──► edge-tts ──► "CABLE Input"  ──► VRChat mic = "CABLE Output"
                                                                   (VRChat auto lip-syncs her avatar)

Ways to talk to her:
    * type in this console                      (always on)
    * speak into a microphone  (--listen)       speech-to-text -> Luna replies

Run:
    python platforms/vrchat/vrchat_luna.py                 # console + chatbox + voice
    python platforms/vrchat/vrchat_luna.py --listen        # also hear your mic
    python platforms/vrchat/vrchat_luna.py --no-voice      # chatbox text only
    python platforms/vrchat/vrchat_luna.py --list-devices  # show audio device names

Settings (.env, all optional):
    VRCHAT_AUDIO_DEVICE=CABLE Input     output device Luna's voice is played into
    VRCHAT_LISTEN_DEVICE=               microphone name to listen on (blank = system default)
    VRCHAT_OSC_HOST=127.0.0.1
    VRCHAT_OSC_PORT=9000
"""
import argparse
import asyncio
import os
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import luna  # noqa: E402,F401  (loads project .env)
from luna.brain import AIHandler  # noqa: E402
from luna.core.intent import is_luna_mentioned  # noqa: E402
from platforms.vrchat.osc import VRChatOSC  # noqa: E402

HOST_NAME = os.getenv("VRCHAT_HOST_NAME", "Kai")


def parse_args():
    p = argparse.ArgumentParser(description="Luna AI in VRChat (OSC chatbox + voice)")
    p.add_argument("--listen", action="store_true", help="Listen on a microphone with speech-to-text")
    p.add_argument("--wake-word", action="store_true", help="Only answer heard speech that mentions 'Luna'")
    p.add_argument("--no-voice", action="store_true", help="Chatbox only, no TTS")
    p.add_argument("--idle", type=int, default=0, help="Say something on her own after N quiet seconds (0 = off)")
    p.add_argument("--list-devices", action="store_true", help="Print audio device names and exit")
    return p.parse_args()


def list_devices():
    import sounddevice as sd
    print("\nOUTPUT devices (use for VRCHAT_AUDIO_DEVICE):")
    for d in sd.query_devices():
        if d["max_output_channels"] > 0:
            print("   ", d["name"])
    try:
        import speech_recognition as sr
        print("\nINPUT devices (use for VRCHAT_LISTEN_DEVICE):")
        for name in sr.Microphone.list_microphone_names():
            print("   ", name)
    except Exception as e:
        print(f"\n(speech_recognition unavailable: {e})")


class LunaInVRChat:
    def __init__(self, args):
        self.args = args
        self.osc = VRChatOSC()
        self.ai = AIHandler()
        self.queue: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.speaking = False
        self.voice = None
        if not args.no_voice:
            from luna.voice import VoiceHandler
            self.voice = VoiceHandler()
            self.voice.audio_device = os.getenv("VRCHAT_AUDIO_DEVICE", "CABLE Input").strip()
            self.voice.set_audio_device()

    # ── Inputs ─────────────────────────────────────────────────────────────
    def push(self, user: str, text: str, heard: bool):
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (user, text, heard))

    async def console_loop(self):
        while True:
            text = (await asyncio.to_thread(input, "you > ")).strip()
            if text.lower() in ("quit", "exit"):
                self.osc.clear()
                os._exit(0)
            if text:
                await self.queue.put((HOST_NAME, text, False))

    def mic_thread(self):
        import speech_recognition as sr
        wanted = os.getenv("VRCHAT_LISTEN_DEVICE", "").strip().lower()
        index = None
        if wanted:
            for i, name in enumerate(sr.Microphone.list_microphone_names()):
                if wanted in name.lower():
                    index = i
                    break
            if index is None:
                print(f"[mic] '{wanted}' not found, using default microphone")
        rec = sr.Recognizer()
        rec.dynamic_energy_threshold = True
        with sr.Microphone(device_index=index) as source:
            rec.adjust_for_ambient_noise(source, duration=1)
            print("[mic] listening...")
            while True:
                try:
                    audio = rec.listen(source, phrase_time_limit=12)
                    if self.speaking:          # don't react to her own voice
                        continue
                    text = rec.recognize_google(audio)
                    if text:
                        print(f"\n[heard] {text}")
                        self.push(HOST_NAME, text, True)
                except sr.UnknownValueError:
                    pass
                except Exception as e:
                    print(f"[mic] {e}")

    # ── Output ─────────────────────────────────────────────────────────────
    async def respond(self, user: str, text: str):
        self.osc.typing(True)
        reply = await self.ai.async_generate_response(text, "VRChat", user)
        self.osc.typing(False)
        if not reply:
            return
        print(f"luna > {reply}")
        self.speaking = True
        try:
            chat = asyncio.create_task(self.osc.say(reply))
            if self.voice:
                # VoiceHandler blocks while audio plays, so keep it off the event loop.
                await asyncio.to_thread(asyncio.run, self.voice.generate_and_play_audio(reply))
            await chat
        finally:
            self.speaking = False

    async def idle_loop(self):
        while True:
            await asyncio.sleep(self.args.idle)
            if self.queue.empty() and not self.speaking:
                line = await self.ai.async_get_random_comment()
                if line:
                    print(f"luna (idle) > {line}")
                    self.speaking = True
                    try:
                        chat = asyncio.create_task(self.osc.say(line))
                        if self.voice:
                            await asyncio.to_thread(asyncio.run, self.voice.generate_and_play_audio(line))
                        await chat
                    finally:
                        self.speaking = False

    async def run(self):
        self.loop = asyncio.get_running_loop()
        print("=" * 60)
        print("            LUNA AI VTUBER - VRCHAT MODE")
        print("=" * 60)
        print(f"  Brain    : {self.ai.provider} / {self.ai.model}")
        print(f"  OSC      : {self.osc.host}:{self.osc.port}  (/chatbox/input)")
        print(f"  Voice    : {'OFF (chatbox only)' if not self.voice else self.voice.audio_device or 'default output'}")
        print(f"  Mic      : {'ON' if self.args.listen else 'OFF (use --listen)'}")
        print("=" * 60)
        print("Type to talk to Luna. 'quit' to exit.\n")

        self.osc.send_chatbox("Luna is online!")
        if self.args.listen:
            threading.Thread(target=self.mic_thread, daemon=True).start()
        asyncio.create_task(self.console_loop())
        if self.args.idle > 0:
            asyncio.create_task(self.idle_loop())

        while True:
            user, text, heard = await self.queue.get()
            if heard and self.args.wake_word and not is_luna_mentioned(text):
                continue
            try:
                await self.respond(user, text)
            except Exception as e:
                self.osc.typing(False)
                print(f"[error] {e}")


def main():
    args = parse_args()
    if args.list_devices:
        list_devices()
        return
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(LunaInVRChat(args).run())
    except KeyboardInterrupt:
        print("\nLuna logged off.")


if __name__ == "__main__":
    main()
