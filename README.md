# AI VTuber Chatbot

A chaotic, funny AI VTuber that responds to live chat on Twitch and TikTok simultaneously.

**Repository:** [https://github.com/Haruuowo/AI_VirtualYoutuber](https://github.com/Haruuowo/AI_VirtualYoutuber)

## Setup

1. **Clone or download this repository.**
   - If using Git: `git clone https://github.com/Haruuowo/AI_VirtualYoutuber.git`

2. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

3. **Get API Keys:**

   - **Twitch:**
     - Go to [Twitch Developer Console](https://dev.twitch.tv/console/apps)
     - Create an app to get Client ID
     - Get OAuth token: Use [Twitch Token Generator](https://twitchtokengenerator.com/) or similar

   - **TikTok Live:**
     - Username: Your TikTok username
     - Session ID: Use browser dev tools to get sessionid from TikTok cookies

   - **Anthropic Claude:**
     - Sign up at [Anthropic](https://www.anthropic.com/)
     - Get API key from dashboard

   - **ElevenLabs:**
     - Sign up at [ElevenLabs](https://elevenlabs.io/)
     - Get API key and choose a voice ID

4. **Virtual Audio Cable (for VTube Studio lip sync):**
   - Install [VB-Audio Virtual Cable](https://www.vb-audio.com/Cable/)
   - Set AUDIO_DEVICE in .env to the virtual cable name (e.g., "CABLE Input")

5. **Configure environment:**
   - Copy `.env.example` to `.env`
   - Fill in all the API keys and settings

6. **Run:**
   ```
   python main.py
   ```

## Features

- Listens to Twitch and TikTok chat simultaneously
- AI responds every 10 seconds to the latest message
- TTS audio playback through specified device
- Ignores bots and short messages
- Fills silence with random comments