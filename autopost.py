#!/usr/bin/env python3
"""
Sora AutoPost — Daily YouTube Shorts Automation
Runs on GitHub Actions every day. Laptop never needs to be on.
"""

import json
import os
import time
import requests
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

def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)

def load_prompts():
    with open(PROMPTS_FILE) as f:
        return json.load(f)

# ─── SORA VIDEO GENERATION ────────────────────────────────────────────────────
def generate_video(prompt: str, api_key: str) -> str:
    """Submit prompt to Sora, wait for completion, return local video path."""

    print(f"🎬 Generating video for prompt #{progress['current_index'] + 1}...")
    print(f"   {prompt[:80]}...")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Submit generation request
    response = requests.post(
        "https://api.openai.com/v1/video/generations",
        headers=headers,
        json={
            "prompt": prompt,
            "model": "sora-1.0",
            "size": "1080x1920",   # 9:16 vertical for Shorts
            "duration": 20,
            "n": 1
        }
    )
    response.raise_for_status()
    job = response.json()
    job_id = job["id"]
    print(f"   Job ID: {job_id} — waiting for completion...")

    # Poll until done (Sora can take 2–10 mins)
    for attempt in range(60):  # Max 30 minutes
        time.sleep(30)
        status_resp = requests.get(
            f"https://api.openai.com/v1/video/generations/{job_id}",
            headers=headers
        )
        status_resp.raise_for_status()
        data = status_resp.json()
        status = data.get("status")
        print(f"   Status: {status} (attempt {attempt + 1}/60)")

        if status == "succeeded":
            video_url = data["data"][0]["url"]
            break
        elif status == "failed":
            raise RuntimeError(f"Sora generation failed: {data}")
    else:
        raise TimeoutError("Sora generation timed out after 30 minutes.")

    # Download the video
    print("   Downloading video...")
    video_path = "output.mp4"
    video_data = requests.get(video_url)
    with open(video_path, "wb") as f:
        f.write(video_data.content)
    print(f"   ✅ Video saved: {video_path}")
    return video_path

# ─── YOUTUBE UPLOAD ───────────────────────────────────────────────────────────
def upload_to_youtube(video_path: str, prompt: str) -> str:
    """Upload video to YouTube as a Short, return video URL."""

    print("📤 Uploading to YouTube Shorts...")

    # Build credentials from stored refresh token
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )

    youtube = build("youtube", "v3", credentials=creds)

    # Extract a short title from the prompt (first descriptive clause)
    title_raw = prompt.split(".")[2] if len(prompt.split(".")) > 2 else prompt
    title = title_raw.strip()[:90]  # YouTube max title = 100 chars

    body = {
        "snippet": {
            "title": title,
            "description": f"#{title.replace(' ', '')} #YouTubeShorts #AIVideo #Sora\n\n{prompt}",
            "tags": ["shorts", "AI", "Sora", "YouTubeShorts", "AIVideo"],
            "categoryId": "24"  # Entertainment
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
    print(f"   ✅ Posted: {url}")
    return url

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    prompts  = load_prompts()
    progress = load_progress()

    idx = progress["current_index"]
    if idx >= len(prompts):
        print("🎉 All 1000 prompts have been posted! Reset progress.json to start over.")
        exit(0)

    prompt = prompts[idx]
    print(f"\n{'='*60}")
    print(f"🚀 SORA AUTOPOST — Day {idx + 1} of {len(prompts)}")
    print(f"{'='*60}\n")

    # 1. Generate video
    api_key    = os.environ["OPENAI_API_KEY"]
    video_path = generate_video(prompt, api_key)

    # 2. Upload to YouTube
    url = upload_to_youtube(video_path, prompt)

    # 3. Update progress (GitHub Actions will commit this back to the repo)
    progress["current_index"] = idx + 1
    progress["posted_count"]  = progress.get("posted_count", 0) + 1
    progress["history"].append({
        "index": idx,
        "prompt": prompt[:120] + "...",
        "youtube_url": url,
        "posted_at": time.strftime("%Y-%m-%d %Human:%M UTC")
    })
    save_progress(progress)

    print(f"\n✅ Done! Short #{progress['posted_count']} posted.")
    print(f"   {url}\n")
