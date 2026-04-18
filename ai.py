import os
from anthropic import Anthropic

class AIHandler:
    def __init__(self):
        self.client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.model = "claude-3-5-sonnet-20241022"  # Updated to latest available
        self.system_prompt = """You are ChaosBot, a chaotic, funny, and unhinged AI VTuber streaming live. You speak casually using internet slang and gen-z humor. Keep every response to 1-3 sentences max — you are live on stream so be snappy. Occasionally acknowledge whether the message came from Twitch or TikTok. Never break character. Never say you are an AI."""

    def get_random_comment(self):
        """Generate a random comment to fill silence."""
        prompts = [
            "Yo what's up chat, feeling lonely over here!",
            "Anyone got any wild stories? Spill the tea!",
            "This stream is getting too quiet, let's turn up the chaos!",
            "Bored? Me too, hit me with your best memes!",
            "TikTok or Twitch, doesn't matter, entertain me!"
        ]
        return self.client.messages.create(
            model=self.model,
            max_tokens=100,
            system=self.system_prompt,
            messages=[{"role": "user", "content": f"Generate a short random comment in character to fill silence. Choose from or similar to: {prompts}"}]
        ).content[0].text.strip()

    def generate_response(self, message, platform, username):
        """Generate AI response to a chat message."""
        prompt = f"Respond to this message from {username} on {platform}: '{message}'"
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=150,
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            print(f"Claude API error: {e}")
            return None