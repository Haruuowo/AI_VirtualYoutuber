import sys
import asyncio
import os
import random
from collections import deque
from dotenv import load_dotenv
from bot import run_bots
from ai import AIHandler
from voice import VoiceHandler

async def main():
    cwd = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(cwd, '.env')
    load_dotenv(dotenv_path=env_path, override=True)

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
                print(f"Luna: {response}")
                await voice_handler.generate_and_play_audio(response)
                last_response_time = current_time

        elif current_time - last_incoming_time >= 45 and current_time - last_response_time >= 45:
            if not voice_handler.is_speaking:
                if random.random() < 0.3:
                    comment = ai_handler.continue_last_thought()
                else:
                    comment = ai_handler.get_random_comment()
                if comment:
                    print(f"[Luna banter]: {comment}")
                    await voice_handler.generate_and_play_audio(comment)
                last_response_time = current_time

        await asyncio.sleep(0.1)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Luna is going to sleep. Bye!")