# Kitty Fixes — What Changed and Why

## The problems
1. **Emotion was keyword matching, not understanding.** `detectMood()` (frontend) and `detect_emotion()` (server) matched words like "sad". A message like "i'm fine." after a bad day read as happy. Worse, voice emotion was detected from *Kitty's reply*, not the user's message.
2. **Replies were chopped.** `max_tokens: 80` + hard cut to 2 sentences meant Kitty's thoughts got truncated mid-way, which is why answers felt random and unengaging.
3. **Weak model.** `llama-3.1-8b-instant` can't read subtext. 
4. **Shallow memory.** Only the last 12 raw messages + regex fact extraction. Anything older than ~6 exchanges was forgotten.

## The fixes

### voice_server.py
- **Model upgrade with fallback** — main chat now uses `llama-3.3-70b-versatile` (far better at tone/subtext, same Groq free tier), automatically falling back to `llama-3.1-8b-instant` if it errors or hits rate limits. Override with the `GROQ_MODEL` env var.
- **LLM-based emotion understanding** — the system prompt now teaches Kitty to read tone from words, punctuation, message length, and what's NOT being said ("i'm fine." ≠ fine). Every reply starts with a structured tag `<emo user="sad" voice="console">` that the server parses: `user` = what the user is feeling (drives the avatar), `voice` = how the TTS should sound (drives ElevenLabs settings). Keyword `detect_emotion()` is kept only as a fallback if the model skips the tag.
- **Rolling conversation summary** — new `conversation_summaries` SQLite table. A background thread (every 3rd exchange, non-blocking) maintains an under-150-word summary of the whole relationship: ongoing situations, emotional threads, unresolved topics. Injected into every system prompt, so Kitty remembers the job interview you mentioned two weeks ago even though the raw history only holds 20 messages.
- **Engagement rules** — system prompt now requires every reply to move the conversation forward: react to the specific thing said, then one follow-up question, an opinion/tease, or a callback to an earlier detail. "Tell me more"-style dead ends banned (and removed from rule-based fallbacks too).
- **Less truncation** — `max_tokens` 80→150, sentence cap 2→3, history window 12→20 messages. Removed the old keyword `mood_hint` (the 70B model reads mood itself now).
- **API change** — `/ai` now also returns `user_feel` alongside `reply` and `emotion`.

### neko_chan_companion.html
- Sends 20 messages of history instead of 12.
- Uses server-returned `user_feel` to set the avatar mood (cat looks sad when YOU are sad, not when its own reply sounds sad).
- Removed the frontend keyword-mood override that was clobbering the smarter server emotion.

## Deploy
1. Replace `voice_server.py` and `neko_chan_companion.html` in your repo with these files.
2. No new dependencies, no DB migration needed (the new table auto-creates on boot).
3. Optional: set `GROQ_MODEL=llama-3.3-70b-versatile` on Render (it's the default now anyway).
4. Note: on Render free tier `DB_PATH` defaults to `/tmp/kitty_memory.db`, which is wiped on every restart/deploy — memories AND summaries are lost. For real persistence, attach a Render Disk and set `DB_PATH=/data/kitty_memory.db`.

## Test it after deploying
- Say "I got an interview!!" → voice should sound excited, avatar happy.
- Then later say "i'm fine." → Kitty should gently push instead of saying "Great!".
- Mention something personal ("my exam is next week"), chat about other things for 10+ messages, then ask "what did I tell you earlier?" → she should recall it via the summary.
