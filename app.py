"""
AI VTuber Studio — Desktop Control Center & Sandbox

A lightweight desktop app (CustomTkinter) so anyone can configure, test,
and launch their own AI VTuber without touching an IDE, terminal, or .env files.

Run:  python app.py    (or double-click start_studio.bat)
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import customtkinter as ctk
from dotenv import dotenv_values

from ai import AIHandler, DEFAULT_SYSTEM_PROMPT, PERSONA_FILE, load_persona_config
from core.intent import is_luna_mentioned, parse_minecraft_intent
from voice import VoiceHandler

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

EDGE_VOICES = [
    "en-US-AnaNeural",
    "en-US-AriaNeural",
    "en-US-JennyNeural",
    "en-US-MichelleNeural",
    "en-US-AvaNeural",
    "en-GB-SoniaNeural",
    "ja-JP-NanamiNeural",
]

GROQ_MODELS = [
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def get_output_devices() -> list[str]:
    """List system audio output devices for VTube Studio / speaker routing."""
    names = ["Default System Output"]
    try:
        import sounddevice as sd
        seen = set()
        for dev in sd.query_devices():
            if dev.get("max_output_channels", 0) > 0:
                n = dev.get("name", "").strip()
                if n and n not in seen:
                    seen.add(n)
                    names.append(n)
    except Exception:
        pass
    return names


def read_env_dict() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(ENV_PATH).items()}


def write_env_updates(updates: dict[str, str]):
    """Update .env preserving comments and existing keys."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                new_lines.append(f"{k}={remaining.pop(k)}")
                continue
        new_lines.append(line)
    for k, v in remaining.items():
        new_lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = str(v)


