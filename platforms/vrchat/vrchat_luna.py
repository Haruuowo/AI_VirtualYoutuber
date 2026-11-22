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
from platforms.vrchat.vision import (  # noqa: E402
    VRChatVision,
    capture_luna_vrchat_frame,
    is_facing_blank_wall,
    is_visual_question,
    track_target_person,
)
from platforms.vrchat.world_watcher import (  # noqa: E402
    VRChatWorldState,
    default_vrc_log_dir,
    get_target_instance_for_luna,
    watch_vrchat_logs,
)

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


def _ensure_steamvr_running():
    """Starts SteamVR (vrstartup.exe) with driver_luna Virtual HMD + Hands if not already running."""
    if sys.platform != "win32":
        return
    res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq vrserver.exe", "/NH"], capture_output=True, text=True)
    if "vrserver.exe" in res.stdout.lower():
        print("[steamvr] SteamVR (vrserver.exe) is already running with driver_luna!")
        return
    vrstartup = Path(r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrstartup.exe")
    if vrstartup.exists():
        print("[steamvr] Starting SteamVR with Luna's Virtual 6-DOF HMD + Hand Controllers...")
        subprocess.Popen([str(vrstartup)], cwd=str(vrstartup.parent))
        time.sleep(4.5)


def _configure_vrchat_registry_for_luna():
    """
    Ensures VRChat's Windows Registry PlayerPrefs are configured so Luna works
    seamlessly in SteamVR Mode:
      1. VRC_HOME_ACCESS_TYPE = 1 (Friends+ / ~hidden Home instance so you can 1-click
         'Join' her from your Desktop Social menu without ever needing an invite!)
      2. VRC_MAIN_MENU_MOVEMENT_LOCKED = 0 (So VRChat's startup MainMenuLiveNow popup
         never locks her walking/turning!)
      3. VRC_INPUT_MIC_DEVICE_NAME_VR = 'CABLE Output (VB-Audio Virtual Cable)'
      4. UI.Settings.Osc = 1 & VRC_INPUT_OSC = 1
    """
    if sys.platform != "win32":
        return
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\VRChat\VRChat",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        )
        winreg.SetValueEx(k, "VRC_HOME_ACCESS_TYPE_h350155342", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "VRC_MAIN_MENU_MOVEMENT_LOCKED_h1046269715", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(k, "UI.Settings.Osc_h1043380067", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "VRC_INPUT_OSC_h1104161515", 0, winreg.REG_DWORD, 1)
        cable_bytes = b"CABLE Output (VB-Audio Virtual Cable)\x00"
        winreg.SetValueEx(k, "VRC_INPUT_MIC_DEVICE_NAME_VR_h2729556311", 0, winreg.REG_BINARY, cable_bytes)
        winreg.CloseKey(k)
    except Exception as e:
        print(f"[registry] Note: could not update VRChat prefs: {e}")


def ensure_luna_vrchat_window(use_vr: bool = True):
    """
    Launches ONLY Luna's VRChat window (Profile 1, OSC port 9002) ONCE if it isn't
    already open. Never restarts or loops an already-running VRChat window!
    """
    _configure_vrchat_registry_for_luna()
    if use_vr:
        _ensure_steamvr_running()
    vrc_dir = _find_vrc_dir()
    if not vrc_dir:
        return
    launch_exe = str(vrc_dir / "launch.exe")
    luna_args = [launch_exe, "--profile=1"]
    if not use_vr:
        luna_args.append("--no-vr")
    luna_args.extend([
        "-screen-width", "854", "-screen-height", "480", "-screen-fullscreen", "0",
        "--fps=30", "--osc=9002:127.0.0.1:9003",
    ])

    count = _count_vrchat_processes()
    if count == 0 or (count == 1 and not _newest_log_is_profile1()):
        mode_lbl = "6-DOF STEAMVR MODE" if use_vr else "Desktop Mode"
        print(f"[launcher] Opening LUNA's VRChat window (Profile 1, {mode_lbl}, Friends+ Home, 854x480, OSC 9002)...")
        subprocess.Popen(luna_args, cwd=str(vrc_dir))
    else:
        print("[launcher] Luna's VRChat window (Profile 1) is already open!")

AUTONOMOUS_THOUGHT_PROMPTS = [
    "Look at what your eyes currently see in front of you in VRChat and make a sassy, smug comment about it.",
    "Start a teasing conversation with Kai or the people nearby in the VRChat room based on where you are and what you see.",
    "You just glanced around the VRChat room. Smugly judge an avatar, mirror, or detail you see in front of you.",
    "Share a spontaneous sassy thought or ask Kai / nearby players a sharp question while hanging out in VRChat.",
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
    p.add_argument("--vr", action="store_true", help="Launch Luna in SteamVR 6-DOF Virtual Headset + Hands mode (driver_luna)")
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
        self.last_user_activity = 0.0
        self.last_join_greet = 0.0
        self.last_auto_travel = 0.0
        self.use_vr = getattr(args, "vr", False) or os.getenv("VRCHAT_VR_MODE", "false").lower() in ("true", "1", "yes")
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

    _WHISPER_NOISE = {"", ".", "...", "you", "you.", "thank you", "thank you.", "thanks for watching", "thanks for watching!"}

    def _transcribe_audio(self, rec, audio_data) -> str:
        """Uses fast Groq whisper-large-v3-turbo (~0.18s) when GROQ_API_KEY is set, else Google STT."""
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        if groq_key:
            try:
                client = getattr(self, "_groq_stt", None)
                if client is None:
                    from groq import Groq
                    client = Groq(api_key=groq_key)
                    self._groq_stt = client
                wav_bytes = audio_data.get_wav_data(convert_rate=16000, convert_width=2)
                resp = client.audio.transcriptions.create(
                    file=("speech.wav", wav_bytes),
                    model="whisper-large-v3-turbo",
                    language="en",
                )
                text = (getattr(resp, "text", None) or "").strip()
                return "" if text.lower() in self._WHISPER_NOISE else text
            except Exception:
                pass
        return rec.recognize_google(audio_data)

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
        rec.pause_threshold = 0.55
        rec.non_speaking_duration = 0.35
        try:
            with sr.Microphone(device_index=index) as source:
                rec.adjust_for_ambient_noise(source, duration=0.8)
                print(f"[{tag}] listening for {speaker_label}...")
                while True:
                    try:
                        audio = rec.listen(source, phrase_time_limit=7)
                        if self.speaking:
                            continue
                        text = self._transcribe_audio(rec, audio)
                        if text:
                            who = speaker_label
                            if tag == "world-ears":
                                players = [p for p in self.world.snapshot()["players"] if p.lower() != HOST_NAME.lower()]
                                if players:
                                    who = f"Nearby Player ({', '.join(players[:2])})"
                            print(f"\n[{tag} heard {who}] {text}")
                            # Instant visual feedback in VRChat that she heard you
                            self.osc.typing(True)
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
        chosen_action = action or self.ai.last_action or VRChatOSC.pick_reaction_for_reply(reply)
        self.world.record_action(chosen_action)
        print(f"{tag} [{chosen_action}] > {reply}")

        self.speaking = True
        self.osc.vr_tracker.set_speaking(True)
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
            self.osc.vr_tracker.set_speaking(False)
            if use_ptt:
                await asyncio.sleep(0.2)
                self.osc.client.send_message("/input/Voice", 0)
            self.speaking = False
            self.last_activity = time.time()

    async def _stare_at_speaker(self, allow_approach: bool = False) -> bool:
        """
        Uses local YOLOv8-nano (<20ms, 0 API tokens) to spot an actual avatar in view
        and turn Luna cleanly toward them. Never locks onto walls or posters!
        """
        frame = await asyncio.to_thread(capture_luna_vrchat_frame)
        if frame is None:
            self.osc.apply_cutesy_smug_face(blush=0.4, happy=True)
            return False

        found, offset, height_ratio = await asyncio.to_thread(track_target_person, frame)
        if not found:
            self.osc.apply_cutesy_smug_face(blush=0.4, happy=True)
            return False

        if abs(offset) > 0.20:
            await self.osc.lock_gaze_on_offset(offset)
        else:
            self.osc.apply_cutesy_smug_face(blush=0.45, happy=True)

        if allow_approach and height_ratio < 0.52 and abs(offset) <= 0.30:
            await self.osc.move(vertical=0.85, duration=1.4)

        return True

    async def respond(self, user: str, text: str):
        self.last_activity = time.time()
        self.last_user_activity = self.last_activity
        mode_change, requested_action = detect_movement_request(text)
        if mode_change:
            self.world.set_behavior(mode_change, requested_action or mode_change)
            print(f"[autonomy] behavior mode -> {mode_change.upper()}")

        # Turn toward visible avatar while thinking of her reply (no wall staring!)
        stare_task = asyncio.create_task(self._stare_at_speaker(allow_approach=False))

        # Only call cloud vision when you ask a visual question ("what do you see?", "look at my avatar")
        # or if she hasn't looked in 45s — saves vision tokens and keeps replies fast (<0.8s)!
        vision_steer = None
        if self.vision and (is_visual_question(text) or time.time() - self.vision.last_seen_ts > 45.0):
            obs = await self.vision.look(question=text if is_visual_question(text) else None)
            if obs["scene"]:
                self.world.set_visual_scene(obs["scene"])
                print(f"[eyes] {obs['scene']} (steer -> {obs['steer']})")
            if is_visual_question(text):
                vision_steer = obs["steer"]

        self.osc.typing(True)
        reply = await self.ai.async_generate_response(text, "VRChat", user)
        await stare_task
        self.osc.typing(False)

        await self._speak_and_move(reply, action=requested_action or vision_steer, tag="luna")

    # ── Autonomous Body Loop (Cutesy-Smug Idle & Active Walking) ──────────

    async def body_loop(self):
        """
        Keeps Luna's avatar standing smoothly right after you speak, then actively
        roams, turns away from walls, and walks toward detected players:
          - Only pauses walking for 15s after a user/player speaks to her.
          - When in ROAM mode, avoids walls with OpenCV and walks/approaches players
            spotted by YOLOv8-nano instead of staying locked in place.
        """
        self.osc.apply_cutesy_smug_face(blush=0.4, happy=True, sleepy_smug=True)
        while True:
            await asyncio.sleep(random.uniform(6.0, 10.5))
            if not self.autonomy_enabled or self.speaking:
                continue

            mode = self.world.snapshot()["mode"]
            recently_talking = (time.time() - self.last_user_activity) < 15.0

            if mode == "chill" or recently_talking:
                await self._stare_at_speaker(allow_approach=True)
                action = random.choices(["cutesy_idle", "stare_smug", "wave"], weights=[60, 32, 8], k=1)[0]
            else:
                frame = await asyncio.to_thread(capture_luna_vrchat_frame)
                if frame is not None and await asyncio.to_thread(is_facing_blank_wall, frame):
                    action = random.choice(["turn_left", "turn_right"])
                    print(f"[eyes] wall ahead (OpenCV) -> {action}")
                else:
                    await self._stare_at_speaker(allow_approach=True)
                    action = random.choices(
                        ["wander_step", "curious_approach", "cutesy_idle", "look_around"],
                        weights=[45, 25, 15, 15],
                        k=1,
                    )[0]

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
        if not self.autonomy_enabled or kind not in ("world_change", "player_join"):
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
        print(f"  Dataset   : {len(self.ai.retriever.rows)} rows (HuggingFace Neuro-sama-QnA + neuro.alpha + Sassy/Smug)")
        eyes_desc = "OFF"
        if self.vision:
            cloud_v = f"Gemini {self.vision.gemini_model}" if self.vision.gemini_key else self.vision.model
            eyes_desc = f"ON ({cloud_v} + YOLOv8-nano avatar tracking)"
        print(f"  Eyes      : {eyes_desc}")
        print(f"  VRChat    : world='{snap['world']}' | players={snap['players'] or ['(solo)']}")
        print(f"  OSC       : {self.osc.host}:{self.osc.port}  (chatbox + /input/* locomotion)")
        print(f"  6-DOF VR  : Streaming 60Hz Head + Left/Right Hands -> UDP 127.0.0.1:27015 (driver_luna)")
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
    use_vr = getattr(args, "vr", False) or os.getenv("VRCHAT_VR_MODE", "false").lower() in ("true", "1", "yes")
    if not getattr(args, "no_launch", False):
        ensure_luna_vrchat_window(use_vr=use_vr)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(LunaInVRChat(args).run())
    except KeyboardInterrupt:
        print("\nLuna logged off.")


if __name__ == "__main__":
    main()
