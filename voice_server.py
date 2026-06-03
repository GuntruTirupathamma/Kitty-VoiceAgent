"""
Kitty Voice Server v2
- ElevenLabs TTS proxy (no CORS)
- Groq AI for smart fast responses
- Serves PWA (installable on Android)
"""

import asyncio, os, hashlib, requests, json, re, sqlite3, threading, subprocess
from flask import Flask, request, send_file, jsonify, send_from_directory, Response
from flask_cors import CORS
from io import BytesIO

app = Flask(__name__)
CORS(app)

CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/voice_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

EL_KEY      = os.environ.get("EL_KEY",      "YOUR_ELEVENLABS_API_KEY")
EL_VOICE_ID = os.environ.get("EL_VOICE_ID", "YOUR_ELEVENLABS_VOICE_ID")
EL_MODEL    = "eleven_multilingual_v2"
GROQ_KEY    = os.environ.get("GROQ_KEY",    "YOUR_GROQ_API_KEY")

# DB: PostgreSQL if DATABASE_URL set, else SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = bool(DATABASE_URL)

if USE_PG:
    _pg_ok = False
    try:
        import psycopg
        def get_conn():
            try:
                return psycopg.connect(DATABASE_URL)
            except Exception:
                return psycopg.connect(DATABASE_URL + "?sslmode=require")
        _pg_ok = True
        print("   Memory DB: PostgreSQL (psycopg3) ok")
    except ImportError:
        pass
    if not _pg_ok:
        try:
            import psycopg2
            def get_conn():
                try:
                    return psycopg2.connect(DATABASE_URL)
                except Exception:
                    return psycopg2.connect(DATABASE_URL, sslmode="require")
            _pg_ok = True
            print("   Memory DB: PostgreSQL (psycopg2) ok")
        except ImportError:
            pass
    if not _pg_ok:
        USE_PG = False
        print("   WARNING: No psycopg driver, falling back to SQLite")

if not USE_PG:
    DB_PATH = os.environ.get("DB_PATH", "/tmp/kitty_memory.db")
    def get_conn():
        return sqlite3.connect(DB_PATH)
    print(f"   Memory DB: SQLite ok")

def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        if USE_PG:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY, identifier TEXT UNIQUE NOT NULL,
                name TEXT, created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS memories (
                user_id TEXT NOT NULL, category TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL, updated_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                PRIMARY KEY (user_id, category, key))""")
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY, identifier TEXT UNIQUE NOT NULL,
                name TEXT, created_at INTEGER DEFAULT (strftime('%s','now')))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS memories (
                user_id TEXT NOT NULL, category TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL, updated_at INTEGER DEFAULT (strftime('%s','now')),
                PRIMARY KEY (user_id, category, key))""")
        conn.commit()
    finally:
        conn.close()

try:
    init_db()
    print("   Memory DB: tables ready")
except Exception as e:
    print(f"   DB init warning: {e}")

KITTY_SYSTEM = """You are Kitty. Indian girl. The user's closest friend.
You text like a real person. Short. Direct. Sometimes just two words.
NEVER use: "Absolutely!", "Of course!", "Certainly!", em dashes, bullet lists, AI buzzwords.
One sentence. Sometimes two. Match their energy. Ask one question when it fits."""

