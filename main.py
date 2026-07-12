import sys
import asyncio
import os
import random
import argparse
from collections import deque
from dotenv import load_dotenv
from ai import AIHandler
from voice import VoiceHandler

def _web_mode_enabled() -> bool:
    """Check env or argparse flag for web studio mode."""
    return os.getenv("WEB_STUDIO", "").lower() == "true"

def _parse_args():
    parser = argparse.ArgumentParser(description="Luna AI VTuber")
    parser.add_argument("--web", action="store_true",
                        help="Launch the local Web Studio (FastAPI + browser UI) "
                             "instead of the live Twitch/TikTok/Mic bot loop. "
                             "Equivalent to setting WEB_STUDIO=true in .env.")
    return parser.parse_args()

def _load_env():
    cwd = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(cwd, '.env')
    load_dotenv(dotenv_path=env_path, override=True)

async def main():
    _load_env()

    # Legacy live mode — import bot.py lazily so web-only users don't need
    # pyaudio / TikTokLive installed
    try:
        from bot import run_bots
    except ImportError as e:
        print(f"Failed to import bot.py: {e}")
        print("Tip: run with --web to use the Web Studio (no Twitch/TikTok/Mic deps),")
        print("     or install the missing dependency: python -m pip install -r requirements.txt")
        sys.exit(1)

    pending_messages = deque()
    ai_handler = AIHandler()
    voice_handler = VoiceHandler()
    voice_handler.set_audio_device()

    # Start Twitch / TikTok / Mic
    bot_task = asyncio.create_task(run_bots(pending_messages, voice_handler))
    bot_task.add_done_callback(lambda t: print("Bot task ended:", t.exception() if t.exception() else "ok"))

    # Start Minecraft brain if enabled
    if os.getenv('MINECRAFT_ENABLED', '').lower() == 'true':
        from minecraft.minecraft import MinecraftBot
        mc_brain = MinecraftBot(ai_handler, voice_handler)
        mc_task = asyncio.create_task(mc_brain.start())
        mc_task.add_done_callback(lambda t: print("Minecraft task ended:", t.exception() if t.exception() else "ok"))
        print("Minecraft mode enabled")

    last_response_time = asyncio.get_event_loop().time()
    last_incoming_time = asyncio.get_event_loop().time()

    print("Luna is awake and listening...")

    while True:
        current_time = asyncio.get_event_loop().time()

        if pending_messages and not voice_handler.is_speaking:
            last_incoming_time = current_time

            await asyncio.sleep(0.5)
            batch = []
            while pending_messages and len(batch) < 5:
                batch.append(pending_messages.popleft())

            # skip single word messages 35% of the time
            if len(batch) == 1:
                words = batch[0]['message'].strip().split()
                if len(words) <= 1 and random.random() < 0.35:
                    print(f"[skip] {batch[0]['username']}: {batch[0]['message']}")
                    await asyncio.sleep(0.1)
                    continue

            if len(batch) == 1:
                response = ai_handler.generate_response(
                    batch[0]['message'],
                    batch[0]['platform'],
                    batch[0]['username']
                )
            else:
                response = ai_handler.generate_batch_response(batch)

            if response:
                display, speech = ai_handler.split_response(response)
                print(f"Luna: {display}")
                if speech is not None:
                    await voice_handler.generate_and_play_audio(speech)
                last_response_time = current_time

        elif current_time - last_incoming_time >= 45 and current_time - last_response_time >= 45:
            if not voice_handler.is_speaking:
                if random.random() < 0.3:
                    comment = ai_handler.continue_last_thought()
                else:
                    comment = ai_handler.get_random_comment()
                if comment:
                    display, speech = ai_handler.split_response(comment)
                    print(f"[Luna banter]: {display}")
                    if speech is not None:
                        await voice_handler.generate_and_play_audio(speech)
                last_response_time = current_time

        await asyncio.sleep(0.1)

if __name__ == "__main__":
    args = _parse_args()
    _load_env()

    if args.web or _web_mode_enabled():
        # Web Studio mode — bypass bot.py (avoids requiring pyaudio /
        # TikTokLive / websockets when only the web UI is needed).
        # run_web_studio() calls uvicorn.run() which manages its own event
        # loop, so it cannot be invoked from inside asyncio.run(main()).
        from studio import run_web_studio
        run_web_studio()
        sys.exit(0)

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Luna is going to sleep. Bye!")