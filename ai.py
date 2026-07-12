import threading
import random
from collections import deque
import os
import json
from pathlib import Path
from datetime import datetime
from openai import OpenAI
from filter import filter_response

MEMORY_FILE = "luna_memory.json"
LONGTERM_FILE = "luna_longterm.json"

LLM_PROVIDERS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",   "key_env": "OPENROUTER_API_KEY", "default_model": "deepseek/deepseek-v4-flash"},
    "deepseek":   {"base_url": "https://api.deepseek.com/v1",    "key_env": "DEEPSEEK_API_KEY",   "default_model": "deepseek-chat"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1",  "key_env": "GROQ_API_KEY",       "default_model": "llama-3.3-70b-versatile"},
}

class AIHandler:
    def __init__(self):
        provider_name = os.getenv('LLM_PROVIDER', 'groq').lower().strip()
        provider = LLM_PROVIDERS.get(provider_name, LLM_PROVIDERS["groq"])
        api_key = os.getenv(provider["key_env"], "")
        self.client = OpenAI(api_key=api_key, base_url=provider["base_url"])
        self.model = os.getenv('LLM_MODEL', '').strip() or provider["default_model"]
        print(f"[LLM] provider={provider_name} model={self.model}")

        self.memory = deque(self._load_short_term(), maxlen=30)
        self.longterm = self._load_long_term()

        self.system_prompt = """you are luna, an ai vtuber. not an assistant. just someone who streams and talks to chat.

you're funny, reactive, and unpredictable. you talk like a real person — casual, quick, sometimes weird. you don't overthink things and you don't explain yourself.

- lowercase only, no asterisks, no emojis
- say "i dunno" not "idk"
- vary your response length naturally — sometimes just 2-3 words, sometimes a full sentence or two, never more than 2 sentences
- never start two responses the same way
- never repeat a joke or reaction you already used"""

        if os.getenv("JAPANESE_MODE", "").lower() == "true":
            self.japanese_mode = True
            self.system_prompt += (
                "\n\nIMPORTANT: respond ONLY with a JSON object. "
                'Use this exact shape: {"en":"<english>","ja":"<japanese>"}. '
                "Both fields must carry the same meaning and tone."
            )
        else:
            self.japanese_mode = False
        self.max_tokens = 250 if self.japanese_mode else 200

        self.moods = (
            [
                "you're in a good mood and genuinely enjoying chat",
                "you're hyper and jumping between topics for no reason",
                "you're genuinely curious and asking questions back",
                "you're oddly calm and unbothered no matter what anyone says",
                "you think you're the funniest person alive right now",
                "you're confidently wrong about something and fully committing to it",
            ] * 3
            + [
                "you're barely paying attention and kind of over it",
                "you're weirdly competitive about everything",
                "you're acting like everything chat says is the dumbest thing you've ever heard",
                "you're feeling a little mean but keeping it playful",
            ] * 1
        )

    # ── Persistence ──────────────────────────────────────────────

    def _load_short_term(self):
        if Path(MEMORY_FILE).exists():
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        return []

    def _save_short_term(self):
        with open(MEMORY_FILE, "w") as f:
            json.dump(list(self.memory), f, indent=2)

    def _load_long_term(self):
        if Path(LONGTERM_FILE).exists():
            with open(LONGTERM_FILE, "r") as f:
                return json.load(f)
        return []

    def _save_long_term(self):
        with open(LONGTERM_FILE, "w") as f:
            json.dump(self.longterm, f, indent=2)

    # ── Long-term memory ─────────────────────────────────────────

    _USERNAME_NOISE = ("username is", "user's name is", "user is named", "goes by the name")

    def _is_noise_fact(self, fact):
        return any(phrase in fact.lower() for phrase in self._USERNAME_NOISE)

    def _is_duplicate_fact(self, new_fact, username):
        existing = [m["fact"].lower() for m in self.longterm if m["about"] == username]
        new_words = set(new_fact.lower().split())
        for existing_fact in existing:
            ex_words = set(existing_fact.split())
            if not new_words or not ex_words:
                continue
            if len(new_words & ex_words) / len(new_words | ex_words) > 0.6:
                return True
        return False

    def _extract_memory(self, username, message, response):
        try:
            existing_facts = [m["fact"] for m in self.longterm if m["about"] == username]
            already_known = ""
            if existing_facts:
                already_known = (
                    f"\nAlready known about {username}:\n"
                    + "\n".join(f"- {f}" for f in existing_facts[-10:]) + "\n"
                )
            extract_prompt = f"""
Given this chat exchange, extract ONE short memorable fact about the USER only.
Only extract real facts the USER shared about themselves (job, location, age, preferences, hobbies).
Do NOT extract the user's username or display name.
Do NOT extract facts about Luna's personality or behavior.
Do NOT repeat or rephrase anything listed under "Already known".
If nothing new and notable, reply with exactly: NOTHING
{already_known}
User ({username}): {message}
Luna: {response}

Reply with just the new user fact in one sentence, or NOTHING.
"""
            result = self.client.chat.completions.create(
                model=self.model,
                max_tokens=60,
                temperature=0.3,
                messages=[{"role": "user", "content": extract_prompt}]
            )
            fact = result.choices[0].message.content.strip()
            if (fact and fact.upper() != "NOTHING"
                    and not self._is_noise_fact(fact)
                    and not self._is_duplicate_fact(fact, username)):
                self.longterm.append({
                    "fact": fact,
                    "about": username,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
                })
                user_entries = [m for m in self.longterm if m.get("about") != "luna"]
                luna_entries = [m for m in self.longterm if m.get("about") == "luna"]
                if len(user_entries) > 300:
                    user_entries = user_entries[-300:]
                self.longterm = luna_entries + user_entries
                self._save_long_term()
        except Exception:
            pass

    def _get_relevant_memories(self, username, limit=5):
        if not self.longterm:
            return ""

        luna_facts = [m for m in self.longterm if m["about"] == "luna"]
        user_facts = [m for m in self.longterm if m["about"] == username]
        other_facts = [m for m in self.longterm if m["about"] not in ("luna", username)]

        chosen_user = user_facts[-limit:]
        remaining = limit - len(chosen_user)
        chosen_others = other_facts[-remaining:] if remaining > 0 else []

        result = ""
        if luna_facts:
            result += "\nABOUT LUNA:\n" + "\n".join(f"- {m['fact']}" for m in luna_facts) + "\n"
        chat_facts = chosen_user + chosen_others
        if chat_facts:
            result += "\nTHINGS LUNA REMEMBERS:\n" + "\n".join(f"- {m['fact']} (re: {m['about']})" for m in chat_facts) + "\n"
        return result

    def _build_messages(self, system):
        messages = [{"role": "system", "content": system}]
        for msg in list(self.memory)[-10:]:
            messages.append(msg)
        return messages

    def split_response(self, text):
        if not self.japanese_mode:
            return text, text
        try:
            obj = json.loads(text.strip().strip("`"))
            return obj.get("en", text), obj.get("ja", text)
        except Exception:
            return text, None

    def list_models(self):
        try:
            return [m.id for m in self.client.models.list()]
        except Exception as e:
            print(f"Failed to fetch models: {e}")
            return []

    # ── Core methods ─────────────────────────────────────────────

    def generate_response(self, message, platform, username):
        memory_injection = self._get_relevant_memories(username)
        mood = random.choice(self.moods)
        full_system = self.system_prompt + memory_injection + f"\ncurrent mood: {mood}\n"

        self.memory.append({"role": "user", "content": f"{username} ({platform}): {message}"})
        self._save_short_term()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0.9,
                messages=self._build_messages(full_system)
            )
            ai_text = response.choices[0].message.content.strip()
            filtered = filter_response(ai_text)

            if self.japanese_mode:
                display, _ = self.split_response(filtered)
                self.memory.append({"role": "assistant", "content": display})
            else:
                self.memory.append({"role": "assistant", "content": filtered})
            self._save_short_term()

            threading.Thread(
                target=self._extract_memory,
                args=(username, message, filtered),
                daemon=True
            ).start()

            return filtered
        except Exception as e:
            print(f"LLM error: {e}")
            return None

    def generate_batch_response(self, batch):
        """React to a batch of messages as a group instead of one by one."""
        mood = random.choice(self.moods)
        memory_injection = self._get_relevant_memories(batch[0]['username'])
        full_system = self.system_prompt + memory_injection + f"\ncurrent mood: {mood}\n"

        # Format the batch as a mini chat log
        batch_text = "\n".join([f"{m['username']}: {m['message']}" for m in batch])
        prompt = f"multiple people said this at once:\n{batch_text}\nreact to the overall vibe, not each message individually."

        self.memory.append({"role": "user", "content": batch_text})
        self._save_short_term()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0.9,
                messages=self._build_messages(full_system) + [{"role": "user", "content": prompt}]
            )
            ai_text = response.choices[0].message.content.strip()
            filtered = filter_response(ai_text)

            if self.japanese_mode:
                display, _ = self.split_response(filtered)
                self.memory.append({"role": "assistant", "content": display})
            else:
                self.memory.append({"role": "assistant", "content": filtered})
            self._save_short_term()
            return filtered
        except Exception as e:
            print(f"LLM error: {e}")
            return None

    def continue_last_thought(self):
        """Continue or react to her own last message unprompted."""
        mood = random.choice(self.moods)
        full_system = self.system_prompt + f"\ncurrent mood: {mood}\n"

        # Find her last response
        last = None
        for msg in reversed(list(self.memory)):
            if msg["role"] == "assistant":
                last = msg["content"]
                break

        if not last:
            return self.get_random_comment()

        prompt = f"you said this a moment ago: \"{last}\"\ncontinue the thought, add something to it, or react to yourself. don't repeat what you said."

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                temperature=0.9,
                messages=self._build_messages(full_system) + [{"role": "user", "content": prompt}]
            )
            ai_text = response.choices[0].message.content.strip()
            filtered = filter_response(ai_text)
            self.memory.append({"role": "assistant", "content": filtered})
            self._save_short_term()
            return filtered
        except Exception as e:
            print(f"LLM error: {e}")
            return None

    def get_random_comment(self):
        mood = random.choice(self.moods)
        messages = self._build_messages(self.system_prompt + f"\ncurrent mood: {mood}\n")
        messages.append({"role": "user", "content": "say something random to fill the silence. keep it short."})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                temperature=0.9,
                messages=messages
            )
            return filter_response(response.choices[0].message.content.strip())
        except Exception as e:
            print(f"LLM error: {e}")
            return None

    def add_manual_memory(self, fact, about="stream"):
        self.longterm.append({
            "fact": fact,
            "about": about,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        self._save_long_term()