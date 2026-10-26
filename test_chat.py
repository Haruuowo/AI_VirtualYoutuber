import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv('.env')

from ai import AIHandler
from voice import VoiceHandler

def select_model():
    print("\nSelect Luna's Brain / Model:")
    print("  [1] Ollama: qwen2.5:7b         (Recommended: Witty, best stream banter, fits 8GB VRAM)")
    print("  [2] Ollama: llama3.2:3b        (Ultra Fast: Near-instant replies)")
    print("  [3] Ollama: dolphin-llama3:8b  (Chaotic & Uncensored persona)")
    print("  [4] Cloud: Groq (qwen3.8-27b)  (Cloud API)")
    
    choice = input("\nSelect model [1-4] (default is 1): ").strip()
    
    if choice == '2':
        return 'ollama', 'llama3.2:3b'
    elif choice == '3':
        return 'ollama', 'dolphin-llama3:8b'
    elif choice == '4':
        return 'groq', 'qwen/qwen3.8-27b'
    else:
        return 'ollama', 'qwen2.5:7b'

def select_audio():
    configured_device = os.getenv('AUDIO_DEVICE', '').strip()
    print("\nSelect Audio Output:")
    print("  [1] Default Headphones / Speakers (Hear Luna directly)")
    print(f"  [2] Virtual Cable: '{configured_device}' (For VTube Studio / OBS)")
    print("  [3] No TTS / Text Only (Instant terminal replies, zero audio latency)")
    choice = input("\nSelect audio [1-3] (default is 1): ").strip()
    if choice == '3':
        return 'none'
    elif choice == '2':
        return 'cable'
    else:
        return 'default'

async def run_interactive_test():
    print("=" * 60)
    print("        LUNA AI VTUBER - INTERACTIVE TEST CONSOLE")
    print("=" * 60)
    
    no_tts_cli = '--no-tts' in sys.argv or '--text' in sys.argv
    
    provider, model = select_model()
    audio_mode = 'none' if no_tts_cli else select_audio()
    
    print("\nInitializing Luna...")
    ai = AIHandler(provider=provider, model=model)
    voice = None
    
    if audio_mode != 'none':
        voice = VoiceHandler()
        if audio_mode == 'cable':
            voice.set_audio_device()
            print(f"-> Audio routed to Virtual Cable (Device ID: {voice.device_id})")
        else:
            voice.device_id = None
            print("-> Audio routed to Default Speakers / Headphones")
        tts_label = voice.tts_engine.upper()
    else:
        tts_label = "DISABLED (Text Only Mode)"
        print("-> TTS is disabled (Fast terminal chat mode)")

    print(f"\n[Active Brain]: {ai.provider.upper()} -> {ai.model}")
    print(f"[TTS Engine]  : {tts_label}")
    print("\nLuna is live! Type anything to talk to her (type 'quit' to exit):")
    print("-" * 60)
    
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "\nYou: ")
            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("Luna: bye bye, see ya next stream!")
                break
            
            print("Thinking...")
            response = ai.generate_response(user_input, "Console", "Kai")
            print(f"Luna: {response}")
            
            if response and voice and audio_mode != 'none':
                print("[Playing audio...]")
                await voice.generate_and_play_audio(response)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting test. Goodbye!")
            break
        except asyncio.CancelledError:
            break

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run_interactive_test())
    except KeyboardInterrupt:
        print("\nLuna is going to sleep. Bye!")