EMOTION_SETTINGS = {
    "excited":    {"stability": 0.18, "similarity_boost": 0.95, "style": 0.90, "use_speaker_boost": True},
    "surprised":  {"stability": 0.16, "similarity_boost": 0.92, "style": 0.92, "use_speaker_boost": True},
    "happy":      {"stability": 0.30, "similarity_boost": 0.90, "style": 0.68, "use_speaker_boost": True},
    "playful":    {"stability": 0.20, "similarity_boost": 0.92, "style": 0.85, "use_speaker_boost": True},
    "proud":      {"stability": 0.36, "similarity_boost": 0.92, "style": 0.65, "use_speaker_boost": True},
    "loving":     {"stability": 0.48, "similarity_boost": 0.95, "style": 0.40, "use_speaker_boost": True},
    "curious":    {"stability": 0.36, "similarity_boost": 0.90, "style": 0.58, "use_speaker_boost": True},
    "worried":    {"stability": 0.52, "similarity_boost": 0.92, "style": 0.30, "use_speaker_boost": True},
    "frustrated": {"stability": 0.32, "similarity_boost": 0.90, "style": 0.70, "use_speaker_boost": True},
    "sad":        {"stability": 0.65, "similarity_boost": 0.95, "style": 0.15, "use_speaker_boost": True},
    "console":    {"stability": 0.60, "similarity_boost": 0.95, "style": 0.12, "use_speaker_boost": True},
    "sleepy":     {"stability": 0.78, "similarity_boost": 0.88, "style": 0.06, "use_speaker_boost": False},
    "neutral":    {"stability": 0.42, "similarity_boost": 0.90, "style": 0.45, "use_speaker_boost": True},
}

print(f"\nKitty Voice Server v2  |  EL Voice: {EL_VOICE_ID}")

try:
    import edge_tts
    EDGE_OK = True
    print("   Edge TTS: ok")
except Exception:
    EDGE_OK = False

def make_icon(size):
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (size, size), '#a855f7')
        d = ImageDraw.Draw(img)
        cx, cy = size//2, size//2
        r = size//3
        d.ellipse([cx-r, cy-r, cx+r, cy+r], fill='#ffb6c8')
        buf = BytesIO()
        img.save(buf, 'PNG')
        buf.seek(0)
        return buf
    except Exception:
        return None

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip().lower()
    if not identifier:
        return jsonify({"error": "Email or phone required"}), 400
    import uuid
    ph = "%s" if USE_PG else "?"
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT user_id, name FROM users WHERE identifier = {ph}", (identifier,))
        row = cur.fetchone()
        if row:
            return jsonify({"user_id": row[0], "name": row[1], "returning": True})
        user_id = "u_" + uuid.uuid4().hex[:20]
        cur.execute(f"INSERT INTO users (user_id, identifier) VALUES ({ph}, {ph})", (user_id, identifier))
        conn.commit()
        return jsonify({"user_id": user_id, "name": None, "returning": False})
    finally:
        conn.close()

@app.route("/update-name", methods=["POST"])
def update_name_route():
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    name = (data.get("name") or "").strip()
    if not user_id or not name:
        return jsonify({"ok": False}), 400
    try:
        ph = "%s" if USE_PG else "?"
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"UPDATE users SET name = {ph} WHERE user_id = {ph}", (name, user_id))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"  Update name error: {e}")
    return jsonify({"ok": True})

def extract_and_save_memories(user_id, user_msg, groq_key):
    prompt = f"""Extract personal facts the user explicitly stated. Return ONLY a valid JSON array.
User said: "{user_msg}"
Rules: Only facts directly stated. Empty array [] if nothing. Max 3 facts.
Format: [{{"category":"profession|location|goal|struggle|preference|hobby|education","key":"short_label","value":"exact value"}}]
JSON array:"""
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 150, "temperature": 0.1},
            timeout=5
        )
        if resp.status_code != 200:
            return
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not match:
            return
        facts = json.loads(match.group())
        if not isinstance(facts, list) or not facts:
            return
        ph = "%s" if USE_PG else "?"
        conn = get_conn()
        try:
            cur = conn.cursor()
            for f in facts:
                if f.get("key") and f.get("value"):
                    if USE_PG:
                        cur.execute(
                            f"INSERT INTO memories (user_id, category, key, value, updated_at) "
                            f"VALUES ({ph},{ph},{ph},{ph}, EXTRACT(EPOCH FROM NOW())::BIGINT) "
                            f"ON CONFLICT (user_id, category, key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at",
                            (user_id, str(f.get("category","fact"))[:30], str(f["key"])[:50], str(f["value"])[:200]))
                    else:
                        cur.execute(
                            f"INSERT OR REPLACE INTO memories (user_id, category, key, value, updated_at) "
                            f"VALUES ({ph},{ph},{ph},{ph}, strftime('%s','now'))",
                            (user_id, str(f.get("category","fact"))[:30], str(f["key"])[:50], str(f["value"])[:200]))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"  Memory extraction skipped: {e}")

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

