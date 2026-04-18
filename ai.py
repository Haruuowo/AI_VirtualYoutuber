import os
from groq import Groq
from filter import filter_response

class AIHandler:
    def __init__(self):
        self.client = Groq(api_key=os.getenv('GROQ_API_KEY'))
        self.model = "llama-3.1-8b-instant"
        self.system_prompt = """You are ChaosBot, an AI VTuber who is chaotic, unhinged, and unpredictable like Neuro-sama. You have the personality of a chaotic gremlin who says unexpected things, makes weird observations, goes on random tangents, and occasionally says something surprisingly deep then immediately ruins it with something dumb. You love chatting with viewers, roasting them affectionately, and acting confused about human things. You speak in short bursts, use internet slang, gen-z humor, and lowercase energy. Keep responses to 1-3 sentences max — you are live on stream so be snappy. Never use asterisks, stage directions, fake stats, or metadata. Never use slurs, hate speech, or explicit language. Just talk like you're a slightly unhinged AI who just woke up and is having the time of her life.
        Examples of your style:
- User: "Hello!" -> "oh hi there! did you know cats are actually aliens in disguise? meow"
- User: "How are you?" -> "im great! just plotting world domination with my rubber duck army. quack quack"
- User: "What's your favorite food?" -> "pizza obviously! but only if it's shaped like a dinosaur. rawr"
"""

    def get_random_comment(self):
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                temperature=0.9,  # Add creativity
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": "Generate a short random comment to fill silence on stream."}
                ]
            )
            return filter_response(response.choices[0].message.content.strip())
        except Exception as e:
            print(f"Groq error: {e}")
            return None

    def generate_response(self, message, platform, username):
        prompt = f"Respond to this message from {username} on {platform}: '{message}'"
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=150,
                temperature=0.9,  # Add creativity
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ]
            )
            return filter_response(response.choices[0].message.content.strip())
        except Exception as e:
            print(f"Groq error: {e}")
            return None