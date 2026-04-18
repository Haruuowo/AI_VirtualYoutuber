import asyncio
import os
from collections import deque
from dotenv import load_dotenv
from bot import run_bots
from ai import AIHandler
from voice import VoiceHandler

async def main():
    load_dotenv()
    
    pending_messages = deque()
    ai_handler = AIHandler()
    voice_handler = VoiceHandler()
    voice_handler.set_audio_device()
    
    # Start bots in background
    bot_task = asyncio.create_task(run_bots(pending_messages))
    
    last_response_time = 0
    last_incoming_time = 0
    responded_messages = set()
    
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
                    voice_handler.generate_and_play_audio(response)
                last_response_time = current_time
        
        # If no messages for 30 seconds, say something random
        if current_time - last_incoming_time >= 30 and current_time - last_response_time >= 30:
            random_comment = ai_handler.get_random_comment()
            if random_comment:
                print(f"[AI] ChaosBot: {random_comment}")
                voice_handler.generate_and_play_audio(random_comment)
            last_response_time = current_time
        
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())