import asyncio
import httpx
import random
import os
from ai import AIHandler

BRIDGE_URL = "http://localhost:3001"

# How often Luna checks the game state and decides what to do (seconds)
THINK_INTERVAL = 8

# Events Luna reacts to out loud
REACTIVE_EVENTS = {
    'died':           (lambda d: f"died at {d.get('position', '?')}", True),
    'took_damage':    (lambda d: f"took damage, now at {d.get('health', '?')} health", True),
    'hostile_nearby': (lambda d: f"there's a {d.get('mob', 'mob')} {d.get('distance', '?')} blocks away", True),
    'nightfall':      (lambda d: "it just became night", False),
    'mined_block':    (lambda d: f"just mined {d.get('block', 'a block')}", False),
    'item_collected': (lambda d: f"picked up {d.get('item', 'something')}", False),
    'explored':       (lambda d: "finished exploring in a direction", False),
    'fled':           (lambda d: f"ran away from a {d.get('from', 'mob')}", True),
    'attacked':       (lambda d: f"attacked a {d.get('mob', 'mob')}", False),
    'mine_failed':    (lambda d: "tried to mine but couldn't find anything", False),
    'attacked_by':    (lambda d: f"just got attacked by {d.get('attacker', 'someone')} and is fighting back", True),
    'harvested_crop': (lambda d: f"just harvested some {d.get('crop', 'crops')}", False),
    'replanted_crop': (lambda d: f"replanted {d.get('seed', 'seeds')} in the farm", False),
    'deposited_items':(lambda d: "put the harvested crops into the chest", False),
    'disconnected':   (lambda d: "got disconnected from the server", True),
    'spawned':        (lambda d: "just spawned into the world", False),
}

class MinecraftBot:
    def __init__(self, director, ai_handler=None):
        self.director = director
        # Use Ollama locally by default to avoid Groq cloud rate limits
        self.game_ai = AIHandler(
            provider=os.getenv('MC_AI_PROVIDER', os.getenv('AI_PROVIDER', 'ollama')),
            model=os.getenv('MC_AI_MODEL', os.getenv('OLLAMA_MODEL', 'llama3.2:3b'))
        )
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
        """Fetch latest events from Node.js bot and comment on them out loud via Director."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{BRIDGE_URL}/events", timeout=2)
                events = response.json().get('events', [])
                
                for ev in events:
                    ev_type = ev.get('type')
                    ev_data = ev.get('data', {})
                    
                    if ev_type == 'chat':
                        username = ev_data.get('username')
                        msg = ev_data.get('message', '')
                        if username and username.lower() != 'luna':
                            print(f"[MC Chat] {username}: {msg}")
                            if self.director:
                                self.director.pending_messages.append({
                                    'platform': 'Minecraft',
                                    'username': username,
                                    'message': msg
                                })
                        continue

                    if ev_type in REACTIVE_EVENTS:
                        comment_func, is_critical = REACTIVE_EVENTS[ev_type]
                        comment = comment_func(ev_data)
                        print(f"[MC Event Reaction] Luna: {comment}")
                        
                        # Record into world state for chat awareness
                        if self.director and self.director.world_state:
                            self.director.world_state.record_event(comment)

                        # Generate reaction prompt using dedicated game LLM
                        prompt = f"react to this situation that just happened in minecraft: {comment}. keep it very short and casual (1 sentence max)."
                        reaction = await self.game_ai.async_generate_response(prompt, 'Minecraft', 'event')
                        
                        if reaction and self.director:
                            # Route through priority speech queue
                            await self.director.speak_game_event(reaction, is_critical=is_critical, ttl=10.0)
                            # Post the text back into server chat
                            await client.post(f"{BRIDGE_URL}/action", json={'action': 'chat', 'params': {'message': reaction}})
                            break # React to one major event per tick
                            
            except Exception:
                pass # Bridge temporarily unreachable

    async def _think_and_act(self):
        """Get current state, let game AI decide an action, and sync to Director's WorldState."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{BRIDGE_URL}/state", timeout=2)
                state = response.json()
            except Exception:
                return # Can't talk to bot body right now

        if not state.get('connected'):
            return

        # Update Director's WorldState so chat LLM has accurate context
        if self.director and self.director.world_state:
            self.director.world_state.update_from_bot_state(state)

        hostiles = state.get('hostile_mobs', [])
        is_moving = state.get('is_moving', False)

        # If Luna is already actively walking/navigating to a destination and there's no urgent monster threat, let her finish!
        if is_moving and not hostiles:
            return

        # Build clean string for planner
        players = state.get('nearby_players', [])
        items = state.get('nearby_items', [])
        mature_crops = state.get('mature_crops')

        state_summary = (
            f"Health: {state.get('health')}/20, "
            f"Food: {state.get('food')}/20, "
            f"Time: {state.get('time')}"
        )
        if players:
            state_summary += f", Nearby Players: {', '.join([p['name'] for p in players])}"
        if hostiles:
            state_summary += f", Hostile Monsters: {', '.join([m['name'] for m in hostiles])}"
        else:
            state_summary += ", Hostile Monsters: None"
        if items:
            state_summary += f", Items on Ground: {len(items)} nearby"
        if mature_crops:
            state_summary += f", Farm Crops: {mature_crops} ready to harvest"

        # Ask game LLM what action to take next
        decision_prompt = (
            f"you are playing minecraft right now. current state: {state_summary}. "
            f"you must NEVER stand still or stay idle. always be doing something active: farm crops, explore, gather resources, follow nearby players, or mine. "
            f"pick ONE action to do next: farm, explore, collect, mine, follow, flee, attack, eat. "
            f"reply in this exact format: ACTION: <action> | REASON: <reason>"
        )

        response = await self.game_ai.async_generate_response(decision_prompt, 'Minecraft', 'game')
        if not response:
            return

        action = _parse_action(response)
        print(f"[MC decision] {response} → {action}")

        # Update world state activity
        if self.director and self.director.world_state:
            self.director.world_state.current_activity = action

        # Enqueue reason to Director's speech queue as a game milestone / update
        if self.director and '|' in response:
            reason_text = response.split('|')[1].replace('REASON:', '').strip()
            if reason_text and not reason_text.lower().startswith('action:'):
                await self.director.speak_game_event(reason_text, is_critical=False, ttl=12.0)

        # Send action to Node.js express bridge
        async with httpx.AsyncClient() as client:
            try:
                await client.post(f"{BRIDGE_URL}/action", json={'action': action, 'params': {'current_action': action}}, timeout=5)
            except Exception as e:
                print(f"Failed to send action: {e}")

def _parse_action(text):
    """Extract action name safely from LLM response. Defaults to explore so Luna never stays idle."""
    valid = ['farm', 'harvest', 'deposit', 'store', 'explore', 'collect', 'gather', 'mine', 'follow', 'flee', 'attack', 'eat']
    text_lower = text.lower()
    
    if "action:" in text_lower:
        try:
            part = text_lower.split("action:")[1].split("|")[0].strip()
            for v in valid:
                if v in part:
                    if v == 'harvest': return 'farm'
                    if v == 'store': return 'deposit'
                    if v == 'gather': return 'collect'
                    return v
        except Exception:
            pass
            
    for action in valid:
        if action in text_lower:
            if action == 'harvest': return 'farm'
            if action == 'store': return 'deposit'
            if action == 'gather': return 'collect'
            return action
            
    return 'explore'