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
    """Singleton loader for Kokoro-82M ONNX TTS (~0.0 GB GPU VRAM)."""
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


def _upsample_24k_to_48k_stereo_clean(samples_24k: np.ndarray) -> np.ndarray:
    """
    Clean 2x upsample from 24,000 Hz mono to 48,000 Hz stereo with ZERO pitch
    or formant warping (no chipmunk effect!).
    """
    s = np.asarray(samples_24k, dtype=np.float32).ravel()
    if len(s) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    nz = np.flatnonzero(np.abs(s) > 0.006)
    if len(nz) > 0:
        pad = min(960, len(s))
        s = s[max(0, int(nz[0]) - pad):min(len(s), int(nz[-1]) + pad)]
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
        self.tts_engine = os.getenv('TTS_ENGINE', 'baelz').lower().strip()
        if self.tts_engine in ('none', 'off', 'disabled'):
            self.tts_enabled = False
        self.edge_voice = os.getenv('EDGE_VOICE', 'en-US-AnaNeural').strip()
        self.voice_pitch = os.getenv('VOICE_PITCH', '+6Hz').strip()
        self.voice_rate = os.getenv('VOICE_RATE', '+10%').strip()
        self.ref_audio_path = os.getenv('REF_AUDIO_PATH', '').strip()
        self.device_id = None
        self.mirror_device_id = None
        self.is_speaking = False

        if self.tts_enabled and self.tts_engine in ('baelz', 'bae', 'gura', 'rvc'):
            try:
                from luna.rvc_infer.converter import get_rvc_converter, RVC_VOICE_MODELS
                get_rvc_converter(self.tts_engine)
                label = RVC_VOICE_MODELS.get(self.tts_engine, RVC_VOICE_MODELS["baelz"])[2]
                print(f"[Voice] Loaded {label}")
            except Exception as e:
                print(f"[RVC Init Warning] {e}")
        elif self.tts_enabled and self.tts_engine == 'kokoro' and os.path.exists(KOKORO_MODEL):
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

    async def prepare_audio(self, text: str, emotion: str | None = None) -> tuple[np.ndarray, int] | None:
        """Pre-synthesizes full continuous audio buffer before opening VRChat PTT (/input/Voice=1)."""
        if not self.tts_enabled or not text:
            return None
        if self.tts_engine in ('baelz', 'bae', 'gura', 'rvc'):
            try:
                rvc_48k = await self._synthesize_rvc(text, emotion=emotion)
                if rvc_48k is not None and len(rvc_48k) > 0:
                    return (rvc_48k, 48000)
            except Exception as e:
                print(f"RVC v2 prepare error: {e}")
        return None

    async def play_prepared_audio(self, samples: np.ndarray, samplerate: int):
        """Plays an already-prepared audio buffer cleanly while managing is_speaking state."""
        if not self.tts_enabled or samples is None or len(samples) == 0:
            return
        try:
            self.is_speaking = True
            await asyncio.to_thread(self._play_samples, samples, samplerate)
        except Exception as e:
            print(f"Prepared audio playback error: {e}")
        finally:
            await asyncio.sleep(0.25)
            self.is_speaking = False

    async def generate_and_play_audio(self, text, emotion: str | None = None):
        if not self.tts_enabled or not text:
            return
        try:
            self.is_speaking = True
            if self.tts_engine in ('baelz', 'bae', 'gura', 'rvc'):
                success = await self._play_rvc(text, emotion=emotion)
                if not success:
                    print("RVC v2 failed, falling back to edge-tts...")
                    await self._play_edge_tts(text, emotion=emotion)
            elif self.tts_engine == 'kokoro':
                success = await self._play_kokoro(text, emotion=emotion)
                if not success:
                    print("Kokoro-82M failed, falling back to edge-tts...")
                    await self._play_edge_tts(text, emotion=emotion)
            elif self.tts_engine == 'gptsovits':
                success = await self._play_gptsovits(text)
                if not success:
                    print("GPT-SoVITS failed, falling back to edge-tts...")
                    await self._play_edge_tts(text, emotion=emotion)
            elif self.tts_engine == 'elevenlabs' and self.elevenlabs_key and self.voice_id:
                success = await self._play_elevenlabs(text)
                if not success:
                    print("ElevenLabs failed, falling back to edge-tts...")
                    await self._play_edge_tts(text, emotion=emotion)
            else:
                await self._play_edge_tts(text, emotion=emotion)
        except Exception as e:
            print(f"Audio error: {e}")
        finally:
            await asyncio.sleep(0.25)
            self.is_speaking = False

    async def _synthesize_rvc(self, text: str, emotion: str | None = None) -> np.ndarray | None:
        """
        Synthesizes the full reply with `en-US-AnaNeural` and converts it as ONE continuous
        buffer through the configured RVC v2 model (Hakos Baelz / Gawr Gura) so there is
        never a multi-second mid-sentence gap on CPU.
        """
        full_text = text.strip()
        if not full_text:
            return None

        import edge_tts
        from luna.rvc_infer.converter import get_rvc_converter

        conv = await asyncio.to_thread(get_rvc_converter, self.tts_engine)
        pitch, rate = self._get_neuro_edge_prosody(full_text, emotion=emotion)
        stamp = int(time.time() * 1000)
        mp3_p = os.path.join(BASE_DIR, f"temp_rvc_{os.getpid()}_{stamp}.mp3")
        wav_p = os.path.join(BASE_DIR, f"temp_rvc_{os.getpid()}_{stamp}.wav")
        try:
            comm = edge_tts.Communicate(full_text, self.edge_voice, pitch=pitch, rate=rate)
            await comm.save(mp3_p)
            await asyncio.to_thread(mp3_to_wav, mp3_p, wav_p)
            data, sr = sf.read(wav_p)
            return await asyncio.to_thread(
                conv.convert_to_48k_stereo, data, sr, 0.0, 0.33
            )
        finally:
            for p in (mp3_p, wav_p):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    async def _play_rvc(self, text: str, emotion: str | None = None) -> bool:
        try:
            rvc_48k = await self._synthesize_rvc(text, emotion=emotion)
            if rvc_48k is None or len(rvc_48k) == 0:
                return False
            await asyncio.to_thread(self._play_samples, rvc_48k, 48000)
            return True
        except Exception as e:
            print(f"RVC v2 error: {e}")
            return False

    async def _play_kokoro(self, text: str, emotion: str | None = None) -> bool:
        """Clean Kokoro-82M synthesis (pure af_heart, 1.0x pitch, zero chipmunk formant shift)."""
        try:
            k = await asyncio.to_thread(_get_kokoro)
            style = k.get_voice_style("af_heart")
            em = (emotion or "").lower()
            speed = 0.96 if em in ("hug", "blush", "headpat") else (1.08 if "!" in text else 1.03)

            clauses = [c.strip() for c in _SENTENCE_SPLIT_RE.split(text.strip()) if c.strip()]
            if not clauses:
                return False

            def _synth(clause: str):
                s24, _ = k.create(clause, voice=style, speed=speed, lang="en-us")
                return _upsample_24k_to_48k_stereo_clean(s24)

            next_task = asyncio.create_task(asyncio.to_thread(_synth, clauses[0]))
            for i in range(len(clauses)):
                audio_48k = await next_task
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

    def _get_neuro_edge_prosody(self, clause: str, emotion: str | None = None) -> tuple[str, str]:
        """
        Native neural pitch (+6Hz..+12Hz) & rate (+5%..+12%) inside Microsoft's neural
        vocoder for `en-US-AnaNeural` — keeps vocal tract formants 100% natural (zero
        chipmunk effect!) while giving her Neuro-sama's young age and emotional bounce.
        """
        em = (emotion or "").lower()
        c = clause.strip()
        if em in ("hug", "blush", "headpat", "boop"):
            return ("+5Hz", "+4%")
        if "?!" in c or c.endswith("?"):
            return ("+11Hz", "+10%")
        if "!" in c or em in ("sassy_react", "cheer", "clap", "wave", "dance", "point", "stick"):
            return ("+12Hz", "+12%")
        return ("+8Hz", "+8%")

    async def _play_edge_tts(self, text, emotion: str | None = None):
        """
        Pure neural synthesis via `en-US-AnaNeural` (no NumPy pitch-warping, zero chipmunk
        distortion!) with per-sentence native neural pitch & speed variation.
        """
        try:
            import edge_tts
            clauses = [c.strip() for c in _SENTENCE_SPLIT_RE.split(text.strip()) if c.strip()]
            if not clauses:
                return

            async def _synth_clause(idx: int, clause: str):
                pitch, rate = self._get_neuro_edge_prosody(clause, emotion=emotion)
                mp3_p = os.path.join(BASE_DIR, f"temp_audio_{os.getpid()}_{idx}.mp3")
                wav_p = os.path.join(BASE_DIR, f"temp_audio_{os.getpid()}_{idx}.wav")
                try:
                    comm = edge_tts.Communicate(clause, self.edge_voice, pitch=pitch, rate=rate)
                    await comm.save(mp3_p)
                    await asyncio.to_thread(mp3_to_wav, mp3_p, wav_p)
                    data, samplerate = sf.read(wav_p)
                    return data, samplerate
                finally:
                    for p in (mp3_p, wav_p):
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except OSError:
                                pass

            next_task = asyncio.create_task(_synth_clause(0, clauses[0]))
            for i in range(len(clauses)):
                data, samplerate = await next_task
                if i + 1 < len(clauses):
                    next_task = asyncio.create_task(_synth_clause(i + 1, clauses[i + 1]))
                if len(data) > 0:
                    await asyncio.to_thread(self._play_samples, data, samplerate)
        except Exception as e:
            print(f"edge-tts error: {e}")
