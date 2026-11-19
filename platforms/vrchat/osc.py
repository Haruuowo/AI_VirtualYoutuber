"""
Thin wrapper around VRChat's OSC API.

VRChat listens for OSC on 127.0.0.1:9000 (enable it in-game:
Action Menu -> Options -> OSC -> Enabled). No mods required.

Docs: https://docs.vrchat.com/docs/osc-overview
"""
import asyncio
import os
import textwrap

from pythonosc.udp_client import SimpleUDPClient

CHATBOX_LIMIT = 144          # VRChat hard limit per chatbox message
PAGE_DELAY = 2.2             # VRChat rate-limits the chatbox; ~2s between pages is safe


class VRChatOSC:
    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or os.getenv("VRCHAT_OSC_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("VRCHAT_OSC_PORT", "9000"))
        self.client = SimpleUDPClient(self.host, self.port)

    # ── Chatbox ────────────────────────────────────────────────────────────
    def typing(self, on: bool):
        """Shows / hides the '...' typing bubble above the avatar."""
        self.client.send_message("/chatbox/typing", bool(on))

    def send_chatbox(self, text: str, notify: bool = False):
        """Sends one message (already <= 144 chars) straight to the chatbox."""
        # args: [text, send immediately (skip keyboard), play notification sfx]
        self.client.send_message("/chatbox/input", [text[:CHATBOX_LIMIT], True, bool(notify)])

    @staticmethod
    def paginate(text: str) -> list[str]:
        text = " ".join(text.split())
        if len(text) <= CHATBOX_LIMIT:
            return [text] if text else []
        # leave room for a " (1/3)" page marker
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

    # ── Avatar parameters (optional, avatar-specific) ──────────────────────
    def set_param(self, name: str, value):
        """Drives an avatar parameter, e.g. a gesture/emote toggle your avatar exposes."""
        self.client.send_message(f"/avatar/parameters/{name}", value)
