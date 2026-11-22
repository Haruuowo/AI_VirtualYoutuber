"""
Luna's Eyes in VRChat — Real-Time Window Capture, Vision LLM Scene Understanding,
and Visual Target Tracking / Wall Avoidance.
"""
import asyncio
import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
import re
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0

# Explicit 64-bit Win32 handle types so pointers > 32-bit never truncate
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL

DEBUG_FRAME_PATH = Path(__file__).resolve().parent / "last_eye_view.jpg"


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int


def find_vrchat_windows() -> list[tuple[int, tuple[int, int, int, int], int]]:
    """
    Returns all visible non-minimized VRChat windows as [(hwnd, (l, t, r, b), area), ...],
    sorted from smallest area (Luna's 854x480 window) to largest (main window).
    """
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _lparam):
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title.lower() == "vrchat":
            rc = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                w = max(0, rc.right - rc.left)
                h = max(0, rc.bottom - rc.top)
                if w > 200 and h > 200:
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
        if float(rgb.std()) < 4.0:
            return None
        return Image.fromarray(rgb, "RGB")
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def capture_luna_vrchat_frame(max_w: int = 384, max_h: int = 216, save_debug: bool = True) -> Image.Image | None:
    """
    Captures Luna's VRChat window (prefers the smaller VRChat window when two are open),
    saves a debug copy to `platforms/vrchat/last_eye_view.jpg`, and returns a resized PIL Image
    (default 384x216 for low-token Vision LLM calls, or 854x480 for crisp OCR).
    """
    wins = find_vrchat_windows()
    if not wins:
        # Do NOT fall back to full-desktop grab (which captures VS Code / black terminal!)
        return None

    hwnd, (l, t, r, b), _ = wins[0]
    w, h = r - l, b - t
    img = None
    try:
        img = _capture_hwnd_printwindow(hwnd, w, h)
    except Exception:
        img = None
    if img is None:
        try:
            img = ImageGrab.grab(bbox=(l, t, r, b))
        except Exception:
            return None

    resized = img.resize((max_w, max_h), Image.Resampling.BILINEAR)
    if save_debug:
        try:
            resized.save(DEBUG_FRAME_PATH, format="JPEG", quality=78)
        except Exception:
            pass
    return resized


def is_facing_blank_wall(img: Image.Image) -> bool:
    """Fast OpenCV heuristic (<2 ms): checks if the center of Luna's view is a flat wall."""
    arr = np.asarray(img)
    h, w, _ = arr.shape
    crop = arr[int(h * 0.2):int(h * 0.8), int(w * 0.25):int(w * 0.75)]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
    std_val = float(gray.std())
    return edge_ratio < 0.008 and std_val < 18.0


_YOLO_MODEL = None
_YOLO_LOCK = threading.Lock()
_YOLO_WEIGHTS = Path(__file__).resolve().parents[2] / "yolov8n.pt"


def _get_yolo():
    """Lazy-loads and warms up YOLOv8-nano once so per-frame tracking runs in ~32 ms."""
    global _YOLO_MODEL
    if _YOLO_MODEL is not None:
        return _YOLO_MODEL
    with _YOLO_LOCK:
        if _YOLO_MODEL is None:
            from ultralytics import YOLO
            model = YOLO(str(_YOLO_WEIGHTS))
            model.predict(
                np.zeros((216, 384, 3), dtype=np.uint8),
                classes=[0, 15, 16, 77],
                conf=0.18,
                imgsz=384,
                verbose=False,
            )
            _YOLO_MODEL = model
    return _YOLO_MODEL


