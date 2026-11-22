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


def _apply_neuro_contour_48k(
    data_48k: np.ndarray,
    clause: str = "",
    emotion: str | None = None,
    base_pitch: float = 1.0,
) -> np.ndarray:
    """
    Applies a dynamic time-varying F0 pitch & volume inflection contour to a 48kHz
    audio waveform in <0.5ms so questions rise ('?!'), sassy exclamations bounce ('!'),
    and hugs/blushes sound warm and expressive instead of flat/monotone.
    """
    arr = np.asarray(data_48k, dtype=np.float32)
    if arr.ndim == 2:
        s = arr[:, 0].ravel()
    else:
        s = arr.ravel()
    n_in = len(s)
    if n_in < 32:
        return np.zeros((0, 2), dtype=np.float32)

    # Trim dead leading/trailing silence so sentences flow together naturally
    nz = np.flatnonzero(np.abs(s) > 0.006)
    if len(nz) > 0:
        pad = min(1400, n_in)  # ~30ms breath pad
        start_i = max(0, int(nz[0]) - pad)
        end_i = min(n_in, int(nz[-1]) + pad)
        s = s[start_i:end_i]
        n_in = len(s)

    em = (emotion or "").lower()
    text = clause.strip()
    is_question = text.endswith("?") or "?!" in text
    is_exclaim = "!" in text or em in ("sassy_react", "cheer", "clap", "wave", "dance", "point", "stick")
    is_soft = em in ("hug", "blush", "headpat", "boop")

    u = np.linspace(0.0, 1.0, n_in, dtype=np.float32)
    contour = np.ones(n_in, dtype=np.float32) * float(base_pitch)

    if is_question:
        # Expressive incredulous / teasing question inflection: slight mid dip then +8.5% rising tail!
        contour *= 1.0 - 0.025 * np.sin(np.pi * u) + 0.085 * np.clip((u - 0.60) / 0.40, 0.0, 1.0) ** 1.35
    elif is_exclaim:
        # Bouncy sassy/energetic arc: +6.5% punch on the opening reaction + lively mid wave
        contour *= 1.0 + 0.065 * np.exp(-((u - 0.18) / 0.16) ** 2) + 0.03 * np.sin(2.5 * np.pi * u)
    elif is_soft:
        # Warm, sweet tsundere contour
        contour *= 0.98 + 0.035 * np.sin(1.5 * np.pi * u)
    else:
        # Lively conversational contour
        contour *= 1.0 + 0.04 * np.sin(2.0 * np.pi * u) - 0.02 * u

    avg_step = float(np.mean(contour))
    n_out = max(1, int(n_in / avg_step))
    u_out = np.linspace(0.0, 1.0, n_out, dtype=np.float32)
    steps_out = np.interp(u_out, u, contour)
    in_indices = np.clip(np.cumsum(steps_out), 0.0, float(n_in - 1))

    s48 = np.interp(in_indices, np.arange(n_in, dtype=np.float32), s).astype(np.float32)

    if is_exclaim or is_question:
        env = 1.0 + 0.15 * np.exp(-((u_out - 0.20) / 0.22) ** 2)
        s48 = np.clip(s48 * env, -0.98, 0.98)

    return np.column_stack((s48, s48))


