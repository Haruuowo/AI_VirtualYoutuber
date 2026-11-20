"""
Live VRChat World & Player Awareness via VRChat's local output logs.

VRChat writes real-time events to:
    %LocalAppData%Low/VRChat/VRChat/output_log_*.txt

By tailing the latest log file (100% read-only and safe — the same method used
by VRCX), Luna automatically knows:
    * What VRChat world she just entered ("The Black Cat", "VRChat Home", etc.)
    * Which players are currently in the room with her
    * When someone new joins or leaves, so she can greet or react on her own!
"""
import asyncio
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

RE_ROOM = re.compile(r"\[Behaviour\] (?:Joining or Creating Room|Entering Room): (.+)")
RE_JOIN = re.compile(r"\[Behaviour\] OnPlayerJoined ([^\r\n(]+)")
RE_LEFT = re.compile(r"\[Behaviour\] OnPlayerLeft ([^\r\n(]+)")
RE_LOCAL = re.compile(r'Initialized PlayerAPI "([^"]+)" is local')


def log_owner(path: Path) -> str | None:
    """Returns the account name that wrote this VRChat log (the 'local' player)."""
    owner = None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = RE_LOCAL.search(line)
                if m:
                    owner = m.group(1).strip()
    except OSError:
        pass
    return owner


def pick_luna_log(folder: Path) -> Path | None:
    """
    With two VRChat windows open, both write logs to the same folder.
    Pick Luna's log:  VRCHAT_LUNA_USER match  >  not VRCHAT_MAIN_USER  >  newest.
    """
    if not folder.exists():
        return None
    logs = sorted(folder.glob("output_log_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    recent = [p for p in logs if time.time() - p.stat().st_mtime < 6 * 3600][:4] or logs[:1]
    if not recent:
        return None
    luna_user = os.getenv("VRCHAT_LUNA_USER", "").strip().lower()
    main_user = os.getenv("VRCHAT_MAIN_USER", "").strip().lower()
    if not luna_user and not main_user:
        return recent[0]
    owners = [(p, (log_owner(p) or "").lower()) for p in recent]
    for p, owner in owners:
        if luna_user and owner == luna_user:
            return p
    for p, owner in owners:
        if main_user and owner and owner != main_user:
            return p
    return recent[0]


def default_vrc_log_dir() -> Path:
    appdata = os.getenv("APPDATA", "")
    if appdata:
        return Path(appdata).parent / "LocalLow" / "VRChat" / "VRChat"
    return Path.home() / "AppData" / "LocalLow" / "VRChat" / "VRChat"


class VRChatWorldState:
    """
    Shared situational awareness for Luna's brain in VRChat.
    Implements `get_prompt_context()` so `luna.brain.AIHandler` injects it
    into every LLM prompt automatically.
    """

    def __init__(self, self_name: str = "Luna"):
        self._lock = threading.Lock()
        self.self_name = self_name
        self.world_name: str = "VRChat World"
        self.players: set[str] = set()
        self.behavior_mode: str = "roam"       # 'roam' (exploring) or 'chill' (staying put)
        self.last_action: str = "looking around"
        self.recent_events: deque[str] = deque(maxlen=5)

    def set_world(self, name: str):
        with self._lock:
            self.world_name = name.strip()
            self.players.clear()
            self.recent_events.append(f"Entered world '{self.world_name}'")

    def player_joined(self, name: str) -> bool:
        clean = name.strip()
        if not clean or clean.lower() == self.self_name.lower():
            return False
        with self._lock:
            is_new = clean not in self.players
            self.players.add(clean)
            if is_new:
                self.recent_events.append(f"{clean} joined the room")
            return is_new

    def player_left(self, name: str):
        clean = name.strip()
        with self._lock:
            if clean in self.players:
                self.players.discard(clean)
                self.recent_events.append(f"{clean} left the room")

    def set_behavior(self, mode: str, action: str | None = None):
        with self._lock:
            self.behavior_mode = mode
            if action:
                self.last_action = action

    def record_action(self, action: str):
        with self._lock:
            self.last_action = action

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "world": self.world_name,
                "players": sorted(self.players),
                "mode": self.behavior_mode,
                "last_action": self.last_action,
                "events": list(self.recent_events),
            }

    def get_prompt_context(self) -> str:
        snap = self.snapshot()
        players_str = ", ".join(snap["players"][:10]) if snap["players"] else "just you and Kai nearby"
        events_str = "; ".join(snap["events"][-3:]) if snap["events"] else "hanging out"
        mode_desc = (
            "freely roaming and exploring the room on your own account"
            if snap["mode"] == "roam"
            else "hanging out in one spot next to Kai"
        )
        return (
            f"[LIVE VRCHAT STATE: You are physically inside VRChat on your own avatar account. "
            f"Current world: \"{snap['world']}\". "
            f"People in room: {players_str}. "
            f"Your movement state: {mode_desc} (last move: {snap['last_action']}). "
            f"Recent events: {events_str}.]"
        )


async def watch_vrchat_logs(state: VRChatWorldState, on_event, log_dir: Path | None = None):
    """
    Background task that watches VRChat's latest output_log_*.txt and calls:
        await on_event(kind, value)
    where kind is 'world_change' or 'player_join'.
    """
    folder = log_dir or default_vrc_log_dir()
    current_path: Path | None = None
    file_obj = None
    last_pick = 0.0

    try:
        while True:
            # Re-check which log is Luna's every 10s (a new window/login creates a new log).
            newest = current_path
            if time.time() - last_pick > 10 or current_path is None:
                newest = await asyncio.to_thread(pick_luna_log, folder)
                last_pick = time.time()
            if newest and newest != current_path:
                print(f"[vrchat] reading log: {newest.name} (account: {log_owner(newest) or 'not logged in yet'})")
                if file_obj:
                    try:
                        file_obj.close()
                    except Exception:
                        pass
                current_path = newest
                file_obj = current_path.open("r", encoding="utf-8", errors="ignore")
                # Pre-scan existing file for current world & players without firing old events
                for line in file_obj:
                    m_room = RE_ROOM.search(line)
                    if m_room:
                        state.set_world(m_room.group(1))
                    m_join = RE_JOIN.search(line)
                    if m_join:
                        state.player_joined(m_join.group(1))
                    m_left = RE_LEFT.search(line)
                    if m_left:
                        state.player_left(m_left.group(1))

            if file_obj:
                line = file_obj.readline()
                while line:
                    m_room = RE_ROOM.search(line)
                    if m_room:
                        world = m_room.group(1).strip()
                        state.set_world(world)
                        await on_event("world_change", world)
                    m_join = RE_JOIN.search(line)
                    if m_join:
                        player = m_join.group(1).strip()
                        if state.player_joined(player):
                            await on_event("player_join", player)
                    m_left = RE_LEFT.search(line)
                    if m_left:
                        state.player_left(m_left.group(1).strip())
                    line = file_obj.readline()

            await asyncio.sleep(1.0)
    finally:
        if file_obj:
            try:
                file_obj.close()
            except Exception:
                pass
