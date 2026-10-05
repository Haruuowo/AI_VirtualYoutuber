import sys
import asyncio
import os
from dotenv import load_dotenv

from core import WorldState, StreamDirector
from bot import run_bots
from ai import AIHandler
from voice import VoiceHandler
from minecraft.minecraft_controller import MinecraftController

async def main():
    cwd = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(cwd, '.env')
    load_dotenv(dotenv_path=env_path, override=True)

    print("=" * 60)
    print("        LUNA AI VTUBER - STREAM & MINECRAFT DIRECTOR")
    print("=" * 60)

    # 1. Initialize Shared World State & Persona Brain
    world_state = WorldState()
    ai_handler = AIHandler(world_state=world_state)
    voice_handler = VoiceHandler()
    voice_handler.set_audio_device()

    # 2. In-game Controller & Director
    mc_controller = MinecraftController()
    director = StreamDirector(
        ai_handler=ai_handler,
        voice_handler=voice_handler,
        world_state=world_state,
        mc_controller=mc_controller
    )
    director.start()

    # 3. Start Chat & Voice Bots (Twitch, TikTok, Mic)
    bot_task = asyncio.create_task(run_bots(director.pending_messages, voice_handler))
    bot_task.add_done_callback(lambda t: print("Bot task ended:", t.exception() if t.exception() else "ok"))

    # 4. Start Minecraft Autonomous Brain (if enabled)
    if os.getenv('MINECRAFT_ENABLED', '').lower() == 'true':
        from minecraft.minecraft import MinecraftBot
        mc_brain = MinecraftBot(director=director)
        mc_task = asyncio.create_task(mc_brain.start())
        mc_task.add_done_callback(lambda t: print("Minecraft task ended:", t.exception() if t.exception() else "ok"))
        print("-> Minecraft autonomous mode active")

    print("\nLuna is awake, listening to chat & watching Minecraft...")
    print("-" * 60)

    # 5. Main Director Loop
    while True:
        try:
            await director.process_chat_cycle()
        except Exception as e:
            print(f"[Director Loop Error]: {e}")
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nLuna is going to sleep. Bye!")