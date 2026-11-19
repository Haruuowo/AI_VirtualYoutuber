import time
from typing import Dict, Any, List, Optional
import threading

class WorldState:
    """
    Thread-safe shared snapshot of Luna's in-game Minecraft state.
    Used by:
      - Minecraft game brain (writes state & events)
      - Luna's chat brain (reads state to inject into LLM context)
      - Stream overlays and director
    """
    def __init__(self):
        self._lock = threading.RLock()
        self.connected: bool = False
        self.health: int = 20
        self.food: int = 20
        self.position: Dict[str, float] = {"x": 0, "y": 0, "z": 0}
        self.dimension: str = "overworld"
        self.time_of_day: str = "day"  # 'day' or 'night'
        self.current_activity: str = "idle"
        self.current_goal: str = "survive and explore"
        self.nearby_players: List[Dict[str, Any]] = []
        self.nearby_hostiles: List[Dict[str, Any]] = []
        self.inventory_summary: str = "empty"
        self.last_significant_event: Optional[str] = None
        self.last_event_time: float = 0.0
        self.last_updated: float = time.time()

    def update_from_bot_state(self, state_dict: Dict[str, Any]):
        """Update state using data received from the Minecraft bridge."""
        with self._lock:
            self.connected = state_dict.get("connected", True)
            self.health = state_dict.get("health", self.health)
            self.food = state_dict.get("food", self.food)
            
            raw_pos = state_dict.get("position", {})
            if isinstance(raw_pos, dict):
                self.position = {
                    "x": round(raw_pos.get("x", 0), 1),
                    "y": round(raw_pos.get("y", 0), 1),
                    "z": round(raw_pos.get("z", 0), 1)
                }

            self.time_of_day = state_dict.get("time", self.time_of_day)
            
            # Entities
            nearby = state_dict.get("nearby_players", [])
            self.nearby_players = [p for p in nearby if p.get("type") == "player" or "player" in str(p.get("name", "")).lower()]
            self.nearby_hostiles = [p for p in nearby if p.get("type") == "mob" or p not in self.nearby_players]

            if "current_action" in state_dict:
                self.current_activity = state_dict["current_action"]
            if "goal" in state_dict:
                self.current_goal = state_dict["goal"]
            if "inventory" in state_dict:
                self.inventory_summary = state_dict["inventory"]

            self.last_updated = time.time()

    def record_event(self, event_description: str):
        """Record a notable game event (e.g. 'found diamonds', 'attacked by creeper')."""
        with self._lock:
            self.last_significant_event = event_description
            self.last_event_time = time.time()

    def get_summary(self) -> str:
        """Compact one-line summary of what Luna is currently experiencing in game."""
        with self._lock:
            if not self.connected:
                return "not connected to minecraft right now"
            
            summary = (
                f"playing minecraft: activity='{self.current_activity}', goal='{self.current_goal}', "
                f"health={self.health}/20, hunger={self.food}/20, time={self.time_of_day}, "
                f"pos=({self.position['x']}, {self.position['y']}, {self.position['z']})"
            )
            
            if self.nearby_hostiles:
                hostile_names = [f"{h.get('name', 'mob')}({h.get('dist', '?')}m)" for h in self.nearby_hostiles[:3]]
                summary += f", danger nearby: [{', '.join(hostile_names)}]"
            
            if self.nearby_players:
                player_names = [p.get('name', 'player') for p in self.nearby_players[:3]]
                summary += f", nearby players: [{', '.join(player_names)}]"

            if self.last_significant_event and (time.time() - self.last_event_time < 30):
                summary += f", just happened: {self.last_significant_event}"

            return summary

    def get_prompt_context(self) -> str:
        """Injectable context string for the AI persona prompt."""
        with self._lock:
            if not self.connected:
                return ""
            return f"\n[live game status: {self.get_summary()}]"
