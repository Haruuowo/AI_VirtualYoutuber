import os
import sounddevice as sd
import soundfile as sf
import speech_recognition as sr

class VoiceHandler:
    def __init__(self):
        self.audio_device = os.getenv('AUDIO_DEVICE', '').strip()
        self.elevenlabs_key = os.getenv('ELEVENLABS_API_KEY', '').strip()
        self.voice_id = os.getenv('ELEVENLABS_VOICE_ID', '').strip()
        self.tts_engine = os.getenv('TTS_ENGINE', 'edge').lower().strip()
        self.edge_voice = os.getenv('EDGE_VOICE', 'en-US-AnaNeural').strip()
        self.device_id = None
        self.is_speaking = False
        self.recognizer = sr.Recognizer()

    def set_audio_device(self):
        if self.audio_device:
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if self.audio_device.lower() in device['name'].lower() and device['max_output_channels'] > 0:
                    self.device_id = i
                    print(f"Audio device set: {device['name']} (ID: {i})")
                    return
            print(f"Audio device not found: {self.audio_device}, using default")

    async def generate_and_play_audio(self, text):
        try:
            self.is_speaking = True
            if self.tts_engine == 'elevenlabs' and self.elevenlabs_key and self.voice_id:
                success = await self._play_elevenlabs(text)
                if not success:
                    print("ElevenLabs failed, falling back to edge-tts...")
                    await self._play_edge_tts(text)
            else:
                await self._play_edge_tts(text)
        except Exception as e:
            print(f"Audio error: {e}")
        finally:
            self.is_speaking = False

    async def _play_elevenlabs(self, text):
        try:
            from elevenlabs.client import ElevenLabs
            import asyncio

            client = ElevenLabs(api_key=self.elevenlabs_key)
            
            audio = await asyncio.to_thread(
                client.text_to_speech.convert,
                voice_id=self.voice_id,
                text=text,
                model_id="eleven_turbo_v2",
                voice_settings={
                    "stability": 0.4,
                    "similarity_boost": 0.8,
                    "style": 0.5,
                    "use_speaker_boost": True
                }
            )

            with open("temp_audio.mp3", "wb") as f:
                for chunk in audio:
                    f.write(chunk)

            data, samplerate = sf.read("temp_audio.mp3")
            sd.play(data, samplerate, device=self.device_id)
            sd.wait()
            os.remove("temp_audio.mp3")
            return True

        except Exception as e:
            print(f"ElevenLabs error: {e}")
            return False

    async def _play_edge_tts(self, text):
        import edge_tts
        communicate = edge_tts.Communicate(text, self.edge_voice)
        await communicate.save("temp_audio.mp3")
        data, samplerate = sf.read("temp_audio.mp3")
        sd.play(data, samplerate, device=self.device_id)
        sd.wait()
        os.remove("temp_audio.mp3")

    def listen_for_speech(self):
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source)
                audio = self.recognizer.listen(source, timeout=5)
                return self.recognizer.recognize_google(audio)
        except Exception:
            return None