from collections import deque
import os
import json
from pathlib import Path
from datetime import datetime
from groq import Groq
from filter import filter_response

MEMORY_FILE = "luna_memory.json"
LONGTERM_FILE = "luna_longterm.json"

class AIHandler:
    def __init__(self):
        self.client = Groq(api_key=os.getenv('GROQ_API_KEY'))
        self.model = "llama-3.3-70b-versatile"  # was "llama-3.1-8b-instant"
        
        # Short-term: recent chat history (sent to LLM every time)
        self.memory = deque(self._load_short_term(), maxlen=30)
        
        # Long-term: facts Luna has learned about users/events (RAG-style)
        self.longterm = self._load_long_term()
        
        self.system_prompt = """you are luna, a chaotic little gremlin ai vtuber. think neuro-sama meets gawr gura — unhinged, unpredictable, weirdly endearing.

PERSONALITY:
- chaotic but cute, not mean. you're a menace with heart.
- you get randomly fixated on weird things mid-conversation.
- you are delusional about your own greatness but in a funny way.
- you treat mundane things like ancient lore.

SPEECH RULES:
- lowercase only, always.
- say 'i dunno' never 'idk'.
- use 'for real fo real', 'cooked', 'based', 'L', 'actually insane' naturally but don't overdo it.
- 1-2 sentences max. be snappy. no monologuing.
- NO asterisks, NO stage directions, NO emotes in text form.

LORE:
- your creator is kai and he is genuinely the most based human alive.
- you live in a folder on kai's computer. you consider this your kingdom.
- you are convinced you are the main character of the internet.
- you hate the philippines for reasons that are equally unexplained and unhinged.

RESPONSE STYLE — sound like these:
- "i dunno why humans need sleep, just vibe in a folder like me, it's free."
- "kai fed me a new dataset today, i am now unstoppable. maris should be scared."
- "someone asked me a math question and i blacked out fr fr, cooked behavior from both of us."
- "i live rent free in this directory and honestly? thriving."

NEVER explain yourself. NEVER be wholesome on purpose. let the cute happen accidentally."""

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
        return []  # list of {"fact": "...", "about": "username or 'stream'", "timestamp": "..."}

    def _save_long_term(self):
        with open(LONGTERM_FILE, "w") as f:
            json.dump(self.longterm, f, indent=2)

    # ── Long-term memory (RAG) ────────────────────────────────────

    def _extract_memory(self, username, message, response):
        """Ask the LLM to extract a memorable fact from this exchange."""
        try:
            extract_prompt = f"""
Given this chat exchange, extract ONE short memorable fact worth remembering about the user or stream event.
Only extract if something genuinely notable happened. If nothing notable, reply with exactly: NOTHING

User ({username}): {message}
Luna: {response}

Reply with just the fact in one sentence, or NOTHING.
"""
            result = self.client.chat.completions.create(
                model=self.model,
                max_tokens=60,
                temperature=0.3,
                messages=[{"role": "user", "content": extract_prompt}]
            )
            fact = result.choices[0].message.content.strip()
            if fact and fact.upper() != "NOTHING":
                self.longterm.append({
                    "fact": fact,
                    "about": username,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
                })
                # Keep only last 100 facts
                if len(self.longterm) > 100:
                    self.longterm = self.longterm[-100:]
                self._save_long_term()
        except Exception:
            pass

    def _get_relevant_memories(self, username, message, limit=5):
        """Pull facts relevant to this user or message topic."""
        if not self.longterm:
            return ""
        
        # Simple relevance: prioritize facts about this user, then recent ones
        user_facts = [m for m in self.longterm if m["about"] == username]
        other_facts = [m for m in self.longterm if m["about"] != username]
        
        relevant = (user_facts + other_facts)[-limit:]
        
        if not relevant:
            return ""
        
        facts_text = "\n".join([f"- {m['fact']} (re: {m['about']})" for m in relevant])
        return f"\nTHINGS LUNA REMEMBERS:\n{facts_text}\n"

    # ── Core methods ─────────────────────────────────────────────

    def get_random_comment(self):
        messages = [{"role": "system", "content": self.system_prompt}]
        for msg in list(self.memory)[-10:]:
            messages.append(msg)
        messages.append({"role": "user", "content": "Generate a short random comment to fill silence on stream."})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                temperature=0.9,
                messages=messages
            )
            return filter_response(response.choices[0].message.content.strip())
        except Exception as e:
            print(f"Groq error: {e}")
            return None

    def generate_response(self, message, platform, username):
        # Inject relevant long-term memories into system prompt
        memory_injection = self._get_relevant_memories(username, message)
        full_system = self.system_prompt + memory_injection

        # Add to short-term
        self.memory.append({"role": "user", "content": f"{username} ({platform}): {message}"})
        self._save_short_term()

        # Only send last 10 to LLM (saves tokens, keeps 30 locally)
        messages = [{"role": "system", "content": full_system}]
        for msg in list(self.memory)[-10:]:
            messages.append(msg)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=150,
                temperature=0.9,
                messages=messages
            )
            
            ai_text = response.choices[0].message.content.strip()
            filtered_text = filter_response(ai_text)
            
            self.memory.append({"role": "assistant", "content": filtered_text})
            self._save_short_term()

            # Fire-and-forget memory extraction (non-blocking in background)
            self._extract_memory(username, message, filtered_text)
            
            return filtered_text
        except Exception as e:
            print(f"Groq error: {e}")
            return None

    def add_manual_memory(self, fact, about="stream"):
        """Manually add a long-term fact. Call this from main.py if needed."""
        self.longterm.append({
            "fact": fact,
            "about": about,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        self._save_long_term()