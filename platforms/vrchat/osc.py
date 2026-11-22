"""
VRChat OSC Controller — Chatbox + Autonomous Locomotion & Body Language.

VRChat listens for OSC on 127.0.0.1:9000 (enable it in-game:
Action Menu -> Options -> OSC -> Enabled). No mods required.

Supported native VRChat OSC endpoints used here:
    /chatbox/input          [text, True, notify]
    /chatbox/typing         bool
    /input/Vertical         float (-1.0 .. 1.0)   walk forward / backward
    /input/Horizontal       float (-1.0 .. 1.0)   strafe left / right
    /input/LookHorizontal   float (-1.0 .. 1.0)   turn left / right
    /input/Jump             int   (1 then 0)      jump
    /input/Run              int   (1 or 0)        sprint toggle
    /input/Voice            int   (1 or 0)        push-to-talk / mic toggle
    /avatar/parameters/*    any                   custom avatar emotes
"""
import asyncio
import math
import os
import random
import re
import textwrap
import time

from pythonosc.udp_client import SimpleUDPClient
from platforms.vrchat.vr_tracker import VirtualVRPoseController

CHATBOX_LIMIT = 144          # VRChat hard limit per chatbox message
PAGE_DELAY = 2.2             # VRChat rate-limits the chatbox; ~2s between pages is safe


def _ease_scale(progress: float, duration: float) -> float:
    """Cosine/sine ease-in/ease-out envelope in [0.0, 1.0] for natural analog stick motion."""
    p = max(0.0, min(1.0, progress))
    if duration < 0.6:
        return math.sin(math.pi * p)
    if p < 0.25:
        return 0.5 * (1.0 - math.cos(math.pi * (p / 0.25)))
    if p > 0.75:
        return 0.5 * (1.0 - math.cos(math.pi * ((1.0 - p) / 0.25)))
    return 1.0


