import sys
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

    pending_messages = deque()
    ai_handler = AIHandler()
    voice_handler = VoiceHandler()
    voice_handler.set_audio_device()
    
    # Start the bots (Twitch, TikTok, Mic)
    bot_task = asyncio.create_task(run_bots(pending_messages, voice_handler))
    bot_task.add_done_callback(lambda t: print("Bot task ended:", t.exception() if t.exception() else "ok"))
    
    last_response_time = asyncio.get_event_loop().time()
    last_incoming_time = asyncio.get_event_loop().time()
    
    print("Luna is awake and listening...")

    while True:
        current_time = asyncio.get_event_loop().time()
        
        # 1. If there are messages in the queue AND Luna isn't currently speaking
        if pending_messages and not voice_handler.is_speaking:
            last_incoming_time = current_time
            
            # Get the oldest message (First In, First Out)
            msg_data = pending_messages.popleft()
            
            print(f"Processing message from {msg_data['username']}...")
            
            # Generate the AI response
            response = ai_handler.generate_response(
                msg_data['message'], 
                msg_data['platform'], 
                msg_data['username']
            )
            
            if response:
                print(f"[{msg_data['platform']}] {msg_data['username']}: {response}")
                # This will set voice_handler.is_speaking to True until finished
                await voice_handler.generate_and_play_audio(response)
                last_response_time = current_time
        
        # 2. Random banter if it's been quiet for 45 seconds
        elif current_time - last_incoming_time >= 45 and current_time - last_response_time >= 45:
            if not voice_handler.is_speaking:
                random_comment = ai_handler.get_random_comment()
                if random_comment:
                    print(f"[AI] Luna (Banter): {random_comment}")
                    await voice_handler.generate_and_play_audio(random_comment)
                last_response_time = current_time
        
        # Small sleep to prevent CPU spiking
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Luna is going to sleep. Bye!")