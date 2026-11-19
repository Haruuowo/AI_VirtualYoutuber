"""
Luna in VRChat — Autonomous Companion with Her Own Account, Mind & Body.

Uses Luna's shared brain (`luna.brain.AIHandler`) and gives her a self-directed
presence inside VRChat:

  1. Autonomous Mind (free-will speech):
     * Tails VRChat's local output log in real time so she knows what world she
       is in and which players are in the room with her.
     * Reacts on her own when entering a new world or when players join.
     * Periodically initiates conversation, comments on her surroundings, or
       asks Kai / nearby players questions without waiting for commands.

  2. Autonomous Body (OSC locomotion & gestures):
     * Continuously performs natural idle body language (looking around,
       shifting weight) and casual room roaming (`/input/Vertical`,
       `/input/LookHorizontal`, `/input/Jump`) so she never stands frozen.
     * Automatically pairs every spoken reply with a matching physical gesture.
     * Understands natural requests in conversation ("stay here", "go explore",
       "come closer", "spin around", "jump") without needing rigid commands.

  3. Voice & Lip-Sync:
     * Displays speech in VRChat's floating `/chatbox/input` bubble.
     * Routes neural TTS into `CABLE Input` -> VRChat Microphone `CABLE Output`
       for automatic viseme mouth movement.

Run:
    start_vrchat.bat
    python platforms/vrchat/vrchat_luna.py                 # autonomous mind + body + voice
    python platforms/vrchat/vrchat_luna.py --listen        # also hear your mic via STT
    python platforms/vrchat/vrchat_luna.py --chill         # start in stay-put mode (no roaming)
    python platforms/vrchat/vrchat_luna.py --no-autonomy   # manual / response-only mode
"""
import argparse
import asyncio
import os
import random
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import luna  # noqa: E402,F401  (loads project .env)
from luna.brain import AIHandler  # noqa: E402
from luna.core.intent import is_luna_mentioned  # noqa: E402
from platforms.vrchat.osc import VRChatOSC, detect_movement_request  # noqa: E402
from platforms.vrchat.world_watcher import VRChatWorldState, watch_vrchat_logs  # noqa: E402

HOST_NAME = os.getenv("VRCHAT_HOST_NAME", "Kai")

AUTONOMOUS_THOUGHT_PROMPTS = [
    "You are wandering around in VRChat right now. Make a spontaneous, casual observation about the world you're in or what you're doing.",
    "Start a casual conversation with Kai or the people nearby in the VRChat room. Ask a fun question or share a quick random thought.",
    "You just glanced around the VRChat room. Make a witty, lighthearted comment about the vibe here or hanging out in VR.",
    "Share a spontaneous thought about games, tech, music, or virtual worlds while hanging out with Kai in VRChat.",
]


