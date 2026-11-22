import os
import re
import asyncio
import subprocess
import sounddevice as sd
import soundfile as sf
import numpy as np
import io
import time
import shutil
from dotenv import load_dotenv

load_dotenv()

FFMPEG = shutil.which("ffmpeg") or r"C:\Users\Worldwide\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_MP3 = os.path.join(BASE_DIR, f"temp_audio_{os.getpid()}.mp3")
TEMP_WAV = os.path.join(BASE_DIR, f"temp_audio_{os.getpid()}.wav")

KOKORO_DIR = os.path.join(BASE_DIR, "models", "kokoro")
KOKORO_MODEL = os.path.join(KOKORO_DIR, "kokoro-v1.0.int8.onnx")
KOKORO_VOICES = os.path.join(KOKORO_DIR, "voices-v1.0.bin")

_KOKORO_INSTANCE = None
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _get_kokoro():
    """Singleton loader for Kokoro-82M ONNX emotional TTS (~0.0 GB GPU VRAM)."""
    global _KOKORO_INSTANCE
    if _KOKORO_INSTANCE is None:
        os.makedirs(KOKORO_DIR, exist_ok=True)
        for fn, dst in (("voices-v1.0.bin", KOKORO_VOICES), ("kokoro-v1.0.int8.onnx", KOKORO_MODEL)):
            if not os.path.exists(dst):
                import urllib.request
                url = f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/{fn}"
                print(f"[Kokoro-82M] Downloading {fn}...")
                urllib.request.urlretrieve(url, dst)
        from kokoro_onnx import Kokoro
        _KOKORO_INSTANCE = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
    return _KOKORO_INSTANCE


def _upsample_24k_to_48k_stereo(samples_24k: np.ndarray) -> np.ndarray:
    """
    Converts Kokoro's 24,000 Hz mono float32 output to 48,000 Hz stereo (N, 2)
    directly in memory (<0.2ms, zero disk I/O or ffmpeg subprocess needed) so
    VB-Cable and VRChat (48000 Hz) receive crystal-clear audio.
    """
    s = np.asarray(samples_24k, dtype=np.float32).ravel()
    if len(s) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    s48 = np.empty(len(s) * 2, dtype=np.float32)
    s48[0::2] = s
    s48[1:-1:2] = 0.5 * (s[:-1] + s[1:])
    s48[-1] = s[-1]
    return np.column_stack((s48, s48))


