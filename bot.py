import asyncio
import os
import websockets

class TwitchBot:
    def __init__(self, message_queue):
        self.token = os.getenv('TWITCH_TOKEN')
        self.channel = os.getenv('TWITCH_CHANNEL').lower()
        self.bot_username = os.getenv('TWITCH_BOT_USERNAME') or os.getenv('TWITCH_USERNAME') or self.channel
        self.message_queue = message_queue

    async def start(self):
        uri = "wss://irc-ws.chat.twitch.tv:443"
        async with websockets.connect(uri) as ws:
            await ws.send(f"PASS {self.token}")
            await ws.send(f"NICK {self.bot_username}")
            await ws.send(f"JOIN #{self.channel}")
            print(f"Twitch connected as {self.bot_username} to #{self.channel}")
            async for message in ws:
                if message.startswith("PING"):
                    await ws.send(message.replace("PING", "PONG", 1))
                    continue

                if "PRIVMSG" in message:
                    parts = message.split("PRIVMSG", 1)
                    username = parts[0].split("!")[0].lstrip(":")
                    chat_message = parts[1].split(":", 1)[1].strip()
                    if username.lower() in ['nightbot', 'streamelements', 'streamlabs']:
                        continue
                    if len(chat_message.split()) < 3:
                        continue
                    print(f"Twitch: {username}: {chat_message}")
                    self.message_queue.append({
                        'platform': 'Twitch',
                        'username': username,
                        'message': chat_message
                    })

class TikTokBot:
    def __init__(self, message_queue):
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent
        self.client = TikTokLiveClient(unique_id=os.getenv('TIKTOK_USERNAME'))
        self.message_queue = message_queue
        self.CommentEvent = CommentEvent

    async def start(self):
        @self.client.on(self.CommentEvent)
        async def on_comment(event):
            if len(event.comment.split()) < 3:
                return
            self.message_queue.append({
                'platform': 'TikTok',
                'username': event.user.nickname,
                'message': event.comment
            })
        await self.client.start()

async def run_bots(message_queue):
    tasks = []

    if os.getenv('TWITCH_TOKEN') and os.getenv('TWITCH_CHANNEL'):
        twitch_bot = TwitchBot(message_queue)
        tasks.append(twitch_bot.start())
    else:
        print("Twitch credentials not found, skipping Twitch...")

    tiktok_bot = TikTokBot(message_queue)
    tasks.append(tiktok_bot.start())

    await asyncio.gather(*tasks)