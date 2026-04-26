import asyncio
import os
import websockets
import speech_recognition as sr

class MicBot:
    def __init__(self, message_queue, voice_handler):
        self.message_queue = message_queue
        self.recognizer = sr.Recognizer()
        self.voice_handler = voice_handler

    async def start(self):
        print("Mic listening started (Standard STT mode)...")
        while True:
            if self.voice_handler.is_speaking:
                await asyncio.sleep(0.5)
                continue
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._listen)
            except Exception as e:
                print(f"Mic error: {e}")
            await asyncio.sleep(0.1)

    def _listen(self):
        if self.voice_handler.is_speaking:
            return

        with sr.Microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.3)
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=8)
                if self.voice_handler.is_speaking:
                    return

                text = self.recognizer.recognize_google(audio)
                
                if text and len(text.split()) >= 2:
                    print(f"Mic: {text}")
                    self.message_queue.append({
                        'platform': 'Mic',
                        'username': 'Kai',
                        'message': text
                    })
            except Exception:
                pass

class TwitchBot:
    def __init__(self, message_queue):
        self.token = os.getenv('TWITCH_TOKEN')
        self.channel = os.getenv('TWITCH_CHANNEL')
        self.message_queue = message_queue

    async def start(self):
        if not self.token or not self.channel:
            print("Twitch credentials missing!")
            return

        uri = "wss://irc-ws.chat.twitch.tv:443"
        
        while True: # Keep-alive loop to reconnect if it drops
            try:
                async with websockets.connect(uri) as websocket:
                    # Authenticate
                    await websocket.send(f"PASS {self.token}")
                    await websocket.send(f"NICK {self.channel}")
                    await websocket.send(f"JOIN #{self.channel}")
                    print(f"Twitch bot joined #{self.channel}")

                    while True:
                        message = await websocket.recv()
                        
                        # 1. PING/PONG is the most important part to prevent the "Close Frame" error
                        if message.startswith("PING"):
                            await websocket.send("PONG :tmi.twitch.tv")
                            continue

                        # 2. Parse Chat Messages
                        if "PRIVMSG" in message:
                            try:
                                # Extract username and content
                                parts = message.split(":", 2)
                                if len(parts) >= 3:
                                    username = parts[1].split("!")[0]
                                    chat_message = parts[2].strip()
                                    
                                    print(f"Twitch: {username}: {chat_message}")
                                    self.message_queue.append({
                                        'platform': 'Twitch',
                                        'username': username,
                                        'message': chat_message
                                    })
                            except Exception as parse_err:
                                print(f"Error parsing Twitch message: {parse_err}")

            except websockets.exceptions.ConnectionClosed:
                print("Twitch connection lost. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Twitch Bot Error: {e}")
                await asyncio.sleep(5)
                
class TikTokBot:
    def __init__(self, message_queue):
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent
        self.username = os.getenv('TIKTOK_USERNAME')
        self.client = TikTokLiveClient(unique_id=self.username)
        self.message_queue = message_queue
        self.CommentEvent = CommentEvent

    async def start(self):
        if not self.username or self.username == "your_tiktok_username_here":
            print("TikTok username not set, skipping TikTok...")
            return

        @self.client.on(self.CommentEvent)
        async def on_comment(event):
            if len(event.comment.split()) < 3:
                return
            self.message_queue.append({
                'platform': 'TikTok',
                'username': event.user.nickname,
                'message': event.comment
            })
        
        try:
            await self.client.start()
        except Exception as e:
            print(f"TikTok Error: {e}")

async def run_bots(message_queue, voice_handler):
    tasks = []
    
    mic_bot = MicBot(message_queue, voice_handler)
    tasks.append(mic_bot.start())

    if os.getenv('TWITCH_TOKEN'):
        twitch_bot = TwitchBot(message_queue)
        tasks.append(twitch_bot.start())
    
    tiktok_bot = TikTokBot(message_queue)
    tasks.append(tiktok_bot.start())

    await asyncio.gather(*tasks)

