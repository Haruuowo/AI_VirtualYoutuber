import os
import sys
import asyncio
import subprocess
import time
from dotenv import load_dotenv

load_dotenv('.env')

from core import WorldState, StreamDirector
from ai import AIHandler
from voice import VoiceHandler
from minecraft.minecraft import MinecraftBot
from minecraft.minecraft_controller import MinecraftController

BRIDGE_SCRIPT = os.path.join(os.path.dirname(__file__), "minecraft", "minecraft_bot.js")

def check_or_start_bridge():
    """Starts a fresh Node.js Mineflayer bridge with latest code."""
    if sys.platform == 'win32':
        subprocess.run(
            'powershell -Command "Get-NetTCPConnection -LocalPort 3001 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"',
            shell=True,
            capture_output=True
        )
    time.sleep(0.5)
    print("-> Starting fresh Minecraft Bot Bridge (Node.js)...")
    mc_dir = os.path.join(os.path.dirname(__file__), "minecraft")
    process = subprocess.Popen(
        ["node", "minecraft_bot.js"],
        cwd=mc_dir,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    time.sleep(3.0)
    return process

def select_audio():
    configured_device = os.getenv('AUDIO_DEVICE', '').strip()
    print("\nSelect Audio Output:")
    print("  [1] Default Headphones / Speakers (Hear Luna directly)")
    print(f"  [2] Virtual Cable: '{configured_device}' (For OBS / VTube Studio)")
    choice = input("\nSelect audio [1/2] (default is 1): ").strip()
    return choice == '2'

async def console_input_loop(director):
    """Allows Kai to chat with Luna or give in-game commands from the console."""
    loop = asyncio.get_event_loop()
    print("\nLuna is playing Minecraft! Type anything to talk to her (or type 'quit' to exit):")
    print("Examples: 'what are you doing?', 'luna come here', 'luna mine stone'")
    print("-" * 65)

    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "\nYou: ")
            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("Exiting Minecraft session...")
                break

            # Send as direct high-priority message from kairuuowo
            director.pending_messages.append({
                'platform': 'Console',
                'username': 'kairuuowo',
                'message': user_input
            })

        except (KeyboardInterrupt, EOFError):
            break
        except asyncio.CancelledError:
            break

async def main():
    print("=" * 65)
    print("       LUNA MINECRAFT COMPANION (OFF-STREAM MODE)")
    print("=" * 65)
    print("Server:", os.getenv('MC_HOST', 'Haruuuuuowo-obpy.exaroton.me'))
    print("Port  :", os.getenv('MC_PORT', '64559'))

    use_virtual_cable = select_audio()

    # 1. Start or connect to Node bridge
    node_proc = check_or_start_bridge()

    # 2. Setup WorldState, AI, and Voice
    world_state = WorldState()
    ai_handler = AIHandler(world_state=world_state)
    voice_handler = VoiceHandler()

    if use_virtual_cable:
        voice_handler.set_audio_device()
        print(f"-> Audio routed to Virtual Cable: {voice_handler.device_id}")
    else:
        voice_handler.device_id = None
        print("-> Audio routed to Default Speakers / Headphones")

    mc_controller = MinecraftController()
    director = StreamDirector(
        ai_handler=ai_handler,
        voice_handler=voice_handler,
        world_state=world_state,
        mc_controller=mc_controller
    )
    director.start()

    # 3. Start Minecraft Brain
    mc_brain = MinecraftBot(director=director)
    mc_task = asyncio.create_task(mc_brain.start())

    # 4. Director Background Loop
    async def director_loop():
        while True:
            try:
                await director.process_chat_cycle()
            except Exception as e:
                print(f"[Director Error]: {e}")
            await asyncio.sleep(0.1)

    director_task = asyncio.create_task(director_loop())

    # 5. Interactive Console Loop
    try:
        await console_input_loop(director)
    finally:
        print("\nStopping Luna Minecraft session...")
        mc_task.cancel()
        director_task.cancel()
        await director.speech_queue.stop()
        if node_proc:
            print("Terminating Minecraft node bridge...")
            node_proc.terminate()
            node_proc.wait()
        print("Done. Goodbye!")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
