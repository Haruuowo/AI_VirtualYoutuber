import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Any

# Priority levels (lower number = higher priority)
P0_CRITICAL = 0       # Immediate game danger / death scream / panic ("CREEPER!")
P1_CHAT_DIRECT = 1    # Directly addressed Luna (Mic Kai, @luna, "luna ...")
P2_GAME_MILESTONE = 2 # Game progress reactions ("found diamonds!", "crafted bed")
P3_CHAT_NORMAL = 3    # General chat banter / batch messages
P4_IDLE_BANTER = 4    # Spontaneous streamer monologue when chat is quiet

@dataclass(order=True)
class SpeechItem:
    priority: int
    created_at: float = field(compare=False)
    text: str = field(compare=False)
    ttl: float = field(compare=False, default=15.0)  # Seconds before item expires and is dropped
    source: str = field(compare=False, default="chat")
    username: Optional[str] = field(compare=False, default=None)

class SpeechQueue:
    """
    Priority-based speech queue with TTL for Luna.
    Ensures only ONE voice output plays at a time and high-priority events
    (direct questions to Luna or in-game danger) supersede casual chatter.
    """
    def __init__(self, voice_handler):
        self.voice_handler = voice_handler
        self._queue = asyncio.PriorityQueue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running = False

    def start(self):
        """Start the background speech worker task."""
        if not self._is_running:
            self._is_running = True
            self._worker_task = asyncio.create_task(self._process_queue())
            self._worker_task.add_done_callback(
                lambda t: print(f"[SpeechQueue] Worker stopped: {t.exception() if t.exception() else 'ok'}")
            )

    async def enqueue(self, text: str, priority: int = P3_CHAT_NORMAL, ttl: float = 15.0, source: str = "chat", username: Optional[str] = None):
        """Enqueue speech item to be spoken by Luna."""
        if not text or not text.strip():
            return
        clean_text = text.strip()
        item = SpeechItem(
            priority=priority,
            created_at=time.time(),
            text=clean_text,
            ttl=ttl,
            source=source,
            username=username
        )
        await self._queue.put(item)
        print(f"[SpeechQueue] Queued (P{priority} - {source}): {clean_text[:40]}...")

    def clear(self):
        """Clear all pending speech items in the queue."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Exception:
                break

    async def stop(self):
        """Gracefully stop the speech worker."""
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _process_queue(self):
        while self._is_running:
            try:
                item: SpeechItem = await self._queue.get()
                now = time.time()

                # Check if item expired (TTL)
                if (now - item.created_at) > item.ttl:
                    print(f"[SpeechQueue] Dropped expired item (P{item.priority}, {now - item.created_at:.1f}s old): {item.text[:30]}...")
                    self._queue.task_done()
                    continue

                # Wait if voice is currently speaking from an external trigger
                while self.voice_handler.is_speaking:
                    await asyncio.sleep(0.1)

                print(f"[SpeechQueue] Speaking (P{item.priority}): {item.text}")
                await self.voice_handler.generate_and_play_audio(item.text)
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[SpeechQueue] Error during speech playback: {e}")
                await asyncio.sleep(0.2)