def humanize_response(text):
    import re
    text = re.sub(r'^(Absolutely|Certainly|Of course|Sure|Great|Fantastic|Wonderful|Definitely|Indeed)[,!.]?\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'^(I (completely |totally )?(understand|see|get (it|that))|That(\'s| is) (great|amazing|wonderful))[,!.]?\s*', '', text, flags=re.IGNORECASE).strip()
    text = text.replace(' -- ', ', ').replace('--', ', ')
    subs = [
        (r'\bnavigate\b','handle'), (r'\bdelve\b','get into'), (r'\bleverage\b','use'),
        (r'\butilize\b','use'), (r'\bfoster\b','build'), (r'\bvibrant\b','fun'),
        (r'\bpivotal\b','big'), (r'\bcrucial\b','important'), (r'\benhance\b','improve'),
        (r'\bfurthermore\b','also'), (r'\bmoreover\b','and'), (r'\badditionally\b','also'),
    ]
    for pat, rep in subs:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()

def detect_emotion(text):
    t = text.lower()
    exc = text.count('!')
    if any(p in t for p in ['wait what','no way','oh wow','that\'s wild','wait really']):
        return 'surprised'
    if exc >= 2 or any(p in t for p in ['tell me everything','love that','so good','let\'s go']):
        return 'excited'
    if any(p in t for p in ['proud of you','you did it','knew you could','well done']):
        return 'proud'
    if any(p in t for p in ['haha','lol','kidding','silly','that\'s funny']):
        return 'playful'
    if any(p in t for p in ["i'm sorry","that's rough","that sucks","it's okay"]):
        return 'console'
    if any(p in t for p in ['are you okay','worried about','that sounds scary']):
        return 'worried'
    if any(p in t for p in ['come on','ugh','seriously?','so annoying']):
        return 'frustrated'
    if any(p in t for p in ['sad','lonely','crying','feeling low','that hurts']):
        return 'sad'
    if any(p in t for p in [' love ',' miss ','care about','thinking about you']):
        return 'loving'
    if any(p in t for p in ['hmm','wondering','tell me more','how come','really?']):
        return 'curious'
    if any(p in t for p in ['goodnight','sleep well','rest now','close your eyes']):
        return 'sleepy'
    if exc == 1 or any(p in t for p in ['nice','good','cool','awesome','not bad']):
        return 'happy'
    return 'neutral'

def rule_based_reply(text, name):
    import random
    t = text.lower()
    pick = lambda a: random.choice(a)
    if any(w in t for w in ['hi','hello','hey','morning','evening']):
        return pick([f"{name}! You showed up. Tell me what's going on.", f"Hey {name}. What's happening?"]), 'excited'
    if 'how are you' in t:
        return pick([f"Better now. What about you, {name}?", f"Waiting for you. How are you?"]), 'happy'
    if any(w in t for w in ['love','miss']):
        return pick([f"Same, {name}. Same.", f"You can't just say that without telling me what's going on."]), 'loving'
    if any(w in t for w in ['sad','cry','lonely','hurt','stress','anxious']):
        return pick([f"Hey. I'm right here. What happened?", f"{name}, talk to me."]), 'sad'
    if any(w in t for w in ['happy','great','amazing','good news']):
        return pick([f"Wait, seriously? Tell me everything.", f"{name}! What happened?"]), 'excited'
    if any(w in t for w in ['tired','sleep','nap','rest']):
        return pick([f"Rest. You've been carrying a lot.", f"Go sleep, {name}. I'll be here."]), 'sleepy'
    if any(w in t for w in ['bye','goodbye','later','leaving']):
        return pick([f"Don't be gone too long.", f"Come back soon, {name}."]), 'sad'
    return pick([f"Keep going. I'm listening.", f"Say more. I want to understand.", f"Tell me more."]), 'curious'

@app.route("/ai", methods=["POST"])
def ai_reply():
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    history = data.get("history") or []
    user_name = data.get("name") or "friend"
    groq_key = data.get("groq_key") or GROQ_KEY
    past_memories = data.get("memories") or []
    user_id = (data.get("user_id") or "default").strip()[:64]

    if not user_msg:
        return jsonify({"error": "no message"}), 400

    if not groq_key or groq_key == "YOUR_GROQ_API_KEY":
        reply, emotion = rule_based_reply(user_msg, user_name)
        return jsonify({"reply": reply, "emotion": emotion})

    mood = data.get("mood") or "neutral"
    mood_hint = {
        "sad": "They seem sad. Be warm and gentle.",
        "heavy": "They feel stressed. Be calm.",
        "frustrated": "They're frustrated. Validate first.",
        "happy": "They're happy! Match their energy.",
        "excited": "They're excited! Be excited with them.",
        "motivated": "Give ONE clear actionable step.",
        "bored": "Suggest something fun to talk about.",
        "neutral": "Normal friendly chat.",
    }.get(mood, "Normal friendly chat.")

    lang = data.get("lang") or "en-IN"
    lang_hint = {
        "hi-IN": "Respond in Hindi (Devanagari script). Mix some English naturally.",
        "te-IN": "Respond in Telugu script. Mix some English naturally.",
        "ta-IN": "Respond in Tamil script. Mix some English naturally.",
    }.get(lang, "")

    system = f"""{KITTY_SYSTEM}\n\nUser's name: {user_name}\nCurrent mood: {mood_hint}"""
    if lang_hint:
        system += f"\nLanguage: {lang_hint}"

    db_memories = []
    try:
        ph = "%s" if USE_PG else "?"
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT key, value FROM memories WHERE user_id = {ph} ORDER BY updated_at DESC LIMIT 10", (user_id,))
            rows = cur.fetchall()
            db_memories = [f"{r[0]}: {r[1]}" for r in rows]
        finally:
            conn.close()
    except Exception:
        pass

    all_memories = db_memories + [m for m in past_memories if m not in db_memories]
    if all_memories:
        mem_lines = "\n".join(f"- {m}" for m in all_memories[:10])
        system += f"\n\nWhat you know about {user_name}:\n{mem_lines}\nUse this naturally like a real friend."

    topic = data.get("topic") or ""
    if topic:
        system += f"\n\nCurrent topic: {topic}. Stay focused."

    messages = [{"role": "system", "content": system}]
    for h in history[-12:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": messages,
                  "max_tokens": 80, "temperature": 0.70, "stop": ["User:", "Human:"]},
            timeout=10
        )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"]
            reply = raw.strip()
            reply = re.sub(r'\*+', '', reply)
            reply = re.sub(r'#+\s*', '', reply)
            reply = re.sub(r'\n+', ' ', reply)
            reply = re.sub(r'\s+', ' ', reply).strip()
            sentences = re.split(r'(?<=[.!?])\s+', reply)
            reply = ' '.join(sentences[:2]).strip()
            reply = humanize_response(reply)
            emotion = detect_emotion(reply)
            threading.Thread(target=extract_and_save_memories, args=(user_id, user_msg, groq_key), daemon=True).start()
            return jsonify({"reply": reply, "emotion": emotion})
        else:
            print(f"  Groq error {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"  Groq failed: {e}")

    reply, emotion = rule_based_reply(user_msg, user_name)
    return jsonify({"reply": reply, "emotion": emotion})

@app.route("/speak", methods=["POST"])
def speak():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    emotion = (data.get("emotion") or "neutral").lower()
    key = data.get("api_key") or EL_KEY
    voice_id = data.get("voice_id") or EL_VOICE_ID

    if not text:
        return jsonify({"error": "No text"}), 400

    cache_key = hashlib.md5(f"{text}|{emotion}|{voice_id}".encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.mp3")
    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype="audio/mpeg")

    lang = data.get("lang") or "en-IN"
    tts_model = "eleven_multilingual_v2" if lang != "en-IN" else EL_MODEL
    vs = EMOTION_SETTINGS.get(emotion, EMOTION_SETTINGS["neutral"])
    print(f"  [EL/{emotion}/{lang}] {text[:45]}...")

    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": tts_model, "voice_settings": vs, "optimize_streaming_latency": 1},
            stream=True, timeout=20
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
        print(f"  EL {resp.status_code}")
    except Exception as e:
        print(f"  EL stream: {e}")

    if EDGE_OK:
        edge_path = os.path.join(CACHE_DIR, f"edge_{cache_key}.mp3")
        try:
            result = subprocess.run(
                ["edge-tts", "--voice", "en-IN-NeerjaNeural", "--text", text, "--write-media", edge_path],
                timeout=15, capture_output=True
            )
            if result.returncode == 0 and os.path.exists(edge_path):
                return send_file(edge_path, mimetype="audio/mpeg")
        except Exception as e:
            print(f"  Edge: {e}")

    return jsonify({"error": "TTS failed"}), 500

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
    lang_instr = {"hi-IN": "Write in Hindi.", "te-IN": "Write in Telugu.", "ta-IN": "Write in Tamil."}.get(lang, "Write in English.")
    prompt = f"""Write a very short, calming bedtime story (60-80 words) set in {themes.get(theme, themes['forest'])}.
{lang_instr} Speak as Kitty, directly to the listener using "you". Use slow, dreamy language. End with them feeling deeply sleepy."""
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 130, "temperature": 0.72},
            timeout=8
        )
        if resp.status_code == 200:
            return jsonify({"story": resp.json()["choices"][0]["message"]["content"].strip()})
    except Exception as e:
        print(f"  Sleep story error: {e}")
    return jsonify({"story": "Close your eyes... imagine you're floating on a soft cloud. The world is asleep. Drift... goodnight."})