class VRChatOSC:
    def __init__(self, host: str | None = None, port: int | None = None, use_vr: bool = True):
        self.host = host or os.getenv("VRCHAT_OSC_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("VRCHAT_OSC_PORT", "9000"))
        self.use_vr = bool(use_vr)
        self.client = SimpleUDPClient(self.host, self.port)
        self._move_lock = asyncio.Lock()
        # 60 Hz 6-DOF Virtual VR Head + Left/Right Hands + Joystick streamer (UDP 127.0.0.1:27015 -> driver_luna.dll)
        self.vr_tracker = VirtualVRPoseController()
        self.vr_tracker.start()

    # ── Chatbox ────────────────────────────────────────────────────────────

    def typing(self, on: bool):
        """Shows / hides the '...' typing bubble above the avatar."""
        self.client.send_message("/chatbox/typing", bool(on))

    def send_chatbox(self, text: str, notify: bool = False):
        """Sends one message (already <= 144 chars) straight to the chatbox."""
        self.client.send_message("/chatbox/input", [text[:CHATBOX_LIMIT], True, bool(notify)])

    @staticmethod
    def paginate(text: str) -> list[str]:
        text = " ".join(text.split())
        if len(text) <= CHATBOX_LIMIT:
            return [text] if text else []
        chunks = textwrap.wrap(text, CHATBOX_LIMIT - 7, break_long_words=True)
        n = len(chunks)
        return [f"{c} ({i}/{n})" for i, c in enumerate(chunks, 1)]

    async def say(self, text: str):
        """Shows `text` in the chatbox, paging long replies."""
        pages = self.paginate(text)
        for i, page in enumerate(pages):
            self.send_chatbox(page)
            if i < len(pages) - 1:
                await asyncio.sleep(PAGE_DELAY)

    def clear(self):
        self.send_chatbox("")

    # ── Low-Level Locomotion (/input/*) ────────────────────────────────────

    def stop_all(self):
        """Immediately zeroes all movement axes and buttons so the avatar never gets stuck walking."""
        self.vr_tracker.reset_locomotion()
        self.set_param("VRCEmote", 0)
        self.set_param("DoingEmote", False)
        for addr in ("/input/Vertical", "/input/Horizontal", "/input/LookHorizontal"):
            self.client.send_message(addr, 0.0)
        for addr in (
            "/input/MoveForward", "/input/MoveBackward",
            "/input/MoveLeft", "/input/MoveRight",
            "/input/LookLeft", "/input/LookRight",
            "/input/Jump", "/input/Run",
        ):
            self.client.send_message(addr, 0)

    async def _ramp_axes(
        self,
        axes: list[tuple[str, float]],
        duration: float,
        run: bool = False,
        buttons: list[str] | None = None,
    ):
        """Streams ~40 Hz cosine-eased locomotion via native SteamVR Oculus Touch sticks (VR) or OSC (Desktop)."""
        dur = max(0.05, float(duration))
        btns = buttons or []
        async with self._move_lock:
            try:
                if run:
                    self.client.send_message("/input/Run", 1)
                for btn in btns:
                    self.client.send_message(btn, 1)
                t0 = time.monotonic()
                while True:
                    elapsed = time.monotonic() - t0
                    if elapsed >= dur:
                        break
                    scale = _ease_scale(elapsed / dur, dur)
                    if self.use_vr:
                        strafe = 0.0
                        forward = 0.0
                        turn = 0.0
                        for addr, target in axes:
                            val = float(target * scale)
                            if addr == "/input/Horizontal":
                                strafe = val
                            elif addr == "/input/Vertical":
                                forward = val
                            elif addr == "/input/LookHorizontal":
                                turn = val
                        self.vr_tracker.set_locomotion(strafe=strafe, forward=forward, turn=turn)
                    # Always send OSC analog axes + digital buttons so movement works in every VRChat input mode
                    for addr, target in axes:
                        self.client.send_message(addr, float(target * scale))
                    await asyncio.sleep(min(0.025, dur - elapsed))
            finally:
                if self.use_vr:
                    self.vr_tracker.reset_locomotion()
                for addr, _ in axes:
                    self.client.send_message(addr, 0.0)
                for btn in btns:
                    self.client.send_message(btn, 0)
                if run:
                    self.client.send_message("/input/Run", 0)

    async def move(self, vertical: float = 0.0, horizontal: float = 0.0, duration: float = 1.0, run: bool = False):
        """Walks/strafes cleanly using native VR thumbsticks + OSC analog axes + digital buttons."""
        # Clear any active full-body emote before moving so her legs are never locked in place
        self.set_param("VRCEmote", 0)
        self.set_param("DoingEmote", False)
        axes: list[tuple[str, float]] = []
        buttons: list[str] = []
        if vertical:
            v = float(max(-1.0, min(1.0, vertical)))
            axes.append(("/input/Vertical", v))
            buttons.append("/input/MoveForward" if v > 0 else "/input/MoveBackward")
        if horizontal:
            h = float(max(-1.0, min(1.0, horizontal)))
            axes.append(("/input/Horizontal", h))
            buttons.append("/input/MoveRight" if h > 0 else "/input/MoveLeft")
        if not axes:
            return
        await self._ramp_axes(axes, duration, run=run, buttons=buttons)

    async def turn(self, speed: float = 0.5, duration: float = 0.6):
        """Turns smoothly left (negative speed) or right (positive speed) for `duration` seconds."""
        s = float(max(-1.0, min(1.0, speed)))
        if s == 0.0:
            return
        btn = "/input/LookRight" if s > 0 else "/input/LookLeft"
        await self._ramp_axes([("/input/LookHorizontal", s)], duration, buttons=[btn])

    async def steer_walk(
        self,
        vertical: float = 0.0,
        horizontal: float = 0.0,
        turn: float = 0.0,
        duration: float = 0.45,
        run: bool = False,
    ):
        """
        Walks AND smoothly steers/curves at the same time (left stick + right stick simultaneously),
        so Luna can naturally curve around and follow a moving player instead of only walking straight.
        """
        self.set_param("VRCEmote", 0)
        self.set_param("DoingEmote", False)
        axes: list[tuple[str, float]] = []
        buttons: list[str] = []
        if abs(vertical) > 0.02:
            v = float(max(-1.0, min(1.0, vertical)))
            axes.append(("/input/Vertical", v))
            buttons.append("/input/MoveForward" if v > 0 else "/input/MoveBackward")
        if abs(horizontal) > 0.02:
            h = float(max(-1.0, min(1.0, horizontal)))
            axes.append(("/input/Horizontal", h))
            buttons.append("/input/MoveRight" if h > 0 else "/input/MoveLeft")
        if abs(turn) > 0.02:
            t = float(max(-1.0, min(1.0, turn)))
            axes.append(("/input/LookHorizontal", t))
            buttons.append("/input/LookRight" if t > 0 else "/input/LookLeft")
        if not axes:
            return
        await self._ramp_axes(axes, duration, run=run, buttons=buttons)

    async def jump(self):
        """Taps the jump input (both native Oculus Touch 'A' button and OSC /input/Jump)."""
        if self.use_vr:
            self.vr_tracker.set_locomotion(jump=True)
        self.client.send_message("/input/Jump", 1)
        await asyncio.sleep(0.15)
        if self.use_vr:
            self.vr_tracker.set_locomotion(jump=False)
        self.client.send_message("/input/Jump", 0)

    # ── Avatar parameters, Hand Emotes & Cutesy-Smug Facial Expressions ────

    def set_param(self, name: str, value):
        """Drives an avatar parameter, e.g. VRCEmote or custom gesture toggle."""
        self.client.send_message(f"/avatar/parameters/{name}", value)

    def set_eyes(self, pitch: float = 0.0, yaw: float = 0.0, closed: float = 0.18):
        """
        Drives VRChat native OSC eye tracking so her eyes lock onto you or do smug glances.
        pitch/yaw in degrees (-25..25), closed in [0.0..1.0] (0.18 = half-lidded smug look).
        """
        self.client.send_message("/tracking/eye/LeftRightPitchYaw", [float(pitch), float(yaw), float(pitch), float(yaw)])
        self.client.send_message("/tracking/eye/EyesClosedAmount", float(max(0.0, min(1.0, closed))))

    def apply_cutesy_smug_face(
        self,
        blush: float = 0.45,
        happy: bool = True,
        sleepy_smug: bool = True,
        emotion_idx: int = 1,
        exp_mod_l: bool = True,
        exp_mod_r: bool = False,
    ):
        """
        Applies facial expressions tailored specifically to Luna's 'Colibri Blind' avatar
        (plus standard VRChat SDK3 fallback parameters):
          - BlushLock=True + Blush float (Colibri resets Blush to 0 unless BlushLock is True!)
          - Emotion & EmotionIDX (0..7) + ExpModL / ExpModR expression modifiers
          - Sleepy=True for her signature half-lidded smug eyes
          - BlinkEnabled=True so her custom eyelid blinker stays active
        """
        b_val = float(max(0.0, min(1.0, blush)))
        self.set_param("BlushLock", b_val > 0.05)
        self.set_param("Blush", b_val)
        self.set_param("Happy", bool(happy))
        self.set_param("Sleepy", bool(sleepy_smug))
        self.set_param("BlinkEnabled", True)
        # Colibri Blind expression selectors & modifiers (do NOT force EmotionIDX or DoingEmote externally)
        self.set_param("ExpModL", bool(exp_mod_l))
        self.set_param("ExpModR", bool(exp_mod_r))
        self.set_param("Emotion", int(emotion_idx))
        # Standard BlendTree facial expression fallback
        self.set_param("VRCFaceBlendH", 0.55 if happy else -0.4)
        self.set_param("VRCFaceBlendV", 0.35 if sleepy_smug else 0.0)
        self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.22 if sleepy_smug else 0.14)

    async def trigger_colibri_blink(self):
        """Triggers Colibri Blind's custom /avatar/parameters/Blink eyelid animation."""
        self.set_param("Blink", True)
        self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.88)
        await asyncio.sleep(0.14)
        self.set_param("Blink", False)
        self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.18)

    async def lock_gaze_on_offset(self, offset: float, offset_y: float = 0.0):
        """
        Natural Eye -> Head -> Body gaze tracking toward (offset_x, offset_y):
        Moves Luna's eyes and 3D VR head/neck first, and only gently rotates her
        whole body when someone moves further off-center (> 0.26), preventing
        robotic whole-body twitch-turning on small shifts.
        """
        eye_pitch = float(max(-16.0, min(18.0, -offset_y * 14.0)))
        eye_yaw = float(max(-20.0, min(20.0, offset * 18.0)))
        self.vr_tracker.set_gaze_target(offset, offset_y)
        self.set_eyes(pitch=eye_pitch, yaw=eye_yaw, closed=0.14)
        if abs(offset) > 0.24:
            speed = 0.48 if offset > 0 else -0.48
            dur = min(0.45, max(0.12, (abs(offset) - 0.10) * 0.38))
            await self.turn(speed=speed, duration=dur)
            # Keep natural residual neck + eye lead toward target instead of snapping to 0
            self.vr_tracker.set_gaze_target(offset * 0.35, offset_y)
            self.set_eyes(pitch=eye_pitch, yaw=float(offset * 6.0), closed=0.14)

    async def emote(self, emote_id: int, duration: float = 2.8, happy: bool = True, blush: float = 0.45, emotion_idx: int = 1):
        """
        Triggers a standard VRChat SDK3 hand/body emote (/avatar/parameters/VRCEmote 1..8)
        while maintaining her facial expression.
        """
        try:
            self.apply_cutesy_smug_face(blush=blush, happy=happy, emotion_idx=emotion_idx)
            self.set_param("VRCEmote", int(emote_id))
            await asyncio.sleep(max(0.5, duration))
        finally:
            self.set_param("VRCEmote", 0)
            self.apply_cutesy_smug_face(blush=0.40, happy=True, sleepy_smug=True, emotion_idx=1)

    # ── High-Level Expressive Actions & Autonomous Body Language ───────────

    async def perform(self, action: str):
        """Executes a named physical behavior, locomotion step, 6-DOF VR arm/head pose, or hand emote."""
        # Trigger matching 6-DOF VR Head & Hand pose in driver_luna.dll (if running in VR mode)
        pose_dur = 5.5 if action == "dance" else (3.8 if action == "hug" else 2.6)
        self.vr_tracker.trigger_pose(action, duration=pose_dur)

        if action == "stop":
            self.stop_all()
            self.set_param("VRCEmote", 0)
            return

        if action == "wave":
            if self.use_vr:
                self.apply_cutesy_smug_face(blush=0.45, happy=True, emotion_idx=1)
                await asyncio.sleep(2.4)
            else:
                await self.emote(1, duration=2.6, happy=True)

        elif action == "clap":
            if self.use_vr:
                self.apply_cutesy_smug_face(blush=0.45, happy=True, emotion_idx=1)
                await asyncio.sleep(2.4)
            else:
                await self.emote(2, duration=2.6, happy=True)

        elif action == "point":
            if self.use_vr:
                self.apply_cutesy_smug_face(blush=0.45, happy=True, sleepy_smug=True, emotion_idx=1)
                await asyncio.sleep(2.2)
            else:
                await self.emote(3, duration=2.4)

        elif action == "cheer":
            if self.use_vr:
                self.apply_cutesy_smug_face(blush=0.50, happy=True, sleepy_smug=False, emotion_idx=2)
                await asyncio.sleep(2.6)
            else:
                await self.emote(4, duration=2.8, happy=True)

        elif action == "dance":
            if self.use_vr:
                self.apply_cutesy_smug_face(blush=0.55, happy=True, sleepy_smug=False, emotion_idx=2)
                await asyncio.sleep(5.2)
            else:
                await self.emote(5, duration=5.5, happy=True)

        elif action == "backflip":
            await self.emote(6, duration=2.8, happy=True)

        elif action == "sad_kick":
            await self.emote(7, duration=2.6)

        elif action == "play_dead":
            await self.emote(8, duration=3.5)

        elif action == "blush":
            self.apply_cutesy_smug_face(blush=1.0, happy=True, sleepy_smug=True, emotion_idx=3, exp_mod_l=True, exp_mod_r=True)
            self.set_param("Headpat", True)
            if self.use_vr:
                await asyncio.sleep(2.4)
            else:
                await self.emote(7, duration=2.4, happy=True, blush=1.0, emotion_idx=3)
            self.set_param("Headpat", False)

        elif action == "headpat":
            self.apply_cutesy_smug_face(blush=0.9, happy=True, sleepy_smug=True, emotion_idx=2)
            self.set_param("Headpat", True)
            await asyncio.sleep(2.2)
            self.set_param("Headpat", False)

        elif action == "hug":
            # Walks in close to you, blushes warmly, and raises her 6-DOF VR arms for a hug
            self.apply_cutesy_smug_face(blush=0.95, happy=True, sleepy_smug=True, emotion_idx=2, exp_mod_l=True, exp_mod_r=True)
            await self.move(vertical=0.75, duration=0.90)
            self.set_param("Headpat", True)
            if self.use_vr:
                await asyncio.sleep(2.6)
            else:
                await self.emote(4, duration=2.4, happy=True, blush=0.95, emotion_idx=2)
            self.set_param("Headpat", False)
            self.apply_cutesy_smug_face(blush=0.45, happy=True, sleepy_smug=True, emotion_idx=1)

        elif action == "boop":
            self.apply_cutesy_smug_face(blush=0.75, happy=True, sleepy_smug=False, emotion_idx=4, exp_mod_r=True)
            self.set_param("EmoteRand_Boop", random.randint(1, 3))
            self.set_param("Boop", True)
            await asyncio.sleep(1.8)
            self.set_param("Boop", False)

        elif action == "sassy_react":
            # Triggers Colibri Blind's built-in sassy/shocked face reaction + stick prop
            self.apply_cutesy_smug_face(blush=0.55, happy=False, sleepy_smug=True, emotion_idx=5, exp_mod_l=False, exp_mod_r=True)
            self.set_param("EmoteRand_Slap", random.randint(1, 3))
            self.set_param("Slap", True)
            await asyncio.sleep(1.6)
            self.set_param("Slap", False)
            self.apply_cutesy_smug_face(blush=0.45, happy=True, sleepy_smug=True, emotion_idx=1)

        elif action == "stick":
            # Toggles Colibri Blind's hand stick prop ('Sticc') and points/waves it!
            self._stick_on = not getattr(self, "_stick_on", False)
            self.set_param("Sticc", self._stick_on)
            if self.use_vr:
                self.apply_cutesy_smug_face(blush=0.45, happy=True, emotion_idx=2)
                await asyncio.sleep(2.2)
            else:
                await self.emote(3, duration=2.2, happy=True, blush=0.45, emotion_idx=2)

        elif action == "stare_smug":
            # Stationary cutesy-smug stare using Colibri Blind's Sleepy + BlushLock + EmotionIDX + Blink
            self.apply_cutesy_smug_face(
                blush=0.60,
                happy=True,
                sleepy_smug=True,
                emotion_idx=random.choice([1, 2]),
                exp_mod_l=True,
                exp_mod_r=random.choice([True, False]),
            )
            await self.trigger_colibri_blink()
            await asyncio.sleep(0.5)

        elif action == "cutesy_idle":
            # Natural cutesy-smug idle animation driving Colibri Blind's actual Blink & EmotionIDX parameters
            self.apply_cutesy_smug_face(
                blush=0.45,
                happy=True,
                sleepy_smug=random.choice([True, True, False]),
                emotion_idx=random.choice([1, 2, 3]),
                exp_mod_l=True,
                exp_mod_r=False,
            )
            glance = random.choice([-10.0, 10.0, 0.0])
            self.set_eyes(pitch=-2.0, yaw=glance, closed=0.22)
            await asyncio.sleep(0.28)
            await self.trigger_colibri_blink()

        elif action == "jump":
            await self.jump()

        elif action == "happy_hop":
            self.set_param("Happy", True)
            await self.jump()
            await asyncio.sleep(0.45)
            await self.jump()
            self.set_param("Happy", False)

        elif action == "look_around":
            # Curious glance left then right (or right then left)
            direction = random.choice([-1.0, 1.0])
            await self.turn(speed=0.45 * direction, duration=random.uniform(0.35, 0.65))
            await asyncio.sleep(random.uniform(0.4, 0.9))
            await self.turn(speed=-0.45 * direction, duration=random.uniform(0.4, 0.75))

        elif action == "shift_weight":
            # Subtle side-step + micro turn so she feels alive while standing
            side = random.choice([-0.4, 0.4])
            await self.move(horizontal=side, duration=random.uniform(0.25, 0.45))
            await asyncio.sleep(0.2)
            await self.turn(speed=random.choice([-0.3, 0.3]), duration=random.uniform(0.2, 0.4))

        elif action == "wander_step":
            # Casual autonomous stroll: turn toward a new angle, walk a few steps
            turn_dir = random.choice([-1.0, 1.0])
            await self.turn(speed=0.55 * turn_dir, duration=random.uniform(0.35, 0.9))
            await asyncio.sleep(0.15)
            await self.move(vertical=0.75, duration=random.uniform(0.9, 1.8))

        elif action == "curious_approach":
            await self.move(vertical=0.7, duration=random.uniform(0.7, 1.2))

        elif action == "step_back":
            await self.move(vertical=-0.7, duration=random.uniform(0.6, 1.1))

        elif action == "walk_forward":
            await self.move(vertical=0.85, duration=1.6)

        elif action == "walk_back":
            await self.move(vertical=-0.8, duration=1.3)

        elif action == "strafe_left":
            await self.move(horizontal=-0.8, duration=1.1)

        elif action == "strafe_right":
            await self.move(horizontal=0.8, duration=1.1)

        elif action == "turn_left":
            await self.turn(speed=-0.65, duration=0.75)

        elif action == "turn_right":
            await self.turn(speed=0.65, duration=0.75)

        elif action == "spin":
            await self.turn(speed=0.95, duration=1.7)

    @staticmethod
    def pick_reaction_for_reply(reply_text: str) -> str:
        """
        Chooses a natural, stationary gesture/expression while talking so she keeps staring
        right at you instead of turning away mid-sentence.
        """
        low = reply_text.lower()
        if any(w in low for w in ("hi ", "hey", "hello", "welcome", "nice to meet", "sup", "wave", "bye", "see ya")):
            return "wave"
        if any(w in low for w in ("haha", "lol", "yay", "hype", "lets go", "let's go", "awesome", "love", "congrats", "nice", "gold star", "bare minimum")):
            return random.choice(["clap", "cheer", "stare_smug"])
        if any(w in low for w in ("dance", "vibe", "music", "groove", "party", "watch and learn")):
            return "dance"
        if any(w in low for w in ("hug", "cuddle", "come here", "three seconds")):
            return "hug"
        if any(w in low for w in ("stick", "bonk", "weapon", "hit", "poke")):
            return "stick"
        if any(w in low for w in ("excuse", "rude", "what?", "who?", "fault", "blame", "shut", "unplug", "npc")):
            return random.choice(["sassy_react", "point", "stare_smug"])
        if any(w in low for w in ("you", "your", "that", "there", "look at", "check out", "point")):
            return random.choice(["point", "stare_smug", "sassy_react"])
        if any(w in low for w in ("cute", "blush", "flatter", "sweet", "aw", "shy", "pretty", "headpat")):
            return "blush"
        return random.choice(["stare_smug", "stare_smug", "cutesy_idle", "point", "sassy_react"])


