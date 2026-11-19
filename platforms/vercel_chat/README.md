# Luna AI — Vercel Serverless Web Chatbot

A standalone web chatbot deployment of **Luna AI**, ready to deploy on **Vercel** with zero backend infrastructure.

---

## What's in this folder:
- **`api/chat.py`**: Vercel Python Serverless Function running Luna's personality engine, sanitization filters, and Groq LLM inference.
- **`public/index.html`**: Clean, lightweight web interface styled in Luna's retro PictoChat dark mode with mobile responsiveness.
- **`vercel.json`**: Automatic route rewrites for static hosting + serverless API endpoints.
- **`requirements.txt`**: Python dependencies for Vercel build (`groq`).

---

## How to Deploy to Vercel (2 Minutes):

### Method 1: Using the Vercel CLI
1. Open your terminal in this folder:
   ```bash
   cd luna-vercel-app
   ```
2. Run Vercel:
   ```bash
   vercel
   ```
3. Add your Groq API key when prompted, or in the Vercel Dashboard under **Project Settings &rarr; Environment Variables**:
   - `GROQ_API_KEY`: Your Groq API key from [console.groq.com](https://console.groq.com)
   - `GROQ_MODEL`: `qwen/qwen3.8-27b` (default)

### Method 2: Git Repository / GitHub
1. You can push this folder to its own GitHub repository or connect your existing repo to Vercel with the **Root Directory** set to `luna-vercel-app`.
2. Add `GROQ_API_KEY` in your Vercel Project Environment Variables.
3. Deploy!