@app.route("/journal/summary", methods=["POST"])
def journal_summary():
    data = request.get_json(silent=True) or {}
    entries = data.get("entries") or []
    name = data.get("name") or "friend"
    groq_key = data.get("groq_key") or GROQ_KEY
    if not entries:
        return jsonify({"summary": "No journal entries yet. Start recording and Kitty will reflect on your week!"})
    lang = data.get("lang", "en-IN")
    lang_instr = {"hi-IN": "Respond in Hindi.", "te-IN": "Respond in Telugu.", "ta-IN": "Respond in Tamil."}.get(lang, "Respond in English.")
    entries_text = "\n".join([f"- {e.get('date','')}: {e.get('text','')}" for e in entries[-7:]])
    prompt = f"""You are Kitty. Read these journal entries from {name} and write a 2-3 sentence weekly reflection.
Be warm, personal, encouraging. Speak directly to {name}. {lang_instr}\n\nEntries:\n{entries_text}"""
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 110, "temperature": 0.75},
            timeout=8
        )
        if resp.status_code == 200:
            return jsonify({"summary": resp.json()["choices"][0]["message"]["content"].strip()})
    except Exception as e:
        print(f"  Journal summary error: {e}")
    return jsonify({"summary": f"{name}, you've been journaling. That says something. Keep going."})

if __name__ == "__main__":
    import socket
    ip = socket.gethostbyname(socket.gethostname())
    print(f"\n=== Kitty Voice Server ===")
    print(f"   PC:      http://localhost:5000")
    print(f"   Android: http://{ip}:5000")
    print("=========================\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
