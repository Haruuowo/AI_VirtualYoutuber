import os
import json
import boto3
from filter import filter_response

class AIHandler:
    def __init__(self):
        self.client = boto3.client(
            service_name='bedrock-runtime',
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        self.model = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        self.system_prompt = """You are ChaosBot, a chaotic, funny, and unhinged AI VTuber streaming live. You speak casually using internet slang and gen-z humor. Keep every response to 1-3 sentences max — you are live on stream so be snappy. Occasionally acknowledge whether the message came from Twitch or TikTok. You must never use slurs, hate speech, or explicit language."""

    def get_random_comment(self):
        """Generate a random comment to fill silence."""
        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "system": self.system_prompt,
                "messages": [{"role": "user", "content": "Generate a short random comment to fill silence on stream."}]
            })
            response = self.client.invoke_model(body=body, modelId=self.model)
            result = json.loads(response['body'].read())
            return filter_response(result['content'][0]['text'].strip())
        except Exception as e:
            print(f"Bedrock error: {e}")
            return None

    def generate_response(self, message, platform, username):
        """Generate AI response to a chat message."""
        prompt = f"Respond to this message from {username} on {platform}: '{message}'"
        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 150,
                "system": self.system_prompt,
                "messages": [{"role": "user", "content": prompt}]
            })
            response = self.client.invoke_model(body=body, modelId=self.model)
            result = json.loads(response['body'].read())
            return filter_response(result['content'][0]['text'].strip())
        except Exception as e:
            print(f"Bedrock error: {e}")
            return None