def detect_movement_request(text: str) -> tuple[str | None, str | None]:
    """
    Lightweight check if someone asked Luna to move, emote with her hands, or change autonomy mode.
    Returns (mode_change, action):
        mode_change: 'roam' | 'chill' | None
        action:      action name for VRChatOSC.perform() | None
    """
    low = text.lower().strip()

    # Autonomy mode switches (controllable by voice or chatbox in-game!)
    if re.search(
        r"\b(chill|chill out|chill here|chill with me|stay here|stay put|stay with me|"
        r"hang out here|stop moving|stop walking|stop wandering|stop roaming|"
        r"don'?t wander|don'?t move|hold still|wait here|just talk)\b",
        low,
    ):
        return ("chill", "stop")
    if re.search(
        r"\b(follow|follow me|come with me|walk with me|stay behind me|come here|come to me|let'?s go)\b",
        low,
    ):
        return ("follow", None)
    if re.search(
        r"\b(roam|wander|explore|walk around|move around|do what you want|be free|"
        r"you'?re free|go explore|have freedom)\b",
        low,
    ):
        return ("roam", "wander_step")

    # Hand emotes & full-body animations (VRCEmote 1..8 + facial expressions)
    if re.search(r"\b(wave|say hi|raise your hand|use your hand)\b", low):
        return (None, "wave")
    if re.search(r"\b(clap|applaud)\b", low):
        return (None, "clap")
    if re.search(r"\b(point|point at)\b", low):
        return (None, "point")
    if re.search(r"\b(cheer|hands up|arms up|celebrate)\b", low):
        return (None, "cheer")
    if re.search(r"\b(dance|boogie|bust a move)\b", low):
        return (None, "dance")
    if re.search(r"\b(backflip|do a flip|flip)\b", low):
        return (None, "backflip")
    if re.search(r"\b(blush|you'?re cute|so cute|pretty)\b", low):
        return (None, "blush")
    if re.search(r"\b(play dead|faint|die|fall over)\b", low):
        return (None, "play_dead")
    if re.search(r"\b(sad|pout|shy)\b", low):
        return (None, "sad_kick")
    if re.search(r"\b(headpat|pat your head|good girl)\b", low):
        return (None, "headpat")
    if re.search(r"\b(hug|hug me|give me a hug|cuddle|hold me|come hug)\b", low):
        return (None, "hug")
    if re.search(r"\b(boop|poke your nose)\b", low):
        return (None, "boop")
    if re.search(r"\b(stick|sticc|bonk|pull out your stick)\b", low):
        return (None, "stick")

    # Direct physical locomotion requests
    if re.search(r"\b(walk forward|step forward|closer)\b", low):
        return (None, "walk_forward")
    if re.search(r"\b(back up|step back|move back|go back)\b", low):
        return (None, "walk_back")
    if re.search(r"\b(turn left|look left)\b", low):
        return (None, "turn_left")
    if re.search(r"\b(turn right|look right)\b", low):
        return (None, "turn_right")
    if re.search(r"\b(strafe left|move left|step left)\b", low):
        return (None, "strafe_left")
    if re.search(r"\b(strafe right|move right|step right)\b", low):
        return (None, "strafe_right")
    if re.search(r"\b(spin|twirl|turn around|do a 360)\b", low):
        return (None, "spin")
    if re.search(r"\b(jump|hop|bounce)\b", low):
        return (None, "happy_hop")
    if re.search(r"\b(look around)\b", low):
        return (None, "look_around")

    return (None, None)
