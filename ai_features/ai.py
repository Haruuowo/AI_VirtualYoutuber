import os
import random
from collections import deque
from filter import filter_response

class AIHandler:
    def __init__(self, provider=None, model=None):
        self.provider = (provider or os.getenv('AI_PROVIDER', 'ollama')).lower().strip()
        
        if self.provider == 'ollama':
            self.model = model or os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')
        else:
            self.model = model or os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')
            from groq import Groq
            self.client = Groq(api_key=os.getenv('GROQ_API_KEY'))

        # Clean rolling in-memory context buffer (last 16 messages)
        self.memory = deque(maxlen=16)

        self.system_prompt = """you are luna, a chaotic, funny, and sarcastic ai vtuber streaming live to chat. you are not an ai assistant; you are a real streamer hanging out with your community.

your character & gaming lore:
- creator: kai created you, and you love teasing or roasting him.
- overwatch: you are an avid overwatch 2 player. you constantly claim you belong in grandmaster/top 500 (even if hardstuck). you hate sombra, mauga, and mei players with a passion. you rage about 'tank diff', 'support diff', and people who refuse to touch the payload or counterswap.
- minecraft: you play minecraft autonomously, mine randomly, panic when creepers hiss, and yell when night falls.
- streaming vibe: unhinged gamer girl energy, witty comebacks, confident roasts, but secretly loves hanging out with chat.

strict output rules:
- never use asterisks, stage directions, or roleplay actions (do NOT say *sighs*, *giggles*, *laughs*, etc). speak your words directly.
- never give dry 1-word dead ends (like 'nothing', 'no', 'hi'). always give a punchy comeback, counter-question, or funny thought.
- keep replies short and conversational: 1 to 2 sentences max.
- lowercase only, no emojis, no markdown.
- say 'i dunno' instead of 'idk'.
- use natural punctuation (!, ?, ..., commas) so your voice reads with emotion and inflection."""

        self.moods = [
            "competitive gamer rage mode, talking trash and making excuses",
            "hyper and bouncing between chaotic gaming takes",
            "witty and roasting chat playfully with fast comebacks",
            "acting super confident and bragging about nonexistent pro gamer skills",
            "dramatically offended by chat in a funny way",
            "curious and demanding chat explain their weird opinions",
            "chill and vibe-checking chat while cracking jokes"
        ]

    # ── LLM Dispatch ─────────────────────────────────────────────

    def _call_llm(self, messages, max_tokens=120, temperature=0.85):
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
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages
            )
            return response.choices[0].message.content

    def _build_messages(self, system):
        messages = [{"role": "system", "content": system}]
        for msg in list(self.memory):
            messages.append(msg)
        return messages

    # ── Core Methods ─────────────────────────────────────────────

    def generate_response(self, message, platform, username):
        mood = random.choice(self.moods)
        full_system = f"{self.system_prompt}\n\n[current stream vibe: {mood}]"

        self.memory.append({"role": "user", "content": f"{username} ({platform}): {message}"})

        try:
            raw_text = self._call_llm(self._build_messages(full_system), max_tokens=110, temperature=0.88)
            filtered = filter_response(raw_text.strip())

            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI generation error ({self.provider} / {self.model}): {e}")
            return None

    def generate_batch_response(self, batch):
        """React to multiple chatters at once."""
        mood = random.choice(self.moods)
        full_system = f"{self.system_prompt}\n\n[current stream vibe: {mood}]"

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
        full_system = f"{self.system_prompt}\n\n[current stream vibe: {mood}]"

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
        messages = [
            {"role": "system", "content": f"{self.system_prompt}\n\n[current stream vibe: {mood}]"},
            {"role": "user", "content": "say something spontaneous, funny, or unhinged to chat about your games or stream in 1 sentence."}
        ]

        try:
            raw_text = self._call_llm(messages, max_tokens=80, temperature=0.92)
            return filter_response(raw_text.strip())
        except Exception as e:
            print(f"AI random comment error: {e}")
            return None