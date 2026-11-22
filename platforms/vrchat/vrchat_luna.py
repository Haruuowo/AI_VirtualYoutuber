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
import ctypes
import os
import random
import struct
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
    read_chatbox_bubbles,
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
    try:
        from platforms.vrchat.openvr_driver.install_driver import ensure_steamvr_chaperone
        ensure_steamvr_chaperone()
    except Exception as e:
        print(f"[steamvr] Chaperone note: {e}")
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
      5. Disable VRChat noise suppression & noise gate so Gawr Gura RVC speech never clips!
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
        winreg.SetValueEx(k, "VRC_INPUT_MIC_NOISE_SUPPRESSION_h2093770996", 0, winreg.REG_DWORD, 0)
        cable_bytes = b"CABLE Output (VB-Audio Virtual Cable)\x00"
        real_mic_bytes = b"Microphone Array (Realtek(R) Audio)\x00"
        winreg.SetValueEx(k, "VRC_INPUT_MIC_DEVICE_NAME_VR_h2729556311", 0, winreg.REG_BINARY, cable_bytes)
        winreg.SetValueEx(k, "VRC_INPUT_MIC_DEVICE_NAME_Desktop_h2596021377", 0, winreg.REG_BINARY, real_mic_bytes)
        winreg.CloseKey(k)

        # Unity PlayerPrefs stores floats as 8-byte IEEE-754 doubles under REG_DWORD (4)
        advapi32 = ctypes.windll.advapi32
        hkey = ctypes.c_void_p()
        rc = advapi32.RegOpenKeyExW(0x80000001, r"Software\VRChat\VRChat", 0, 0x0002, ctypes.byref(hkey))
        if rc == 0:
            try:
                zero_double = struct.pack("<d", 0.0)
                buf = ctypes.create_string_buffer(zero_double, 8)
                advapi32.RegSetValueExW(
                    hkey,
                    "VRC_INPUT_MIC_NOISE_GATE_h1714811770",
                    0,
                    4,  # REG_DWORD (Unity 8-byte float storage)
                    buf,
                    8,
                )
            finally:
                advapi32.RegCloseKey(hkey)
    except Exception as e:
        print(f"[registry] Note: could not update VRChat prefs: {e}")


