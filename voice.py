import os
import asyncio
import sounddevice as sd
import soundfile as sf
import edge_tts
import speech_recognition as sr

class VoiceHandler:
    def __init__(self):
        self.audio_device = os.getenv('AUDIO_DEVICE')
        self.device_id = None
        self.voice = "en-US-AnaNeural"
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
            temp_file = "temp_audio.mp3"
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(temp_file)
            data, samplerate = sf.read(temp_file)
            sd.play(data, samplerate, device=self.device_id)
            sd.wait()
            os.remove(temp_file)
        except Exception as e:
            print(f"Audio error: {e}")

    def listen_for_speech(self):
        """Listen for speech and return recognized text."""
        try:
            with sr.Microphone() as source:
                print("Listening...")
                self.recognizer.adjust_for_ambient_noise(source)
                audio = self.recognizer.listen(source, timeout=5)
                text = self.recognizer.recognize_google(audio)
                print(f"Heard: {text}")
                return text
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            print("Could not understand audio")
            return None
        except sr.RequestError as e:
            print(f"Speech recognition error: {e}")
            return None