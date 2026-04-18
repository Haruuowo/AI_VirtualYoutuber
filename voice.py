import os
import pygame
from elevenlabs import ElevenLabs

class VoiceHandler:
    def __init__(self):
        self.client = ElevenLabs(api_key=os.getenv('ELEVENLABS_API_KEY'))
        self.voice_id = os.getenv('ELEVENLABS_VOICE_ID')
        pygame.mixer.init()
        self.audio_device = os.getenv('AUDIO_DEVICE')

    def generate_and_play_audio(self, text):
        """Generate TTS audio and play it."""
        try:
            audio = self.client.generate(
                text=text,
                voice=self.voice_id,
                model="eleven_monolingual_v1"
            )
            # Save audio to temp file
            temp_file = "temp_audio.mp3"
            with open(temp_file, "wb") as f:
                f.write(audio)
            
            # Play audio
            pygame.mixer.music.load(temp_file)
            pygame.mixer.music.play()
            
            # Wait for playback to finish
            while pygame.mixer.music.get_busy():
                pygame.time.wait(100)
            
            # Clean up
            os.remove(temp_file)
            
        except Exception as e:
            print(f"ElevenLabs or audio playback error: {e}")

    def set_audio_device(self):
        """Set audio output device if specified."""
        if self.audio_device:
            try:
                pygame.mixer.quit()
                pygame.mixer.init(devicename=self.audio_device)
            except Exception as e:
                print(f"Failed to set audio device {self.audio_device}: {e}")
                pygame.mixer.init()  # Fallback to default