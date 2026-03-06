#!/usr/bin/env python3
"""
Trend AutoPost — Daily YouTube Shorts Automation
Pipeline:
  1. Fetch today's #1 trending topic (Google Trends via pytrends - FREE)
  2. Download matching stock video clips (Pexels API - FREE)
  3. Stitch clips + add text overlay (ffmpeg - FREE)
  4. Upload to YouTube Shorts (YouTube API - FREE)
Runs on GitHub Actions daily. No credits. No cookies. No maintenance.
"""

import json
import os
import time
import subprocess
import requests
import random
from pytrends.request import TrendReq
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

PROGRESS_FILE = "progress.json"

# ─── PROGRESS ─────────────────────────────────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"posted_count": 0, "history": []}

def save_progress(p):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(p, f, indent=2)

# ─── STEP 1: GET TRENDING TOPIC ───────────────────────────────────────────────
def get_trending_topic() -> str:
    print("📈 Fetching today's trending topic from Google Trends...")
    try:
        pytrends = TrendReq(hl='en-US', tz=0)
        trending = pytrends.trending_searches(pn='united_kingdom')
        topic = trending.iloc[0][0]
        print(f"   Trending topic: {topic}")
        return topic
    except Exception as e:
        print(f"   Google Trends failed ({e}), using fallback...")
        fallbacks = [
            "Technology", "Nature", "Space", "Ocean", "Mountains",
            "Cities", "Animals", "Food", "Sports", "Music"
        ]
        topic = random.choice(fallbacks)
        print(f"   Using fallback topic: {topic}")
        return topic

# ─── STEP 2: DOWNLOAD STOCK VIDEO CLIPS FROM PEXELS ──────────────────────────
def download_clips(topic: str, pexels_key: str, num_clips: int = 3) -> list:
    print(f"🎬 Searching Pexels for '{topic}' video clips...")

    headers = {"Authorization": pexels_key}
    params  = {"query": topic, "per_page": 15, "orientation": "portrait", "size": "medium"}

    resp = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    videos = data.get("videos", [])
    if not videos:
        # Try a more generic search if topic returns nothing
        print(f"   No results for '{topic}', trying generic search...")
        params["query"] = "nature landscape"
        resp = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params)
        data = resp.json()
        videos = data.get("videos", [])

    if not videos:
        raise RuntimeError("No videos found on Pexels.")

    # Shuffle and pick clips
    random.shuffle(videos)
    selected = videos[:num_clips]

    clip_paths = []
    for i, video in enumerate(selected):
        # Get the HD video file
        video_files = sorted(video["video_files"], key=lambda x: x.get("width", 0), reverse=True)
        # Prefer portrait files
        portrait = [f for f in video_files if f.get("width", 0) < f.get("height", 1)]
        file_url = (portrait[0] if portrait else video_files[0])["link"]

        path = f"clip_{i}.mp4"
        print(f"   Downloading clip {i+1}/{num_clips}...")
        r = requests.get(file_url, stream=True, timeout=60)
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        clip_paths.append(path)
        print(f"   Saved: {path}")

    return clip_paths

# ─── STEP 3: STITCH CLIPS + ADD TEXT OVERLAY ──────────────────────────────────
def create_short(clip_paths: list, topic: str, output_path: str = "output.mp4"):
    print("✂️  Stitching clips and adding text overlay with ffmpeg...")

    # Write concat list
    with open("concat.txt", "w") as f:
        for path in clip_paths:
            f.write(f"file '{path}'\n")

    # Step A: Concat all clips
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", "concat.txt",
        "-c", "copy", "combined.mp4"
    ], check=True, capture_output=True)

    # Step B: Resize to 9:16 (1080x1920), trim to 30s, add text overlay
    # Clean topic text for ffmpeg (escape special chars)
    safe_topic = topic.replace("'", "").replace(":", "").replace("\\", "")[:40]

    filter_complex = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "setsar=1,"
        f"drawtext=text='{safe_topic}':fontsize=72:fontcolor=white:"
        "borderw=4:bordercolor=black:"
        "x=(w-text_w)/2:y=h-200:"
        "font=DejaVuSans-Bold[v]"
    )

    subprocess.run([
        "ffmpeg", "-y",
        "-i", "combined.mp4",
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", "30",          # Max 30 seconds for a Short
        "-r", "30",          # 30fps
        output_path
    ], check=True, capture_output=True)

    print(f"   Video ready: {output_path}")
    return output_path

# ─── STEP 4: UPLOAD TO YOUTUBE ────────────────────────────────────────────────
def upload_to_youtube(video_path: str, topic: str) -> str:
    print("📤 Uploading to YouTube Shorts...")

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )

    youtube = build("youtube", "v3", credentials=creds)

    title = f"{topic} — Trending Now 🔥 #Shorts"[:100]
    description = (
        f"#{topic.replace(' ', '')} #Trending #Shorts #viral\n\n"
        f"Today's trending topic: {topic}"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [topic, "trending", "shorts", "viral", "today"],
            "categoryId": "25"   # News & Politics — good for trending content
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
    progress    = load_progress()
    pexels_key  = os.environ["PEXELS_API_KEY"]

    print(f"\n{'='*60}")
    print(f"TREND AUTOPOST — Short #{progress['posted_count'] + 1}")
    print(f"{'='*60}\n")

    # 1. Get trending topic
    topic = get_trending_topic()

    # 2. Download clips
    clip_paths = download_clips(topic, pexels_key, num_clips=3)

    # 3. Create Short
    video_path = create_short(clip_paths, topic)

    # 4. Upload to YouTube
    url = upload_to_youtube(video_path, topic)

    # 5. Save progress
    progress["posted_count"] = progress.get("posted_count", 0) + 1
    progress["history"].append({
        "topic": topic,
        "youtube_url": url,
        "posted_at": time.strftime("%Y-%m-%d %H:%M UTC")
    })
    save_progress(progress)

    print(f"\n✅ Done! Short #{progress['posted_count']} posted: {url}")
    print(f"   Topic: {topic}\n")
