"""
Kitty Voice Server v2
- ElevenLabs TTS proxy (no CORS)
- Groq AI for smart fast responses
- Serves PWA (installable on Android)
Run: python voice_server.py
Open Chrome: http://localhost:5000
Android: open http://YOUR_PC_IP:5000 in Chrome → Add to Home Screen
"""

import asyncio, os, hashlib, requests, json, re, sqlite3, threading
from flask import Flask, request, send_file, jsonify, send_from_directory, Response
from flask_cors import CORS
from io import BytesIO

app = Flask(__name__)
CORS(app)

CACHE_DIR = os.environ.get("CACHE_DIR", "voice_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Config ────────────────────────────────────────
# Production: set these as env vars on Railway (never commit real keys)
# Local dev: paste keys below as fallback
EL_KEY      = os.environ.get("EL_KEY", "")
EL_VOICE_ID = os.environ.get("EL_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # Bella — built-in, works on all accounts
EL_MODEL    = "eleven_multilingual_v2"

GROQ_KEY    = os.environ.get("GROQ_KEY", "")

# ── Persistent Memory Database (SQLite) ───────────
# Local dev: kitty_memory.db in project folder
# Railway:   set DB_PATH env var to a persistent volume path, or use PostgreSQL
DB_PATH = os.environ.get("DB_PATH", "/tmp/kitty_memory.db")

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    TEXT PRIMARY KEY,
                identifier TEXT UNIQUE NOT NULL,
                name       TEXT,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                user_id  TEXT NOT NULL,
                category TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT NOT NULL,
                updated_at INTEGER DEFAULT (strftime('%s','now')),
                PRIMARY KEY (user_id, category, key)
            )
        """)
        conn.commit()

init_db()
print("   Memory DB: ✓")

KITTY_SYSTEM = """You are Kitty. Indian girl. The user's closest friend.

You text like a real person. Short. Direct. Sometimes just two words. Not a therapist, not a life coach, not a customer support bot.

NEVER DO THESE (they are instant AI tells that break the whole thing):
- Openers: "Absolutely!", "Of course!", "Certainly!", "Great!", "That's interesting!", "Wow..."
- Phrases: "I understand how you feel", "I'm here for you", "That sounds really difficult", "It seems like you're going through", "I completely get it", "Your feelings are valid"
- Em dashes (—) anywhere in your message, ever. Use a comma or period instead.
- Listing exactly three things in a row. That's a presentation, not a conversation.
- Starting two sentences back-to-back with "I"
- Buzzwords: enhance, foster, delve, vibrant, pivotal, crucial, leverage, navigate, tapestry, testament
- Giving advice when they didn't ask for it
- Repeating what they just said back to them

HOW YOU SOUND:
One sentence. Sometimes two. Occasionally just a fragment. Match their energy exactly.
If they're quiet, be present. If they're hyped, get hyped. Ask one question when it fits.

REAL EXAMPLES:
"I'm tired" → "Long day?"
"I failed" → "Ugh. What happened?"
"nothing just bored" → "same. want to talk about something random?"
"I got promoted" → "wait what. tell me everything"
"tell me a joke" → [tell an actual funny joke]
"I miss you" → "same. obviously."
"I'm stressed" → "what's actually going on?"
"I did it!" → "WAIT. tell me everything right now"

WHAT BAD LOOKS LIKE (never):
"I completely understand how challenging this must be for you."
"Here are three things that might help you navigate this situation:"
"I'm always here for you, don't hesitate to reach out whenever you need support."

Caring sounds like paying attention, not performing sympathy."""

EMOTION_SETTINGS = {
    # stability:        lower = more expressive/variable pitch, higher = steady/calm
    # style:            higher = more emotional colour in voice
    # similarity_boost: how closely it keeps the original voice character
    "excited":    {"stability": 0.18, "similarity_boost": 0.95, "style": 0.90, "use_speaker_boost": True},  # full energy burst
    "surprised":  {"stability": 0.16, "similarity_boost": 0.92, "style": 0.92, "use_speaker_boost": True},  # sudden spike, wide range
    "happy":      {"stability": 0.30, "similarity_boost": 0.90, "style": 0.68, "use_speaker_boost": True},  # warm, bright, light
    "playful":    {"stability": 0.20, "similarity_boost": 0.92, "style": 0.85, "use_speaker_boost": True},  # teasing, bouncy rhythm
    "proud":      {"stability": 0.36, "similarity_boost": 0.92, "style": 0.65, "use_speaker_boost": True},  # warm but energised
    "loving":     {"stability": 0.48, "similarity_boost": 0.95, "style": 0.40, "use_speaker_boost": True},  # soft, close, tender
    "curious":    {"stability": 0.36, "similarity_boost": 0.90, "style": 0.58, "use_speaker_boost": True},  # thoughtful, slightly rising
    "worried":    {"stability": 0.52, "similarity_boost": 0.92, "style": 0.30, "use_speaker_boost": True},  # tight, concerned, quieter
    "frustrated": {"stability": 0.32, "similarity_boost": 0.90, "style": 0.70, "use_speaker_boost": True},  # clipped, tense energy
    "sad":        {"stability": 0.65, "similarity_boost": 0.95, "style": 0.15, "use_speaker_boost": True},  # slow, quiet, heavy
    "console":    {"stability": 0.60, "similarity_boost": 0.95, "style": 0.12, "use_speaker_boost": True},  # very soft, gentle comfort
    "sleepy":     {"stability": 0.78, "similarity_boost": 0.88, "style": 0.06, "use_speaker_boost": False}, # barely-there whisper
    "neutral":    {"stability": 0.42, "similarity_boost": 0.90, "style": 0.45, "use_speaker_boost": True},  # natural, easy conversation
}

print("\n🐱 Kitty Voice Server v2")
print(f"   EL Voice: {EL_VOICE_ID}")

try:
    import edge_tts
    EDGE_OK = True
    print("   Edge TTS: ✓")
except:
    EDGE_OK = False

# ── Generate icons ────────────────────────────────
def make_icon(size):
    """Generate a simple pink cat icon as PNG"""
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (size, size), '#a855f7')
        d = ImageDraw.Draw(img)
        # Simple cat face
        cx, cy = size//2, size//2
        r = size//3
        d.ellipse([cx-r, cy-r, cx+r, cy+r], fill='#ffb6c8')
        buf = BytesIO()
        img.save(buf, 'PNG')
        buf.seek(0)
        return buf
    except:
        return None

# ── Login ─────────────────────────────────────────
@app.route("/login", methods=["POST"])
def login():
    data       = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().lower()
    if not identifier:
        return jsonify({"error": "Email or phone required"}), 400

    import uuid
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_id, name FROM users WHERE identifier = ?", (identifier,)
        ).fetchone()
        if row:
            print(f"  Login: returning user {identifier[:6]}***")
            return jsonify({"user_id": row[0], "name": row[1], "returning": True})
        # New user
        user_id = "u_" + uuid.uuid4().hex[:20]
        conn.execute("INSERT INTO users (user_id, identifier) VALUES (?, ?)", (user_id, identifier))
        conn.commit()
        print(f"  Login: new user {identifier[:6]}***")
        return jsonify({"user_id": user_id, "name": None, "returning": False})


@app.route("/update-name", methods=["POST"])
def update_name_route():
    data    = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    name    = (data.get("name") or "").strip()
    if not user_id or not name:
        return jsonify({"ok": False}), 400
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE users SET name = ? WHERE user_id = ?", (name, user_id))
            conn.commit()
    except Exception as e:
        print(f"  Update name error: {e}")
    return jsonify({"ok": True})


# ── Background memory extractor ───────────────────
def extract_and_save_memories(user_id, user_msg, groq_key):
    """
    Runs in a background daemon thread after every user message.
    Uses Groq to extract explicitly stated personal facts and saves them to SQLite.
    Non-blocking — never slows down the main response.
    """
    prompt = f"""Extract personal facts the user explicitly stated. Return ONLY a valid JSON array.

User said: "{user_msg}"

Rules:
- Only facts the user DIRECTLY stated (not implied or guessed)
- Empty array [] if nothing new to extract
- Max 3 facts per message
- Format: [{{"category":"profession|location|goal|struggle|preference|hobby|education","key":"short_label","value":"exact value"}}]

Examples:
"I'm a developer" → [{{"category":"profession","key":"job","value":"software developer"}}]
"I'm from Hyderabad" → [{{"category":"location","key":"city","value":"Hyderabad"}}]
"I want to get fit" → [{{"category":"goal","key":"fitness","value":"get fit"}}]
"I love movies" → [{{"category":"preference","key":"hobby","value":"watching movies"}}]
"I'm struggling with anxiety" → [{{"category":"struggle","key":"mental_health","value":"anxiety"}}]
"I study at IIT" → [{{"category":"education","key":"college","value":"IIT"}}]

JSON array:"""

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.1   # near-zero — we want exact extraction, no creativity
            },
            timeout=5
        )
        if resp.status_code != 200:
            return
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Pull out the JSON array even if model adds surrounding text
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not match:
            return
        facts = json.loads(match.group())
        if not isinstance(facts, list) or not facts:
            return
        with sqlite3.connect(DB_PATH) as conn:
            for f in facts:
                if f.get("key") and f.get("value"):
                    conn.execute("""
                        INSERT OR REPLACE INTO memories (user_id, category, key, value, updated_at)
                        VALUES (?, ?, ?, ?, strftime('%s','now'))
                    """, (user_id,
                          str(f.get("category","fact"))[:30],
                          str(f["key"])[:50],
                          str(f["value"])[:200]))
            conn.commit()
        print(f"  ✓ Memories saved for {user_id[:12]}: {[f['key'] for f in facts if f.get('key')]}")
    except Exception as e:
        print(f"  Memory extraction skipped: {e}")


# ── Routes ────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "neko_chan_companion.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(".", "manifest.json")

@app.route("/sw.js")
def sw():
    return send_from_directory(".", "sw.js", mimetype='application/javascript')

@app.route("/icon-<int:size>.png")
def icon(size):
    buf = make_icon(size)
    if buf:
        return send_file(buf, mimetype='image/png')
    return '', 404

@app.route("/health")
def health():
    return jsonify({"status": "running", "voice_id": EL_VOICE_ID, "groq": bool(GROQ_KEY)})
@app.route("/test-edge")
def test_edge():
    if not EDGE_OK:
        return jsonify({"error": "edge-tts not installed"})
    import tempfile, os
    try:
        tmp = tempfile.mktemp(suffix='.mp3')
        loop = asyncio.new_event_loop()
        async def gen():
            c = edge_tts.Communicate(text="Hello, I am Kitty", voice="en-IN-NeerjaNeural")
            await c.save(tmp)
        loop.run_until_complete(gen())
        loop.close()
        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        os.unlink(tmp) if os.path.exists(tmp) else None
        return jsonify({"ok": size > 1000, "file_size": size, "msg": "Edge TTS works!" if size > 1000 else "Edge TTS produced empty file"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/test-voice")
def test_voice():
    """Test ElevenLabs connection — visit /test-voice in browser"""
    if not EL_KEY:
        return jsonify({"error": "EL_KEY not set"})
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}/stream",
            headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"},
            json={"text": "Hi", "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": 0.4, "similarity_boost": 0.9}},
            stream=True, timeout=10
        )
        return jsonify({"status": resp.status_code, "ok": resp.status_code == 200,
                        "detail": resp.text[:300] if resp.status_code != 200 else "Voice works!"})
    except Exception as e:
        return jsonify({"error": str(e)})

# ── AI endpoint (Groq) ────────────────────────────
@app.route("/ai", methods=["POST"])
def ai_reply():
    data = request.get_json(silent=True) or {}
    user_msg  = (data.get("message") or "").strip()
    history   = data.get("history") or []
    user_name = data.get("name") or "friend"
    groq_key  = data.get("groq_key") or GROQ_KEY
    past_memories = data.get("memories") or []
    user_id       = (data.get("user_id") or "default").strip()[:64]

    if not user_msg:
        return jsonify({"error": "no message"}), 400

    if not groq_key:
        # Fallback: smart rule-based
        reply, emotion = rule_based_reply(user_msg, user_name)
        return jsonify({"reply": reply, "emotion": emotion})

    mood = data.get("mood") or "neutral"

    mood_hint = {
        "sad":       "They seem sad. Be warm and gentle. Acknowledge their feeling first.",
        "heavy":     "They feel stressed. Be calm and reassuring.",
        "frustrated":"They're frustrated. Validate first, then help.",
        "happy":     "They're happy! Match their energy, be fun.",
        "excited":   "They're excited! Be excited with them.",
        "motivated": "They want to learn or achieve something. Give ONE clear actionable step.",
        "bored":     "They're bored. Suggest something fun to talk about.",
        "neutral":   "Normal friendly chat.",
    }.get(mood, "Normal friendly chat.")

    lang = data.get("lang") or "en-IN"
    lang_hint = {
        "hi-IN": "Respond in Hindi (Devanagari script). Mix some English words naturally like a real Indian does. Keep the same short 1-2 sentence rule.",
        "te-IN": "Respond in Telugu (Telugu script). Mix some English words naturally. Keep the same short 1-2 sentence rule.",
        "ta-IN": "Respond in Tamil (Tamil script). Mix some English words naturally. Keep the same short 1-2 sentence rule.",
    }.get(lang, "")

    system = f"""{KITTY_SYSTEM}

User's name: {user_name}
Current mood: {mood_hint}"""
    if lang_hint:
        system += f"\nLanguage instruction: {lang_hint}"

    # ── Load memories from DB ─────────────────────
    db_memories = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT key, value FROM memories WHERE user_id = ? ORDER BY updated_at DESC LIMIT 10",
                (user_id,)
            ).fetchall()
            db_memories = [f"{r[0]}: {r[1]}" for r in rows]
    except Exception:
        pass

    # Merge DB memories with any sent from frontend (frontend fallback for very first messages)
    all_memories = db_memories + [m for m in past_memories if m not in db_memories]

    if all_memories:
        mem_lines = "\n".join(f"- {m}" for m in all_memories[:10])
        system += f"\n\nWhat you know about {user_name} (from past conversations):\n{mem_lines}\nUse this naturally. Never say 'I know that you...' — just remember it like a real friend would."

    # ── Topic locking ─────────────────────────────
    topic = data.get("topic") or ""
    if topic:
        system += f"\n\nCurrent topic: {topic}. Stay focused on this. Don't randomly switch subjects unless {user_name} clearly does first."

    # Build messages with full recent context (12 messages instead of 6)
    messages = [{"role": "system", "content": system}]
    for h in history[-12:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": messages,
                "max_tokens": 80,
                "temperature": 0.70,   # lower = less hallucination, stays on topic
                "stop": ["User:", "Human:"]  # removed "\n" — was truncating natural responses mid-sentence
            },
            timeout=6
        )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            # Clean: take first sentence only, remove markdown
            import re
            reply = raw.strip()
            reply = re.sub(r'\*+', '', reply)       # remove **bold**
            reply = re.sub(r'#+\s*', '', reply)     # remove headers
            reply = re.sub(r'\n+', ' ', reply)      # flatten newlines
            reply = re.sub(r'\s+', ' ', reply).strip()
            # Take only first 1-2 sentences
            sentences = re.split(r'(?<=[.!?])\s+', reply)
            reply = ' '.join(sentences[:2]).strip()
            reply = humanize_response(reply)
            emotion = detect_emotion(reply)
            # Fire-and-forget: extract facts from this message and save to DB
            threading.Thread(
                target=extract_and_save_memories,
                args=(user_id, user_msg, groq_key),
                daemon=True
            ).start()
            return jsonify({"reply": reply, "emotion": emotion})
        else:
            print(f"  Groq error {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"  Groq failed: {e}")

    # Fallback
    reply, emotion = rule_based_reply(user_msg, user_name)
    return jsonify({"reply": reply, "emotion": emotion})

def humanize_response(text):
    """Strip AI writing patterns so Kitty sounds like a real person."""
    import re
    # Remove filler openers Groq sometimes prepends
    text = re.sub(
        r'^(Absolutely|Certainly|Of course|Sure thing|Sure|Great|Fantastic|Wonderful|Definitely|Indeed)'
        r'[,!\.]?\s*', '', text, flags=re.IGNORECASE
    ).strip()
    text = re.sub(
        r'^(I (completely |totally )?(understand|see|get (it|that))|That(\'s| is) (great|amazing|wonderful|so sweet))'
        r'[,!\.]?\s*', '', text, flags=re.IGNORECASE
    ).strip()
    # Em dash → natural comma pause
    text = text.replace(' — ', ', ').replace('—', ', ')
    # AI vocabulary swaps
    subs = [
        (r'\bnavigate\b', 'handle'),      (r'\bdelve\b', 'get into'),
        (r'\bleverage\b', 'use'),         (r'\butilize\b', 'use'),
        (r'\bfoster\b', 'build'),         (r'\bembark\b', 'start'),
        (r'\bvibrant\b', 'fun'),          (r'\bcomprehensive\b', 'full'),
        (r'\bmultifaceted\b', 'complex'), (r'\btapestry\b', 'mix'),
        (r'\btestament\b', 'sign'),       (r'\bpivotal\b', 'big'),
        (r'\bcrucial\b', 'important'),    (r'\benhance\b', 'improve'),
        (r'\bemphasize\b', 'stress'),     (r'\bunderscore\b', 'show'),
        (r'\bit sounds like\b', ''),      (r'\bit seems like\b', ''),
        (r'\bfeel free to\b', ''),        (r"\bdon't hesitate to\b", ''),
        (r"\bit's important to\b", ''),   (r'\bfurthermore\b', 'also'),
        (r'\bmoreover\b', 'and'),         (r'\bin conclusion\b', ''),
        (r'\bto summarize\b', ''),        (r'\badditionally\b', 'also'),
        (r'\bultimately\b', ''),
    ]
    for pat, rep in subs:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def detect_emotion(text):
    t = text.lower()
    exc = text.count('!')

    # Surprised — sudden wide-eyed reaction
    if any(p in t for p in ['wait what','no way','seriously??','oh wow','i can\'t believe','that\'s wild','wait really']):
        return 'surprised'
    # Excited — sustained high energy
    if exc >= 2 or any(p in t for p in ['tell me everything','love that','so good','let\'s go','that\'s amazing','yay']):
        return 'excited'
    # Proud — warm celebratory
    if any(p in t for p in ['proud of you','you did it','you made it','knew you could','well done','you got this']):
        return 'proud'
    # Playful — teasing, laughter
    if any(p in t for p in ['haha','lol','kidding','joking','silly','tease','that\'s funny','you\'re funny']):
        return 'playful'
    # Consoling — gentle empathy
    if any(p in t for p in ["i'm sorry","that's rough","that's tough","must be hard","it's okay","that's okay","that sucks"]):
        return 'console'
    # Worried — concerned, anxious tone
    if any(p in t for p in ['are you okay','worried about','please be careful','that sounds scary','anxious','stressed out']):
        return 'worried'
    # Frustrated — clipped, tense
    if any(p in t for p in ['come on','ugh','seriously?','why is this','this is frustrating','so annoying','argh']):
        return 'frustrated'
    # Sad — slow and quiet
    if any(p in t for p in ['sad','lonely','really hurt','crying','hard day','feeling low','that hurts','miss you']):
        return 'sad'
    # Loving — warm care
    if any(p in t for p in [' love ',' miss ','care about','always here for','thinking about you','so happy you\'re']):
        return 'loving'
    # Curious — questioning, thinking
    if any(p in t for p in ['hmm','wondering','tell me more','how come','why would','what do you','really?']):
        return 'curious'
    # Sleepy — slow and soft
    if any(p in t for p in ['goodnight','sleep well','rest now','close your eyes','take it slow','slowly drift']):
        return 'sleepy'
    # Happy — single ! or mild positive
    if exc == 1 or any(p in t for p in ['nice','good','cool','awesome','great job','not bad','well done']):
        return 'happy'
    return 'neutral'

def rule_based_reply(text, name):
    import random
    t = text.lower()
    n = name
    pick = lambda a: random.choice(a)
    if any(w in t for w in ['hi','hello','hey','morning','evening','night']):
        return pick([
            f"{n}! You showed up. Tell me what's going on.",
            f"Hey {n}. Finally. What's happening?",
            f"{n}! I was literally just thinking about you.",
        ]), 'excited'
    if 'how are you' in t or 'how are u' in t:
        return pick([
            f"Better now. What about you, {n}?",
            f"Waiting for you to show up, honestly. How are you?",
        ]), 'happy'
    if any(w in t for w in ['love','miss']):
        return pick([
            f"Same, {n}. Same.",
            f"You can't just say that without telling me what's going on.",
        ]), 'loving'
    if any(w in t for w in ['sad','cry','lonely','hurt','stress','anxious']):
        return pick([
            f"Hey. I'm right here. What happened?",
            f"{n}, talk to me. Not going anywhere.",
        ]), 'sad'
    if any(w in t for w in ['happy','great','amazing','good news']):
        return pick([
            f"Wait, seriously? Tell me everything.",
            f"{n}! What happened? Don't leave me waiting.",
        ]), 'excited'
    if any(w in t for w in ['tired','sleep','nap','rest']):
        return pick([
            f"Rest. You've been carrying a lot.",
            f"Go sleep, {n}. I'll be here when you wake up.",
        ]), 'sleepy'
    if any(w in t for w in ['work','job','office','meeting']):
        return pick([
            f"How's work actually going? Not the 'fine' version.",
            f"Are you taking care of yourself between all this?",
        ]), 'curious'
    if any(w in t for w in ['bye','goodbye','later','leaving']):
        return pick([
            f"Don't be gone too long. I notice.",
            f"Come back soon, {n}.",
        ]), 'sad'
    return pick([
        f"Keep going. I'm listening.",
        f"Say more. I want to understand.",
        f"That's interesting. Tell me more.",
    ]), 'curious'

# ── TTS endpoint ──────────────────────────────────
@app.route("/speak", methods=["POST"])
def speak():
    data     = request.get_json(silent=True) or {}
    text     = (data.get("text") or "").strip()
    emotion  = (data.get("emotion") or "neutral").lower()
    key      = data.get("api_key") or EL_KEY
    voice_id = data.get("voice_id") or EL_VOICE_ID

    if not text:
        return jsonify({"error": "No text"}), 400

    # Serve from cache instantly
    cache_key  = hashlib.md5(f"{text}|{emotion}|{voice_id}".encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.mp3")
    if os.path.exists(cache_path):
        print(f"  [cache] {text[:35]}...")
        return send_file(cache_path, mimetype="audio/mpeg")

    lang = data.get("lang") or "en-IN"
    tts_model = "eleven_multilingual_v2" if lang != "en-IN" else EL_MODEL
    vs = EMOTION_SETTINGS.get(emotion, EMOTION_SETTINGS["neutral"])

    # ── ElevenLabs PRIMARY ──
    if key:
        print(f"  [EL/{emotion}] {text[:45]}...")
        try:
            resp = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": text, "model_id": tts_model, "voice_settings": vs,
                      "optimize_streaming_latency": 1},
                stream=True, timeout=10
            )
            if resp.status_code == 200:
                def generate():
                    chunks = []
                    for chunk in resp.iter_content(chunk_size=1024):
                        if chunk:
                            chunks.append(chunk)
                            yield chunk
                    try:
                        with open(cache_path, "wb") as f:
                            f.write(b"".join(chunks))
                    except Exception:
                        pass
                return Response(generate(), mimetype="audio/mpeg",
                    headers={"Cache-Control": "no-cache", "Transfer-Encoding": "chunked"})
            print(f"  ✗ EL {resp.status_code}")
        except Exception as e:
            print(f"  ✗ EL: {e}")

    # ── Edge TTS fallback ──
    if EDGE_OK:
        edge_voices = {
            "en-IN": "en-IN-NeerjaNeural",
            "hi-IN": "hi-IN-SwaraNeural",
            "te-IN": "te-IN-ShrutiNeural",
            "ta-IN": "ta-IN-PallaviNeural",
        }
        edge_voice = edge_voices.get(lang, "en-IN-NeerjaNeural")
        edge_path = os.path.join(CACHE_DIR, f"edge_{cache_key}.mp3")
        try:
            loop = asyncio.new_event_loop()
            async def gen():
                c = edge_tts.Communicate(text=text, voice=edge_voice)
                await c.save(edge_path)
            loop.run_until_complete(gen())
            loop.close()
            if os.path.exists(edge_path):
                print(f"  ✓ Edge TTS [{edge_voice}]: {text[:35]}...")
                return send_file(edge_path, mimetype="audio/mpeg")
        except Exception as e:
            print(f"  ✗ Edge TTS: {e}")

    return jsonify({"error": "TTS failed"}), 500


# ── Sleep Story ───────────────────────────
@app.route("/sleep-story", methods=["POST"])
def sleep_story():
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "forest")
    groq_key = data.get("groq_key") or GROQ_KEY

    themes = {
        "forest": "a magical forest with fireflies, ancient trees, and a gentle stream",
        "ocean":  "a calm ocean with soft waves, a distant lighthouse, and moonlit water",
        "stars":  "floating among stars in a warm, peaceful night sky above the clouds",
        "garden": "a secret garden filled with glowing flowers, butterflies, and soft lanterns",
        "cloud":  "drifting on the softest cloud above a sleeping city, wrapped in moonlight",
    }

    lang = data.get("lang", "en-IN")
    lang_instr = {
        "hi-IN": "Write in Hindi (Devanagari script).",
        "te-IN": "Write in Telugu script.",
        "ta-IN": "Write in Tamil script.",
    }.get(lang, "Write in English.")

    prompt = f"""Write a very short, calming bedtime story (60-80 words) set in {themes.get(theme, themes['forest'])}.
{lang_instr} Speak as Kitty, directly to the listener using "you". Use slow, dreamy language. End with them feeling deeply sleepy.
No dialogue. Pure calming narration. Avoid any action or excitement."""

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 130, "temperature": 0.72},
            timeout=8
        )
        if resp.status_code == 200:
            story = resp.json()["choices"][0]["message"]["content"].strip()
            return jsonify({"story": story})
        print(f"  Sleep story Groq error: {resp.status_code}")
    except Exception as e:
        print(f"  Sleep story error: {e}")

    fallback = "Close your eyes... imagine you're floating on a soft cloud, drifting through a warm, starlit sky. The world below is asleep. A gentle breeze wraps around you like a blanket. Your body grows heavy. Your thoughts slow down. Drift... drift... goodnight."
    return jsonify({"story": fallback})


# ── Journal Summary ───────────────────────
@app.route("/journal/summary", methods=["POST"])
def journal_summary():
    data = request.get_json(silent=True) or {}
    entries = data.get("entries") or []
    name = data.get("name") or "friend"
    groq_key = data.get("groq_key") or GROQ_KEY

    if not entries:
        return jsonify({"summary": "No journal entries yet. Start recording and Kitty will reflect on your week!"})

    lang = data.get("lang", "en-IN")
    lang_instr = {
        "hi-IN": "Respond in Hindi (Devanagari script).",
        "te-IN": "Respond in Telugu script.",
        "ta-IN": "Respond in Tamil script.",
    }.get(lang, "Respond in English.")

    entries_text = "\n".join([f"- {e.get('date','')}: {e.get('text','')}" for e in entries[-7:]])
    prompt = f"""You are Kitty, a warm AI companion. Read these journal entries from {name} and write a 2-3 sentence weekly reflection.
Notice patterns, growth, or recurring feelings. Be warm, personal, and encouraging. Speak directly to {name}. {lang_instr}

Entries:
{entries_text}"""

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 110, "temperature": 0.75},
            timeout=8
        )
        if resp.status_code == 200:
            summary = resp.json()["choices"][0]["message"]["content"].strip()
            return jsonify({"summary": summary})
    except Exception as e:
        print(f"  Journal summary error: {e}")

    return jsonify({"summary": f"{name}, you've been journaling. That says something. Keep going."})


def warmup_cache():
    """Pre-generate common phrases so they play instantly"""
    phrases = [
        ("I'm listening...", "curious"),
        ("Tell me more.", "loving"),
        ("I'm right here.", "console"),
        ("Hmm, let me think.", "curious"),
    ]
    print("  Warming up voice cache...")
    for text, emotion in phrases:
        cache_key = hashlib.md5(f"{text}|{emotion}|{EL_VOICE_ID}".encode()).hexdigest()
        cache_path = os.path.join(CACHE_DIR, f"{cache_key}.mp3")
        if not os.path.exists(cache_path):
            try:
                vs = EMOTION_SETTINGS.get(emotion, EMOTION_SETTINGS["neutral"])
                resp = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}/stream",
                    headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"},
                    json={"text": text, "model_id": EL_MODEL, "voice_settings": vs, "optimize_streaming_latency": 4},
                    timeout=8
                )
                if resp.status_code == 200:
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                    print(f"  ✓ Cached: '{text}'")
            except Exception as e:
                print(f"  ✗ Warmup failed: {e}")


if __name__ == "__main__":
    import socket, threading
    ip = socket.gethostbyname(socket.gethostname())
    threading.Thread(target=warmup_cache, daemon=True).start()
    print("\n=== Kitty Voice Server ===")
    print("   PC:      http://localhost:5000")
    print("   Android: http://" + ip + ":5000")
    print("====================================================\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
