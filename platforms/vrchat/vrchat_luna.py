"""
Luna in VRChat — Autonomous Companion with Eyes (AI Vision), Ears (Mic + World Audio),
Mind (Spontaneous Thoughts), and Body (OSC Locomotion).

Capabilities:
  1. AI Vision (`platforms/vrchat/vision.py`):
     * Captures Luna's VRChat window in real time (even when behind your main window).
     * Uses multimodal `qwen/qwen3.8-27b` + OpenCV wall detection so she can
       describe avatars, outfits, mirrors, and room details, avoid walking into
       walls, and visually turn/walk toward people she sees on screen!

  2. Dual Ears (Your Mic + Other VRChat Players):
     * Listens to your microphone (`VRCHAT_LISTEN_DEVICE` / default mic).
     * Also listens to nearby players talking in VRChat if `VRCHAT_WORLD_LISTEN_DEVICE`
       (e.g. Stereo Mix / Voicemeeter / Secondary Virtual Cable) is set or auto-detected.

  3. Autonomous Mind & Body:
     * Tails VRChat's local log file for world changes and player joins/leaves.
     * Roams, glances around, hops, approaches people she sees, and initiates
       conversations on her own.
"""
import argparse
import asyncio
import os
import random
import subprocess
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
from platforms.vrchat.vision import VRChatVision, capture_luna_vrchat_frame, is_facing_blank_wall, is_visual_question  # noqa: E402
from platforms.vrchat.world_watcher import VRChatWorldState, default_vrc_log_dir, watch_vrchat_logs  # noqa: E402

HOST_NAME = os.getenv("VRCHAT_HOST_NAME", "Kai")