def detect_avatars_yolo(img: Image.Image) -> list[tuple[float, float, float, float, float]]:
    """
    Runs local YOLOv8-nano avatar detection on `img` (~32 ms at 384px, 0 API tokens).
    Includes COCO classes [0, 15, 16, 77] (person + stylized kemono/chibi avatars) at conf>=0.18,
    and filters out Luna's own 3rd-person head/shoulders at the bottom-center of the frame.
    Returns bounding boxes [(x1, y1, x2, y2, conf), ...] sorted largest/closest first.
    """
    model = _get_yolo()
    w, h = img.size
    results = model.predict(
        np.asarray(img),
        classes=[0, 15, 16, 77],
        conf=0.18,
        imgsz=384,
        verbose=False,
    )
    boxes: list[tuple[float, float, float, float, float]] = []
    for res in results:
        if res.boxes is None:
            continue
        for box in res.boxes:
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            bw, bh = x2 - x1, y2 - y1
            cx = (x1 + x2) / 2.0
            # Filter out Luna's own 3rd-person local avatar head/shoulders in bottom-center
            if y1 > h * 0.50 and y2 > h * 0.92 and (w * 0.33 < cx < w * 0.67) and (bw >= bh * 0.80):
                continue
            conf = float(box.conf[0].item()) if box.conf is not None else 0.5
            boxes.append((x1, y1, x2, y2, conf))
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return boxes


def track_target_person(img: Image.Image) -> tuple[bool, float, float]:
    """
    Finds the closest avatar/person in Luna's VRChat camera view using YOLOv8-nano.
    Returns (found, offset, height_ratio):
        found:        True only when an actual character/person is visible (never walls/posters!)
        offset:       [-1.0 left .. 0.0 center .. +1.0 right]
        height_ratio: fraction of frame height [0.0 .. 1.0] occupied by the avatar
    """
    try:
        boxes = detect_avatars_yolo(img)
    except Exception:
        return (False, 0.0, 0.0)
    if not boxes:
        return (False, 0.0, 0.0)

    w, h = img.size
    x1, y1, x2, y2, _ = boxes[0]
    cx = (x1 + x2) / 2.0
    offset = (cx - (w / 2.0)) / (w / 2.0)
    height_ratio = (y2 - y1) / float(h)
    return (
        True,
        float(max(-1.0, min(1.0, offset))),
        float(max(0.0, min(1.0, height_ratio))),
    )


def find_target_horizontal_offset(img: Image.Image) -> float:
    """Backward-compatible wrapper around `track_target_person`."""
    found, offset, _ = track_target_person(img)
    return offset if found else 0.0


