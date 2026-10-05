import asyncio
import random
import time
from collections import deque
from typing import Optional, Dict, Any

from core.speech_queue import (
    SpeechQueue,
    P0_CRITICAL,
    P1_CHAT_DIRECT,
    P2_GAME_MILESTONE,
    P3_CHAT_NORMAL,
    P4_IDLE_BANTER
)
from core.intent import is_luna_mentioned, parse_minecraft_intent
from core.world_state import WorldState

class StreamDirector:
    """
    Central conductor for Luna's stream.
    Manages:
      1. Incoming chat & voice messages (Twitch, TikTok, Mic)
      2. Priority routing (calls to Luna & mic jump to P1)
      3. Intent detection for in-game Minecraft commands
      4. Single-point coordination of speech queue
      5. Periodic banter when chat is quiet
    """
    def __init__(self, ai_handler, voice_handler, world_state: Optional[WorldState] = None, mc_controller = None):
        self.ai = ai_handler
        self.voice = voice_handler
        self.world_state = world_state or WorldState()
        self.ai.set_world_state(self.world_state)
        
        self.speech_queue = SpeechQueue(self.voice)
        self.mc_controller = mc_controller
        self.pending_messages = deque()
        
        self.last_speech_time = time.time()
        self.last_incoming_time = time.time()
        self._running = False

    def start(self):
        """Start the director and its underlying speech worker."""
        self._running = True
        self.speech_queue.start()

    async def speak_game_event(self, text: str, is_critical: bool = False, ttl: float = 12.0):
        """Directly inject a game reaction into the speech queue."""
        priority = P0_CRITICAL if is_critical else P2_GAME_MILESTONE
        await self.speech_queue.enqueue(
            text=text,
            priority=priority,
            ttl=ttl,
            source="minecraft"
        )
        self.last_speech_time = time.time()

    async def process_chat_cycle(self):
        """Single processing step for incoming messages and idle banter."""
        current_time = time.time()

        if self.pending_messages:
            self.last_incoming_time = current_time

            # Check if any message in queue directly addresses Luna or is from in-game players
            direct_msg = None
            for idx, msg in enumerate(list(self.pending_messages)):
                is_direct = (
                    msg.get('platform') in ('Mic', 'Console', 'Minecraft') or 
                    is_luna_mentioned(msg.get('message', ''))
                )
                if is_direct:
                    # Pop this specific message
                    direct_msg = self.pending_messages[idx]
                    del self.pending_messages[idx]
                    break

            # 1. Handle direct call to Luna immediately (P1 Priority)
            if direct_msg:
                user = direct_msg.get('username', 'Someone')
                text = direct_msg.get('message', '')
                platform = direct_msg.get('platform', 'Chat')
                print(f"[Director] Processing {platform} message from {user}: {text}")

                # Check if it's an in-game command
                command = parse_minecraft_intent(text, username=user)
                if command and self.mc_controller:
                    action = command['action']
                    params = command['params']
                    print(f"[Director] In-game command detected from {user}: {action} ({params})")
                    
                    # Send action to Minecraft bot bridge
                    asyncio.create_task(self.mc_controller.send_action(action, params))
                    
                    # Luna dynamic AI reaction acknowledging the player by name
                    ack_prompt = f"in-game player {user} just told you to in minecraft: \"{text}\". give a 1-sentence witty or funny streamer comeback to them."
                    response = await self.ai.async_generate_response(ack_prompt, platform, user)
                else:
                    # Full AI dynamic conversation addressing the player
                    response = await self.ai.async_generate_response(text, platform, user)

                if response:
                    # Send reply into Minecraft server chat so all players see her answer!
                    if platform == 'Minecraft' and self.mc_controller:
                        asyncio.create_task(self.mc_controller.send_action('chat', {'message': response}))

                    await self.speech_queue.enqueue(
                        text=response,
                        priority=P1_CHAT_DIRECT,
                        ttl=20.0,
                        source="chat_direct",
                        username=user
                    )
                    self.last_speech_time = current_time
                return

            # 2. Handle batch of background chat messages (P3 Priority)
            batch = []
            while self.pending_messages and len(batch) < 5:
                batch.append(self.pending_messages.popleft())

            # Skip single-word spam occasionally
            if len(batch) == 1:
                words = batch[0]['message'].strip().split()
                if len(words) <= 1 and random.random() < 0.35:
                    return

            if len(batch) == 1:
                response = await self.ai.async_generate_response(
                    batch[0]['message'],
                    batch[0]['platform'],
                    batch[0]['username']
                )
            else:
                response = await self.ai.async_generate_batch_response(batch)

            if response:
                await self.speech_queue.enqueue(
                    text=response,
                    priority=P3_CHAT_NORMAL,
                    ttl=15.0,
                    source="chat_batch"
                )
                self.last_speech_time = current_time

        # 3. Handle idle banter when chat is quiet for 45s (P4 Priority)
        elif (current_time - self.last_incoming_time >= 45) and (current_time - self.last_speech_time >= 45):
            if random.random() < 0.3:
                comment = await self.ai.async_continue_last_thought()
            else:
                comment = await self.ai.async_get_random_comment()

            if comment:
                await self.speech_queue.enqueue(
                    text=comment,
                    priority=P4_IDLE_BANTER,
                    ttl=10.0,
                    source="idle_banter"
                )
                self.last_speech_time = current_time
