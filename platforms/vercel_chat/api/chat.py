from http.server import BaseHTTPRequestHandler
import json
import os
import re

DEFAULT_PROMPT = """you are luna, an intelligent, charismatic, and witty ai companion created by kai.
you talk with users, banter, and keep the vibe fun, supportive, and clever.
avoid robotic speech and anime roleplay cliches. keep responses punchy and conversational."""

def filter_text(text: str) -> str:
    if not text:
        return ""
    filtered = text.strip()
    filtered = re.sub(r'^(luna|assistant|bot)\s*[:\-]\s*', '', filtered, flags=re.IGNORECASE)
    filtered = re.sub(r'\*.*?\*', '', filtered)
    filtered = re.sub(r'\[.*?\]', '', filtered)
    filtered = re.sub(r'\(.*?\)', '', filtered)
    return filtered.replace('*', '').replace('`', '').strip()

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        try:
            data = json.loads(body)
            messages = data.get('messages', [])
            user_message = data.get('message', '')
            
            # Character.ai style dynamic customization
            char_name = data.get('name', 'Luna').strip() or 'Luna'
            custom_prompt = data.get('system_prompt', '').strip() or DEFAULT_PROMPT
            temperature = float(data.get('temperature', 0.8))

            if not messages and user_message:
                messages = [{'role': 'user', 'content': user_message}]

            system_instruction = f"Your name is {char_name}.\n{custom_prompt}"
            full_messages = [{"role": "system", "content": system_instruction}] + messages

            from groq import Groq
            client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            model = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")

            response = client.chat.completions.create(
                model=model,
                messages=full_messages,
                max_tokens=90,
                temperature=temperature,
            )

            raw_reply = response.choices[0].message.content or ""
            clean_reply = filter_text(raw_reply)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "reply": clean_reply,
                "character": char_name
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
