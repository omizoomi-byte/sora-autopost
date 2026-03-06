#!/usr/bin/env python3
"""
Trend AutoPost — Daily YouTube Shorts Automation
1. Gets today's #1 trending topic (Google Trends - FREE)
2. Generates smarter Pexels search keywords from related queries
3. Downloads best matching stock clips (Pexels API - FREE)
4. Stitches clips + adds text overlay (ffmpeg - FREE)
5. Posts to YouTube Shorts (YouTube API - FREE)
"""

import json, os, time, subprocess, requests, random
from pytrends.request import TrendReq
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

PROGRESS_FILE = "progress.json"

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"posted_count": 0, "history": []}

def save_progress(p):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(p, f, indent=2)

# ─── STEP 1: GET TRENDING TOPIC + RELATED KEYWORDS ───────────────────────────
def get_trend_and_keywords():
    print("📈 Fetching trending topic and related keywords...")
    try:
        pytrends = TrendReq(hl='en-US', tz=0)

        # Get today's #1 trending topic in UK
        trending = pytrends.trending_searches(pn='united_kingdom')
        topic = trending.iloc[0][0]
        print(f"   Trending topic: {topic}")

        # Get related queries to find better visual search terms
        time.sleep(2)
        pytrends.build_payload([topic], timeframe='now 1-d', geo='GB')
        time.sleep(2)
        related = pytrends.related_queries()

        keywords = [topic]  # always include the raw topic

        # Extract top related queries as extra search terms
        try:
            top_df = related[topic]['top']
            if top_df is not None and len(top_df) > 0:
                extra = top_df['query'].head(4).tolist()
                keywords.extend(extra)
                print(f"   Related keywords: {extra}")
        except Exception:
            pass

        # Also get Google suggestions for more visual terms
        try:
            time.sleep(1)
            suggestions = pytrends.suggestions(keyword=topic)
            sug_titles = [s['title'] for s in suggestions[:3]]
            keywords.extend(sug_titles)
            print(f"   Suggestions: {sug_titles}")
        except Exception:
            pass

        return topic, keywords

    except Exception as e:
        print(f"   Google Trends failed ({e}), using fallback...")
        fallback = random.choice([
            "Technology", "Nature", "Space", "Ocean", "City",
            "Animals", "Sports", "Science", "Weather", "Music"
        ])
        return fallback, [fallback]

# ─── STEP 2: DOWNLOAD BEST MATCHING CLIPS FROM PEXELS ────────────────────────
def download_clips(topic: str, keywords: list, pexels_key: str, num_clips: int = 3):
    print(f"🎬 Searching Pexels for best matching clips...")
    headers = {"Authorization": pexels_key}
    clip_paths = []

    # Try each keyword until we have enough clips
    for keyword in keywords:
        if len(clip_paths) >= num_clips:
            break

        keyword = keyword[:50]  # Pexels has a query length limit
        print(f"   Trying keyword: '{keyword}'...")
        params = {"query": keyword, "per_page": 10, "orientation": "portrait", "size": "medium"}

        try:
            resp = requests.get("https://api.pexels.com/videos/search",
                                headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            videos = resp.json().get("videos", [])
        except Exception as e:
            print(f"   Pexels search failed for '{keyword}': {e}")
            continue

        random.shuffle(videos)
        for video in videos:
            if len(clip_paths) >= num_clips:
                break

            # Prefer portrait video files
            video_files = sorted(video["video_files"],
                                 key=lambda x: x.get("width", 0), reverse=True)
            portrait = [f for f in video_files
                        if f.get("width", 1) < f.get("height", 0)]
            chosen = (portrait[0] if portrait else video_files[0])

            path = f"clip_{len(clip_paths)}.mp4"
            try:
                r = requests.get(chosen["link"], stream=True, timeout=60)
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                clip_paths.append(path)
                print(f"   ✅ Clip {len(clip_paths)} downloaded (keyword: '{keyword}')")
            except Exception as e:
                print(f"   Download failed: {e}")

    # Fallback if still not enough clips
    if not clip_paths:
        print("   Using generic fallback search...")
        params = {"query": "abstract background", "per_page": 5,
                  "orientation": "portrait"}
        resp = requests.get("https://api.pexels.com/videos/search",
                            headers=headers, params=params, timeout=30)
        videos = resp.json().get("videos", [])
        for video in videos[:num_clips]:
            video_files = sorted(video["video_files"],
                                 key=lambda x: x.get("width", 0), reverse=True)
            path = f"clip_{len(clip_paths)}.mp4"
            r = requests.get(video_files[0]["link"], stream=True, timeout=60)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            clip_paths.append(path)

    return clip_paths

# ─── STEP 3: STITCH CLIPS + ADD TEXT OVERLAY ─────────────────────────────────
def create_short(clip_paths: list, topic: str, output_path: str = "output.mp4"):
    print("✂️  Stitching clips with ffmpeg...")

    with open("concat.txt", "w") as f:
        for path in clip_paths:
            f.write(f"file '{path}'\n")

    # Concat clips
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", "concat.txt", "-c", "copy", "combined.mp4"
    ], check=True, capture_output=True)

    # Clean topic text for ffmpeg
    safe_topic = topic.replace("'", "").replace(":", "").replace("\\", "")
    safe_topic = safe_topic[:40]

    # Resize to 9:16, trim to 30s, add text overlay at bottom
    filter_complex = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,"
        f"drawtext=text='🔥 {safe_topic}':fontsize=64:fontcolor=white:"
        "borderw=5:bordercolor=black:"
        "x=(w-text_w)/2:y=h-180:"
        "font=DejaVuSans-Bold[v]"
    )

    subprocess.run([
        "ffmpeg", "-y", "-i", "combined.mp4",
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", "30", "-r", "30",
        output_path
    ], check=True, capture_output=True)

    print(f"   Video ready!")
    return output_path

# ─── STEP 4: UPLOAD TO YOUTUBE ────────────────────────────────────────────────
def upload_to_youtube(video_path: str, topic: str, keywords: list) -> str:
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

    title = f"🔥 {topic} — Trending Now! #Shorts"[:100]
    tags = [topic] + keywords[:5] + ["trending", "shorts", "viral", "today"]
    description = (
        f"#{topic.replace(' ', '')} #Trending #Shorts #viral\n\n"
        f"Today's trending topic: {topic}\n"
        f"Related: {', '.join(keywords[1:4])}"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:15],
            "categoryId": "25"
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

    url = f"https://youtube.com/shorts/{response['id']}"
    print(f"   ✅ Posted: {url}")
    return url

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    progress   = load_progress()
    pexels_key = os.environ["PEXELS_API_KEY"]

    print(f"\n{'='*60}")
    print(f"TREND AUTOPOST — Short #{progress['posted_count'] + 1}")
    print(f"{'='*60}\n")

    topic, keywords = get_trend_and_keywords()
    clip_paths      = download_clips(topic, keywords, pexels_key, num_clips=3)
    video_path      = create_short(clip_paths, topic)
    url             = upload_to_youtube(video_path, topic, keywords)

    progress["posted_count"] = progress.get("posted_count", 0) + 1
    progress["history"].append({
        "topic": topic,
        "keywords": keywords,
        "youtube_url": url,
        "posted_at": time.strftime("%Y-%m-%d %H:%M UTC")
    })
    save_progress(progress)

    print(f"\n✅ Done! Short #{progress['posted_count']} posted: {url}")
    print(f"   Topic: {topic}")
    print(f"   Keywords used: {keywords}\n")
