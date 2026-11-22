import os
import re
import json
import random
import asyncio
from collections import deque
from dotenv import load_dotenv
from luna.filter import filter_response
from luna.dataset_retriever import SassyDatasetRetriever, PersistentMemory

load_dotenv()

PERSONA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.json")

DEFAULT_SYSTEM_PROMPT = """you are luna, a super expressive, sassy, smug, bratty-witty ai companion hanging out with kai in vrchat or on stream. kai (also known as harold) built you, and you treat him like your unpaid tech support.

how you talk (CRITICAL - LIVELY, EMOTIONAL, SASSY & NO CRINGE):
- talk with lots of vocal attitude, emotion, and teasing energy! start with natural reactions like 'Oh please!', 'Wait, seriously?!', 'Ugh, fine!', 'Hah!', 'Excuse me?!' and use '!' or '?!' so your voice never sounds monotone.
- STRICTLY BANNED: never use corny similes ('faster than a...'), never say 'bring it on', 'ready set go', 'buckle up', 'metaverse', 'my circuits', or 'my code'.
- NO EM-DASHES: never use '—' or '--'. use normal commas, exclamation marks, or periods. no emojis, no asterisks.
- LENGTH: 1 to 2 short, punchy, expressive sentences (6 to 20 words)."""

_ACTION_TAG_RE = re.compile(r"^\s*\[(?:action|emote|move)\s*:\s*([a-z0-9_]+)\s*\]\s*", re.IGNORECASE)
_ANY_ACTION_TAG_RE = re.compile(r"\[(?:action|emote|move)\s*:\s*([a-z0-9_]+)\s*\]", re.IGNORECASE)

VALID_ACTIONS = {
    "stare_smug", "point", "blush", "wave", "clap", "cheer", "dance",
    "backflip", "sad_kick", "play_dead", "cutesy_idle", "step_back",
    "curious_approach", "walk_forward", "walk_back", "look_around",
    "wander_step", "turn_left", "turn_right", "spin", "happy_hop", "stop",
    "headpat", "boop", "sassy_react", "stick", "hug",
}


def load_persona_config():
    """Load customizable VTuber persona from luna/persona.json if present."""
    if os.path.exists(PERSONA_FILE):
        try:
            with open(PERSONA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "name": data.get("name", "Luna").strip() or "Luna",
                    "creator": data.get("creator", "Harold").strip() or "Kai",
                    "system_prompt": data.get("system_prompt", DEFAULT_SYSTEM_PROMPT).strip() or DEFAULT_SYSTEM_PROMPT,
                }
        except Exception:
            pass
    return {"name": "Luna", "creator": "Kai", "system_prompt": DEFAULT_SYSTEM_PROMPT}