def _find_vrc_dir() -> Path | None:
    env_dir = os.getenv("VRC_INSTALL_PATH", "").strip()
    candidates = [
        Path(env_dir) if env_dir else None,
        Path(r"D:\SteamLibrary\steamapps\common\VRChat"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\VRChat"),
        Path(r"E:\SteamLibrary\steamapps\common\VRChat"),
    ]
    for c in candidates:
        if c and (c / "launch.exe").exists():
            return c
    return None


def _kill_other_luna_brains():
    """Ensures only one vrchat_luna.py process runs at a time."""
    if sys.platform != "win32":
        return
    my_pid = os.getpid()
    cmd = (
        f"Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
        f"Where-Object {{ $_.ProcessId -ne {my_pid} -and $_.CommandLine -match 'vrchat_luna' }} | "
        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)


def _count_vrchat_processes() -> int:
    if sys.platform != "win32":
        return 0
    res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq VRChat.exe", "/NH"], capture_output=True, text=True)
    return sum(1 for line in res.stdout.splitlines() if "vrchat.exe" in line.lower())


def _newest_log_is_profile1() -> bool:
    folder = default_vrc_log_dir()
    if not folder.exists():
        return False
    logs = sorted(folder.glob("output_log_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return False
    try:
        head = logs[0].read_text(encoding="utf-8", errors="ignore")[:12000]
        return "Arg: --profile=1" in head
    except OSError:
        return False


def ensure_luna_vrchat_window():
    """
    Launches ONLY Luna's VRChat window (Profile 1, OSC port 9002) if it isn't
    already open. Your main VRChat account (Profile 0) is opened separately
    by you via Steam so the two Easy Anti-Cheat splash screens never collide.
    """
    vrc_dir = _find_vrc_dir()
    if not vrc_dir:
        return
    launch_exe = str(vrc_dir / "launch.exe")
    luna_args = [
        launch_exe, "--profile=1", "--no-vr",
        "-screen-width", "854", "-screen-height", "480", "-screen-fullscreen", "0",
        "--fps=30", "--osc=9002:127.0.0.1:9003",
    ]

    count = _count_vrchat_processes()
    if count == 0 or (count == 1 and not _newest_log_is_profile1()):
        print("[launcher] Opening LUNA's VRChat window (Profile 1, 854x480, OSC 9002)...")
        subprocess.Popen(luna_args, cwd=str(vrc_dir))
    else:
        print("[launcher] Luna's VRChat window (Profile 1) is already open!")

AUTONOMOUS_THOUGHT_PROMPTS = [
    "Look at what your eyes currently see in front of you in VRChat and make a natural, witty comment about it.",
    "Start a casual conversation with Kai or the people nearby in the VRChat room based on where you are and what you see.",
    "You just glanced around the VRChat room. Comment on an avatar, detail, or the vibe you see in front of you.",
    "Share a spontaneous thought or ask Kai / nearby players a fun question while hanging out in VRChat.",
]


def parse_args():
    p = argparse.ArgumentParser(description="Luna AI in VRChat — Autonomous Companion with Vision & Voice")
    p.add_argument("--host", type=str, default=None, help="VRChat OSC target IP (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="VRChat OSC target port (default: 9002 or VRCHAT_OSC_PORT)")
    p.add_argument("--ptt", action="store_true", default=False, help="Send /input/Voice OSC push-to-talk (only use if VRChat Toggle Voice is OFF)")
    p.add_argument("--listen", action="store_true", help="Listen on microphone (and optional world audio) via STT")
    p.add_argument("--wake-word", action="store_true", help="Only answer heard speech that mentions 'Luna'")
    p.add_argument("--no-voice", action="store_true", help="Chatbox only, no TTS audio")
    p.add_argument("--no-vision", action="store_true", help="Disable AI vision screen capture")
    p.add_argument("--no-autonomy", action="store_true", help="Disable autonomous roaming and spontaneous thoughts")
    p.add_argument("--chill", action="store_true", help="Start in stay-put mode (subtle idle movement, no roaming)")
    p.add_argument("--think-every", type=int, default=38, help="Average seconds between autonomous thoughts (default: 38)")
    p.add_argument("--no-launch", action="store_true", help="Do not auto-launch missing VRChat windows on startup")
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
        print("\nINPUT devices (use for VRCHAT_LISTEN_DEVICE or VRCHAT_WORLD_LISTEN_DEVICE):")
        for name in sr.Microphone.list_microphone_names():
            print("   ", name)
    except Exception as e:
        print(f"\n(speech_recognition unavailable: {e})")


class LunaInVRChat:
    def __init__(self, args):
        self.args = args
        self.osc = VRChatOSC(host=getattr(args, "host", None), port=getattr(args, "port", None))
        self.world = VRChatWorldState(self_name=os.getenv("VRCHAT_LUNA_USER", "Luna") or "Luna")
        if getattr(args, "chill", False):
            self.world.set_behavior("chill", "hanging out in place")
        self.ai = AIHandler(world_state=self.world)
        self.vision = None if getattr(args, "no_vision", False) else VRChatVision()
        self.queue: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.speaking = False
        self.last_activity = time.time()
        self.last_join_greet = 0.0
        self.autonomy_enabled = not getattr(args, "no_autonomy", False)
        self.voice = None
        if not getattr(args, "no_voice", False):
            from luna.voice import VoiceHandler
            vrc_cable = os.getenv("VRCHAT_AUDIO_DEVICE", "CABLE Input").strip()
            if os.getenv("VRCHAT_MONITOR", "true").lower() in ("false", "0", "no"):
                self.voice = VoiceHandler()
                self.voice.audio_device = vrc_cable
            else:
                self.voice = VoiceHandler(mirror_device=vrc_cable)
            self.voice.set_audio_device()

    # ── Inputs (Console + Your Mic + VRChat World Players Audio) ───────────

    def push(self, user: str, text: str, heard: bool):
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (user, text, heard))

    async def console_loop(self):
        while True:
            try:
                text = (await asyncio.to_thread(input, "you > ")).strip()
            except EOFError:
                return
            if text.lower() in ("quit", "exit"):
                self.osc.stop_all()
                self.osc.clear()
                os._exit(0)
            if text:
                await self.queue.put((HOST_NAME, text, False))

    def _start_stt_listener(self, device_query: str, speaker_label: str, tag: str):
        """Runs a background Speech-to-Text listener on a specific input device."""
        import speech_recognition as sr
        index = None
        names = sr.Microphone.list_microphone_names()
        if device_query:
            q = device_query.lower()
            for i, name in enumerate(names):
                if q in name.lower():
                    index = i
                    print(f"[{tag}] bound to '{name}' (index {i})")
                    break
            if index is None:
                print(f"[{tag}] device '{device_query}' not found; skipping {tag}")
                return
        rec = sr.Recognizer()
        rec.dynamic_energy_threshold = True
        try:
            with sr.Microphone(device_index=index) as source:
                rec.adjust_for_ambient_noise(source, duration=1)
                print(f"[{tag}] listening for {speaker_label}...")
                while True:
                    try:
                        audio = rec.listen(source, phrase_time_limit=12)
                        if self.speaking:
                            continue
                        text = rec.recognize_google(audio)
                        if text:
                            # Attribute to a nearby player in the room if heard from world audio
                            who = speaker_label
                            if tag == "world-ears":
                                players = [p for p in self.world.snapshot()["players"] if p.lower() != HOST_NAME.lower()]
                                if players:
                                    who = f"Nearby Player ({', '.join(players[:2])})"
                            print(f"\n[{tag} heard {who}] {text}")
                            self.push(who, text, True)
                    except sr.UnknownValueError:
                        pass
                    except Exception as e:
                        print(f"[{tag}] {e}")
        except Exception as e:
            print(f"[{tag}] could not open audio input: {e}")

    def start_all_ears(self):
        """Starts listening to your microphone (and optional VRCHAT_WORLD_LISTEN_DEVICE if explicitly configured)."""
        mic_dev = os.getenv("VRCHAT_LISTEN_DEVICE", "").strip()
        threading.Thread(
            target=self._start_stt_listener,
            args=(mic_dev, HOST_NAME, "mic"),
            daemon=True,
        ).start()

        # Only open a 2nd listener if VRCHAT_WORLD_LISTEN_DEVICE is explicitly set in .env
        # (Opening a disabled Stereo Mix endpoint causes PyAudio to call Pa_Terminate() and crash PortAudio)
        world_dev = os.getenv("VRCHAT_WORLD_LISTEN_DEVICE", "").strip()
        if world_dev:
            threading.Thread(
                target=self._start_stt_listener,
                args=(world_dev, "VRChat Player", "world-ears"),
                daemon=True,
            ).start()

    # ── Core Speech + Body Coordinator ─────────────────────────────────────

    async def _speak_and_move(self, reply: str, action: str | None = None, tag: str = "luna"):
        if not reply:
            return
        chosen_action = action or VRChatOSC.pick_reaction_for_reply(reply)
        self.world.record_action(chosen_action)
        print(f"{tag} [{chosen_action}] > {reply}")

        self.speaking = True
        self.last_activity = time.time()
        use_ptt = not getattr(self.args, "no_ptt", False) and os.getenv("VRCHAT_PTT", "true").lower() not in ("false", "0", "no")
        try:
            if use_ptt:
                # Hold Push-To-Talk open in VRChat (Mic Mode: PushToTalk) while audio plays into CABLE Input
                self.osc.client.send_message("/input/Voice", 1)
                await asyncio.sleep(0.08)
            chat_task = asyncio.create_task(self.osc.say(reply))
            move_task = asyncio.create_task(self.osc.perform(chosen_action))
            if self.voice:
                await asyncio.to_thread(asyncio.run, self.voice.generate_and_play_audio(reply))
            await asyncio.gather(chat_task, move_task, return_exceptions=True)
        finally:
            if use_ptt:
                await asyncio.sleep(0.2)
                self.osc.client.send_message("/input/Voice", 0)
            self.speaking = False
            self.last_activity = time.time()

    async def respond(self, user: str, text: str):
        self.last_activity = time.time()
        mode_change, requested_action = detect_movement_request(text)
        if mode_change:
            self.world.set_behavior(mode_change, requested_action or mode_change)
            print(f"[autonomy] behavior mode -> {mode_change.upper()}")

        # Always take a fresh look through her eyes before answering so she sees what's in front of her!
        vision_steer = None
        if self.vision and (is_visual_question(text) or time.time() - self.vision.last_seen_ts > 8.0):
            obs = await self.vision.look(question=text if is_visual_question(text) else None)
            if obs["scene"]:
                self.world.set_visual_scene(obs["scene"])
                print(f"[eyes] {obs['scene']} (steer -> {obs['steer']})")
            vision_steer = obs["steer"]

        self.osc.typing(True)
        reply = await self.ai.async_generate_response(text, "VRChat", user)
        self.osc.typing(False)

        await self._speak_and_move(reply, action=requested_action or vision_steer, tag="luna")

    # ── Autonomous Body Loop (Vision-Guided Roaming & Wall Avoidance) ──────

    async def body_loop(self):
        """Keeps Luna's avatar physically alive—using her eyes on every step to steer toward people and avoid walls."""
        while True:
            await asyncio.sleep(random.uniform(7.0, 12.0))
            if not self.autonomy_enabled or self.speaking:
                continue

            mode = self.world.snapshot()["mode"]
            if mode == "roam":
                action = None
                if self.vision:
                    obs = await self.vision.look()
                    if obs["scene"]:
                        self.world.set_visual_scene(obs["scene"])
                        print(f"[eyes] {obs['scene']} -> {obs['steer']}")
                    action = obs["steer"]
                if not action or action == "stay":
                    action = "look_around"
            else:
                action = random.choice(["look_around", "shift_weight"])

            self.world.record_action(action)
            try:
                await self.osc.perform(action)
            except Exception as e:
                print(f"[body] {e}")

    # ── Autonomous Mind Loop (Vision-Grounded Spontaneous Thoughts) ────────

    async def mind_loop(self):
        """Gives Luna free will to look around her VRChat view and comment on what she sees."""
        base_interval = max(15, int(getattr(self.args, "think_every", 38) or 38))
        while True:
            wait_s = random.uniform(base_interval * 0.8, base_interval * 1.35)
            await asyncio.sleep(wait_s)
            if not self.autonomy_enabled or self.speaking or not self.queue.empty():
                continue
            if time.time() - self.last_activity < base_interval * 0.7:
                continue

            # Take a fresh look through her eyes right before speaking!
            vision_steer = None
            if self.vision:
                obs = await self.vision.look()
                if obs["scene"]:
                    self.world.set_visual_scene(obs["scene"])
                    print(f"[eyes] {obs['scene']} (steer -> {obs['steer']})")
                vision_steer = obs["steer"]

            snap = self.world.snapshot()
            if snap["visual_scene"]:
                prompt = (
                    f"[System: You are hanging out in VRChat world '{snap['world']}'. "
                    f"Your eyes currently see: \"{snap['visual_scene']}\". "
                    f"Make a spontaneous, natural comment about what you see or ask Kai / nearby players a quick question.]"
                )
            elif snap["players"] and random.random() < 0.45:
                someone = random.choice(snap["players"])
                prompt = f"[System: You are hanging out in VRChat world '{snap['world']}'. {someone} is in the room near you. Say something casual or ask them/Kai a quick question.]"
            else:
                prompt = f"[System: {random.choice(AUTONOMOUS_THOUGHT_PROMPTS)}]"

            try:
                self.osc.typing(True)
                reply = await self.ai.async_generate_response(prompt, "VRChat", "System")
                self.osc.typing(False)
                await self._speak_and_move(reply, action=vision_steer, tag="luna (autonomous)")
            except Exception as e:
                self.osc.typing(False)
                print(f"[mind] {e}")

    async def on_vrc_event(self, kind: str, value: str):
        """Triggered automatically by VRChat log watcher when worlds or players change."""
        if not self.autonomy_enabled:
            return
        if kind == "world_change":
            print(f"\n[vrchat] Entered world: {value}")
            await asyncio.sleep(4.0)
            if not self.speaking:
                if self.vision:
                    obs = await self.vision.look()
                    if obs["scene"]:
                        self.world.set_visual_scene(obs["scene"])
                prompt = f"[System: You just spawned into the VRChat world '{value}'. Look at what your eyes see and give your spontaneous first impression in 1-2 short sentences.]"
                self.osc.typing(True)
                reply = await self.ai.async_generate_response(prompt, "VRChat", "System")
                self.osc.typing(False)
                await self._speak_and_move(reply, action="look_around", tag="luna (world)")

        elif kind == "player_join":
            print(f"\n[vrchat] Player joined: {value}")
            now = time.time()
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
        asyncio.create_task(watch_vrchat_logs(self.world, self.on_vrc_event))
        await asyncio.sleep(0.5)
        snap = self.world.snapshot()

        print("=" * 64)
        print("        LUNA AI VTUBER - AUTONOMOUS VRCHAT COMPANION")
        print("=" * 64)
        print(f"  Brain     : {self.ai.provider} / {self.ai.model}")
        print(f"  Eyes      : {'ON (' + self.vision.model + ' + OpenCV wall/avatar tracking)' if self.vision else 'OFF'}")
        print(f"  VRChat    : world='{snap['world']}' | players={snap['players'] or ['(solo)']}")
        print(f"  OSC       : {self.osc.host}:{self.osc.port}  (chatbox + /input/* locomotion)")
        print(f"  Autonomy  : {'ON (' + snap['mode'].upper() + ' mode)' if self.autonomy_enabled else 'OFF (manual)'}")
        print(f"  Voice Out : {'OFF (chatbox only)' if not self.voice else ' + '.join(x for x in (self.voice.audio_device, self.voice.mirror_device) if x)}")
        print(f"  Ears      : {'ON (Mic + World Audio)' if self.args.listen else 'OFF (use --listen)'}")
        print("=" * 64)
        print("Tip: Ask her 'what do you see?', 'look at my avatar', 'stay here', or 'roam around'.")
        print("Type below (or speak into your mic) to talk to Luna. Type 'quit' to exit.\n")

        if self.args.listen:
            self.start_all_ears()
        asyncio.create_task(self.console_loop())
        if self.autonomy_enabled:
            asyncio.create_task(self.body_loop())
            asyncio.create_task(self.mind_loop())

        # Take an initial look through her VRChat window and greet!
        if self.vision:
            obs = await self.vision.look()
            if obs["scene"]:
                self.world.set_visual_scene(obs["scene"])
                print(f"[eyes] {obs['scene']} (steer -> {obs['steer']})")
        wake_prompt = (
            f"[System: You just opened your eyes inside VRChat in the world '{snap['world']}'. "
            f"Say a quick hello to Kai and mention what you see in front of you right now.]"
        )
        self.osc.typing(True)
        greeting = await self.ai.async_generate_response(wake_prompt, "VRChat", "System")
        self.osc.typing(False)
        await self._speak_and_move(greeting, action="wander_step", tag="luna (awake)")

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
    _kill_other_luna_brains()
    if not getattr(args, "no_launch", False):
        ensure_luna_vrchat_window()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(LunaInVRChat(args).run())
    except KeyboardInterrupt:
        print("\nLuna logged off.")


if __name__ == "__main__":
    main()
