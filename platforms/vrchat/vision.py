"""
Luna's Eyes in VRChat — Real-Time Window Capture, Vision LLM Scene Understanding,
and Visual Target Tracking / Wall Avoidance.

How it works:
  1. Finds Luna's VRChat window handle (HWND) on Windows. When two VRChat
     windows are open (your main window + Luna's 854x480 Profile 1 window),
     it automatically picks the smaller window belonging to Luna.
  2. Captures the window using Win32 `PrintWindow(..., PW_RENDERFULLCONTENT)`
     so Luna can STILL see through her window even when your main VRChat window
     is covering it! (Falls back to `PIL.ImageGrab.grab` if needed.)
  3. Sends a compact 640x360 JPEG frame to `qwen/qwen3.8-27b` (multimodal vision)
     to get both:
       * A concise description of what is in front of her (avatars, mirrors,
         room details, text/signs)
       * A visual steering decision (`turn_left`, `turn_right`, `approach`,
         `unstick_wall`, or `stay`) so she avoids walking into walls and can
         visually turn/walk toward people she sees!
"""
import asyncio
import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
import re
import time

import cv2
import numpy as np
from PIL import Image, ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def find_vrchat_windows() -> list[tuple[int, tuple[int, int, int, int], int]]:
    """
    Returns all visible VRChat windows as [(hwnd, (l, t, r, b), area), ...],
    sorted from smallest area (Luna's 854x480 window) to largest (main window).
    """
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "vrchat" in buf.value.strip().lower():
            rc = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                w = max(0, rc.right - rc.left)
                h = max(0, rc.bottom - rc.top)
                if w > 100 and h > 100:
                    found.append((int(hwnd), (rc.left, rc.top, rc.right, rc.bottom), w * h))
        return True

    user32.EnumWindows(enum_cb, 0)
    found.sort(key=lambda item: item[2])
    return found


def _capture_hwnd_printwindow(hwnd: int, width: int, height: int) -> Image.Image | None:
    """Captures a window via Win32 PrintWindow even if another window overlaps it."""
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not ok:
            return None
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bmi), 0)
        if lines != height:
            return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 4))
        rgb = arr[:, :, [2, 1, 0]]  # BGRA -> RGB
        # Verify it's not an all-black unrendered surface
        if float(rgb.mean()) < 2.0:
            return None
        return Image.fromarray(rgb, "RGB")
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def capture_luna_vrchat_frame(max_w: int = 640, max_h: int = 360) -> Image.Image | None:
    """
    Captures Luna's VRChat window (prefers the smaller VRChat window when two are open)
    and resizes it to `max_w x max_h`.
    """
    wins = find_vrchat_windows()
    img = None
    if wins:
        hwnd, (l, t, r, b), _ = wins[0]
        w, h = r - l, b - t
        img = _capture_hwnd_printwindow(hwnd, w, h)
        if img is None:
            try:
                img = ImageGrab.grab(bbox=(l, t, r, b))
            except Exception:
                img = None
    if img is None:
        try:
            img = ImageGrab.grab()
        except Exception:
            return None
    return img.resize((max_w, max_h), Image.Resampling.BILINEAR)


def is_facing_blank_wall(img: Image.Image) -> bool:
    """
    Fast OpenCV heuristic (<2 ms): checks if the center of Luna's view has very
    low edge density / texture variance (e.g. nose pressed against a flat wall).
    """
    arr = np.asarray(img)
    h, w, _ = arr.shape
    crop = arr[int(h * 0.2):int(h * 0.8), int(w * 0.25):int(w * 0.75)]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
    std_val = float(gray.std())
    return edge_ratio < 0.008 and std_val < 18.0


class VRChatVision:
    """Gives Luna real-time visual perception of her VRChat window."""

    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.model = os.getenv("VISION_MODEL", "qwen/qwen3.8-27b").strip()
        self.last_scene: str = ""
        self.last_steer: str = "wander_step"
        self.last_seen_ts: float = 0.0

    @staticmethod
    def _encode_jpeg_b64(img: Image.Image, quality: int = 72) -> str:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _call_vision_sync(self, img: Image.Image, question: str | None = None) -> dict:
        if not self.api_key:
            return {"scene": "", "steer": "look_around"}

        from groq import Groq
        client = Groq(api_key=self.api_key)
        b64 = self._encode_jpeg_b64(img)

        if question:
            prompt = (
                f"You are Luna's eyes inside VRChat. The user asked: \"{question}\"\n"
                "Describe clearly and concisely (in 1-2 short sentences) what you see in this VRChat view "
                "(any avatars, their appearance/outfits, the room/environment, mirrors, signs, or objects), "
                "and also decide the best movement action from: "
                "[turn_left, turn_right, curious_approach, step_back, wander_step, look_around, stay].\n"
                "Respond in JSON: {\"scene\": \"...\", \"steer\": \"...\"}"
            )
        else:
            prompt = (
                "You are Luna's eyes inside VRChat (first-person view). "
                "1) In 1 short sentence, describe what is directly in front of you (any player avatars and where they are on screen: left/center/right/close, or if you are facing a wall/corner/mirror/open room). "
                "2) Pick ONE physical movement from [turn_left, turn_right, curious_approach, step_back, wander_step, look_around]: "
                "- If facing a close wall, door frame, or obstacle -> turn_left or turn_right\n"
                "- If a person/avatar is visible on the left -> turn_left\n"
                "- If a person/avatar is visible on the right -> turn_right\n"
                "- If a person/avatar is centered at medium distance -> curious_approach\n"
                "- If a person/avatar is right in your face (very close) -> step_back or look_around\n"
                "- If open space -> wander_step\n"
                "Respond ONLY with valid JSON: {\"scene\": \"...\", \"steer\": \"...\"}"
            )

        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=110,
            temperature=0.3,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Extract JSON block even if wrapped in markdown
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                return {
                    "scene": str(data.get("scene", "")).strip(),
                    "steer": str(data.get("steer", "look_around")).strip(),
                }
            except Exception:
                pass
        return {"scene": raw[:180], "steer": "look_around"}

    async def look(self, question: str | None = None) -> dict:
        """
        Captures Luna's VRChat window and analyzes what she sees.
        Returns {'scene': str, 'steer': str, 'wall': bool}.
        """
        img = await asyncio.to_thread(capture_luna_vrchat_frame)
        if img is None:
            return {"scene": "", "steer": "look_around", "wall": False}

        wall = await asyncio.to_thread(is_facing_blank_wall, img)
        try:
            res = await asyncio.to_thread(self._call_vision_sync, img, question)
        except Exception as e:
            print(f"[vision] {e}")
            res = {"scene": "", "steer": "turn_right" if wall else "look_around"}

        steer = res.get("steer", "look_around")
        if wall and steer in ("wander_step", "curious_approach", "walk_forward"):
            steer = "turn_right"
        if steer not in ("turn_left", "turn_right", "curious_approach", "step_back", "wander_step", "look_around", "stay", "happy_hop"):
            steer = "look_around"

        scene = res.get("scene", "")
        if scene:
            self.last_scene = scene
            self.last_steer = steer
            self.last_seen_ts = time.time()
        return {"scene": scene, "steer": steer, "wall": wall}


def is_visual_question(text: str) -> bool:
    """Returns True if the user is asking Luna to look at or describe something visually."""
    low = text.lower()
    return bool(
        re.search(
            r"\b(what do you see|look at|can you see|see me|my avatar|what am i wearing|"
            r"how do i look|rate my avatar|what is in front|where am i|follow me|come to me|"
            r"look here|watch this|in the mirror|what color)\b",
            low,
        )
    )
