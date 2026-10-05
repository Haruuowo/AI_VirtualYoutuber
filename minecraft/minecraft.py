import sys
import os
import asyncio
import httpx
import random
import time

# Ensure project root is in sys.path when running minecraft.py directly
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ai import AIHandler

BRIDGE_URL = "http://localhost:3001"

# How often Luna checks the game state and decides what to do (seconds)
THINK_INTERVAL = 8

# Only major, memorable events Luna reacts to out loud (avoids non-stop yapping)
REACTIVE_EVENTS = {
    'died':             (lambda d: f"died at {d.get('position', '?')}", True),
    'attacked_by':      (lambda d: f"got attacked by {d.get('attacker', 'someone')} and is fighting back", True),
    'hurt_near_leader': (lambda d: f"{d.get('leader', 'Kai')} accidentally tapped or punched Luna, Luna roasts them deadpan", False),
    'hostile_nearby':   (lambda d: f"a creeper or monster sneaked up {d.get('distance', '?')} blocks away", True),
    'disconnected':     (lambda d: "got disconnected from the server", True),
}

class MinecraftBot:
    def __init__(self, director, ai_handler=None):
        self.director = director
        # Use Ollama locally by default to avoid Groq cloud rate limits
        self.game_ai = AIHandler(
            provider=os.getenv('MC_AI_PROVIDER', os.getenv('AI_PROVIDER', 'ollama')),
            model=os.getenv('MC_AI_MODEL', os.getenv('OLLAMA_MODEL', 'dolphin-llama3:8b'))
        )
        self.last_events = []
        self.last_reaction_time = 0

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
                        now = time.time()
                        # Only react out loud if at least 40 seconds have passed (avoids spamming speech)
                        if now - self.last_reaction_time < 40.0:
                            continue
                        self.last_reaction_time = now

                        comment_func, is_critical = REACTIVE_EVENTS[ev_type]
                        comment = comment_func(ev_data)
                        print(f"[MC Event Reaction] Luna: {comment}")
                        
                        # Record into world state for chat awareness
                        if self.director and self.director.world_state:
                            self.director.world_state.record_event(comment)

                        # Generate reaction prompt using dedicated game LLM
                        prompt = f"react to this in minecraft: {comment}. be blunt, sarcastic, or funny. 1 short sentence max. no cringe clichés, talk like a real human gamer on discord."
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

        # If in Companion / Explore Mode, Luna's autonomous companion loop directly manages
        # following the player, defending from mobs, and mining ores. Do not send random actions!
        if state.get('companion_mode'):
            if self.director and self.director.world_state:
                self.director.world_state.current_activity = 'exploring with player'
            return

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

        # Send action to Node.js express bridge
        async with httpx.AsyncClient() as client:
            try:
                await client.post(f"{BRIDGE_URL}/action", json={'action': action, 'params': {'current_action': action}}, timeout=5)
            except Exception as e:
                print(f"Failed to send action: {e}")

def _parse_action(text):
    """Extract action name safely from LLM response. Defaults to explore so Luna never stays idle."""
    valid = ['farm', 'harvest', 'deposit', 'store', 'explore', 'collect', 'gather', 'mine', 'follow', 'flee', 'attack', 'eat', 'companion_mode', 'stop_companion']
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

if __name__ == '__main__':
    import subprocess
    import sys
    print("=" * 65)
    print("NOTE: For full voice TTS, console chat, and Twitch, run:")
    print("      python play_minecraft.py")
    print("=" * 65)

    bridge_proc = None
    try:
        httpx.get("http://localhost:3001/state", timeout=1.0)
    except Exception:
        print("-> Starting Minecraft Bot Bridge (Node.js) so Luna joins the server...")
        mc_dir = os.path.dirname(os.path.abspath(__file__))
        bridge_proc = subprocess.Popen(
            ["node", "minecraft_bot.js"],
            cwd=mc_dir,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        time.sleep(2.5)

    try:
        bot = MinecraftBot(director=None)
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("\nStopping Minecraft Bot...")
    finally:
        if bridge_proc:
            bridge_proc.terminate()