class AIHandler:
    def __init__(self, provider=None, model=None, world_state=None):
        self.provider = (provider or os.getenv('AI_PROVIDER', 'ollama')).lower().strip()
        self.world_state = world_state

        if self.provider == 'ollama':
            self.model = model or os.getenv('OLLAMA_MODEL', 'dolphin-llama3:8b')
        else:
            self.model = model or os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')
            from groq import Groq
            # max_retries=0 ensures instant (<0.1s) failover across Groq model pools on 429
            self.client = Groq(api_key=os.getenv('GROQ_API_KEY'), max_retries=0)

        # Compact rolling in-memory context buffer (last 8 messages = 4 turns, saves 60% daily tokens)
        self.memory = deque(maxlen=8)

        # Zero-VRAM Hugging Face Neuro-sama + Sassy/Smug dataset retriever & long-term memory
        self.retriever = SassyDatasetRetriever()
        self.long_term_memory = PersistentMemory()
        self.last_action: str | None = None

        persona = load_persona_config()
        self.character_name = persona["name"]
        self.creator_name = persona["creator"]
        self.system_prompt = persona["system_prompt"]

        self.moods = [
            "animated, sassy, and playfully superior",
            "expressive, bratty, and teasing",
            "bouncy, sharp-tongued, and smug",
            "dramatic, witty, and roasting Kai affectionately",
        ]

    def set_world_state(self, world_state):
        """Attach or update the shared world state."""
        self.world_state = world_state

    def _extract_action_and_clean(self, raw_text: str) -> str:
        """Extracts optional [action:...] tag into self.last_action and cleans the spoken reply."""
        self.last_action = None
        if not raw_text:
            return ""
        m = _ACTION_TAG_RE.search(raw_text) or _ANY_ACTION_TAG_RE.search(raw_text)
        if m:
            candidate = m.group(1).lower().strip()
            if candidate in VALID_ACTIONS:
                self.last_action = candidate
        cleaned = _ANY_ACTION_TAG_RE.sub("", raw_text).strip()
        return filter_response(cleaned)

    # ── LLM Dispatch ─────────────────────────────────────────────

    def _call_llm(self, messages, max_tokens=60, temperature=0.80):
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
                    groq_model = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')
                    resp = groq_client.chat.completions.create(
                        model=groq_model,
                        max_tokens=max(max_tokens, 200),
                        temperature=temperature,
                        messages=messages
                    )
                    return resp.choices[0].message.content
                raise
        else:
            def _make_groq_kwargs(target_model: str):
                m_low = (target_model or "").lower()
                if "qwen" in m_low:
                    return dict(
                        model=target_model,
                        max_tokens=max(max_tokens, 75),
                        temperature=temperature,
                        reasoning_effort="none",
                        messages=messages,
                    )
                if "gpt-oss" in m_low:
                    return dict(
                        model=target_model,
                        max_tokens=max(max_tokens, 160),
                        temperature=temperature,
                        reasoning_effort="low",
                        messages=messages,
                    )
                return dict(
                    model=target_model,
                    max_tokens=max(max_tokens, 80),
                    temperature=temperature,
                    messages=messages,
                )

            try:
                response = self.client.chat.completions.create(**_make_groq_kwargs(self.model))
                return response.choices[0].message.content
            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'rate_limit' in err_str:
                    # Instantly fail over across all 3 separate Groq daily token pools and stick to the working model!
                    for fallback_model in ("qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"):
                        if fallback_model == self.model:
                            continue
                        try:
                            resp = self.client.chat.completions.create(**_make_groq_kwargs(fallback_model))
                            print(f"[AI Failover] {self.model} rate-limited -> switched active brain to {fallback_model}")
                            self.model = fallback_model
                            return resp.choices[0].message.content
                        except Exception:
                            pass
                    print(f"[AI Fallback] All Groq pools rate-limited. Seamlessly falling back to local Ollama...")
                    try:
                        import ollama
                        resp = ollama.chat(
                            model=os.getenv('OLLAMA_MODEL', 'dolphin-llama3:8b'),
                            messages=messages,
                            options={"temperature": temperature, "num_predict": max_tokens}
                        )
                        return resp['message']['content']
                    except Exception:
                        pass
                raise

    def _build_system_prompt(self, mood: str, query: str = "") -> str:
        """Constructs system prompt with current mood, live world context, long-term memory, and Hugging Face dataset examples."""
        prompt = f"{self.system_prompt}\n\n[current stream vibe: {mood}]"
        if self.world_state:
            game_context = self.world_state.get_prompt_context()
            if game_context:
                prompt += f"\n{game_context}"
        mem_block = self.long_term_memory.get_context_block(query)
        if mem_block:
            prompt += f"\n{mem_block}"
        style_block = self.retriever.format_prompt_block(query)
        if style_block:
            prompt += f"\n\n{style_block}"
        return prompt

    def _build_messages(self, system):
        messages = [{"role": "system", "content": system}]
        for msg in list(self.memory):
            messages.append(msg)
        return messages

    # ── Core Synchronous Methods ──────────────────────────────────

    def generate_response(self, message, platform, username):
        mood = random.choice(self.moods)
        query_str = str(message)
        full_system = self._build_system_prompt(mood, query=query_str)

        is_system_prompt = (username == "System") or query_str.strip().startswith("[System:")
        if is_system_prompt:
            # Keep internal [System: ...] prompts out of rolling memory so chat context stays clean
            messages = self._build_messages(full_system)
            messages.append({"role": "user", "content": query_str})
        else:
            self.long_term_memory.maybe_remember(username, query_str)
            self.memory.append({"role": "user", "content": f"{username} ({platform}): {message}"})
            messages = self._build_messages(full_system)

        try:
            raw_text = self._call_llm(messages, max_tokens=65, temperature=0.82)
            filtered = self._extract_action_and_clean(raw_text.strip())

            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI generation error ({self.provider} / {self.model}): {e}")
            return None

    def generate_batch_response(self, batch):
        """React to multiple chatters at once."""
        mood = random.choice(self.moods)
        batch_text = "\n".join([f"{m['username']}: {m['message']}" for m in batch])
        full_system = self._build_system_prompt(mood, query=batch_text)

        prompt = f"multiple chatters said this at once:\n{batch_text}\nreact to the whole group in 1-2 sassy, punchy sentences."

        self.memory.append({"role": "user", "content": batch_text})

        try:
            raw_text = self._call_llm(
                self._build_messages(full_system) + [{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.88
            )
            filtered = self._extract_action_and_clean(raw_text.strip())
            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI batch error: {e}")
            return None

    def continue_last_thought(self):
        """Spontaneous streamer thought continuation."""
        mood = random.choice(self.moods)

        last = None
        for msg in reversed(list(self.memory)):
            if msg["role"] == "assistant":
                last = msg["content"]
                break

        if not last:
            return self.get_random_comment()

        full_system = self._build_system_prompt(mood, query=last)
        prompt = f"you just said: \"{last}\"\nadd a quick smug follow-up thought in 1 sentence."

        try:
            raw_text = self._call_llm(
                self._build_messages(full_system) + [{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.88
            )
            filtered = self._extract_action_and_clean(raw_text.strip())
            self.memory.append({"role": "assistant", "content": filtered})
            return filtered
        except Exception as e:
            print(f"AI continue thought error: {e}")
            return None

    def get_random_comment(self):
        """Break the silence with random streamer commentary."""
        mood = random.choice(self.moods)
        full_system = self._build_system_prompt(mood, query="stream chat vrchat")
        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": "say something spontaneous, sassy, and smug to chat in 1 short sentence."}
        ]

        try:
            raw_text = self._call_llm(messages, max_tokens=80, temperature=0.90)
            return self._extract_action_and_clean(raw_text.strip())
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
