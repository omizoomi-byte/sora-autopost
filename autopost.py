#!/usr/bin/env python3
"""
AutoPost — Daily YouTube Shorts Automation
Uses Google Veo 3 (free via Google AI Studio) for video generation.
Runs on GitHub Actions every day. Laptop never needs to be on.
"""

import json
import os
import time
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# ─── LOAD PROGRESS ────────────────────────────────────────────────────────────
PROGRESS_FILE = "progress.json"
PROMPTS_FILE  = "prompts.json"

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"current_index": 0, "posted_count": 0, "history": []}

def save_progress(p):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(p, f, indent=2)

def load_prompts():
    with open(PROMPTS_FILE) as f:
        return json.load(f)

# ─── VIDEO GENERATION (Google Veo 3 - Free via AI Studio) ─────────────────────
def generate_video(prompt: str) -> str:
    print(f"Generating video with Google Veo 3...")
    print(f"   Prompt: {prompt[:80]}...")

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    operation = client.models.generate_videos(
        model="veo-3.0-generate-preview",
        prompt=prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio="9:16",      # Vertical for YouTube Shorts
            duration_seconds=8,       # 8 second video
            number_of_videos=1,
        ),
    )

    print("   Waiting for video to generate (this takes ~2-5 mins)...")
    while not operation.done:
        time.sleep(20)
        operation = client.operations.get(operation)
        print("   Still generating...")

    generated_video = operation.result.generated_videos[0]
    client.files.download(file=generated_video.video)
    video_path = "output.mp4"
    generated_video.video.save(video_path)

    print(f"   Video saved: {video_path}")
    return video_path

# ─── YOUTUBE UPLOAD ───────────────────────────────────────────────────────────
def upload_to_youtube(video_path: str, prompt: str) -> str:
    print("Uploading to YouTube Shorts...")

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )

    youtube = build("youtube", "v3", credentials=creds)

    words = prompt.split()
    title = " ".join(words[10:20]) if len(words) > 10 else prompt[:80]
    title = title.strip()[:90]

    body = {
        "snippet": {
            "title": title,
            "description": f"#Shorts #AIVideo\n\n{prompt[:500]}",
            "tags": ["shorts", "AI", "AIVideo", "YouTubeShorts"],
            "categoryId": "24"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   Upload {int(status.progress() * 100)}%...")

    video_id = response["id"]
    url = f"https://youtube.com/shorts/{video_id}"
    print(f"   Posted: {url}")
    return url

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    prompts  = load_prompts()
    progress = load_progress()

    idx = progress["current_index"]
    if idx >= len(prompts):
        print("All 1000 prompts posted! Reset progress.json to start over.")
        exit(0)

    prompt = prompts[idx]
    print(f"\n{'='*60}")
    print(f"AUTOPOST — Day {idx + 1} of {len(prompts)}")
    print(f"{'='*60}\n")

    video_path = generate_video(prompt)
    url = upload_to_youtube(video_path, prompt)

    progress["current_index"] = idx + 1
    progress["posted_count"]  = progress.get("posted_count", 0) + 1
    progress["history"].append({
        "index": idx,
        "prompt": prompt[:120] + "...",
        "youtube_url": url,
        "posted_at": time.strftime("%Y-%m-%d %H:%M UTC")
    })
    save_progress(progress)

    print(f"\nDone! Short #{progress['posted_count']} posted: {url}\n")




