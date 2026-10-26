import os
import sys
import argparse
import asyncio
from dotenv import load_dotenv

load_dotenv('.env')

from ai import AIHandler

def parse_args():
    parser = argparse.ArgumentParser(description="Luna VTuber - Fast Terminal Chat (Text-Only Mode)")
    parser.add_argument('--provider', choices=['ollama', 'groq'], default=None, help="AI Provider (ollama or groq)")
    parser.add_argument('--model', type=str, default=None, help="Model name (e.g. qwen2.5:7b, llama3.2:3b, qwen/qwen3.8-27b)")
    parser.add_argument('--user', type=str, default="Kai", help="Your username in chat (default: Kai)")
    parser.add_argument('--voice', action='store_true', help="Enable TTS voice playback")
    return parser.parse_args()

async def main():
    args = parse_args()

    provider = args.provider or os.getenv('AI_PROVIDER', 'ollama').lower()
    
    if args.model:
        model = args.model
    elif provider == 'ollama':
        model = os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')
    else:
        model = os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')

    print("=" * 60)
    print("        LUNA AI VTUBER - TERMINAL CHAT (TEXT ONLY)")
    print("=" * 60)
    print(f"  Brain    : {provider.upper()} -> {model}")
    print(f"  TTS      : {'ENABLED' if args.voice else 'DISABLED (Pure Text Mode)'}")
    print(f"  Chatter  : {args.user}")
    print("=" * 60)

    ai = AIHandler(provider=provider, model=model)
    voice = None
    if args.voice:
        from voice import VoiceHandler
        voice = VoiceHandler()
        voice.set_audio_device()

    print("\nLuna: hey! good to see you. what are we getting into today?")
    
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, f"\n{args.user}: ")
            text = user_input.strip()
            
            if not text:
                continue

            if text.lower() in ('quit', 'exit', 'q'):
                print("\nLuna: alright, catch you later! thanks for hanging out.")
                break

            if text.lower() == '/clear':
                ai.memory.clear()
                print("[System]: Luna's conversation memory has been cleared.")
                continue

            response = ai.generate_response(text, platform="Terminal", username=args.user)
            if response:
                print(f"Luna: {response}")
                if voice:
                    await voice.generate_and_play_audio(response)
            else:
                print("Luna: hmm, give me a sec, brain lag.")

        except (KeyboardInterrupt, EOFError):
            print("\nLuna: catch you later!")
            break

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
