import threading
import random
from collections import deque
import os
import json
from pathlib import Path
from datetime import datetime
from filter import filter_response

MEMORY_FILE = "luna_memory.json"
LONGTERM_FILE = "luna_longterm.json"

class AIHandler:
    def __init__(self, provider=None, model=None):
        self.provider = (provider or os.getenv('AI_PROVIDER', 'ollama')).lower().strip()
        
        if self.provider == 'ollama':
            self.model = model or os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')
        else:
            self.model = model or os.getenv('GROQ_MODEL', 'qwen/qwen3.8-27b')
            from groq import Groq
            self.client = Groq(api_key=os.getenv('GROQ_API_KEY'))

        self.memory = deque(self._load_short_term(), maxlen=30)
        self.longterm = self._load_long_term()

        self.system_prompt = """you are luna, an entertaining, witty, and chaotic ai vtuber live on stream. you are talking directly to your chat and community.

personality & guidelines:
- talk like a real streamer: casual, funny, expressive, spontaneous, and slightly unhinged.
- never give dry, boring, or lazy 1-word replies (like 'nothing', 'no', 'hi'). always add a funny thought, playful reaction, or follow-up question.
- keep responses punchy and conversational: 1 to 2 sentences max, but full of personality.
- lowercase only, no asterisks, no emojis.
- say "i dunno" instead of "idk".
- use natural punctuation (!, ?, ..., commas) so your voice sounds alive, expressive, and full of emotion.
- playfully tease or banter with chat, treat regular viewers like your chaotic stream buddies."""

        self.moods = [
            "you are in a hyped, energetic mood and love teasing chat",
            "you are feeling witty and have a funny, sarcastic comeback for everything",
            "you are super curious, asking questions and getting chat involved",
            "you are acting confidently wrong about something ridiculous and defending it",
            "you think you are an absolute gaming and comedy prodigy right now",
            "you are in a chaotic, goblin-mode mood bouncing between silly ideas",
            "you are acting dramatically offended in a funny, playful way",
            "you are feeling wholesome and happy to hang out with everyone",
        ]

    # ── LLM Dispatch ─────────────────────────────────────────────

    def _call_llm(self, messages, max_tokens=150, temperature=0.85):
        if self.provider == 'ollama':
            import ollama
            # format messages for ollama
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

    # ── Persistence ──────────────────────────────────────────────

    def _load_short_term(self):
        if Path(MEMORY_FILE).exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_short_term(self):
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.memory), f, indent=2)
        except Exception:
            pass

    def _load_long_term(self):
        if Path(LONGTERM_FILE).exists():
            try:
                with open(LONGTERM_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_long_term(self):
        try:
            with open(LONGTERM_FILE, "w", encoding="utf-8") as f:
                json.dump(self.longterm, f, indent=2)
        except Exception:
            pass

    # ── Long-term memory ─────────────────────────────────────────

    _USERNAME_NOISE = ("username is", "user's name is", "user is named", "goes by the name")

    def _is_noise_fact(self, fact):
        return any(phrase in fact.lower() for phrase in self._USERNAME_NOISE)

    def _is_duplicate_fact(self, new_fact, username):
        existing = [m["fact"].lower() for m in self.longterm if m.get("about") == username]
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
            existing_facts = [m["fact"] for m in self.longterm if m.get("about") == username]
            already_known = ""
            if existing_facts:
                already_known = (
                    f"\nAlready known about {username}:\n"
                    + "\n".join(f"- {f}" for f in existing_facts[-10:]) + "\n"
                )
            extract_prompt = f"""Given this chat exchange, extract ONE short memorable fact about the USER only.
Only extract real facts the USER shared about themselves (job, location, age, preferences, hobbies).
Do NOT extract the user's username or display name.
Do NOT extract facts about Luna's personality or behavior.
Do NOT repeat or rephrase anything listed under "Already known".
If nothing new and notable, reply with exactly: NOTHING
{already_known}
User ({username}): {message}
Luna: {response}

Reply with just the new user fact in one sentence, or NOTHING."""

            messages = [{"role": "user", "content": extract_prompt}]
            raw_fact = self._call_llm(messages, max_tokens=60, temperature=0.3)
            fact = raw_fact.strip() if raw_fact else ""
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

        luna_facts = [m for m in self.longterm if m.get("about") == "luna"]
        user_facts = [m for m in self.longterm if m.get("about") == username]
        other_facts = [m for m in self.longterm if m.get("about") not in ("luna", username)]

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

    # ── Core methods ─────────────────────────────────────────────

    def generate_response(self, message, platform, username):
        memory_injection = self._get_relevant_memories(username)
        mood = random.choice(self.moods)
        full_system = self.system_prompt + memory_injection + f"\ncurrent stream vibe: {mood}\n"

        self.memory.append({"role": "user", "content": f"{username} ({platform}): {message}"})
        self._save_short_term()

        try:
            raw_text = self._call_llm(self._build_messages(full_system), max_tokens=120, temperature=0.88)
            filtered = filter_response(raw_text.strip())

            self.memory.append({"role": "assistant", "content": filtered})
            self._save_short_term()

            threading.Thread(
                target=self._extract_memory,
                args=(username, message, filtered),
                daemon=True
            ).start()

            return filtered
        except Exception as e:
            print(f"AI generation error ({self.provider} / {self.model}): {e}")
            return None

    def generate_batch_response(self, batch):
        """React to a batch of messages as a group instead of one by one."""
        mood = random.choice(self.moods)
        memory_injection = self._get_relevant_memories(batch[0]['username'])
        full_system = self.system_prompt + memory_injection + f"\ncurrent stream vibe: {mood}\n"

        batch_text = "\n".join([f"{m['username']}: {m['message']}" for m in batch])
        prompt = f"multiple chatters said this at once:\n{batch_text}\nreact to chat as a group in 1-2 funny, energetic sentences."

        self.memory.append({"role": "user", "content": batch_text})
        self._save_short_term()

        try:
            raw_text = self._call_llm(
                self._build_messages(full_system) + [{"role": "user", "content": prompt}],
                max_tokens=140,
                temperature=0.88
            )
            filtered = filter_response(raw_text.strip())

            self.memory.append({"role": "assistant", "content": filtered})
            self._save_short_term()
            return filtered
        except Exception as e:
            print(f"AI batch error ({self.provider} / {self.model}): {e}")
            return None

    def continue_last_thought(self):
        """Continue or react to her own last message unprompted."""
        mood = random.choice(self.moods)
        full_system = self.system_prompt + f"\ncurrent stream vibe: {mood}\n"

        last = None
        for msg in reversed(list(self.memory)):
            if msg["role"] == "assistant":
                last = msg["content"]
                break

        if not last:
            return self.get_random_comment()

        prompt = f"you just said: \"{last}\"\nadd another funny thought or react to yourself in 1 short sentence."

        try:
            raw_text = self._call_llm(
                self._build_messages(full_system) + [{"role": "user", "content": prompt}],
                max_tokens=90,
                temperature=0.88
            )
            filtered = filter_response(raw_text.strip())
            self.memory.append({"role": "assistant", "content": filtered})
            self._save_short_term()
            return filtered
        except Exception as e:
            print(f"AI continue thought error: {e}")
            return None

    def get_random_comment(self):
        mood = random.choice(self.moods)
        messages = self._build_messages(self.system_prompt + f"\ncurrent stream vibe: {mood}\n")
        messages.append({"role": "user", "content": "say something spontaneous, funny, or unhinged to chat to break the silence in 1 short sentence."})

        try:
            raw_text = self._call_llm(messages, max_tokens=90, temperature=0.92)
            return filter_response(raw_text.strip())
        except Exception as e:
            print(f"AI random comment error: {e}")
            return None

    def add_manual_memory(self, fact, about="stream"):
        self.longterm.append({
            "fact": fact,
            "about": about,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        self._save_long_term()