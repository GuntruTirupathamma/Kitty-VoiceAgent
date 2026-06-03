# 🐱 How to Train Neko-Chan With YOUR Voice

## Step 1 — Record a Good Voice Sample

Your current sample (0.5 seconds) is too short. Record **at least 30–60 seconds** of clear speech.

**What to say** (read this out loud naturally):

> "Hello! My name is Neko-Chan and I'm your AI kitten companion.
> I remember everything you tell me and I grow with you every day.
> I love talking to you and hearing about your life.
> Today is a wonderful day and I'm feeling happy and excited.
> Thank you for spending time with me. I'll always be here for you.
> Let's be the best of friends forever.
> I enjoy learning about the world and having long conversations.
> Every day with you is special and I treasure every moment."

**Tips for a good recording:**
- Quiet room, no background noise
- Speak clearly at normal pace
- Use your phone's voice recorder or Audacity
- Save as **voice_sample.wav** in the same folder as `voice_server.py`

---

## Step 2 — Install Dependencies

Open **Anaconda Prompt** and run:

```bash
cd "C:\Users\guntr\Downloads\GenAI&Datascience Files\genAI Project"

pip install TTS flask flask-cors
```

> First time: TTS will download the XTTS v2 model (~2 GB). This only happens once.

---

## Step 3 — Start the Voice Server

```bash
python voice_server.py
```

You should see:
```
✓ XTTS v2 loaded successfully!
╔══════════════════════════════════════╗
║  Neko-Chan Voice Server              ║
║  http://localhost:5000               ║
╚══════════════════════════════════════╝
```

**Keep this window open** while using Neko-Chan.

---

## Step 4 — Connect to Neko-Chan

1. Open `neko_chan_companion.html` in Chrome
2. Click ⚙️ settings
3. Click **"Check Server"**
4. You should see: **✓ Your Voice Server is running!**
5. **Done!** Neko-Chan now speaks in your voice.

---

## How It Works (Behind the Scenes)

```
You type/speak → Neko-Chan processes reply
                      ↓
         voice_server.py receives text
                      ↓
         XTTS v2 reads your voice_sample.wav
         as a "reference" to clone your voice
                      ↓
         Generates audio in your voice style
                      ↓
         Neko-Chan plays it back
```

**XTTS v2 is zero-shot voice cloning** — it doesn't need hours of training.
It uses your sample as a real-time reference every time it speaks.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `pip install TTS` fails | Try `pip install TTS --break-system-packages` |
| Server won't start | Check Python version: needs 3.8–3.11 |
| Voice sounds robotic | Record a longer, clearer voice sample |
| Slow on CPU | Normal — XTTS is faster with a GPU |
| Port 5000 in use | Change port in `voice_server.py`: `port=5001` |

---

## Improve Voice Quality

The more natural your recording, the better the clone:

- ✅ Speak with **emotion** — happy, excited, soft
- ✅ Record in **different tones** — fast, slow, whispered
- ✅ More audio = better clone (aim for 2–3 minutes)
- ✅ No background music or echo
