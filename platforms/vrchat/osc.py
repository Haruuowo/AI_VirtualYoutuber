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
    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or os.getenv("VRCHAT_OSC_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("VRCHAT_OSC_PORT", "9000"))
        self.client = SimpleUDPClient(self.host, self.port)
        self._move_lock = asyncio.Lock()

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
        """Streams ~40 Hz cosine-eased analog values (plus optional Desktop movement buttons) over OSC."""
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
                    for addr, target in axes:
                        self.client.send_message(addr, float(target * scale))
                    await asyncio.sleep(min(0.025, dur - elapsed))
            finally:
                for addr, _ in axes:
                    self.client.send_message(addr, 0.0)
                for btn in btns:
                    self.client.send_message(btn, 0)
                if run:
                    self.client.send_message("/input/Run", 0)

    async def move(self, vertical: float = 0.0, horizontal: float = 0.0, duration: float = 1.0, run: bool = False):
        """Walks/strafes cleanly using both smooth analog axes and Desktop MoveForward/MoveBackward buttons."""
        axes: list[tuple[str, float]] = []
        buttons: list[str] = []
        if vertical:
            v = float(max(-1.0, min(1.0, vertical)))
            axes.append(("/input/Vertical", v))
            buttons.append("/input/MoveForward" if v > 0 else "/input/MoveBackward")
        if horizontal:
            axes.append(("/input/Horizontal", float(max(-1.0, min(1.0, horizontal)))))
        if not axes:
            return
        await self._ramp_axes(axes, duration, run=run, buttons=buttons)

    async def turn(self, speed: float = 0.5, duration: float = 0.6):
        """Turns smoothly left (negative speed) or right (positive speed) for `duration` seconds."""
        s = float(max(-1.0, min(1.0, speed)))
        if s == 0.0:
            return
        await self._ramp_axes([("/input/LookHorizontal", s)], duration)

    async def jump(self):
        """Taps the jump input."""
        self.client.send_message("/input/Jump", 1)
        await asyncio.sleep(0.15)
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

    def apply_cutesy_smug_face(self, blush: float = 0.4, happy: bool = True, sleepy_smug: bool = False):
        """Applies a persistent cutesy-smug facial expression across her avatar parameters."""
        self.set_param("Happy", bool(happy))
        self.set_param("Blush", float(max(0.0, min(1.0, blush))))
        self.set_param("Sleepy", bool(sleepy_smug))
        # Colibri Blind expression modifier & standard Emotion slot
        self.set_param("ExpModL", bool(happy))
        self.set_param("Emotion", 1 if happy else 0)
        # Blendtree facial expression support (for avatars using VRCFaceBlendH/V)
        self.set_param("VRCFaceBlendH", 0.55 if happy else 0.0)
        self.set_param("VRCFaceBlendV", 0.35)
        self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.22 if sleepy_smug else 0.14)

    async def lock_gaze_on_offset(self, offset: float):
        """
        Turns Luna's head/body toward `offset` (-1.0 left .. +1.0 right) so she centers
        the speaker in her view and locks her eyes straight on them.
        """
        self.apply_cutesy_smug_face(blush=0.45, happy=True, sleepy_smug=False)
        self.set_eyes(pitch=-2.0, yaw=float(offset * 12.0), closed=0.14)
        if abs(offset) > 0.20:
            speed = 0.45 if offset > 0 else -0.45
            dur = min(0.45, max(0.10, abs(offset) * 0.38))
            await self.turn(speed=speed, duration=dur)
        self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.15)

    async def emote(self, emote_id: int, duration: float = 2.8, happy: bool = True, blush: float = 0.45):
        """
        Triggers a standard VRChat SDK3 hand/body emote (/avatar/parameters/VRCEmote 1..8)
        while maintaining her cutesy-smug facial expression.
        """
        try:
            self.apply_cutesy_smug_face(blush=blush, happy=happy)
            self.set_param("VRCEmote", int(emote_id))
            await asyncio.sleep(max(0.5, duration))
        finally:
            self.set_param("VRCEmote", 0)
            self.apply_cutesy_smug_face(blush=0.35, happy=True)

    # ── High-Level Expressive Actions & Autonomous Body Language ───────────

    async def perform(self, action: str):
        """Executes a named physical behavior, locomotion step, or hand emote."""
        if action == "stop":
            self.stop_all()
            self.set_param("VRCEmote", 0)
            return

        if action == "wave":
            await self.emote(1, duration=2.6, happy=True)

        elif action == "clap":
            await self.emote(2, duration=2.6, happy=True)

        elif action == "point":
            await self.emote(3, duration=2.4)

        elif action == "cheer":
            await self.emote(4, duration=2.8, happy=True)

        elif action == "dance":
            await self.emote(5, duration=5.5, happy=True)

        elif action == "backflip":
            await self.emote(6, duration=2.8, happy=True)

        elif action == "sad_kick":
            await self.emote(7, duration=2.6)

        elif action == "play_dead":
            await self.emote(8, duration=3.5)

        elif action == "blush":
            self.apply_cutesy_smug_face(blush=1.0, happy=True, sleepy_smug=True)
            await self.emote(7, duration=2.5, happy=True, blush=1.0)

        elif action == "stare_smug":
            # Stationary cutesy-smug stare: stays locked on you with a slow smug blink & subtle blush
            self.apply_cutesy_smug_face(blush=0.55, happy=True, sleepy_smug=True)
            self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.28)
            await asyncio.sleep(0.35)
            self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.14)
            await asyncio.sleep(0.6)

        elif action == "cutesy_idle":
            # Natural cutesy-smug idle animation: pure facial/eye animation (no body strafe twitching!)
            # so her avatar's built-in Unity breathing/idle animation plays smoothly.
            self.apply_cutesy_smug_face(blush=0.45, happy=True, sleepy_smug=random.choice([True, False]))
            glance = random.choice([-10.0, 10.0, 0.0])
            self.set_eyes(pitch=-2.0, yaw=glance, closed=0.22)
            await asyncio.sleep(0.30)
            # Slow cute blink then return eyes to center stare
            self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.85)
            await asyncio.sleep(0.14)
            self.set_eyes(pitch=-2.0, yaw=0.0, closed=0.16)

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
        Chooses a natural, stationary gesture while talking so she keeps staring
        right at you instead of turning away mid-sentence.
        """
        low = reply_text.lower()
        if any(w in low for w in ("hi ", "hey", "hello", "welcome", "nice to meet", "sup", "wave", "bye", "see ya")):
            return "wave"
        if any(w in low for w in ("haha", "lol", "yay", "hype", "lets go", "let's go", "awesome", "love", "congrats", "nice")):
            return random.choice(["clap", "cheer", "stare_smug"])
        if any(w in low for w in ("dance", "vibe", "music", "groove", "party")):
            return "dance"
        if any(w in low for w in ("you", "your", "that", "there", "look at", "check out", "point", "blame")):
            return random.choice(["point", "stare_smug", "stare_smug"])
        if any(w in low for w in ("cute", "blush", "flatter", "sweet", "aw", "shy", "pretty")):
            return "blush"
        return random.choice(["stare_smug", "stare_smug", "cutesy_idle", "point"])


def detect_movement_request(text: str) -> tuple[str | None, str | None]:
    """
    Lightweight check if someone asked Luna to move, emote with her hands, or change autonomy mode.
    Returns (mode_change, action):
        mode_change: 'roam' | 'chill' | None
        action:      action name for VRChatOSC.perform() | None
    """
    low = text.lower().strip()

    # Autonomy mode switches
    if re.search(r"\b(stay here|stay put|stop moving|don'?t wander|hold still|chill here|wait here)\b", low):
        return ("chill", "stop")
    if re.search(r"\b(roam|wander|explore|walk around|move around|do what you want|be free)\b", low):
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

    # Direct physical locomotion requests
    if re.search(r"\b(come here|walk forward|step forward|closer|follow me|come to me)\b", low):
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