def _mute_luna_vrchat_process(muted_pids: set[int]) -> list[int]:
    """
    Finds ONLY Luna's VRChat.exe process (--profile=1) in Windows Volume Mixer and mutes it
    so the user never hears duplicate audio from her game tab. Strictly skips --profile=0!
    """
    if sys.platform != "win32":
        return []
    newly_muted: list[int] = []
    co_inited = False
    try:
        import comtypes
        import psutil
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        co_inited = True

        for session in AudioUtilities.GetAllSessions():
            proc = session.Process
            if proc is None:
                continue
            try:
                pid = proc.pid
                name = (proc.name() or "").lower()
                if name != "vrchat.exe":
                    continue
                cmdline = " ".join(proc.cmdline()).lower()
                if "--profile=1" not in cmdline or "--profile=0" in cmdline:
                    continue
                vol = session.SimpleAudioVolume
                if vol is None:
                    continue
                if not vol.GetMute():
                    vol.SetMute(1, None)
                    newly_muted.append(pid)
                muted_pids.add(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
    except Exception:
        pass
    finally:
        if co_inited:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass
    return newly_muted


def _kill_luna_profile1_vrchat():
    """Closes ONLY Luna's Profile 1 VRChat window (never touches your Profile 0 main window)."""
    if sys.platform != "win32":
        return
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'VRChat.exe'\" | "
        "Where-Object { $_.CommandLine -match '--profile=1' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True)


def travel_luna_to_instance(target_instance: str, use_vr: bool = True):
    """
    Called ONLY when you explicitly send Luna an Invite in VRChat (or type 'join').
    Relaunches ONLY Luna's Profile 1 VRChat window directly into `vrchat://launch?id=<target_instance>`.
    Never loops and never touches your Profile 0 main window!
    """
    _configure_vrchat_registry_for_luna()
    if use_vr:
        _ensure_steamvr_running()
    vrc_dir = _find_vrc_dir()
    if not vrc_dir:
        return
    url = target_instance if target_instance.startswith("vrchat://") else f"vrchat://launch?id={target_instance}"
    print(f"\n[invite-accepted] Joining your invited world -> {url}")
    _kill_luna_profile1_vrchat()
    time.sleep(2.0)
    launch_exe = str(vrc_dir / "launch.exe")
    luna_args = [launch_exe, "--profile=1"]
    if not use_vr:
        luna_args.append("--no-vr")
    luna_args.extend([
        "-screen-width", "854", "-screen-height", "480", "-screen-fullscreen", "0",
        "--fps=30", "--osc=9002:127.0.0.1:9003",
        url,
    ])
    subprocess.Popen(luna_args, cwd=str(vrc_dir))


def _is_luna_profile1_running() -> bool:
    """Checks live VRChat.exe command lines so Profile 0 is never mistaken for Luna's Profile 1."""
    if sys.platform != "win32":
        return False
    try:
        import psutil
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                if (proc.info["name"] or "").lower() != "vrchat.exe":
                    continue
                cmd = " ".join(proc.info["cmdline"] or []).lower()
                if "--profile=1" in cmd and "--profile=0" not in cmd:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def ensure_luna_vrchat_window(use_vr: bool = True):
    """
    Launches ONLY Luna's VRChat window (Profile 1, OSC port 9002) ONCE if it isn't
    already open. If your main account (DumAlcoholicPa3) is already in a room,
    she spawns straight into your room on startup!
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
    target_instance = get_target_instance_for_luna()
    if target_instance:
        luna_args.append(f"vrchat://launch?id={target_instance}")

    if not _is_luna_profile1_running():
        mode_lbl = "6-DOF STEAMVR MODE" if use_vr else "Desktop Mode"
        print(f"[launcher] Opening LUNA's VRChat window (Profile 1, {mode_lbl}, 854x480, OSC 9002) -> {target_instance}")
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
    p.add_argument("--world-ears", type=str, default=None, help="Audio input device substring for hearing other VRChat players (e.g. 'CABLE-B Output')")
    p.add_argument("--always-listen", action="store_true", help="Start in Always-Listen (Open Mic) mode instead of VRChat Talk-Only (Hold V) mode")
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
        self.use_vr = getattr(args, "vr", False) or os.getenv("VRCHAT_VR_MODE", "false").lower() in ("true", "1", "yes")
        self.osc = VRChatOSC(
            host=getattr(args, "host", None),
            port=getattr(args, "port", None),
            use_vr=self.use_vr,
        )
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
        self.autonomy_enabled = not getattr(args, "no_autonomy", False)
        self._muted_luna_pids: set[int] = set()
        self._recent_self_replies: list[str] = []
        self._seen_chatboxes: dict[str, float] = {}
        self._last_speak_end_ts: float = 0.0
        # VRChat Talk-Gate Toggle: when True, Luna ONLY hears you when you talk in VRChat (Hold V or unmute in VRChat).
        # Press F8 anytime (or type 'toggle' in console) to switch between VRC-Talk-Only and Always-Listen!
        self.vrc_talk_only = not getattr(args, "always_listen", False)
        self.last_vrc_talk_ts = 0.0
        self.vrc_is_unmuted = False
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

    def toggle_ear_mode(self):
        """Toggles between VRChat Talk-Only (Hold V / Unmuted in VRC) and Always-Listen (Open Mic)."""
        self.vrc_talk_only = not self.vrc_talk_only
        mode_str = "VRCHAT TALK ONLY (Hold V in VRChat to talk to Luna)" if self.vrc_talk_only else "ALWAYS LISTEN (Open Mic - hears you anytime)"
        print(f"\n[ears-toggle] Switched Ear Mode -> {mode_str}")

    def _start_vrc_talk_and_hotkey_monitor(self):
        """
        Monitors:
          1. 'V' key (VRChat Push-To-Talk) via Win32 GetAsyncKeyState so Luna knows when
             you are actively talking inside VRChat.
          2. 'F8' global hotkey so you can toggle between VRChat Talk-Only mode and
             Always-Listen mode anytime without leaving VRChat!
          3. Your main VRChat's OSC output port (127.0.0.1:9001) for /avatar/parameters/MuteSelf
             and /avatar/parameters/Voice in case you use Toggle-Mute in VRChat.
        """
        def _osc_mute_watcher():
            try:
                from pythonosc.dispatcher import Dispatcher
                from pythonosc.osc_server import BlockingOSCUDPServer

                disp = Dispatcher()

                def _on_mute(_addr, val):
                    self.vrc_is_unmuted = not bool(val)
                    if self.vrc_is_unmuted:
                        self.last_vrc_talk_ts = time.time()

                def _on_voice(_addr, val):
                    try:
                        if float(val) > 0.02:
                            self.last_vrc_talk_ts = time.time()
                    except Exception:
                        pass

                disp.map("/avatar/parameters/MuteSelf", _on_mute)
                disp.map("/avatar/parameters/Voice", _on_voice)
                srv = BlockingOSCUDPServer(("127.0.0.1", 9001), disp)
                srv.serve_forever()
            except Exception:
                pass

        threading.Thread(target=_osc_mute_watcher, daemon=True).start()

        if sys.platform != "win32":
            return

        def _key_watcher():
            import ctypes
            user32 = ctypes.windll.user32
            VK_V = 0x56
            VK_F8 = 0x77
            f8_was_down = False
            while True:
                # Track V (VRChat Push-To-Talk key)
                if user32.GetAsyncKeyState(VK_V) & 0x8000:
                    self.last_vrc_talk_ts = time.time()

                # Track F8 toggle hotkey (edge-triggered)
                f8_down = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
                if f8_down and not f8_was_down:
                    self.toggle_ear_mode()
                f8_was_down = f8_down
                time.sleep(0.025)

        threading.Thread(target=_key_watcher, daemon=True).start()

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
            if text.lower() in ("join", "join me", "follow me"):
                target = get_target_instance_for_luna()
                await asyncio.to_thread(travel_luna_to_instance, target, self.use_vr)
                continue
            if text.lower() in ("toggle", "ptt", "mute", "ears"):
                self.toggle_ear_mode()
                continue
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
                        # If VRChat Talk-Only mode is active, ignore mic speech unless you held V or unmuted in VRChat!
                        if tag == "mic" and self.vrc_talk_only:
                            if not self.vrc_is_unmuted and (time.time() - self.last_vrc_talk_ts > 2.5):
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
        self._start_vrc_talk_and_hotkey_monitor()
        mic_dev = os.getenv("VRCHAT_LISTEN_DEVICE", "").strip()
        threading.Thread(
            target=self._start_stt_listener,
            args=(mic_dev, HOST_NAME, "mic"),
            daemon=True,
        ).start()

        # Only open a 2nd SpeechRecognition listener if VRCHAT_WORLD_LISTEN_DEVICE or --world-ears is explicitly set;
        # otherwise use non-blocking Windows WASAPI Loopback (`pyaudiowpatch`) on your default output automatically!
        world_dev = (getattr(self.args, "world_ears", None) or os.getenv("VRCHAT_WORLD_LISTEN_DEVICE", "")).strip()
        if world_dev:
            threading.Thread(
                target=self._start_stt_listener,
                args=(world_dev, "VRChat Player", "world-ears"),
                daemon=True,
            ).start()
        elif sys.platform == "win32":
            threading.Thread(
                target=self._start_wasapi_loopback_listener,
                daemon=True,
            ).start()

    def _start_wasapi_loopback_listener(self):
        """
        Automatically captures friends' voices from your default Windows output (Headphones/Speakers)
        via non-blocking WASAPI Loopback (`pyaudiowpatch`) — no Voicemeeter or extra cables needed!
        Ignores audio while Luna is speaking (+1.6s cooldown) so she never echoes herself.
        """
        try:
            import queue
            import numpy as np
            import pyaudiowpatch as pyaudio
            import speech_recognition as sr

            pa = pyaudio.PyAudio()
            try:
                wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_out = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                loopback = None
                for lb in pa.get_loopback_device_info_generator():
                    if default_out["name"] in lb["name"]:
                        loopback = lb
                        break
                if loopback is None:
                    print("[world-ears] No WASAPI loopback device found; skipping auto world-ears.")
                    return

                rate = int(loopback["defaultSampleRate"])
                channels = min(2, max(1, int(loopback["maxInputChannels"])))
                utt_q: queue.Queue[bytes] = queue.Queue(maxsize=4)
                buf: list[np.ndarray] = []
                in_speech = False
                last_voice_ts = 0.0
                speech_start_ts = 0.0

                def _cb(in_data, _frame_count, _time_info, _status):
                    nonlocal buf, in_speech, last_voice_ts, speech_start_ts
                    now = time.time()
                    if self.speaking or (now - self._last_speak_end_ts < 1.6):
                        buf.clear()
                        in_speech = False
                        return (None, pyaudio.paContinue)
                    arr = np.frombuffer(in_data, dtype=np.int16)
                    if channels > 1:
                        arr = arr.reshape(-1, channels).mean(axis=1).astype(np.int16)
                    rms = float(np.sqrt(np.mean((arr.astype(np.float32) / 32768.0) ** 2))) if len(arr) else 0.0
                    if rms > 0.012:
                        if not in_speech:
                            in_speech = True
                            speech_start_ts = now
                            buf.clear()
                        last_voice_ts = now
                        buf.append(arr)
                    elif in_speech:
                        buf.append(arr)
                        if (now - last_voice_ts >= 0.55) or (now - speech_start_ts >= 7.0):
                            if len(buf) >= 4:
                                pcm = np.concatenate(buf).tobytes()
                                try:
                                    utt_q.put_nowait(pcm)
                                except queue.Full:
                                    pass
                            buf.clear()
                            in_speech = False
                    return (None, pyaudio.paContinue)

                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    frames_per_buffer=2048,
                    input=True,
                    input_device_index=loopback["index"],
                    stream_callback=_cb,
                )
                stream.start_stream()
                print(f"[world-ears] Auto-listening to VRChat friends via WASAPI Loopback: '{loopback['name']}'")

                while True:
                    pcm = utt_q.get()
                    if self.speaking or (time.time() - self._last_speak_end_ts < 1.6):
                        continue
                    try:
                        audio_data = sr.AudioData(pcm, sample_rate=rate, sample_width=2)
                        text = self._transcribe_audio(None, audio_data)
                        if not text:
                            continue
                        norm = "".join(ch for ch in text.lower() if ch.isalnum())
                        if norm and any(norm in r or r in norm for r in self._recent_self_replies):
                            continue
                        players = [p for p in self.world.snapshot()["players"] if p.lower() != HOST_NAME.lower()]
                        who = f"Nearby Player ({', '.join(players[:2])})" if players else "VRChat Friend"
                        print(f"\n[world-ears heard {who}] {text}")
                        self.osc.typing(True)
                        self.push(who, text, True)
                    except Exception:
                        pass
            finally:
                pa.terminate()
        except Exception as e:
            print(f"[world-ears] WASAPI loopback unavailable: {e}")

    # ── Core Speech + Body Coordinator ─────────────────────────────────────

    _ROAM_ONLY_ACTIONS = {
        "wander_step", "look_around", "curious_approach", "step_back",
        "turn_left", "turn_right", "walk_forward", "walk_back",
        "strafe_left", "strafe_right", "spin",
    }

    async def _speak_and_move(
        self,
        reply: str,
        action: str | None = None,
        tag: str = "luna",
        allow_roam_action: bool = False,
    ):
        if not reply:
            return
        chosen_action = action or self.ai.last_action or VRChatOSC.pick_reaction_for_reply(reply)
        if not allow_roam_action and self.world.snapshot()["mode"] == "chill":
            if chosen_action in self._ROAM_ONLY_ACTIONS:
                chosen_action = "stare_smug"
        self.world.record_action(chosen_action)
        norm_reply = "".join(ch for ch in reply.lower() if ch.isalnum())
        if norm_reply:
            self._recent_self_replies.append(norm_reply)
            self._recent_self_replies = self._recent_self_replies[-8:]
        print(f"{tag} [{chosen_action}] > {reply}")

        self.speaking = True
        self.last_activity = time.time()
        use_ptt = not getattr(self.args, "no_ptt", False) and os.getenv("VRCHAT_PTT", "true").lower() not in ("false", "0", "no")

        # Pre-render Gawr Gura RVC audio BEFORE opening PTT / chatbox so text, gestures, and voice start in sync
        prepared = None
        if self.voice:
            self.osc.typing(True)
            try:
                prepared = await self.voice.prepare_audio(reply, emotion=chosen_action)
            finally:
                self.osc.typing(False)

        self.osc.vr_tracker.set_speaking(True)
        try:
            if use_ptt:
                # Hold Push-To-Talk open in VRChat (Mic Mode: PushToTalk) while audio plays into CABLE Input
                self.osc.client.send_message("/input/Voice", 1)
                await asyncio.sleep(0.15)
            if prepared is not None:
                self.osc.vr_tracker.set_speech_envelope(*prepared)
            chat_task = asyncio.create_task(self.osc.say(reply))
            move_task = asyncio.create_task(self.osc.perform(chosen_action))
            if self.voice:
                if prepared is not None:
                    await self.voice.play_prepared_audio(*prepared)
                else:
                    await self.voice.generate_and_play_audio(reply, emotion=chosen_action)
            await asyncio.gather(chat_task, move_task, return_exceptions=True)
        finally:
            self.osc.vr_tracker.set_speaking(False)
            if use_ptt:
                await asyncio.sleep(0.45)
                self.osc.client.send_message("/input/Voice", 0)
            self.speaking = False
            self._last_speak_end_ts = time.time()
            self.last_activity = self._last_speak_end_ts

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

        if abs(offset) > 0.14:
            await self.osc.lock_gaze_on_offset(offset)
        else:
            self.osc.vr_tracker.set_gaze_offset(offset)
            self.osc.apply_cutesy_smug_face(blush=0.45, happy=True)
            self.osc.set_eyes(pitch=-2.0, yaw=float(offset * 16.0), closed=0.16)

        if allow_approach and height_ratio < 0.52 and abs(offset) <= 0.30:
            await self.osc.move(vertical=0.85, duration=1.4)

        return True

    async def respond(self, user: str, text: str):
        self.last_activity = time.time()
        self.last_user_activity = self.last_activity
        low_t = text.lower().strip()
        if any(p in low_t for p in ("join my world", "join my room", "accept my invite", "accept invite")):
            target = get_target_instance_for_luna()
            await self._speak_and_move("Fine, I'm joining your world now. Hold on.", action="wave", tag="luna")
            await asyncio.to_thread(travel_luna_to_instance, target, self.use_vr)
            return
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

        await self._speak_and_move(
            reply,
            action=requested_action or vision_steer,
            tag="luna",
            allow_roam_action=bool(requested_action),
        )

    # ── Continuous Gaze-Lock + Autonomous Body Loop ────────────────────────

    async def gaze_track_loop(self):
        """
        Lightweight ~1.2s local YOLOv8-nano gaze tracker (0 API tokens):
        keeps Luna's 3D VR head, eyes, and body continuously turned toward you
        in chill mode instead of staring off into space or wandering!
        """
        while True:
            await asyncio.sleep(1.2)
            if self.speaking or self.world.snapshot()["mode"] != "chill":
                continue
            if self.osc._move_lock.locked():
                continue
            try:
                await self._stare_at_speaker(allow_approach=False)
            except Exception:
                pass

    async def chatbox_watch_loop(self):
        """
        Reads floating VRChat chatbox bubbles via local RapidOCR (~1.6s, 0 VRAM, 0 API tokens)
        so muted friends who talk through the VRChat chatbox can converse with Luna directly!
        """
        pending_text = ""
        pending_hits = 0
        while True:
            await asyncio.sleep(1.6)
            if self.speaking:
                continue
            try:
                img = await asyncio.to_thread(capture_luna_vrchat_frame, 854, 480, False)
                if img is None:
                    continue
                bubbles = await asyncio.to_thread(read_chatbox_bubbles, img)
                if not bubbles:
                    pending_text = ""
                    pending_hits = 0
                    continue

                now = time.time()
                # Prune old seen bubbles after 25s
                self._seen_chatboxes = {k: ts for k, ts in self._seen_chatboxes.items() if now - ts < 25.0}
                players_norm = {"".join(c for c in p.lower() if c.isalnum()) for p in self.world.snapshot()["players"]}

                chosen = None
                for b in bubbles:
                    norm = "".join(c for c in b.lower() if c.isalnum())
                    if not norm or norm in players_norm:
                        continue
                    if any(norm in self_r or self_r in norm for self_r in self._recent_self_replies):
                        continue
                    if norm in self._seen_chatboxes:
                        continue
                    chosen = (norm, b)
                    break

                if not chosen:
                    pending_text = ""
                    pending_hits = 0
                    continue

                norm, raw_text = chosen
                if norm == pending_text:
                    pending_hits += 1
                else:
                    pending_text = norm
                    pending_hits = 1

                # Require 2 consecutive reads so we don't reply to half-typed bubbles or single-frame flicker
                if pending_hits >= 2:
                    self._seen_chatboxes[norm] = now
                    pending_text = ""
                    pending_hits = 0
                    print(f"\n[chatbox-eye] Read friend's chatbox: {raw_text}")
                    self.osc.typing(True)
                    self.push("VRChat Friend (Chatbox)", raw_text, True)
            except Exception:
                pass

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

            if mode == "chill":
                # Stay right in place, turn to keep looking at you, and do stationary expressive idles
                await self._stare_at_speaker(allow_approach=False)
                action = random.choices(["cutesy_idle", "stare_smug"], weights=[65, 35], k=1)[0]
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
        """Triggered automatically by VRChat log watcher when worlds, invites, or players change."""
        if kind == "invite_received":
            # Triggered ONLY when you click 'Invite' on kairu! in VRChat (never loops!)
            print(f"\n[vrchat] Invite received from Kai -> auto-accepting world: {value}")
            await asyncio.to_thread(travel_luna_to_instance, value, self.use_vr)
            return

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
                await self._speak_and_move(reply, action="wave", tag="luna (greet)")

    # ── Main Entry Loop ────────────────────────────────────────────────────

    async def _auto_mute_luna_tab_loop(self):
        """Continuously ensures ONLY Luna's VRChat (--profile=1) window stays muted in Windows Volume Mixer."""
        while True:
            newly = await asyncio.to_thread(_mute_luna_vrchat_process, self._muted_luna_pids)
            for pid in newly:
                print(f"[audio] Auto-muted Luna's VRChat window (PID {pid}, --profile=1) in Windows Volume Mixer")
            await asyncio.sleep(4.0)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        asyncio.create_task(watch_vrchat_logs(self.world, self.on_vrc_event))
        asyncio.create_task(self._auto_mute_luna_tab_loop())
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
        print(f"  6-DOF VR  : Streaming 60Hz Head + Hands + FBT (Waist/Feet) -> UDP 127.0.0.1:27015 (driver_luna)")
        print(f"  Autonomy  : {'ON (' + snap['mode'].upper() + ' mode)' if self.autonomy_enabled else 'OFF (manual)'}")
        print(f"  Voice Out : {'OFF (chatbox only)' if not self.voice else ' + '.join(x for x in (self.voice.audio_device, self.voice.mirror_device) if x)}")
        ear_mode_lbl = "VRC TALK ONLY (Hold V in VRChat | Press F8 to toggle)" if self.vrc_talk_only else "ALWAYS LISTEN (Open Mic | Press F8 to toggle)"
        print(f"  Ears      : {'ON [' + ear_mode_lbl + ']' if self.args.listen else 'OFF (use --listen)'}")
        world_dev = (getattr(self.args, "world_ears", None) or os.getenv("VRCHAT_WORLD_LISTEN_DEVICE", "")).strip()
        if not self.args.listen:
            we_lbl = "OFF (use --listen)"
        elif world_dev:
            we_lbl = f"ON ({world_dev})"
        else:
            we_lbl = "ON (Auto WASAPI Loopback on default speakers/headphones)"
        print(f"  World Ears: {we_lbl}")
        print("=" * 64)
        print("Tip: Hold 'V' in VRChat to talk to Luna, or press 'F8' (or type 'toggle') to switch to Always-Listen!")
        print("Type below (or speak into your mic) to talk to Luna. Type 'quit' to exit.\n")

        if self.args.listen:
            self.start_all_ears()
        asyncio.create_task(self.console_loop())
        asyncio.create_task(self.gaze_track_loop())
        asyncio.create_task(self.chatbox_watch_loop())
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
        await self._speak_and_move(greeting, action="wave", tag="luna (awake)")

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