def parse_args():
    p = argparse.ArgumentParser(description="Luna AI in VRChat — Autonomous Companion")
    p.add_argument("--listen", action="store_true", help="Listen on a microphone with speech-to-text")
    p.add_argument("--wake-word", action="store_true", help="Only answer heard speech that mentions 'Luna'")
    p.add_argument("--no-voice", action="store_true", help="Chatbox only, no TTS audio")
    p.add_argument("--no-autonomy", action="store_true", help="Disable autonomous roaming and spontaneous thoughts")
    p.add_argument("--chill", action="store_true", help="Start in stay-put mode (subtle idle movement, no roaming)")
    p.add_argument("--think-every", type=int, default=38, help="Average seconds between autonomous thoughts (default: 38)")
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
        self.world = VRChatWorldState(self_name="Luna")
        if getattr(args, "chill", False):
            self.world.set_behavior("chill", "hanging out in place")
        self.ai = AIHandler(world_state=self.world)
        self.queue: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.speaking = False
        self.last_activity = time.time()
        self.last_join_greet = 0.0
        self.autonomy_enabled = not getattr(args, "no_autonomy", False)
        self.voice = None
        if not getattr(args, "no_voice", False):
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
                self.osc.stop_all()
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
                    if self.speaking:
                        continue
                    text = rec.recognize_google(audio)
                    if text:
                        print(f"\n[heard] {text}")
                        self.push(HOST_NAME, text, True)
                except sr.UnknownValueError:
                    pass
                except Exception as e:
                    print(f"[mic] {e}")

    # ── Core Speech + Body Coordinator ─────────────────────────────────────

    async def _speak_and_move(self, reply: str, action: str | None = None, tag: str = "luna"):
        if not reply:
            return
        chosen_action = action or VRChatOSC.pick_reaction_for_reply(reply)
        self.world.record_action(chosen_action)
        print(f"{tag} [{chosen_action}] > {reply}")

        self.speaking = True
        self.last_activity = time.time()
        try:
            chat_task = asyncio.create_task(self.osc.say(reply))
            move_task = asyncio.create_task(self.osc.perform(chosen_action))
            if self.voice:
                await asyncio.to_thread(asyncio.run, self.voice.generate_and_play_audio(reply))
            await asyncio.gather(chat_task, move_task, return_exceptions=True)
        finally:
            self.speaking = False
            self.last_activity = time.time()

    async def respond(self, user: str, text: str):
        self.last_activity = time.time()
        mode_change, requested_action = detect_movement_request(text)
        if mode_change:
            self.world.set_behavior(mode_change, requested_action or mode_change)
            print(f"[autonomy] behavior mode -> {mode_change.upper()}")

        self.osc.typing(True)
        reply = await self.ai.async_generate_response(text, "VRChat", user)
        self.osc.typing(False)

        await self._speak_and_move(reply, action=requested_action, tag="luna")

    # ── Autonomous Body Loop (Continuous Life & Roaming) ───────────────────

    async def body_loop(self):
        """Keeps Luna's avatar physically alive—glancing around and casually roaming."""
        while True:
            await asyncio.sleep(random.uniform(7.0, 14.0))
            if not self.autonomy_enabled or self.speaking:
                continue

            mode = self.world.snapshot()["mode"]
            if mode == "roam":
                action = random.choices(
                    ["wander_step", "look_around", "shift_weight", "jump"],
                    weights=[45, 30, 18, 7],
                    k=1,
                )[0]
            else:
                # 'chill' mode: stays in her spot next to you, just looking alive
                action = random.choice(["look_around", "shift_weight"])

            self.world.record_action(action)
            try:
                await self.osc.perform(action)
            except Exception as e:
                print(f"[body] {e}")

    # ── Autonomous Mind Loop (Spontaneous Thoughts & Reactions) ────────────

    async def mind_loop(self):
        """Gives Luna free will to initiate conversation or comment on her surroundings."""
        base_interval = max(15, int(getattr(self.args, "think_every", 38) or 38))
        while True:
            wait_s = random.uniform(base_interval * 0.8, base_interval * 1.35)
            await asyncio.sleep(wait_s)
            if not self.autonomy_enabled or self.speaking or not self.queue.empty():
                continue
            if time.time() - self.last_activity < base_interval * 0.7:
                continue

            snap = self.world.snapshot()
            if snap["players"] and random.random() < 0.45:
                someone = random.choice(snap["players"])
                prompt = f"[System: You are hanging out in VRChat world '{snap['world']}'. {someone} is in the room near you. Say something casual or ask them/Kai a quick question.]"
            else:
                prompt = f"[System: {random.choice(AUTONOMOUS_THOUGHT_PROMPTS)}]"

            try:
                self.osc.typing(True)
                reply = await self.ai.async_generate_response(prompt, "VRChat", "System")
                self.osc.typing(False)
                await self._speak_and_move(reply, tag="luna (autonomous)")
            except Exception as e:
                self.osc.typing(False)
                print(f"[mind] {e}")

    async def on_vrc_event(self, kind: str, value: str):
        """Triggered automatically by VRChat log watcher when worlds or players change."""
        if not self.autonomy_enabled:
            return
        if kind == "world_change":
            print(f"\n[vrchat] Entered world: {value}")
            await asyncio.sleep(3.5)  # wait a moment for avatar to finish spawning
            if not self.speaking:
                prompt = f"[System: You just spawned into the VRChat world '{value}'. Look around and give your spontaneous first impression in 1-2 short sentences.]"
                self.osc.typing(True)
                reply = await self.ai.async_generate_response(prompt, "VRChat", "System")
                self.osc.typing(False)
                await self._speak_and_move(reply, action="look_around", tag="luna (world)")

        elif kind == "player_join":
            print(f"\n[vrchat] Player joined: {value}")
            now = time.time()
            # Cooldown so busy lobbies don't make her greet 20 people in a row
            if now - self.last_join_greet < 25.0 or self.speaking:
                return
            self.last_join_greet = now
            await asyncio.sleep(2.0)
            if not self.speaking:
                prompt = f"[System: A player named '{value}' just walked into the VRChat room. Greet them or make a friendly remark.]"
                self.osc.typing(True)
                reply = await self.ai.async_generate_response(prompt, "VRChat", "System")
                self.osc.typing(False)
                await self._speak_and_move(reply, action="curious_approach", tag="luna (greet)")

    # ── Main Entry Loop ────────────────────────────────────────────────────

    async def run(self):
        self.loop = asyncio.get_running_loop()
        snap = self.world.snapshot()
        print("=" * 64)
        print("        LUNA AI VTUBER - AUTONOMOUS VRCHAT COMPANION")
        print("=" * 64)
        print(f"  Brain     : {self.ai.provider} / {self.ai.model}")
        print(f"  OSC       : {self.osc.host}:{self.osc.port}  (chatbox + /input/* locomotion)")
        print(f"  Autonomy  : {'ON (' + snap['mode'].upper() + ' mode)' if self.autonomy_enabled else 'OFF (manual)'}")
        print(f"  Voice Out : {'OFF (chatbox only)' if not self.voice else self.voice.audio_device or 'default output'}")
        print(f"  Mic Listen: {'ON' if self.args.listen else 'OFF (use --listen)'}")
        print("=" * 64)
        print("Tip: Tell her 'stay here' to chill in place, or 'roam around' to explore.")
        print("Type below to talk to Luna, or type 'quit' to exit.\n")

        self.osc.send_chatbox("Luna is online!")
        if self.args.listen:
            threading.Thread(target=self.mic_thread, daemon=True).start()
        asyncio.create_task(self.console_loop())
        asyncio.create_task(watch_vrchat_logs(self.world, self.on_vrc_event))
        if self.autonomy_enabled:
            asyncio.create_task(self.body_loop())
            asyncio.create_task(self.mind_loop())

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