class ProcessController:
    """Manages a child process (Demo Server, Full Stream, or Minecraft Bot) and streams logs."""

    def __init__(self, name: str, cmd: list[str], cwd: Path, on_log, on_state):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.on_log = on_log
        self.on_state = on_state
        self.proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
            self.on_state(True)
            self.on_log(f"[{self.name}] Started ({' '.join(self.cmd)})\n")
            threading.Thread(target=self._reader, daemon=True).start()
        except Exception as e:
            self.on_log(f"[{self.name} ERROR] Failed to start: {e}\n")
            self.on_state(False)

    def _reader(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.on_log(f"[{self.name}] {line}")
        code = self.proc.wait()
        self.on_log(f"[{self.name}] Stopped (exit code {code})\n")
        self.on_state(False)

    def stop(self):
        if not self.running or not self.proc:
            return
        try:
            self.proc.terminate()
        except Exception:
            pass


class VTuberStudioApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        # Pictochat / NDS Slate aesthetic
        ctk.set_appearance_mode("light")
        self.configure(fg_color="#d7dbe4")

        self.title("☆ PictoChat · Luna AI VTuber Studio ☆")
        self.geometry("960x700")
        self.minsize(860, 620)

        self.env = read_env_dict()
        self.persona = load_persona_config()
        self.sandbox_ai: AIHandler | None = None
        self.demo_url = "http://localhost:8000"

        # Process controllers
        self.proc_demo = ProcessController(
            "Browser Demo",
            [sys.executable, str(ROOT / "demo" / "server.py")],
            ROOT,
            self._append_log,
            lambda r: self._update_svc_badge("demo", r),
        )
        self.proc_stream = ProcessController(
            "Full Stream",
            [sys.executable, str(ROOT / "main.py")],
            ROOT,
            self._append_log,
            lambda r: self._update_svc_badge("stream", r),
        )
        self.proc_mc = ProcessController(
            "MC Bot",
            ["node", "minecraft_bot.js"],
            ROOT / "minecraft",
            self._append_log,
            lambda r: self._update_svc_badge("mc", r),
        )

        self._build_header()
        self._build_tabs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Top Header ──────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color="#eef1f6", border_width=2, border_color="#5c6478", corner_radius=8, height=60)
        hdr.pack(fill="x", side="top", padx=14, pady=(10, 4))

        title_box = ctk.CTkFrame(hdr, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=8)
        self.hdr_title = ctk.CTkLabel(
            title_box,
            text=f"★ {self.persona['name']} · AI VTuber Studio ★",
            font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
            text_color="#4b5d84",
        )
        self.hdr_title.pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="Nintendo DS PictoChat Style Control Center · Zero IDE setup required.",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280",
        ).pack(anchor="w")

        self.save_status = ctk.CTkLabel(hdr, text="", font=ctk.CTkFont(size=12, weight="bold"), text_color="#2b7a4b")
        self.save_status.pack(side="right", padx=12)

        ctk.CTkButton(
            hdr,
            text="Save Settings",
            width=130,
            fg_color="#4b5d84",
            hover_color="#3b4b6b",
            border_width=1.5,
            border_color="#5c6478",
            text_color="#ffffff",
            font=ctk.CTkFont(weight="bold"),
            command=self.save_all,
        ).pack(side="right", padx=14, pady=10)

    # ── Tabs ────────────────────────────────────────────────────────────────

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(
            self,
            corner_radius=8,
            fg_color="#f9fafd",
            segmented_button_fg_color="#c8d3e6",
            segmented_button_selected_color="#4b5d84",
            segmented_button_selected_hover_color="#3b4b6b",
            segmented_button_unselected_color="#c8d3e6",
            segmented_button_unselected_hover_color="#b4c2da",
            text_color="#2a2f3a",
        )
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(4, 14))

        t_launch = self.tabs.add("1. Launch & Demo Test")
        t_persona = self.tabs.add("2. Persona & AI Brain")
        t_voice = self.tabs.add("3. Voice & Audio")
        t_conn = self.tabs.add("4. Stream & Minecraft")
        t_tut = self.tabs.add("5. How to Setup & Tutorial")

        self._build_launch_tab(t_launch)
        self._build_persona_tab(t_persona)
        self._build_voice_tab(t_voice)
        self._build_conn_tab(t_conn)
        self._build_tutorial_tab(t_tut)

    # ── Tab 1: Launch & Demo Sandbox ────────────────────────────────────────

    def _build_launch_tab(self, parent):
        parent.grid_columnconfigure(0, weight=5)
        parent.grid_columnconfigure(1, weight=7)
        parent.grid_rowconfigure(0, weight=1)

        # Left column: Service cards + Live logs
        left = ctk.CTkFrame(parent, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 8), pady=4)
        left.grid_rowconfigure(3, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self.svc_widgets = {}
        self._make_service_card(
            left, 0, "demo",
            "Browser Stage & Live2D Demo",
            "Opens Live2D avatar + Brain inspector in browser",
            self.proc_demo,
            extra_btn=("Open Browser", lambda: webbrowser.open(self.demo_url)),
        )
        self._make_service_card(
            left, 1, "stream",
            "Full Stream Director (main.py)",
            "Twitch + TikTok + Mic + VTube Studio audio",
            self.proc_stream,
        )
        self._make_service_card(
            left, 2, "mc",
            "Minecraft Node.js Bridge",
            "Runs minecraft/minecraft_bot.js via Node",
            self.proc_mc,
        )

        log_frame = ctk.CTkFrame(left)
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Service Logs", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2)
        )
        self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=11), height=140)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        # Right column: Built-in Demo Chat Sandbox
        right = ctk.CTkFrame(parent)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 4), pady=4)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        top_bar = ctk.CTkFrame(right, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            top_bar,
            text="Quick Test Sandbox (In-App Demo)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        self.sandbox_tts_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            top_bar, text="Speak replies", variable=self.sandbox_tts_var, font=ctk.CTkFont(size=12)
        ).pack(side="right")

        self.chat_box = ctk.CTkTextbox(right, font=ctk.CTkFont(size=13), wrap="word")
        self.chat_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self.chat_box.insert(
            "end",
            "Welcome to the built-in test sandbox!\n"
            "Type a message or click a prompt below to test your VTuber's personality, "
            "intent detection, and voice right here inside the app.\n\n",
        )
        self.chat_box.configure(state="disabled")

        chips = ctk.CTkFrame(right, fg_color="transparent")
        chips.grid(row=2, column=0, sticky="ew", padx=12, pady=2)
        for prompt in ["Who made you?", "Luna, mine some diamonds", "Luna, protect me", "Roast my setup"]:
            ctk.CTkButton(
                chips,
                text=prompt,
                height=26,
                fg_color=("gray80", "#252542"),
                hover_color=("gray70", "#36365e"),
                font=ctk.CTkFont(size=11),
                command=lambda p=prompt: self._send_sandbox(p),
            ).pack(side="left", padx=(0, 6))

        input_row = ctk.CTkFrame(right, fg_color="transparent")
        input_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 10))
        input_row.grid_columnconfigure(0, weight=1)
        self.sandbox_entry = ctk.CTkEntry(input_row, placeholder_text="Say something to test your VTuber...")
        self.sandbox_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.sandbox_entry.bind("<Return>", lambda _e: self._send_sandbox())
        ctk.CTkButton(input_row, text="Send", width=80, command=self._send_sandbox).grid(row=0, column=1)

    def _make_service_card(self, parent, row, key, title, subtitle, controller: ProcessController, extra_btn=None):
        card = ctk.CTkFrame(parent)
        card.grid(row=row, column=0, sticky="ew", pady=4)
        card.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkLabel(info, text=title, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(info, text=subtitle, font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w")

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e", padx=10)

        badge = ctk.CTkLabel(btns, text="● STOPPED", text_color="gray60", font=ctk.CTkFont(size=11, weight="bold"))
        badge.pack(side="left", padx=8)

        if extra_btn:
            ctk.CTkButton(btns, text=extra_btn[0], width=100, height=28, command=extra_btn[1]).pack(
                side="left", padx=4
            )

        toggle_btn = ctk.CTkButton(
            btns,
            text="Start",
            width=76,
            height=28,
            fg_color="#10b981",
            hover_color="#059669",
            command=lambda: controller.stop() if controller.running else (self.save_all(silent=True), controller.start()),
        )
        toggle_btn.pack(side="left", padx=4)
        self.svc_widgets[key] = (badge, toggle_btn)

    # ── Tab 2: Persona & AI Brain ───────────────────────────────────────────

    def _build_persona_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        # Character details
        p_card = ctk.CTkFrame(parent)
        p_card.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        p_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(p_card, text="VTuber Identity", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 6)
        )
        ctk.CTkLabel(p_card, text="Character Name:").grid(row=1, column=0, sticky="w", padx=14, pady=6)
        self.char_name_var = ctk.StringVar(value=self.persona["name"])
        ctk.CTkEntry(p_card, textvariable=self.char_name_var).grid(row=1, column=1, sticky="ew", padx=14, pady=6)

        ctk.CTkLabel(p_card, text="Creator / Streamer Name:").grid(row=2, column=0, sticky="w", padx=14, pady=6)
        self.creator_var = ctk.StringVar(value=self.persona["creator"])
        ctk.CTkEntry(p_card, textvariable=self.creator_var).grid(row=2, column=1, sticky="ew", padx=14, pady=(6, 12))

        # Brain provider
        b_card = ctk.CTkFrame(parent)
        b_card.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        b_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(b_card, text="AI Brain Provider", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 6)
        )

        ctk.CTkLabel(b_card, text="Provider:").grid(row=1, column=0, sticky="w", padx=14, pady=4)
        self.provider_var = ctk.StringVar(value=self.env.get("AI_PROVIDER", "groq"))
        ctk.CTkOptionMenu(b_card, values=["groq", "ollama"], variable=self.provider_var).grid(
            row=1, column=1, sticky="ew", padx=14, pady=4
        )

        ctk.CTkLabel(b_card, text="Groq API Key:").grid(row=2, column=0, sticky="w", padx=14, pady=4)
        self.groq_key_var = ctk.StringVar(value=self.env.get("GROQ_API_KEY", ""))
        ctk.CTkEntry(b_card, textvariable=self.groq_key_var, show="*").grid(
            row=2, column=1, sticky="ew", padx=14, pady=4
        )

        ctk.CTkLabel(b_card, text="Groq Model:").grid(row=3, column=0, sticky="w", padx=14, pady=4)
        self.groq_model_var = ctk.StringVar(value=self.env.get("GROQ_MODEL", GROQ_MODELS[0]))
        ctk.CTkComboBox(b_card, values=GROQ_MODELS, variable=self.groq_model_var).grid(
            row=3, column=1, sticky="ew", padx=14, pady=4
        )

        ctk.CTkLabel(b_card, text="Local Ollama Model:").grid(row=4, column=0, sticky="w", padx=14, pady=(4, 10))
        self.ollama_model_var = ctk.StringVar(value=self.env.get("OLLAMA_MODEL", "dolphin-llama3:8b"))
        ctk.CTkEntry(b_card, textvariable=self.ollama_model_var).grid(
            row=4, column=1, sticky="ew", padx=14, pady=(4, 10)
        )

        # System Prompt Editor
        sp_frame = ctk.CTkFrame(parent)
        sp_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
        sp_frame.grid_rowconfigure(1, weight=1)
        sp_frame.grid_columnconfigure(0, weight=1)

        sp_hdr = ctk.CTkFrame(sp_frame, fg_color="transparent")
        sp_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 4))
        ctk.CTkLabel(
            sp_hdr,
            text="Personality & System Prompt (Tell your AI VTuber how to act)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            sp_hdr,
            text="Reset to Default Luna Prompt",
            height=24,
            fg_color="gray30",
            command=lambda: (self.prompt_box.delete("1.0", "end"), self.prompt_box.insert("1.0", DEFAULT_SYSTEM_PROMPT)),
        ).pack(side="right")

        self.prompt_box = ctk.CTkTextbox(sp_frame, font=ctk.CTkFont(size=12))
        self.prompt_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        self.prompt_box.insert("1.0", self.persona["system_prompt"])

    # ── Tab 3: Voice & Audio ────────────────────────────────────────────────

    def _build_voice_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        card = ctk.CTkFrame(parent)
        card.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="Text-to-Speech & VTube Studio Routing", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8)
        )

        ctk.CTkLabel(card, text="TTS Engine:").grid(row=1, column=0, sticky="w", padx=16, pady=6)
        self.tts_engine_var = ctk.StringVar(value=self.env.get("TTS_ENGINE", "edge"))
        self.tts_dropdown = ctk.CTkOptionMenu(
            card,
            values=["edge", "elevenlabs", "gptsovits", "off"],
            variable=self.tts_engine_var,
            command=self._on_tts_engine_changed,
        )
        self.tts_dropdown.grid(row=1, column=1, sticky="w", padx=16, pady=6)

        # Dynamic Engine Cards Container
        self.engine_card_container = ctk.CTkFrame(card, fg_color="transparent")
        self.engine_card_container.grid(row=2, column=0, columnspan=2, sticky="ew", padx=0, pady=2)
        self.engine_card_container.grid_columnconfigure(1, weight=1)

        # 1. Edge-TTS Card
        self.edge_frame = ctk.CTkFrame(self.engine_card_container, fg_color="transparent")
        self.edge_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.edge_frame, text="Edge-TTS Voice (Free, Zero Setup):").grid(
            row=0, column=0, sticky="w", padx=16, pady=4
        )
        self.edge_voice_var = ctk.StringVar(value=self.env.get("EDGE_VOICE", "en-US-AnaNeural"))
        ctk.CTkComboBox(self.edge_frame, values=EDGE_VOICES, variable=self.edge_voice_var, width=320).grid(
            row=0, column=1, sticky="w", padx=16, pady=4
        )

        # 2. ElevenLabs Card
        self.eleven_frame = ctk.CTkFrame(self.engine_card_container, fg_color="transparent")
        self.eleven_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.eleven_frame, text="ElevenLabs API Key:").grid(row=0, column=0, sticky="w", padx=16, pady=4)
        self.eleven_key_var = ctk.StringVar(value=self.env.get("ELEVENLABS_API_KEY", ""))
        ctk.CTkEntry(self.eleven_frame, textvariable=self.eleven_key_var, show="*", width=360).grid(
            row=0, column=1, sticky="w", padx=16, pady=4
        )
        ctk.CTkLabel(self.eleven_frame, text="ElevenLabs Voice ID:").grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.eleven_id_var = ctk.StringVar(value=self.env.get("ELEVENLABS_VOICE_ID", ""))
        ctk.CTkEntry(self.eleven_frame, textvariable=self.eleven_id_var, width=360).grid(
            row=1, column=1, sticky="w", padx=16, pady=4
        )

        # Audio Output Device
        out_row = ctk.CTkFrame(card, fg_color="transparent")
        out_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=0, pady=4)
        out_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out_row, text="Audio Output Device (for VTube Studio / Speakers):").grid(
            row=0, column=0, sticky="w", padx=16, pady=6
        )
        devices = get_output_devices()
        cur_dev = self.env.get("AUDIO_DEVICE", "").strip() or "Default System Output"
        if cur_dev not in devices:
            devices.append(cur_dev)
        self.audio_dev_var = ctk.StringVar(value=cur_dev)
        ctk.CTkComboBox(out_row, values=devices, variable=self.audio_dev_var, width=360).grid(
            row=0, column=1, sticky="w", padx=16, pady=6
        )

        self._update_tts_visibility(self.tts_engine_var.get())

        test_row = ctk.CTkFrame(card, fg_color="transparent")
        test_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 16))
        self.test_voice_entry = ctk.CTkEntry(
            test_row, width=420, placeholder_text="Hey chat! Testing my voice right now."
        )
        self.test_voice_entry.insert(0, "Hey chat! Testing my voice right now.")
        self.test_voice_entry.pack(side="left", padx=(0, 10))
        self.test_voice_btn = ctk.CTkButton(test_row, text="🔊 Test Voice Now", command=self._test_voice)
        self.test_voice_btn.pack(side="left")
        self.voice_status_lbl = ctk.CTkLabel(test_row, text="", font=ctk.CTkFont(size=12))
        self.voice_status_lbl.pack(side="left", padx=12)

    def _on_tts_engine_changed(self, choice: str):
        self._update_tts_visibility(choice)
        self.save_all(silent=True)

    def _update_tts_visibility(self, engine: str):
        engine = (engine or "edge").lower()
        if engine == "elevenlabs":
            self.edge_frame.pack_forget()
            self.eleven_frame.pack(fill="x", expand=True)
        elif engine == "edge":
            self.eleven_frame.pack_forget()
            self.edge_frame.pack(fill="x", expand=True)
        else:
            self.edge_frame.pack_forget()
            self.eleven_frame.pack_forget()

    # ── Tab 4: Stream & Minecraft Connections ───────────────────────────────

    def _build_conn_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        # Left: Stream Inputs
        s_card = ctk.CTkFrame(parent)
        s_card.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        s_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(s_card, text="Live Stream Chat & Mic", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8)
        )

        self.mic_en_var = ctk.BooleanVar(value=self.env.get("MIC_ENABLED", "true").lower() == "true")
        ctk.CTkSwitch(s_card, text="Enable Microphone (Voice STT)", variable=self.mic_en_var).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=14, pady=6
        )

        self.twitch_en_var = ctk.BooleanVar(value=self.env.get("TWITCH_ENABLED", "true").lower() == "true")
        ctk.CTkSwitch(s_card, text="Enable Twitch Chat", variable=self.twitch_en_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=14, pady=6
        )

        ctk.CTkLabel(s_card, text="Twitch Channel:").grid(row=3, column=0, sticky="w", padx=14, pady=4)
        self.twitch_chan_var = ctk.StringVar(value=self.env.get("TWITCH_CHANNEL", ""))
        ctk.CTkEntry(s_card, textvariable=self.twitch_chan_var).grid(row=3, column=1, sticky="ew", padx=14, pady=4)

        ctk.CTkLabel(s_card, text="Twitch OAuth Token:").grid(row=4, column=0, sticky="w", padx=14, pady=4)
        self.twitch_tok_var = ctk.StringVar(value=self.env.get("TWITCH_TOKEN", ""))
        ctk.CTkEntry(s_card, textvariable=self.twitch_tok_var, show="*").grid(
            row=4, column=1, sticky="ew", padx=14, pady=4
        )

        self.tiktok_en_var = ctk.BooleanVar(value=self.env.get("TIKTOK_ENABLED", "true").lower() == "true")
        ctk.CTkSwitch(s_card, text="Enable TikTok Live", variable=self.tiktok_en_var).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 6)
        )

        ctk.CTkLabel(s_card, text="TikTok Username:").grid(row=6, column=0, sticky="w", padx=14, pady=(4, 14))
        self.tiktok_user_var = ctk.StringVar(value=self.env.get("TIKTOK_USERNAME", ""))
        ctk.CTkEntry(s_card, textvariable=self.tiktok_user_var).grid(
            row=6, column=1, sticky="ew", padx=14, pady=(4, 14)
        )

        # Right: Minecraft Bot
        m_card = ctk.CTkFrame(parent)
        m_card.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        m_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(m_card, text="Minecraft Autonomous Bot", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8)
        )

        self.mc_en_var = ctk.BooleanVar(value=self.env.get("MINECRAFT_ENABLED", "false").lower() == "true")
        ctk.CTkSwitch(m_card, text="Enable Minecraft Autonomous Brain", variable=self.mc_en_var).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=14, pady=6
        )

        ctk.CTkLabel(m_card, text="Server Host:").grid(row=2, column=0, sticky="w", padx=14, pady=6)
        self.mc_host_var = ctk.StringVar(value=self.env.get("MC_HOST", "localhost"))
        ctk.CTkEntry(m_card, textvariable=self.mc_host_var).grid(row=2, column=1, sticky="ew", padx=14, pady=6)

        ctk.CTkLabel(m_card, text="Server Port:").grid(row=3, column=0, sticky="w", padx=14, pady=6)
        self.mc_port_var = ctk.StringVar(value=self.env.get("MC_PORT", "25565"))
        ctk.CTkEntry(m_card, textvariable=self.mc_port_var).grid(row=3, column=1, sticky="ew", padx=14, pady=6)

        ctk.CTkLabel(m_card, text="Bot Username:").grid(row=4, column=0, sticky="w", padx=14, pady=6)
        self.mc_user_var = ctk.StringVar(value=self.env.get("MC_USERNAME", "Luna"))
        ctk.CTkEntry(m_card, textvariable=self.mc_user_var).grid(row=4, column=1, sticky="ew", padx=14, pady=6)

        ctk.CTkLabel(m_card, text="MC Version:").grid(row=5, column=0, sticky="w", padx=14, pady=(6, 14))
        self.mc_ver_var = ctk.StringVar(value=self.env.get("MC_VERSION", "1.20.1"))
        ctk.CTkEntry(m_card, textvariable=self.mc_ver_var).grid(row=5, column=1, sticky="ew", padx=14, pady=(6, 14))

    # ── Tab 5: Tutorial & Screenshots ───────────────────────────────────────

    def _build_tutorial_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(parent, fg_color="#f9fafd")
        scroll.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        scroll.grid_columnconfigure(0, weight=1)

        # Header Title
        ctk.CTkLabel(
            scroll,
            text="☆ QUICK-START GUIDE & TUTORIAL ☆",
            font=ctk.CTkFont(family="Plus Jakarta Sans", size=18, weight="bold"),
            text_color="#4b5d84",
        ).pack(anchor="w", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            scroll,
            text="Follow these 4 simple steps to have your AI VTuber live and answering chat in under 2 minutes.",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
        ).pack(anchor="w", padx=16, pady=(0, 14))

        steps = [
            ("STEP 1: ZERO ASSET DOWNLOADS", 
             "The Live2D model (Konata/Luna), WebGL renderers, lip-sync scripts, and fallback response brain are bundled right into the app. No downloading 3D files or manual textures required."),
            ("STEP 2: CONNECT AI BRAIN (TAB 2)", 
             "• Cloud Mode (Fastest): Get a free API key at console.groq.com and paste it in Tab 2.\n• Local Mode (Offline): Install Ollama (ollama.com) and run 'ollama run qwen2.5:7b' for 100% private offline streaming."),
            ("STEP 3: AUDIO & VOICE OUTPUT (TAB 3)", 
             "• Testing: Choose 'edge' for free neural TTS and click '🔊 Test Voice Now'.\n• VTube Studio Lip-Sync: Choose your Virtual Audio Cable ('CABLE Input') as your Audio Output Device so VTube Studio can sync mouth movements."),
            ("STEP 4: LAUNCH STREAM OR DEMO (TAB 1)", 
             "• Click 'Start' on the Browser Stage & Live2D Demo to test Luna in your web browser with simulated chat & Brain Inspector.\n• Click 'Start' on the Full Stream Director to connect live Twitch and TikTok chat simultaneously!")
        ]

        for title, desc in steps:
            card = ctk.CTkFrame(scroll, fg_color="#ffffff", border_width=1.5, border_color="#b0b8c8", corner_radius=6)
            card.pack(fill="x", padx=14, pady=6)
            ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#4b5d84",
            ).pack(anchor="w", padx=12, pady=(8, 2))
            ctk.CTkLabel(
                card,
                text=desc,
                justify="left",
                font=ctk.CTkFont(size=11),
                text_color="#2a2f3a",
            ).pack(anchor="w", padx=12, pady=(0, 8))

        # Screenshots
        shot_path_1 = ROOT / "docs" / "screenshots" / "demo_showcase.png"
        shot_path_2 = ROOT / "docs" / "screenshots" / "studio_app.png"

        try:
            from PIL import Image

            if shot_path_1.exists():
                ctk.CTkLabel(
                    scroll,
                    text="★ Visual Reference: PictoChat Browser Stage & Live2D Avatar ★",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    text_color="#4b5d84",
                ).pack(anchor="w", padx=14, pady=(18, 4))

                img1 = Image.open(str(shot_path_1))
                w, h = img1.size
                ratio = 600 / float(w)
                ctk_img1 = ctk.CTkImage(light_image=img1, size=(600, int(h * ratio)))
                lbl1 = ctk.CTkLabel(scroll, image=ctk_img1, text="")
                lbl1.pack(anchor="w", padx=14, pady=4)

            if shot_path_2.exists():
                ctk.CTkLabel(
                    scroll,
                    text="★ Visual Reference: AI VTuber Studio App Setup ★",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    text_color="#4b5d84",
                ).pack(anchor="w", padx=14, pady=(18, 4))

                img2 = Image.open(str(shot_path_2))
                w, h = img2.size
                ratio = 600 / float(w)
                ctk_img2 = ctk.CTkImage(light_image=img2, size=(600, int(h * ratio)))
                lbl2 = ctk.CTkLabel(scroll, image=ctk_img2, text="")
                lbl2.pack(anchor="w", padx=14, pady=4)
        except Exception as e:
            ctk.CTkLabel(scroll, text=f"(Screenshots preview unavailable: {e})").pack(anchor="w", padx=14)

    # ── Actions & Logic ─────────────────────────────────────────────────────

    def save_all(self, silent: bool = False):
        # Save persona.json
        os.makedirs(os.path.dirname(PERSONA_FILE), exist_ok=True)
        persona_data = {
            "name": self.char_name_var.get().strip() or "Luna",
            "creator": self.creator_var.get().strip() or "Kai",
            "system_prompt": self.prompt_box.get("1.0", "end").strip() or DEFAULT_SYSTEM_PROMPT,
        }
        with open(PERSONA_FILE, "w", encoding="utf-8") as f:
            json.dump(persona_data, f, indent=2)
        self.persona = persona_data
        self.hdr_title.configure(text=f"{persona_data['name']} · AI VTuber Studio")

        # Save .env
        dev = self.audio_dev_var.get().strip()
        if dev == "Default System Output":
            dev = ""
        updates = {
            "AI_PROVIDER": self.provider_var.get(),
            "GROQ_API_KEY": self.groq_key_var.get().strip(),
            "GROQ_MODEL": self.groq_model_var.get().strip(),
            "OLLAMA_MODEL": self.ollama_model_var.get().strip(),
            "TTS_ENGINE": self.tts_engine_var.get(),
            "EDGE_VOICE": self.edge_voice_var.get().strip(),
            "AUDIO_DEVICE": dev,
            "ELEVENLABS_API_KEY": self.eleven_key_var.get().strip(),
            "ELEVENLABS_VOICE_ID": self.eleven_id_var.get().strip(),
            "MIC_ENABLED": "true" if self.mic_en_var.get() else "false",
            "TWITCH_ENABLED": "true" if self.twitch_en_var.get() else "false",
            "TWITCH_CHANNEL": self.twitch_chan_var.get().strip(),
            "TWITCH_TOKEN": self.twitch_tok_var.get().strip(),
            "TIKTOK_ENABLED": "true" if self.tiktok_en_var.get() else "false",
            "TIKTOK_USERNAME": self.tiktok_user_var.get().strip(),
            "MINECRAFT_ENABLED": "true" if self.mc_en_var.get() else "false",
            "MC_HOST": self.mc_host_var.get().strip(),
            "MC_PORT": self.mc_port_var.get().strip(),
            "MC_USERNAME": self.mc_user_var.get().strip(),
            "MC_VERSION": self.mc_ver_var.get().strip(),
        }
        write_env_updates(updates)
        self.sandbox_ai = None  # Refresh sandbox AI with new persona/model
        if not silent:
            self.save_status.configure(text="✓ Saved!")
            self.after(2500, lambda: self.save_status.configure(text=""))

    def _append_log(self, text: str):
        m = re.search(r"http://localhost:\d+", text)
        if m:
            self.demo_url = m.group(0)

        def _do():
            self.log_box.insert("end", text)
            self.log_box.see("end")

        self.after(0, _do)

    def _update_svc_badge(self, key: str, running: bool):
        def _do():
            badge, btn = self.svc_widgets[key]
            if running:
                badge.configure(text="● RUNNING", text_color="#34d399")
                btn.configure(text="Stop", fg_color="#ef4444", hover_color="#dc2626")
            else:
                badge.configure(text="● STOPPED", text_color="gray60")
                btn.configure(text="Start", fg_color="#10b981", hover_color="#059669")

        self.after(0, _do)

    def _append_chat(self, line: str):
        def _do():
            self.chat_box.configure(state="normal")
            self.chat_box.insert("end", line + "\n")
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")

        self.after(0, _do)

    def _send_sandbox(self, preset_text: str | None = None):
        text = preset_text if preset_text is not None else self.sandbox_entry.get().strip()
        if not text:
            return
        if preset_text is None:
            self.sandbox_entry.delete(0, "end")

        self.save_all(silent=True)
        user = self.persona.get("creator", "You")
        char_name = self.persona.get("name", "Luna")

        direct = is_luna_mentioned(text) or True
        cmd = parse_minecraft_intent(text, username=user)
        route_tag = f"P1 direct · intent={cmd['action'] if cmd else 'conversation'}"
        self._append_chat(f"{user}: {text}\n  ↳ [Brain: {route_tag}]")

        def _worker():
            try:
                if self.sandbox_ai is None:
                    self.sandbox_ai = AIHandler()
                reply = self.sandbox_ai.generate_response(text, "Studio", user)
                reply = reply or "(No response — check your API key or Ollama connection)"
                self._append_chat(f"{char_name}: {reply}\n")
                if self.sandbox_tts_var.get():
                    vh = VoiceHandler()
                    vh.set_audio_device()
                    asyncio.run(vh.generate_and_play_audio(reply))
            except Exception as e:
                self._append_chat(f"[Error]: {e}\n")

        threading.Thread(target=_worker, daemon=True).start()

    def _test_voice(self):
        self.save_all(silent=True)
        sample = self.test_voice_entry.get().strip() or "Hello! Testing my voice."
        engine = self.tts_engine_var.get()
        self.voice_status_lbl.configure(text=f"Generating voice ({engine})...", text_color="#38bdf8")
        self.test_voice_btn.configure(state="disabled")

        def _worker():
            err_msg = None
            try:
                vh = VoiceHandler()
                vh.set_audio_device()
                asyncio.run(vh.generate_and_play_audio(sample))
            except Exception as e:
                err_msg = str(e)
                self._append_log(f"[Voice Test Error] {e}\n")

            def _done():
                self.test_voice_btn.configure(state="normal")
                if err_msg:
                    self.voice_status_lbl.configure(text=f"⚠ Error: {err_msg[:40]}", text_color="#f87171")
                else:
                    self.voice_status_lbl.configure(text=f"✓ Played ({engine})", text_color="#34d399")
                self.after(3500, lambda: self.voice_status_lbl.configure(text=""))

            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_close(self):
        for p in (self.proc_demo, self.proc_stream, self.proc_mc):
            p.stop()
        self.destroy()


if __name__ == "__main__":
    app = VTuberStudioApp()
    app.mainloop()