def _parse_vision_json(raw: str) -> dict:
    """Extracts `{"scene": ..., "steer": ...}` even if wrapped in markdown or <think> tags."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return {
                "scene": str(data.get("scene", "")).strip(),
                "steer": str(data.get("steer", "look_around")).strip(),
            }
        except Exception:
            pass
    return {"scene": cleaned[:200], "steer": "look_around"}


class VRChatVision:
    """
    Gives Luna real-time visual perception of her VRChat window.
    Uses Gemini 2.5 Flash Vision (when GEMINI_API_KEY is set) or Groq qwen3.8-27b,
    with an automatic <20 ms zero-token local YOLOv8-nano fallback so she always
    tracks real characters without locking onto walls or burning cloud tokens.
    """

    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash").strip()
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.model = os.getenv("VISION_MODEL", "qwen/qwen3.8-27b").strip()
        self.last_scene: str = ""
        self.last_steer: str = "wander_step"
        self.last_seen_ts: float = 0.0
        self._cloud_cooldown_until: float = 0.0
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    @staticmethod
    def _describe_palette(arr: np.ndarray) -> str:
        mean_rgb = arr.mean(axis=(0, 1))
        r, g, b = float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2])
        brightness = (r + g + b) / 3.0
        light_desc = "bright" if brightness > 150 else ("dim, moody" if brightness < 65 else "cozy")
        if b > r + 22 and b > g + 15:
            tone = "blue-lit"
        elif r > b + 25 and r > g + 15:
            tone = "warm red/amber-toned"
        elif g > r + 20 and g > b + 20:
            tone = "green-tinted"
        elif abs(r - b) < 18 and r > 110 and b > 110 and g < r - 15:
            tone = "neon purple/pink"
        else:
            tone = "neutral-toned"
        return f"{light_desc} {tone}"

    def _analyze_local_opencv(self, img: Image.Image) -> dict:
        """
        Instant (~15 ms, 0 API tokens) YOLOv8-nano character detector + OpenCV room analyzer.
        Detects real avatars, counts them, gauges distance, and chooses non-twitchy steering.
        """
        arr = np.asarray(img)
        h, w, _ = arr.shape
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        palette = self._describe_palette(arr)

        if is_facing_blank_wall(img):
            edges = cv2.Canny(gray, 50, 150)
            left_e = np.count_nonzero(edges[:, : w // 2])
            right_e = np.count_nonzero(edges[:, w // 2 :])
            steer = "turn_left" if left_e > right_e else "turn_right"
            return {"scene": f"Close to a flat wall in a {palette} room; turning toward open space.", "steer": steer}

        # 1. Prefer YOLOv8-nano real humanoid detection (only fall back to HOG if ultralytics import fails)
        yolo_boxes = None
        try:
            yolo_boxes = detect_avatars_yolo(img)
        except Exception:
            yolo_boxes = None

        if yolo_boxes is not None:
            if yolo_boxes:
                x1, y1, x2, y2, _ = yolo_boxes[0]
                cx = ((x1 + x2) / 2.0 - (w / 2.0)) / (w / 2.0)
                height_ratio = (y2 - y1) / float(h)
                count = len(yolo_boxes)
                who = "1 avatar" if count == 1 else f"{count} avatars"

                if cx < -0.20:
                    pos, steer = "on the left side of my view", "turn_left"
                elif cx > 0.20:
                    pos, steer = "on the right side of my view", "turn_right"
                elif height_ratio < 0.25:
                    pos, steer = "ahead across the room", "curious_approach"
                elif height_ratio > 0.78:
                    pos, steer = "right up close in front of me", "step_back"
                else:
                    pos, steer = "right in front of me at conversational distance", "stay"

                return {"scene": f"I see {who}, closest is {pos} in this {palette} room.", "steer": steer}
        else:
            rects, _ = self._hog.detectMultiScale(gray, winStride=(8, 8), padding=(16, 16), scale=1.08)
            if len(rects) > 0:
                x, y, bw, bh = max(rects, key=lambda rect: rect[2] * rect[3])
                cx = (x + bw / 2.0) / float(w)
                size_ratio = (bw * bh) / float(w * h)
                if cx < 0.36:
                    return {"scene": f"I see an avatar on the left side of my view in this {palette} space.", "steer": "turn_left"}
                if cx > 0.64:
                    return {"scene": f"I see an avatar on the right side of my view in this {palette} space.", "steer": "turn_right"}
                if size_ratio > 0.22:
                    return {"scene": f"An avatar is standing right in front of me up close in this {palette} room.", "steer": "stay"}
                return {"scene": f"I see an avatar ahead in the center of this {palette} room.", "steer": "curious_approach"}

        # 2. Open room context when no avatar is visible in frame
        edges = cv2.Canny(gray[int(h * 0.15) : int(h * 0.85), :], 45, 140)
        third = w // 3
        e_left = int(np.count_nonzero(edges[:, :third]))
        e_mid = int(np.count_nonzero(edges[:, third : 2 * third]))
        e_right = int(np.count_nonzero(edges[:, 2 * third :]))

        if e_mid >= e_left and e_mid >= e_right and e_mid > 350:
            return {"scene": f"Looking across an open {palette} VRChat room with details straight ahead.", "steer": "wander_step"}
        if e_left > e_right * 1.35:
            return {"scene": f"Open {palette} room with more detail to my left.", "steer": "turn_left"}
        if e_right > e_left * 1.35:
            return {"scene": f"Open {palette} room with more detail to my right.", "steer": "turn_right"}
        return {"scene": f"Standing in a {palette} VRChat area with open space around me.", "steer": "wander_step"}

    @staticmethod
    def _encode_jpeg_b64(img: Image.Image, quality: int = 72) -> str:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _build_vision_prompt(question: str | None) -> str:
        if question:
            return (
                f"You are Luna's eyes inside VRChat (first-person view). The user asked: \"{question}\"\n"
                "Describe specifically what you see in this VRChat screenshot (any player avatars, their hair/clothes/colors, "
                "nametags, mirrors, signs, furniture, lighting, or walls), and pick a movement from: "
                "[turn_left, turn_right, curious_approach, step_back, wander_step, look_around, stay].\n"
                "Return ONLY valid JSON: {\"scene\": \"...\", \"steer\": \"...\"}"
            )
        return (
            "You are Luna's eyes inside VRChat (first-person game view). "
            "1) In 1 specific sentence, describe what is visible in front of you (any avatars and where they are on screen: left/center/right/close + what they look like, or the room/mirror/wall ahead). "
            "2) Pick ONE movement from [turn_left, turn_right, curious_approach, step_back, wander_step, look_around, stay]: "
            "- If facing a close wall, corner, or obstacle -> turn_left or turn_right\n"
            "- If an avatar/person is on the left side of the screen -> turn_left\n"
            "- If an avatar/person is on the right side of the screen -> turn_right\n"
            "- If an avatar/person is near the center at medium distance -> curious_approach\n"
            "- If an avatar/person is already centered close in front of you -> stay\n"
            "- If open room with no one visible -> wander_step or look_around\n"
            "Return ONLY valid JSON: {\"scene\": \"...\", \"steer\": \"...\"}"
        )

    def _call_gemini_vision_sync(self, b64: str, prompt: str) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
            "generationConfig": {"temperature": 0.25, "maxOutputTokens": 180},
        }
        r = httpx.post(url, json=payload, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_vision_json(raw)

    def _call_vision_sync(self, img: Image.Image, question: str | None = None) -> dict:
        b64 = self._encode_jpeg_b64(img)
        prompt = self._build_vision_prompt(question)

        # Prefer Gemini 2.5 Flash when GEMINI_API_KEY is configured (1,500 free looks/day)
        if self.gemini_key:
            return self._call_gemini_vision_sync(b64, prompt)

        if not self.api_key:
            return self._analyze_local_opencv(img)

        from groq import Groq
        client = Groq(api_key=self.api_key)

        # IMPORTANT: reasoning_effort='none' prevents qwen3.8-27b from exhausting max_tokens inside <think> tags!
        resp = None
        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    max_tokens=160,
                    temperature=0.25,
                    reasoning_effort="none",
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
                break
            except Exception as e:
                if attempt == 0 and ("429" in str(e) or "rate_limit" in str(e)):
                    time.sleep(1.3)
                    continue
                raise
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_vision_json(raw)

    async def look(self, question: str | None = None) -> dict:
        """
        Captures Luna's VRChat window and analyzes what she sees.
        Uses fast local YOLOv8-nano (<20 ms, 0 tokens) immediately if cloud vision is on cooldown.
        """
        img = await asyncio.to_thread(capture_luna_vrchat_frame)
        if img is None:
            return {"scene": "", "steer": "look_around", "wall": False}

        wall = await asyncio.to_thread(is_facing_blank_wall, img)

        if time.time() < self._cloud_cooldown_until:
            res = await asyncio.to_thread(self._analyze_local_opencv, img)
        else:
            try:
                res = await asyncio.to_thread(self._call_vision_sync, img, question)
            except Exception as e:
                err_s = str(e)
                if "429" in err_s or "rate_limit" in err_s or "RESOURCE_EXHAUSTED" in err_s:
                    cooldown = 900.0 if ("TPD" in err_s or "tokens per day" in err_s) else 35.0
                    self._cloud_cooldown_until = time.time() + cooldown
                    print("[eyes] Cloud vision quota reached — switched to Local YOLOv8 Eyes (0ms lag)!")
                else:
                    print(f"[vision] {e}")
                res = await asyncio.to_thread(self._analyze_local_opencv, img)

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


_OCR_ENGINE = None
_OCR_LOCK = threading.Lock()

_UI_NOISE_TEXTS = {
    "visitor", "new user", "user", "known user", "trusted user", "nuisance",
    "friends", "friend", "vrchat", "menu", "mute", "unmute", "fps", "ping",
    "...", "..", ".", "afk", "loading", "offline", "online",
}
_SHORT_VALID_CHAT = {"hi", "hey", "yo", "sup", "bye", "yes", "no", "ok", "okay", "lol", "lmao", "wut", "why", "who", "how"}


def _get_ocr():
    """Lazy-loads local RapidOCR (ONNXRuntime CPU, 0 VRAM, 0 API tokens)."""
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def read_chatbox_bubbles(img: Image.Image) -> list[str]:
    """
    Detects and reads VRChat floating chatbox bubbles (white text inside dark rounded
    rectangles) from an 854x480 capture of Luna's VRChat window.
    """
    if img is None:
        return []
    arr = np.asarray(img)
    h, w, _ = arr.shape
    # Crop out bottom 14% HUD strip
    crop = arr[: int(h * 0.86), :, :]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

    try:
        ocr = _get_ocr()
        result, _ = ocr(crop)
    except Exception:
        return []

    if not result:
        return []

    lines: list[tuple[float, float, str]] = []
    for item in result:
        box, text, conf = item[0], str(item[1]).strip(), float(item[2])
        if conf < 0.62 or not text:
            continue
        low = re.sub(r"\s+", " ", text).strip().lower()
        if low in _UI_NOISE_TEXTS or set(low) <= {".", " ", "-", "_"}:
            continue

        xs = [int(pt[0]) for pt in box]
        ys = [int(pt[1]) for pt in box]
        x1 = max(0, min(xs) - 6)
        x2 = min(w, max(xs) + 6)
        y1 = max(0, min(ys) - 6)
        y2 = min(crop.shape[0], max(ys) + 6)
        if x2 <= x1 or y2 <= y1:
            continue

        patch = gray[y1:y2, x1:x2]
        # VRChat chatbox signature: bright white text (p95 > 185) on dark translucent bubble (p25 < 105)
        if float(np.percentile(patch, 95)) < 185.0 or float(np.percentile(patch, 25)) > 105.0:
            continue

        lines.append((float(min(ys)), float(min(xs)), text))

    if not lines:
        return []

    # Merge vertically adjacent lines belonging to the same multi-line chatbox bubble
    lines.sort(key=lambda it: (it[0], it[1]))
    bubbles: list[str] = []
    cur_y, cur_x, cur_txt = lines[0]
    for y, x, txt in lines[1:]:
        if abs(y - cur_y) <= 28.0 and abs(x - cur_x) <= 90.0:
            cur_txt = f"{cur_txt} {txt}"
            cur_y = y
        else:
            bubbles.append(cur_txt)
            cur_y, cur_x, cur_txt = y, x, txt
    bubbles.append(cur_txt)

    clean_bubbles: list[str] = []
    for b in bubbles:
        b = re.sub(r"\s+", " ", b).strip()
        low = b.lower()
        if low in _UI_NOISE_TEXTS:
            continue
        if len(b) < 4 and low not in _SHORT_VALID_CHAT:
            continue
        clean_bubbles.append(b)
    return clean_bubbles


def is_visual_question(text: str) -> bool:
    """Returns True if the user is asking Luna to look at or describe something visually."""
    low = text.lower()
    return bool(
        re.search(
            r"\b(what do you see|look at|can you see|see me|my avatar|what am i wearing|"
            r"how do i look|rate my avatar|what is in front|where am i|follow me|come to me|"
            r"look here|watch this|in the mirror|what color|describe|around you)\b",
            low,
        )
    )