def _shape_vtuber_voice_48k_stereo(
    samples_24k: np.ndarray,
    clause: str = "",
    emotion: str | None = None,
    base_pitch: float = 1.32,
) -> np.ndarray:
    """Upsamples 24kHz Kokoro audio to 48kHz and shifts pitch into a young anime VTuber register."""
    s = np.asarray(samples_24k, dtype=np.float32).ravel()
    if len(s) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    s48 = np.empty(len(s) * 2, dtype=np.float32)
    s48[0::2] = s
    s48[1:-1:2] = 0.5 * (s[:-1] + s[1:])
    s48[-1] = s[-1]
    return _apply_neuro_contour_48k(s48, clause=clause, emotion=emotion, base_pitch=base_pitch)


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
        self.tts_engine = os.getenv('TTS_ENGINE', 'edge').lower().strip()
        if self.tts_engine in ('none', 'off', 'disabled'):
            self.tts_enabled = False
        self.edge_voice = os.getenv('EDGE_VOICE', 'en-US-AnaNeural').strip()
        self.voice_pitch = os.getenv('VOICE_PITCH', '+18Hz').strip()
        self.voice_rate = os.getenv('VOICE_RATE', '+12%').strip()
        self.ref_audio_path = os.getenv('REF_AUDIO_PATH', '').strip()
        self.device_id = None
        self.mirror_device_id = None
        self.is_speaking = False

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
            time.sleep(0.25)
            self.is_speaking = False

    def _get_kokoro_emotion_style(self, k, text: str, emotion: str | None = None):
        """
        Blends `af_heart` + `jf_alpha` (high anime girl vocal tract formants) and shifts
        pitch by 1.32x (+4.8 semitones) so Kokoro sounds like a young chibi girl instead
        of a deep adult woman!
        """
        em = (emotion or "").lower()
        heart = k.get_voice_style("af_heart")
        alpha = k.get_voice_style("jf_alpha")
        style = 0.72 * heart + 0.28 * alpha

        if em in ("hug", "blush", "headpat", "boop"):
            return style, 0.78, 1.30
        if em in ("wave", "clap", "cheer", "dance", "happy_hop") or "!" in text:
            return style, 0.84, 1.35
        return style, 0.81, 1.32

    async def _play_kokoro(self, text: str, emotion: str | None = None) -> bool:
        try:
            k = await asyncio.to_thread(_get_kokoro)
            style, synth_speed, base_pitch = self._get_kokoro_emotion_style(k, text, emotion=emotion)

            clauses = [c.strip() for c in _SENTENCE_SPLIT_RE.split(text.strip()) if c.strip()]
            if not clauses:
                return False

            def _synth(clause: str):
                s24, _ = k.create(clause, voice=style, speed=synth_speed, lang="en-us")
                return _shape_vtuber_voice_48k_stereo(
                    s24, clause=clause, emotion=emotion, base_pitch=base_pitch
                )

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
        Returns Neuro-sama style young anime girl pitch (+15Hz..+25Hz) and snappy rate
        (+8%..+18%) dynamically per sentence/emotion for `en-US-AnaNeural`.
        """
        em = (emotion or "").lower()
        c = clause.strip()
        if em in ("hug", "blush", "headpat", "boop"):
            return ("+15Hz", "+7%")
        if "?!" in c or c.endswith("?"):
            return ("+22Hz", "+14%")
        if "!" in c or em in ("sassy_react", "cheer", "clap", "wave", "dance", "point", "stick"):
            return ("+24Hz", "+16%")
        return ("+19Hz", "+12%")

    async def _play_edge_tts(self, text, emotion: str | None = None):
        """
        Neuro-sama style voice pipeline:
          - Uses `en-US-AnaNeural` (young girl vocal tract matching Colibri Blind & Neuro-sama)
          - Pitched UP (+15Hz to +24Hz instead of the old -5Hz deep pitch!) with snappy +12%..+16% cadence
          - Synthesizes sentence-by-sentence with dynamic F0 pitch-contour shaping (`_apply_neuro_contour_48k`)
            so questions rise, sassy comebacks bounce, and hugs soften!
        """
        try:
            import edge_tts
            clauses = [c.strip() for c in _SENTENCE_SPLIT_RE.split(text.strip()) if c.strip()]
            if not clauses:
                return

            async def _synth_clause(idx: int, clause: str) -> np.ndarray:
                pitch, rate = self._get_neuro_edge_prosody(clause, emotion=emotion)
                mp3_p = os.path.join(BASE_DIR, f"temp_audio_{os.getpid()}_{idx}.mp3")
                wav_p = os.path.join(BASE_DIR, f"temp_audio_{os.getpid()}_{idx}.wav")
                try:
                    comm = edge_tts.Communicate(clause, self.edge_voice, pitch=pitch, rate=rate)
                    await comm.save(mp3_p)
                    await asyncio.to_thread(mp3_to_wav, mp3_p, wav_p)
                    data, _ = sf.read(wav_p)
                    return _apply_neuro_contour_48k(data, clause=clause, emotion=emotion, base_pitch=1.0)
                finally:
                    for p in (mp3_p, wav_p):
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except OSError:
                                pass

            # Pipeline clauses: play clause 0 as soon as it's ready while clause 1 synthesizes!
            next_task = asyncio.create_task(_synth_clause(0, clauses[0]))
            for i in range(len(clauses)):
                audio_48k = await next_task
                if i + 1 < len(clauses):
                    next_task = asyncio.create_task(_synth_clause(i + 1, clauses[i + 1]))
                if len(audio_48k) > 0:
                    await asyncio.to_thread(self._play_samples, audio_48k, 48000)
        except Exception as e:
            print(f"edge-tts error: {e}")
