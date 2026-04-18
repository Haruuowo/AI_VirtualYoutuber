import asyncio
import os
from collections import deque
from dotenv import load_dotenv
from bot import run_bots
from ai import AIHandler
from voice import VoiceHandler

async def main():
    cwd = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(cwd, '.env')
    load_dotenv(dotenv_path=env_path, override=True)
    print("Loaded env file:", env_path, "exists=", os.path.isfile(env_path))
    print("Twitch Token:", bool(os.getenv('TWITCH_TOKEN')))
    print("Twitch Client ID:", bool(os.getenv('TWITCH_CLIENT_ID')))
    print("Twitch Client Secret:", bool(os.getenv('TWITCH_CLIENT_SECRET')))
    print("Twitch Bot ID:", bool(os.getenv('TWITCH_BOT_ID')))
    print("Twitch Channel:", os.getenv('TWITCH_CHANNEL'))

    pending_messages = deque()
    ai_handler = AIHandler()
    voice_handler = VoiceHandler()
    voice_handler.set_audio_device()
    
    # Start bots in background
    bot_task = asyncio.create_task(run_bots(pending_messages))
    bot_task.add_done_callback(lambda t: print("Bot task ended:", t.exception() if t.exception() else "ok"))
    
    last_response_time = 0
    last_incoming_time = 0
    responded_messages = set()
    
    # Start voice listening in background
    voice_listen_task = asyncio.create_task(voice_listen_loop(pending_messages, voice_handler))
    
    while True:
        current_time = asyncio.get_event_loop().time()
        
        if pending_messages:
            last_incoming_time = current_time
        
        # Every 10 seconds, respond to the latest pending message
        if current_time - last_response_time >= 10 and pending_messages:
            latest_message = pending_messages[-1]  # Get latest
            message_key = (latest_message['platform'], latest_message['username'], latest_message['message'])
            if message_key not in responded_messages:
                responded_messages.add(message_key)
                pending_messages.remove(latest_message)  # Remove after responding
                response = ai_handler.generate_response(latest_message['message'], latest_message['platform'], latest_message['username'])
                if response:
                    print(f"[{latest_message['platform']}] {latest_message['username']}: {response}")
                    await voice_handler.generate_and_play_audio(response)
                last_response_time = current_time
        
        # If no messages for 30 seconds, say something random
        if current_time - last_incoming_time >= 30 and current_time - last_response_time >= 30:
            random_comment = ai_handler.get_random_comment()
            if random_comment:
                print(f"[AI] ChaosBot: {random_comment}")
                await voice_handler.generate_and_play_audio(random_comment)
            last_response_time = current_time
        
        await asyncio.sleep(1)

async def voice_listen_loop(pending_messages, voice_handler):
    """Continuously listen for voice input and add to message queue."""
    while True:
        text = await asyncio.get_event_loop().run_in_executor(None, voice_handler.listen_for_speech)
        if text:
            pending_messages.append({
                'platform': 'Voice',
                'username': 'You',
                'message': text
            })
        await asyncio.sleep(0.1)  # Small delay to prevent tight loop
    

if __name__ == "__main__":
    asyncio.run(main())