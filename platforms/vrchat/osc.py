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
import os
import random
import re
import textwrap

from pythonosc.udp_client import SimpleUDPClient

CHATBOX_LIMIT = 144          # VRChat hard limit per chatbox message
PAGE_DELAY = 2.2             # VRChat rate-limits the chatbox; ~2s between pages is safe


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
        """Immediately zeroes all movement axes so the avatar never gets stuck walking."""
        for addr in ("/input/Vertical", "/input/Horizontal", "/input/LookHorizontal"):
            self.client.send_message(addr, 0.0)
        for addr in ("/input/Jump", "/input/Run"):
            self.client.send_message(addr, 0)

    async def move(self, vertical: float = 0.0, horizontal: float = 0.0, duration: float = 1.0, run: bool = False):
        """Walks/strafes for `duration` seconds and cleanly stops."""
        async with self._move_lock:
            try:
                if run:
                    self.client.send_message("/input/Run", 1)
                if vertical:
                    self.client.send_message("/input/Vertical", float(max(-1.0, min(1.0, vertical))))
                if horizontal:
                    self.client.send_message("/input/Horizontal", float(max(-1.0, min(1.0, horizontal))))
                await asyncio.sleep(max(0.05, duration))
            finally:
                self.client.send_message("/input/Vertical", 0.0)
                self.client.send_message("/input/Horizontal", 0.0)
                if run:
                    self.client.send_message("/input/Run", 0)

    async def turn(self, speed: float = 0.5, duration: float = 0.6):
        """Turns left (negative speed) or right (positive speed) for `duration` seconds."""
        async with self._move_lock:
            try:
                self.client.send_message("/input/LookHorizontal", float(max(-1.0, min(1.0, speed))))
                await asyncio.sleep(max(0.05, duration))
            finally:
                self.client.send_message("/input/LookHorizontal", 0.0)

    async def jump(self):
        """Taps the jump input."""
        self.client.send_message("/input/Jump", 1)
        await asyncio.sleep(0.15)
        self.client.send_message("/input/Jump", 0)

    def set_param(self, name: str, value):
        """Drives an avatar parameter, e.g. VRCEmote or custom gesture toggle."""
        self.client.send_message(f"/avatar/parameters/{name}", value)

    # ── High-Level Expressive Actions & Autonomous Body Language ───────────

    async def perform(self, action: str):
        """Executes a named physical behavior or gesture."""
        if action == "stop":
            self.stop_all()
            return

        if action == "jump":
            await self.jump()

        elif action == "happy_hop":
            await self.jump()
            await asyncio.sleep(0.45)
            await self.jump()

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
        """Chooses a natural body-language gesture that matches what Luna is saying."""
        low = reply_text.lower()
        if any(w in low for w in ("haha", "lol", "yay", "hype", "lets go", "let's go", "awesome", "love")):
            return random.choice(["happy_hop", "jump"])
        if any(w in low for w in ("where", "who", "look", "see", "place", "world", "room", "around")):
            return "look_around"
        if any(w in low for w in ("hi ", "hey", "hello", "welcome", "nice to meet", "sup")):
            return random.choice(["curious_approach", "happy_hop"])
        if any(w in low for w in ("woah", "wait", "chill", "hold up", "scary", "creepy")):
            return "step_back"
        return random.choice(["shift_weight", "look_around", "wander_step"])


def detect_movement_request(text: str) -> tuple[str | None, str | None]:
    """
    Lightweight check if someone asked Luna to move or change her autonomy mode.
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

    # Direct physical requests
    if re.search(r"\b(come here|walk forward|step forward|closer|follow me)\b", low):
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
