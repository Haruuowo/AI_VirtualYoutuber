import asyncio
import httpx
import random
import os

BRIDGE_URL = "http://localhost:3001"

# How often Luna checks the game state and decides what to do (seconds)
THINK_INTERVAL = 8

# Events Luna reacts to out loud
REACTIVE_EVENTS = {
    'died':           lambda d: f"died at {d.get('position', '?')}",
    'took_damage':    lambda d: f"took damage, now at {d.get('health', '?')} health",
    'hostile_nearby': lambda d: f"there's a {d.get('mob', 'mob')} {d.get('distance', '?')} blocks away",
    'nightfall':      lambda d: "it just became night",
    'mined_block':    lambda d: f"just mined {d.get('block', 'a block')}",
    'item_collected': lambda d: f"picked up {d.get('item', 'something')}",
    'explored':       lambda d: "finished exploring in a direction",
    'fled':           lambda d: f"ran away from a {d.get('from', 'mob')}",
    'attacked':       lambda d: f"attacked a {d.get('mob', 'mob')}",
    'mine_failed':    lambda d: "tried to mine but couldn't find anything",
    'disconnected':   lambda d: "got disconnected from the server",
    'spawned':        lambda d: "just spawned into the world",
}

class MinecraftBot:
    def __init__(self, ai_handler, voice_handler):
        self.ai = ai_handler
        self.voice = voice_handler
        self.last_events = []

    async def start(self):
        print("Minecraft Autonomous Brain Loop Started...")
        while True:
            try:
                # 1. Handle incoming game events first
                await self._check_events()
                
                # 2. Main thinking process
                await self._think_and_act()
                
            except Exception as e:
                print(f"Error in Minecraft Brain Loop: {e}")
            
            await asyncio.sleep(THINK_INTERVAL)

    async def _check_events(self):
        """Fetch latest events from Node.js bot and comment on them out loud."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{BRIDGE_URL}/events", timeout=2)
                events = response.json().get('events', [])
                
                for ev in events:
                    ev_type = ev.get('type')
                    ev_data = ev.get('data', {})
                    
                    if ev_type in REACTIVE_EVENTS:
                        comment = REACTIVE_EVENTS[ev_type](ev_data)
                        print(f"[MC Event Reaction] Luna: {comment}")

                        # Generate reaction prompt for personality consistency
                        prompt = f"react to this situation that just happened in minecraft: {comment}. keep it very short and casual."
                        reaction = self.ai.generate_response(prompt, 'Minecraft', 'event')

                        if reaction and not self.voice.is_speaking:
                            display, speech = self.ai.split_response(reaction)
                            if speech is not None:
                                await self.voice.generate_and_play_audio(speech)
                            # Post English display to in-game chat, not Japanese
                            await client.post(f"{BRIDGE_URL}/action", json={'action': 'chat', 'params': {'message': display}})
                            break # Only react to one major thing per tick
                            
            except Exception as e:
                pass # Silent fallback if connection drops briefly

    async def _think_and_act(self):
        """Get current state, let the LLM decide an action, and send it down to the body."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{BRIDGE_URL}/state", timeout=2)
                state = response.json()
            except Exception:
                return # Can't talk to bot body right now

        if not state.get('connected'):
            return

        # Build clean string for context window
        state_summary = (
            f"Health: {state.get('health')}/20, "
            f"Food: {state.get('food')}/20, "
            f"Time: {state.get('time')}, "
            f"Position: {state.get('position')}"
        )
        
        nearby = state.get('nearby_players', [])
        if nearby:
            state_summary += f", Nearby Players: {', '.join([p['name'] for p in nearby])}"

        # Ask Luna what she wants to do
        decision_prompt = (
            f"you are playing minecraft right now. current state: {state_summary}. "
            f"pick ONE action to do next and explain why in 1 sentence. "
            f"available actions: mine, explore, flee, attack, look_around, idle. "
            f"reply in this exact format: ACTION: <action> | REASON: <reason>"
        )

        response = self.ai.generate_response(decision_prompt, 'Minecraft', 'game')
        if not response:
            return

        # Split into English display text + Japanese speech text
        display, speech = self.ai.split_response(response)

        # Parse action from English display text, NOT raw JSON
        action = _parse_action(display)
        print(f"[MC decision] {display} → {action}")

        # Print English, TTS Japanese if available
        if not self.voice.is_speaking:
            if speech is not None:
                await self.voice.generate_and_play_audio(speech)

        # Send action down to Node.js express bridge securely with explicit empty params object
        async with httpx.AsyncClient() as client:
            try:
                await client.post(f"{BRIDGE_URL}/action", json={'action': action, 'params': {'current_action': action}}, timeout=5)
            except Exception as e:
                print(f"Failed to send action: {e}")


def _parse_action(text):
    """Extract action name safely from LLM response."""
    valid = ['mine', 'explore', 'flee', 'attack', 'look_around', 'idle']
    text_lower = text.lower()
    
    if "action:" in text_lower:
        try:
            part = text_lower.split("action:")[1].split("|")[0].strip()
            if part in valid:
                return part
        except Exception:
            pass
            
    # Fallback scan if string format split misses
    for action in valid:
        if action in text_lower:
            return action
            
    return 'idle'