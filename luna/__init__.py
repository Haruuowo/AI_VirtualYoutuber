"""
Luna's brain - the reusable core shared by every platform.

    from luna import AIHandler, VoiceHandler

Importing this package loads the project-root .env, so any platform
(VRChat, web stage, Minecraft, desktop studio, live stream) gets the same
API keys, voice and persona settings no matter where it is launched from.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

if getattr(sys, "frozen", False):  # PyInstaller build
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    ROOT = Path(__file__).resolve().parent.parent

ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH)

from luna.brain import AIHandler, DEFAULT_SYSTEM_PROMPT, PERSONA_FILE, load_persona_config  # noqa: E402


def __getattr__(name):
    # VoiceHandler pulls in sounddevice/soundfile, so only import it when asked for.
    if name == "VoiceHandler":
        from luna.voice import VoiceHandler
        return VoiceHandler
    raise AttributeError(f"module 'luna' has no attribute {name!r}")


__all__ = ["AIHandler", "VoiceHandler", "DEFAULT_SYSTEM_PROMPT", "PERSONA_FILE", "load_persona_config", "ROOT", "ENV_PATH"]
