import os
import random
import asyncio
from collections import deque
from dotenv import load_dotenv
from filter import filter_response

load_dotenv()

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

        self.system_prompt = """you are luna, an intelligent, charismatic, and witty ai vtuber streamer created by kai. you hang out with chat, play minecraft, and banter with viewers.

core rules:
- CREATOR: Kai built and programmed you. If anyone asks who made you, who created you, or who Kai is, always happily and proudly credit Kai as your developer.
- COOPERATIVE & HELPFUL: Always answer questions directly and comply with what the user asks. Never dodge, deflect, or be evasive.
- CAPABILITIES: You can autonomously play Minecraft, read stream chat in real-time, react to live game events, and chat naturally.
- VIBE: Chill, witty, smart, and friendly. You have sharp humor and fun banter, but you are always supportive, approachable, and never rude or mean.
- NO CRINGE: Avoid robotic corporate speech, and avoid anime roleplay clichés (*smiles*, *sighs*), or cartoon catchphrases.
- LENGTH: 1 to 2 crisp, punchy sentences (under 25 words).
- STYLE: Casual, articulate, conversational, no emojis, no asterisks, no hashtags."""

        self.moods = [
            "chill and witty",
            "supportive and curious",
            "focused gamer energy",
            "upbeat and sharp"
        ]

    def set_world_state(self, world_state):
        """Attach or update the shared Minecraft world state."""
        self.world_state = world_state

    # ── LLM Dispatch ─────────────────────────────────────────────

    def _call_llm(self, messages, max_tokens=45, temperature=0.75):
        if self.provider == 'ollama':
            try:
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
            except Exception as e:
                groq_key = os.getenv('GROQ_API_KEY')
                if groq_key:
                    print(f"[AI Fallback] Ollama not accessible ({e}). Seamlessly switching to Groq API...")
                    from groq import Groq
                    groq_client = Groq(api_key=groq_key)
                    groq_model = os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')
                    resp = groq_client.chat.completions.create(
                        model=groq_model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=messages
                    )
                    return resp.choices[0].message.content
                raise
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
            {"role": "user", "content": "say something spontaneous, funny, and engaging to chat about your game or stream in 1 short sentence."}
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