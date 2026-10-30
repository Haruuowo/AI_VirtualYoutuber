import os
import asyncio
import subprocess
import sounddevice as sd
import soundfile as sf
import io
import time
import shutil
from dotenv import load_dotenv

load_dotenv()

FFMPEG = shutil.which("ffmpeg") or r"C:\Users\Worldwide\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_MP3 = os.path.join(BASE_DIR, "temp_audio.mp3")
TEMP_WAV = os.path.join(BASE_DIR, "temp_audio.wav")

def mp3_to_wav(mp3_path, wav_path):
    """Convert MP3 to WAV using ffmpeg subprocess directly."""
    if os.path.exists(wav_path):
        os.remove(wav_path)
    subprocess.run(
        [FFMPEG, "-y", "-i", mp3_path, wav_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )

class VoiceHandler:
    def __init__(self):
        self.tts_enabled = os.getenv('TTS_ENABLED', 'true').lower() in ('true', '1', 'yes')
        self.audio_device = os.getenv('AUDIO_DEVICE', '').strip()
        self.elevenlabs_key = os.getenv('ELEVENLABS_API_KEY', '').strip()
        self.voice_id = os.getenv('ELEVENLABS_VOICE_ID', '').strip()
        self.tts_engine = os.getenv('TTS_ENGINE', 'edge').lower().strip()
        if self.tts_engine in ('none', 'off', 'disabled'):
            self.tts_enabled = False
        self.edge_voice = os.getenv('EDGE_VOICE', 'en-US-AriaNeural').strip()
        self.ref_audio_path = os.getenv('REF_AUDIO_PATH', '').strip()
        self.device_id = None
        self.is_speaking = False

    def set_audio_device(self):
        if not self.tts_enabled:
            return
        if self.audio_device:
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if self.audio_device.lower() in device['name'].lower() and device['max_output_channels'] > 0:
                    self.device_id = i
                    print(f"Audio device set: {device['name']} (ID: {i})")
                    return
            print(f"Audio device not found: {self.audio_device}, using default")

    async def generate_and_play_audio(self, text):
        if not self.tts_enabled:
            return
        try:
            self.is_speaking = True
            if self.tts_engine == 'gptsovits':
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
            time.sleep(1.5)
            self.is_speaking = False

    async def _play_gptsovits(self, text):
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "http://localhost:9880",
                    params={
                        "text": text,
                        "text_lang": "en",
                        "ref_audio_path": self.ref_audio_path,
                        "prompt_lang": "en",
                        "media_type": "wav"
                    },
                    timeout=30
                )
            data, samplerate = sf.read(io.BytesIO(response.content))
            sd.play(data, samplerate, device=self.device_id)
            sd.wait()
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
            sd.play(data, samplerate, device=self.device_id)
            sd.wait()
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
            communicate = edge_tts.Communicate(text, self.edge_voice)
            await communicate.save(TEMP_MP3)

            await asyncio.to_thread(mp3_to_wav, TEMP_MP3, TEMP_WAV)

            data, samplerate = sf.read(TEMP_WAV)
            sd.play(data, samplerate, device=self.device_id)
            sd.wait()
        except Exception as e:
            print(f"edge-tts error: {e}")
        finally:
            for p in [TEMP_MP3, TEMP_WAV]:
                if os.path.exists(p):
                    os.remove(p)
