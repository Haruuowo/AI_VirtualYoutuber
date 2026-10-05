import re
from typing import Dict, Any, Tuple, Optional

# Keywords that indicate a message is directed at Luna
LUNA_MENTION_PATTERN = re.compile(r'\b(luna|@luna|loona)\b', re.IGNORECASE)

# Minecraft command patterns (regex for fast zero-latency intent detection)
COMMAND_PATTERNS = [
    # Explore & Companion Mode (Follow, Defend & Mine)
    (re.compile(r'\b(explore\s+mode|companion\s+mode|adventure\s+mode|explore\s+with\s+me|come\s+explore|let\'?s\s+explore|mine\s+with\s+me|let\'?s\s+mine|let\'?s\s+go\s+mining|come\s+mine|mining\s+buddy|adventure\s+with\s+me|follow\s+and\s+protect(\s+me)?|follow\s+and\s+mine(\s+with\s+me)?)\b', re.IGNORECASE), "companion_mode", {}),
    (re.compile(r'\b(stop\s+exploring|stop\s+companion|cancel\s+explore|stop\s+mining(\s+with\s+me)?|stop\s+follow(ing)?)\b', re.IGNORECASE), "stop_companion", {}),

    # Follow / Come
    (re.compile(r'\b(come\s+here|follow\s+me|come\s+to\s+me|tp\s+to\s+me)\b', re.IGNORECASE), "follow", {}),
    (re.compile(r'\b(follow)\s+([a-zA-Z0-9_]+)\b', re.IGNORECASE), "follow_player", {"group": 2}),
    
    # Mining / Gathering
    (re.compile(r'\b(mine|chop|gather|collect)\s+(wood|trees?|logs?)\b', re.IGNORECASE), "collect", {"block": "oak_log", "count": 8}),
    (re.compile(r'\b(mine|gather|collect)\s+(stone|cobblestone)\b', re.IGNORECASE), "collect", {"block": "stone", "count": 10}),
    (re.compile(r'\b(mine|find|gather)\s+(iron|iron\s+ore)\b', re.IGNORECASE), "collect", {"block": "iron_ore", "count": 4}),
    (re.compile(r'\b(mine|dig|find)\s+(diamonds?)\b', re.IGNORECASE), "collect", {"block": "diamond_ore", "count": 3}),
    (re.compile(r'\b(go\s+mine|start\s+mining)\b', re.IGNORECASE), "mine", {}),

    # Combat & Protection
    (re.compile(r'\b(attack|kill|fight|hit|beat\s+up|destroy)\s+(the\s+)?(zombie|skeleton|creeper|spider|mob|monster|witch|enderman|drowned)\b', re.IGNORECASE), "attack", {"group": 3}),
    (re.compile(r'\b(attack|kill|fight|hit|beat\s+up|destroy)\s+([a-zA-Z0-9_]+)\b', re.IGNORECASE), "attack", {"group": 2}),
    (re.compile(r'\b(attack|kill|fight)\b', re.IGNORECASE), "attack", {}),
    (re.compile(r'\b(run\s+away|flee|escape|retreat)\b', re.IGNORECASE), "flee", {}),
    (re.compile(r'\b(guard\s+me|protect\s+me|defend\s+me|bodyguard|watch\s+my\s+back)\b', re.IGNORECASE), "guard", {}),
    (re.compile(r'\b(protect|defend)\b', re.IGNORECASE), "guard", {}),

    # Friendly Spam Crouch / Greeting
    (re.compile(r'\b(crouch|sneak|spam\s+crouch|crouch\s+spam|friendly\s+crouch|shift\s+shift|peace\s+sign|twerk)\b', re.IGNORECASE), "crouch", {}),

    # Giving & Dropping Items
    (re.compile(r'\b(give\s+me|toss\s+me|drop|throw\s+me)\s+(all|everything|your\s+stuff|items?)\b', re.IGNORECASE), "drop", {"item": "all"}),
    (re.compile(r'\b(give\s+me|toss\s+me|drop|throw\s+me)\s+([a-zA-Z0-9_]+)\b', re.IGNORECASE), "drop", {"group": 2}),

    # Feeding Player
    (re.compile(r'\b(feed\s+me|give\s+me\s+food|i\'?m\s+hungry|feed)\b', re.IGNORECASE), "feed", {}),

    # Gear & Tools
    (re.compile(r'\b(equip|hold|wield|put\s+on)\s+([a-zA-Z0-9_]+)\b', re.IGNORECASE), "equip", {"group": 2}),
    (re.compile(r'\b(place\s+a?\s*torch|light\s+up|torch\s+here|put\s+a?\s*torch)\b', re.IGNORECASE), "torch", {}),

    # Inventory Check
    (re.compile(r'\b(what\s+do\s+you\s+have|show\s+inventory|check\s+inventory|what\'?s\s+in\s+your\s+(inventory|bag|pockets?))\b', re.IGNORECASE), "inventory", {}),

    # Crafting / Survival
    (re.compile(r'\b(craft|make)\s+(a\s+)?(bed)\b', re.IGNORECASE), "craft", {"item": "white_bed"}),
    (re.compile(r'\b(craft|make)\s+(a\s+)?(pickaxe|wooden\s+pickaxe|stone\s+pickaxe)\b', re.IGNORECASE), "craft", {"item": "stone_pickaxe"}),
    (re.compile(r'\b(craft|make)\s+(a\s+)?(sword|iron\s+sword)\b', re.IGNORECASE), "craft", {"item": "iron_sword"}),
    (re.compile(r'\b(sleep|go\s+to\s+sleep|sleep\s+in\s+bed)\b', re.IGNORECASE), "sleep", {}),
    (re.compile(r'\b(eat|eat\s+food)\b', re.IGNORECASE), "eat", {}),

    # Exploration / Wandering
    (re.compile(r'\b(explore|walk\s+around|wander|roam|look\s+around|move\s+around)\b', re.IGNORECASE), "explore", {}),

    # Farming / Harvesting
    (re.compile(r'\b(farm|harvest|replant|tend\s+the\s+crops?|gather\s+wheat|grow\s+food)\b', re.IGNORECASE), "farm", {}),
    (re.compile(r'\b(farm|harvest)\s+(wheat|carrots?|potatoes?|crops?|food|berries|sugar\s+cane)\b', re.IGNORECASE), "farm", {"group": 2}),

    # Chest Storage / Deposit
    (re.compile(r'\b(put\s+(the\s+)?(stuff|items?|crops?|wheat|loot)\s+(in|into|on)\s+(the\s+)?chest|deposit|store\s+(the\s+)?(stuff|items?|crops?|wheat|loot)|empty\s+inventory)\b', re.IGNORECASE), "deposit", {}),

    # Stop / Wait
    (re.compile(r'\b(stop|halt|stay\s+here|wait\s+here|don\'?t\s+move|stay)\b', re.IGNORECASE), "idle", {}),

    # Status check
    (re.compile(r'\b(what\s+are\s+you\s+doing|where\s+are\s+you|what\'?s\s+your\s+status|health\s+check)\b', re.IGNORECASE), "status", {})
]

def is_luna_mentioned(text: str) -> bool:
    """Checks if Luna's name is explicitly invoked in the message."""
    return bool(LUNA_MENTION_PATTERN.search(text))

def parse_minecraft_intent(text: str, username: str = "") -> Optional[Dict[str, Any]]:
    """
    Attempts to match an in-game command from user text.
    Returns a dict with action details if recognized, else None.
    """
    cleaned = LUNA_MENTION_PATTERN.sub("", text).strip()
    
    for pattern, action_name, param_template in COMMAND_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            params = dict(param_template)
            if "group" in params:
                group_idx = params.pop("group")
                matched_val = match.group(group_idx)
                if action_name == "follow_player":
                    params["player"] = matched_val
                elif action_name == "attack":
                    params["mob"] = matched_val
                    params["target"] = matched_val
                elif action_name in ("drop", "equip"):
                    params["item"] = matched_val
            elif action_name in ("follow", "guard", "feed", "drop", "companion_mode", "stop_companion"):
                params["player"] = username or "kairuuowo"

            return {
                "action": action_name,
                "params": params,
                "raw_command": text,
                "sender": username
            }
            
    return None
