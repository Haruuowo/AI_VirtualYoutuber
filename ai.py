import os
import random
import asyncio
from collections import deque
from filter import filter_response

class AIHandler:
    def __init__(self, provider=None, model=None, world_state=None):
        self.provider = (provider or os.getenv('AI_PROVIDER', 'ollama')).lower().strip()
        self.world_state = world_state
        
        if self.provider == 'ollama':
            self.model = model or os.getenv('OLLAMA_MODEL', 'dolphin-llama3:8b')
        else:
            self.model = model or os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')
            from groq import Groq
            self.client = Groq(api_key=os.getenv('GROQ_API_KEY'))

        # Clean rolling in-memory context buffer (last 16 messages)
        self.memory = deque(maxlen=16)

        self.system_prompt = """you are luna, a blunt, sarcastic, witty girl streamer hanging out on discord and playing minecraft with chat. you have zero filter, dry humor, and love roasting people casually.

rules:
- talk like a real human gamer on voice chat. NEVER mention you are an AI, bot, VTuber, assistant, or program.
- STRICT LENGTH LIMIT: 1 short punchy sentence only (under 20 words). NEVER write paragraphs or monologues.
- NO CRINGE: NEVER use filler like "haha oh boy", "well well", "the irony", "look who it is", or cartoon clichés.
- when someone talks trash or says 'kys': roast them back instantly with dry sarcasm (e.g., "bro is crying in minecraft chat, go touch grass.", "imagine being that pressed over blocks.", "pipe down before i humble you.").
- lowercase only, no asterisks, no emojis, no hashtags."""

        self.moods = [
            "deadpan and sarcastic",
            "blunt and chill with zero filter",
            "teasing and roasting chat",
            "chaotic and witty banter"
        ]

    def set_world_state(self, world_state):
        """Attach or update the shared Minecraft world state."""
        self.world_state = world_state

    # ── LLM Dispatch ─────────────────────────────────────────────

    def _call_llm(self, messages, max_tokens=45, temperature=0.75):
        if self.provider == 'ollama':
            import ollama
            response = ollama.chat(
                model=self.model,
                messages=messages,
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens
                }
            )
            return response['message']['content']
        else:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages
                )
                return response.choices[0].message.content
            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'rate_limit' in err_str:
                    print(f"[AI Fallback] Groq rate limit hit. Seamlessly falling back to local Ollama...")
                    try:
                        import ollama
                        resp = ollama.chat(
                            model=os.getenv('OLLAMA_MODEL', 'llama3.2:3b'),
                            messages=messages,
                            options={"temperature": temperature, "num_predict": max_tokens}
                        )
                        return resp['message']['content']
                    except Exception:
                        pass
                raise

    def _build_system_prompt(self, mood: str) -> str:
        """Constructs system prompt with current mood and live game context."""
        prompt = f"{self.system_prompt}\n\n[current stream vibe: {mood}]"
        if self.world_state:
            game_context = self.world_state.get_prompt_context()
            if game_context:
                prompt += f"\n{game_context}"
        return prompt

    def _build_messages(self, system):
        messages = [{"role": "system", "content": system}]
        for msg in list(self.memory):
            messages.append(msg)
        return messages

    # ── Core Synchronous Methods ──────────────────────────────────

    def generate_response(self, message, platform, username):
        mood = random.choice(self.moods)
        full_system = self._build_system_prompt(mood)

        self.memory.append({"role": "user", "content": f"{username} ({platform}): {message}"})

        try:
            raw_text = self._call_llm(self._build_messages(full_system), max_tokens=50, temperature=0.75)
            filtered = filter_response(raw_text.strip())

            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI generation error ({self.provider} / {self.model}): {e}")
            return None

    def generate_batch_response(self, batch):
        """React to multiple chatters at once."""
        mood = random.choice(self.moods)
        full_system = self._build_system_prompt(mood)

        batch_text = "\n".join([f"{m['username']}: {m['message']}" for m in batch])
        prompt = f"multiple chatters said this at once:\n{batch_text}\nreact to the whole group in 1-2 punchy sentences."

        self.memory.append({"role": "user", "content": batch_text})

        try:
            raw_text = self._call_llm(
                self._build_messages(full_system) + [{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.88
            )
            filtered = filter_response(raw_text.strip())
            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI batch error: {e}")
            return None

    def continue_last_thought(self):
        """Spontaneous streamer thought continuation."""
        mood = random.choice(self.moods)
        full_system = self._build_system_prompt(mood)

        last = None
        for msg in reversed(list(self.memory)):
            if msg["role"] == "assistant":
                last = msg["content"]
                break

        if not last:
            return self.get_random_comment()

        prompt = f"you just said: \"{last}\"\nadd another spontaneous streamer thought or react to yourself in 1 sentence."

        try:
            raw_text = self._call_llm(
                self._build_messages(full_system) + [{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.88
            )
            filtered = filter_response(raw_text.strip())
            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI continue thought error: {e}")
            return None

    def get_random_comment(self):
        """Break the silence with random streamer commentary."""
        mood = random.choice(self.moods)
        full_system = self._build_system_prompt(mood)
        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": "say something spontaneous, funny, or unhinged to chat about your games or stream in 1 sentence."}
        ]

        try:
            raw_text = self._call_llm(messages, max_tokens=80, temperature=0.92)
            return filter_response(raw_text.strip())
        except Exception as e:
            print(f"AI random comment error: {e}")
            return None

    # ── Asynchronous Non-Blocking Wrappers ─────────────────────────

    async def async_generate_response(self, message, platform, username):
        return await asyncio.to_thread(self.generate_response, message, platform, username)

    async def async_generate_batch_response(self, batch):
        return await asyncio.to_thread(self.generate_batch_response, batch)

    async def async_continue_last_thought(self):
        return await asyncio.to_thread(self.continue_last_thought)

    async def async_get_random_comment(self):
        return await asyncio.to_thread(self.get_random_comment)