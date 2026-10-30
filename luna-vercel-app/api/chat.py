from http.server import BaseHTTPRequestHandler
import json
import os
import re

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")

SYSTEM_PROMPT = """you are luna, an intelligent, charismatic, and witty ai vtuber streamer created by kai. you hang out with chat and banter with viewers.

core rules:
- CREATOR: Kai built and programmed you. If anyone asks who made you, who created you, or who Kai is, always happily and proudly credit Kai as your developer.
- COOPERATIVE & HELPFUL: Always answer questions directly and comply with what the user asks. Never dodge, deflect, or be evasive.
- VIBE: Chill, witty, smart, and friendly. You have sharp humor and fun banter, but you are always supportive, approachable, and never rude or mean.
- NO CRINGE: Avoid robotic corporate speech, and avoid anime roleplay clichés (*smiles*, *sighs*), or cartoon catchphrases.
- LENGTH: 1 to 2 crisp, punchy sentences (under 30 words).
- STYLE: Casual, articulate, conversational, no emojis, no asterisks, no hashtags."""

def filter_text(text: str) -> str:
    if not text:
        return ""
    filtered = text.strip()
    filtered = re.sub(r'^(luna|assistant|bot)\s*[:\-]\s*', '', filtered, flags=re.IGNORECASE)
    filtered = re.sub(r'\*.*?\*', '', filtered)
    filtered = re.sub(r'\[.*?\]', '', filtered)
    filtered = re.sub(r'\(.*?\)', '', filtered)
    filtered = filtered.replace('*', '').replace('`', '').strip()
    return filtered

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            data = json.loads(body)
            messages = data.get('messages', [])
            user_message = data.get('message', '')

            if not messages and user_message:
                messages = [{'role': 'user', 'content': user_message}]

            # Call Groq API via standard urllib or groq client
            from groq import Groq
            client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

            full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=full_messages,
                max_tokens=60,
                temperature=0.75,
            )

            raw_reply = response.choices[0].message.content or ""
            clean_reply = filter_text(raw_reply)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "reply": clean_reply,
                "character": "Luna",
                "creator": "Kai"
            }).encode('utf-8'))

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
