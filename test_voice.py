import requests

API_KEY  = "sk_45c5bb9dea839333b7c01356bf1bd42caaa3b75ff1fbd25a"
VOICE_ID = "cq026hCJMRqmxYMsUslq"

print("Testing ElevenLabs voice...")

response = requests.post(
    f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
    headers={
        "xi-api-key": API_KEY,
        "Content-Type": "application/json"
    },
    json={
        "text": "Hi! I am Kitty, your AI companion. Can you hear my voice?",
        "model_id": "eleven_turbo_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.85
        }
    }
)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    with open("test_kitty.mp3", "wb") as f:
        f.write(response.content)
    print("✓ Success! Saved as test_kitty.mp3")
    import os
    os.startfile("test_kitty.mp3")
else:
    print(f"✗ Error: {response.text}")