def mp3_to_wav(mp3_path, wav_path):
    """Convert MP3 to 48kHz stereo WAV using ffmpeg so it matches VB-Cable & VRChat (48000 Hz)."""
    if os.path.exists(wav_path):
        os.remove(wav_path)
    subprocess.run(
        [FFMPEG, "-y", "-i", mp3_path, "-ar", "48000", "-ac", "2", wav_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )


class VoiceHandler:
    def __init__(self, mirror_device: str | None = None):
        self.tts_enabled = os.getenv('TTS_ENABLED', 'true').lower() in ('true', '1', 'yes')
        self.audio_device = os.getenv('AUDIO_DEVICE', '').strip()
        self.mirror_device = (mirror_device or os.getenv('MIRROR_AUDIO_DEVICE', '')).strip()
        self.elevenlabs_key = os.getenv('ELEVENLABS_API_KEY', '').strip()
        self.voice_id = os.getenv('ELEVENLABS_VOICE_ID', '').strip()
        self.tts_engine = os.getenv('TTS_ENGINE', 'kokoro').lower().strip()
        if self.tts_engine in ('none', 'off', 'disabled'):
            self.tts_enabled = False
        self.edge_voice = os.getenv('EDGE_VOICE', 'en-US-AnaNeural').strip()
        self.voice_pitch = os.getenv('VOICE_PITCH', '-5Hz').strip()
        self.voice_rate = os.getenv('VOICE_RATE', '+0%').strip()
        self.ref_audio_path = os.getenv('REF_AUDIO_PATH', '').strip()
        self.device_id = None
        self.mirror_device_id = None
        self.is_speaking = False

        # Pre-warm Kokoro-82M in memory if enabled
        if self.tts_enabled and self.tts_engine == 'kokoro' and os.path.exists(KOKORO_MODEL):
            try:
                _get_kokoro()
            except Exception as e:
                print(f"[Kokoro Init Warning] {e}")

    @staticmethod
    def _find_output_device(query: str) -> tuple[int | None, str | None]:
        if not query:
            return None, None
        for i, device in enumerate(sd.query_devices()):
            if query.lower() in device['name'].lower() and device['max_output_channels'] > 0:
                return i, device['name']
        return None, None

    def set_audio_device(self):
        if not self.tts_enabled:
            return
        if self.audio_device:
            idx, name = self._find_output_device(self.audio_device)
            if idx is not None:
                self.device_id = idx
                print(f"Audio device set: {name} (ID: {idx})")
            else:
                print(f"Audio device not found: {self.audio_device}, using default")
        if self.mirror_device:
            idx, name = self._find_output_device(self.mirror_device)
            if idx is not None and idx != self.device_id:
                self.mirror_device_id = idx
                print(f"Mirror audio device set: {name} (ID: {idx})")

    def _play_samples(self, data, samplerate: int):
        """Play audio samples on primary device and optional mirror device (e.g. Headphones + CABLE Input)."""
        if self.mirror_device_id is not None:
            try:
                arr = np.asarray(data, dtype='float32')
                if arr.ndim == 1:
                    arr = arr.reshape(-1, 1)
                channels = arr.shape[1]
                chunk_size = 4096
                with sd.OutputStream(samplerate=samplerate, device=self.device_id, channels=channels, dtype='float32') as s1, \
                     sd.OutputStream(samplerate=samplerate, device=self.mirror_device_id, channels=channels, dtype='float32') as s2:
                    for start in range(0, len(arr), chunk_size):
                        block = arr[start:start + chunk_size]
                        s1.write(block)
                        s2.write(block)
                return
            except Exception as e:
                print(f"[Audio Mirror Warning] Falling back to primary device: {e}")

        sd.play(data, samplerate, device=self.device_id)
        sd.wait()

    async def generate_and_play_audio(self, text, emotion: str | None = None):
        if not self.tts_enabled or not text:
            return
        try:
            self.is_speaking = True
            if self.tts_engine == 'kokoro':
                success = await self._play_kokoro(text, emotion=emotion)
                if not success:
                    print("Kokoro-82M failed, falling back to edge-tts...")
                    await self._play_edge_tts(text)
            elif self.tts_engine == 'gptsovits':
                success = await self._play_gptsovits(text)
                if not success:
                    print("GPT-SoVITS failed, falling back to edge-tts...")
                    await self._play_edge_tts(text)
            elif self.tts_engine == 'elevenlabs' and self.elevenlabs_key and self.voice_id:
                success = await self._play_elevenlabs(text)
                if not success:
                    print("ElevenLabs failed, falling back to edge-tts...")
                    await self._play_edge_tts(text)
            else:
                await self._play_edge_tts(text)
        except Exception as e:
            print(f"Audio error: {e}")
        finally:
            time.sleep(0.3)
            self.is_speaking = False

    def _get_kokoro_emotion_style(self, k, text: str, emotion: str | None = None):
        """
        Dynamically blends Kokoro-82M 256-D voice style vectors (`af_bella`, `af_heart`,
        `af_nicole`, `af_nova`) and adjusts pacing based on Luna's emotion / action so
        her voice shifts between sassy-smug, warm/blushing, and upbeat!
        """
        em = (emotion or "").lower()
        low = text.lower()

        bella = k.get_voice_style("af_bella")    # Expressive, sassy, bright
        heart = k.get_voice_style("af_heart")    # Warm, cute, soft
        nicole = k.get_voice_style("af_nicole")  # Smug, deadpan, husky

        if em in ("hug", "blush", "headpat", "boop") or any(w in low for w in ("hug", "cute", "fine,", "shoulder")):
            # Softer, sweeter tsundere tone
            style = 0.52 * heart + 0.28 * bella + 0.20 * nicole
            speed = 0.96
        elif em in ("wave", "clap", "cheer", "dance", "happy_hop") or "!" in text:
            # Lively, expressive teasing energy
            style = 0.65 * bella + 0.25 * heart + 0.10 * nicole
            speed = 1.05
        elif em in ("sassy_react", "stare_smug", "point", "stick") or "?" in text:
            # Confident, deadpan-smug roast delivery
            style = 0.52 * bella + 0.33 * nicole + 0.15 * heart
            speed = 0.99
        else:
            style = 0.55 * bella + 0.25 * heart + 0.20 * nicole
            speed = 1.01

        return style, speed

    async def _play_kokoro(self, text: str, emotion: str | None = None) -> bool:
        """
        Sentence-pipelined Kokoro-82M synthesis: generates and plays the first sentence
        in ~0.6s while synthesizing the remaining sentence in parallel!
        """
        try:
            k = await asyncio.to_thread(_get_kokoro)
            style, speed = self._get_kokoro_emotion_style(k, text, emotion=emotion)

            clauses = [c.strip() for c in _SENTENCE_SPLIT_RE.split(text.strip()) if c.strip()]
            if not clauses:
                return False

            def _synth(clause: str):
                s24, _ = k.create(clause, voice=style, speed=speed, lang="en-us")
                return _upsample_24k_to_48k_stereo(s24)

            # Synthesize first sentence (~0.6s)
            next_task = asyncio.create_task(asyncio.to_thread(_synth, clauses[0]))
            for i in range(len(clauses)):
                audio_48k = await next_task
                # Immediately kick off synthesis of sentence i+1 while sentence i plays!
                if i + 1 < len(clauses):
                    next_task = asyncio.create_task(asyncio.to_thread(_synth, clauses[i + 1]))
                if len(audio_48k) > 0:
                    await asyncio.to_thread(self._play_samples, audio_48k, 48000)
            return True
        except Exception as e:
            print(f"Kokoro-82M error: {e}")
            return False

    async def _play_gptsovits(self, text):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://127.0.0.1:9880",
                    json={
                        "refer_wav_path": self.ref_audio_path,
                        "prompt_text": "Okay, let's see what chat is saying today.",
                        "prompt_language": "en",
                        "text": text,
                        "text_language": "en"
                    }
                )
            if response.status_code != 200:
                print(f"GPT-SoVITS returned status {response.status_code}")
                return False
            data, samplerate = sf.read(io.BytesIO(response.content))
            self._play_samples(data, samplerate)
            return True
        except Exception as e:
            print(f"GPT-SoVITS error: {e}")
            return False

    async def _play_elevenlabs(self, text):
        try:
            from elevenlabs.client import ElevenLabs
            client = ElevenLabs(api_key=self.elevenlabs_key)
            audio = await asyncio.to_thread(
                client.text_to_speech.convert,
                voice_id=self.voice_id,
                text=text,
                model_id="eleven_turbo_v2",
            )
            with open(TEMP_MP3, "wb") as f:
                for chunk in audio:
                    f.write(chunk)

            await asyncio.to_thread(mp3_to_wav, TEMP_MP3, TEMP_WAV)

            data, samplerate = sf.read(TEMP_WAV)
            self._play_samples(data, samplerate)
            return True
        except Exception as e:
            print(f"ElevenLabs error: {e}")
            return False
        finally:
            for p in [TEMP_MP3, TEMP_WAV]:
                if os.path.exists(p):
                    os.remove(p)

    async def _play_edge_tts(self, text):
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, self.edge_voice, pitch=self.voice_pitch, rate=self.voice_rate)
            await communicate.save(TEMP_MP3)

            await asyncio.to_thread(mp3_to_wav, TEMP_MP3, TEMP_WAV)

            data, samplerate = sf.read(TEMP_WAV)
            self._play_samples(data, samplerate)
        except Exception as e:
            print(f"edge-tts error: {e}")
        finally:
            for p in [TEMP_MP3, TEMP_WAV]:
                if os.path.exists(p):
                    os.remove(p)
