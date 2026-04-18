import asyncio
import os
from twitchio.ext import commands
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, CommentEvent

class TwitchBot(commands.Bot):
    def __init__(self, message_queue):
        super().__init__(
            token=os.getenv('TWITCH_TOKEN'),
            client_id=os.getenv('TWITCH_CLIENT_ID'),
            nick=os.getenv('TWITCH_CHANNEL'),
            prefix='!',
            initial_channels=[os.getenv('TWITCH_CHANNEL')]
        )
        self.message_queue = message_queue

    async def event_message(self, message):
        if message.echo:
            return
        if message.author.name.lower() in ['nightbot', 'streamelements', 'streamlabs']:
            return
        if len(message.content.split()) < 3:
            return
        self.message_queue.append({
            'platform': 'Twitch',
            'username': message.author.name,
            'message': message.content
        })

class TikTokBot:
    def __init__(self, message_queue):
        self.client = TikTokLiveClient(unique_id=os.getenv('TIKTOK_USERNAME'))
        self.message_queue = message_queue

    async def start(self):
        @self.client.on(CommentEvent)
        async def on_comment(event: CommentEvent):
            if len(event.comment.split()) < 3:
                return
            self.message_queue.append({
                'platform': 'TikTok',
                'username': event.user.nickname,
                'message': event.comment
            })
        await self.client.start()

async def run_bots(message_queue):
    twitch_bot = TwitchBot(message_queue)
    tiktok_bot = TikTokBot(message_queue)
    await asyncio.gather(
        twitch_bot.start(),
        tiktok_bot.start()
    )