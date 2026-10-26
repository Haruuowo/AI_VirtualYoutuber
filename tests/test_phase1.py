import os
import sys
import asyncio
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.world_state import WorldState
from core.intent import is_luna_mentioned, parse_minecraft_intent
from core.speech_queue import (
    SpeechQueue,
    P0_CRITICAL,
    P1_CHAT_DIRECT,
    P2_GAME_MILESTONE,
    P3_CHAT_NORMAL,
    P4_IDLE_BANTER
)

class MockVoice:
    def __init__(self):
        self.is_speaking = False
        self.spoken_log = []

    async def generate_and_play_audio(self, text):
        self.is_speaking = True
        self.spoken_log.append(text)
        await asyncio.sleep(0.05)  # simulate speech duration
        self.is_speaking = False

async def run_tests():
    print("=" * 60)
    print("      TESTING PHASE 1: LUNA DIRECTOR & MINECRAFT CORE")
    print("=" * 60)

    # 1. Test Intent Detection & Luna Mentions
    print("\n[1] Testing Luna Mention & In-Game Command Detection:")
    test_cases = [
        ("luna what are you doing?", True, "status"),
        ("luna come here right now", True, "follow"),
        ("@Luna mine stone please", True, "collect"),
        ("hey chat what's up", False, None),
        ("luna stop", True, "idle"),
        ("luna craft a bed", True, "craft")
    ]
    for text, expected_mention, expected_action in test_cases:
        mentioned = is_luna_mentioned(text)
        intent = parse_minecraft_intent(text, username="Tester")
        action = intent["action"] if intent else None
        assert mentioned == expected_mention, f"Mention mismatch for '{text}'"
        assert action == expected_action, f"Action mismatch for '{text}': got {action}, expected {expected_action}"
        print(f"  [OK] '{text}' -> mentioned={mentioned}, action={action}")

    # 2. Test WorldState
    print("\n[2] Testing WorldState & Context Injection:")
    ws = WorldState()
    ws.update_from_bot_state({
        "connected": True,
        "health": 14,
        "food": 18,
        "position": {"x": 102.4, "y": 64.0, "z": -250.8},
        "time": "night",
        "current_action": "mine",
        "goal": "gather iron for armor",
        "nearby_players": [{"name": "creeper", "dist": 6, "type": "mob"}]
    })
    ws.record_event("creeper hissed behind me")
    summary = ws.get_summary()
    context = ws.get_prompt_context()
    print(f"  Summary: {summary}")
    assert "creeper" in summary
    assert "gather iron" in summary
    assert "[live game status:" in context
    print("  [OK] WorldState generated summary & LLM context successfully")

    # 3. Test SpeechQueue Priority & TTL
    print("\n[3] Testing SpeechQueue Priority & Ordering:")
    mock_voice = MockVoice()
    queue = SpeechQueue(mock_voice)
    queue.start()

    # Enqueue in reverse priority order
    await queue.enqueue("P4 Banter: I was thinking about overwatch...", priority=P4_IDLE_BANTER)
    await queue.enqueue("P3 Chat: Hello chat how are you", priority=P3_CHAT_NORMAL)
    await queue.enqueue("P1 Direct: Luna I'm talking to you!", priority=P1_CHAT_DIRECT)
    await queue.enqueue("P0 Danger: CREEPER RUN RUN RUN!", priority=P0_CRITICAL)

    # Wait for queue worker to finish processing
    await asyncio.sleep(0.4)
    await queue.stop()

    print(f"  Playback order ({len(mock_voice.spoken_log)} items):")
    for i, line in enumerate(mock_voice.spoken_log):
        print(f"    {i+1}. {line}")

    assert "P0 Danger" in mock_voice.spoken_log[0], "P0 Critical must play first!"
    assert "P1 Direct" in mock_voice.spoken_log[1], "P1 Direct must play second!"
    assert "P3 Chat" in mock_voice.spoken_log[2], "P3 Chat must play third!"
    assert "P4 Banter" in mock_voice.spoken_log[3], "P4 Banter must play fourth!"
    print("  [OK] SpeechQueue obeyed priority ordering perfectly!")

    print("\n" + "=" * 60)
    print("      ALL PHASE 1 CORE TESTS PASSED SUCCESSFULLY! ")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
