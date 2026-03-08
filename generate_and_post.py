"""
YouTube Shorts Fun Facts Auto-Poster
======================================
Every day this script:
  1. Uses Claude AI to generate 5 fun facts on a random topic
  2. Fetches a relevant free stock video from Pexels
  3. Generates a male voiceover using gTTS (free)
  4. Overlays animated text on the video using MoviePy
  5. Adds free background music
  6. Uploads the finished Short to YouTube automatically

REQUIREMENTS:
  pip install anthropic requests gTTS moviepy google-auth google-auth-oauthlib
              google-auth-httplib2 google-api-python-client

API KEYS NEEDED (all free tiers):
  - PEXELS_API_KEY      pexels.com/api (free)
  - YouTube OAuth       console.cloud.google.com (free)
"""

import os
import json
import random
import logging
import requests
import textwrap
import argparse
import pickle
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime

from gtts import gTTS
from moviepy import (
    VideoFileClip, AudioFileClip, CompositeVideoClip,
    TextClip, concatenate_audioclips, ColorClip, concatenate_videoclips
)
import moviepy.video.fx as vfx
from moviepy.video.fx import Resize

import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
import googleapiclient.http
from google.auth.transport.requests import Request

# ─────────────────────────────────────────────
# CONFIG — edit these
# ─────────────────────────────────────────────
GOOGLE_API_KEY      = os.environ.get("GOOGLE_API_KEY", "")
PEXELS_API_KEY      = os.environ.get("PEXELS_API_KEY", "")
TOKEN_FILE          = "data/token.pickle"
SECRETS_FILE        = "client_secrets.json"
TRACKER_FILE        = "data/used_topics.json"
OUTPUT_DIR          = "data/videos"
SCOPES              = ["https://www.googleapis.com/auth/youtube.upload",
                       "https://www.googleapis.com/auth/youtube.force-ssl"]

# Video settings
VIDEO_WIDTH         = 1080
VIDEO_HEIGHT        = 1920   # 9:16 vertical for Shorts
VIDEO_DURATION      = 45     # seconds — ideal for Shorts
FONT_SIZE           = 70
FACT_DISPLAY_TIME   = 7      # seconds per fact

# Topics pool — Claude picks randomly from these
TOPICS = [
    "the ocean", "space exploration", "ancient Rome", "football/soccer",
    "the human brain", "wild animals", "World War II", "famous inventions",
    "the Amazon rainforest", "Olympic Games", "sharks", "volcanoes",
    "the Roman Empire", "black holes", "Premier League football",
    "the Great Wall of China", "dinosaurs", "famous artists",
    "the solar system", "world records", "famous scientists",
    "extreme weather", "deep sea creatures", "ancient Egypt",
    "Formula 1 racing", "Mount Everest", "the Amazon river",
    "dogs", "cats", "honey bees", "gold", "diamonds", "chocolate",
    "pizza", "coffee", "bananas", "penguins", "elephants", "lions",
    "the moon", "Mars", "the Sahara desert", "the Arctic", "languages"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("data/automation.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TRACKER
# ─────────────────────────────────────────────
def load_tracker():
    if Path(TRACKER_FILE).exists():
        with open(TRACKER_FILE) as f:
            return json.load(f)
    return {"used_topics": [], "posted": [], "total_posted": 0}

def save_tracker(tracker):
    Path(TRACKER_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_FILE, "w") as f:
        json.dump(tracker, f, indent=2, default=str)

def pick_topic(tracker):
    used = set(tracker["used_topics"])
    available = [t for t in TOPICS if t not in used]
    if not available:
        log.info("All topics used — resetting pool")
        tracker["used_topics"] = []
        available = TOPICS
    topic = random.choice(available)
    return topic


# ─────────────────────────────────────────────
# STEP 1: GENERATE FACTS WITH CLAUDE
# ─────────────────────────────────────────────
def generate_facts(topic: str) -> dict:
    log.info(f"🤖 Generating facts about: {topic}")
    prompt = f"""Generate exactly 5 fascinating, surprising fun facts about {topic}.
    
Return ONLY a JSON object in this exact format, nothing else:
{{
  "topic": "{topic}",
  "title": "5 Wild Facts About {topic.title()}",
  "facts": [
    "Fact one here.",
    "Fact two here.",
    "Fact three here.",
    "Fact four here.",
    "Fact five here."
  ],
  "pexels_search": "single search term for relevant background video"
}}

Rules:
- Each fact must be under 20 words
- Facts must be genuinely surprising and interesting
- pexels_search should be 1-2 words max (e.g. "ocean", "football", "space")
- No markdown, no extra text, just the JSON"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GOOGLE_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    log.info(f"✅ Generated facts: {data['title']}")
    return data


# ─────────────────────────────────────────────
# STEP 2: FETCH STOCK VIDEO FROM PEXELS
# ─────────────────────────────────────────────
def fetch_pexels_video(search_term: str, output_path: str) -> str:
    log.info(f"🎬 Fetching stock video for: {search_term}")
    headers = {"Authorization": PEXELS_API_KEY}
    url = f"https://api.pexels.com/videos/search?query={search_term}&orientation=portrait&size=medium&per_page=10"

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    if not data.get("videos"):
        # Fallback to a generic topic
        url = "https://api.pexels.com/videos/search?query=nature&orientation=portrait&size=medium&per_page=5"
        response = requests.get(url, headers=headers, timeout=30)
        data = response.json()

    videos = data["videos"]
    video = random.choice(videos[:5])  # Pick randomly from top 5

    # Get the HD or SD file
    video_files = sorted(video["video_files"], key=lambda x: x.get("width", 0), reverse=True)
    video_url = None
    for vf in video_files:
        if vf.get("width", 0) >= 720:
            video_url = vf["link"]
            break
    if not video_url:
        video_url = video_files[0]["link"]

    # Download the video
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(video_url, stream=True, timeout=60)
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)

    log.info(f"✅ Downloaded stock video: {output_path}")
    return output_path


# ─────────────────────────────────────────────
# STEP 3: GENERATE VOICEOVER
# ─────────────────────────────────────────────
def generate_voiceover(facts_data: dict, output_path: str) -> str:
    log.info("🎙️ Generating voiceover...")

    # Build the script
    script = f"{facts_data['title']}. "
    for i, fact in enumerate(facts_data["facts"], 1):
        script += f"Fact {i}. {fact} "

    tts = gTTS(text=script, lang="en", tld="co.uk", slow=False)  # UK English = male-sounding
    tts.save(output_path)
    log.info(f"✅ Voiceover saved: {output_path}")
    return output_path


# ─────────────────────────────────────────────
# STEP 4: BUILD THE VIDEO
# ─────────────────────────────────────────────
def build_video(facts_data: dict, video_path: str, audio_path: str, output_path: str) -> str:
    log.info("🎞️ Building video...")

    # Load and prepare background video
    bg = VideoFileClip(video_path)

    # Resize to 9:16 portrait
    bg_ratio = bg.w / bg.h
    target_ratio = VIDEO_WIDTH / VIDEO_HEIGHT

    if bg_ratio > target_ratio:
        bg = bg.resized(height=VIDEO_HEIGHT)
        x_center = bg.w / 2
        bg = bg.with_effects([vfx.Crop(x1=x_center - VIDEO_WIDTH/2, x2=x_center + VIDEO_WIDTH/2)])
    else:
        bg = bg.resized(width=VIDEO_WIDTH)
        y_center = bg.h / 2
        bg = bg.with_effects([vfx.Crop(y1=y_center - VIDEO_HEIGHT/2, y2=y_center + VIDEO_HEIGHT/2)])

    # Loop background video to fill duration
    if bg.duration < VIDEO_DURATION:
        loops = int(VIDEO_DURATION / bg.duration) + 1
        bg = concatenate_videoclips([bg] * loops)
    bg = bg.subclipped(0, VIDEO_DURATION)

    # Darken background for readability
    overlay = ColorClip(size=(VIDEO_WIDTH, VIDEO_HEIGHT), color=(0, 0, 0), duration=VIDEO_DURATION)
    overlay = overlay.with_effects([vfx.MultiplyColor(0.55)])

    # Build text clips
    clips = [bg, overlay]

    # Title
    title_clip = (TextClip(
        facts_data["title"].upper(),
        font_size=65,
        color="white",
        font="DejaVu-Sans-Bold",
        stroke_color="black",
        stroke_width=3,
        method="caption",
        size=(VIDEO_WIDTH - 100, None),
        text_align="center"
    )
    .with_position(("center", 120))
    .with_duration(VIDEO_DURATION))
    clips.append(title_clip)

    # Fact number emoji map
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    # Each fact appears one by one
    for i, fact in enumerate(facts_data["facts"]):
        start_time = 2 + (i * FACT_DISPLAY_TIME)
        duration = FACT_DISPLAY_TIME

        # Fact number
        num_clip = (TextClip(
            text=f"FACT #{i+1}",
            font_size=55,
            color="#FFD700",
            font="DejaVu-Sans-Bold",
            stroke_color="black",
            stroke_width=2,
        )
        .with_position(("center", 650))
        .with_start(start_time)
        .with_duration(duration))

        # Fact text
        wrapped = textwrap.fill(fact, width=28)
        fact_clip = (TextClip(
            wrapped,
            font_size=FONT_SIZE,
            color="white",
            font="DejaVu-Sans-Bold",
            stroke_color="black",
            stroke_width=3,
            method="caption",
            size=(VIDEO_WIDTH - 80, None),
            text_align="center"
        )
        .with_position(("center", 750))
        .with_start(start_time)
        .with_duration(duration))

        clips.extend([num_clip, fact_clip])

    # Outro
    outro_clip = (TextClip(
        text="FOLLOW FOR MORE!",
        font_size=72,
        color="#FFD700",
        font="DejaVu-Sans-Bold",
        stroke_color="black",
        stroke_width=3,
    )
    .with_position(("center", 1600))
    .with_start(VIDEO_DURATION - 4)
    .with_duration(4))
    clips.append(outro_clip)

    # Composite all clips
    final = CompositeVideoClip(clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.with_duration(VIDEO_DURATION)

    # Add voiceover
    voiceover = AudioFileClip(audio_path)
    if voiceover.duration > VIDEO_DURATION:
        voiceover = voiceover.subclipped(0, VIDEO_DURATION)

    final = final.with_audio(voiceover)

    # Export
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="data/temp_audio.m4a",
        remove_temp=True,
        verbose=False,
        logger=None
    )

    log.info(f"✅ Video built: {output_path}")
    return output_path


# ─────────────────────────────────────────────
# STEP 5: UPLOAD TO YOUTUBE
# ─────────────────────────────────────────────
def get_youtube_service():
    creds = None
    if Path(TOKEN_FILE).exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                SECRETS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

    Path(TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)

    return googleapiclient.discovery.build("youtube", "v3", credentials=creds)


def upload_to_youtube(video_path: str, facts_data: dict) -> dict:
    log.info("📤 Uploading to YouTube...")
    youtube = get_youtube_service()

    topic = facts_data["topic"]
    title = f"{facts_data['title']} #Shorts"
    description = (
        f"5 amazing fun facts about {topic}!\n\n"
        + "\n".join([f"✅ {f}" for f in facts_data["facts"]])
        + "\n\n#FunFacts #Shorts #DidYouKnow #Facts"
    )
    tags = ["fun facts", "shorts", "did you know", "facts", topic, "amazing facts"]

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        }
    }

    media = googleapiclient.http.MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024*1024
    )

    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info(f"Upload progress: {int(status.progress() * 100)}%")

    log.info(f"✅ Uploaded! Video ID: {response['id']}")
    return {"success": True, "video_id": response["id"], "title": title}


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
def run_daily():
    log.info("=" * 50)
    log.info("🚀 Starting daily Short generation")
    log.info("=" * 50)

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    tracker = load_tracker()
    topic = pick_topic(tracker)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        # Step 1: Generate facts
        facts_data = generate_facts(topic)

        # Step 2: Fetch stock video
        video_path = f"{OUTPUT_DIR}/bg_{timestamp}.mp4"
        fetch_pexels_video(facts_data["pexels_search"], video_path)

        # Step 3: Generate voiceover
        audio_path = f"{OUTPUT_DIR}/voice_{timestamp}.mp3"
        generate_voiceover(facts_data, audio_path)

        # Step 4: Build video
        output_path = f"{OUTPUT_DIR}/short_{timestamp}.mp4"
        build_video(facts_data, video_path, audio_path, output_path)

        # Step 5: Upload to YouTube
        result = upload_to_youtube(output_path, facts_data)

        # Mark topic as used
        tracker["used_topics"].append(topic)
        tracker["posted"].append({
            "topic": topic,
            "title": facts_data["title"],
            "video_id": result.get("video_id"),
            "posted_at": datetime.now().isoformat()
        })
        tracker["total_posted"] = len(tracker["posted"])
        save_tracker(tracker)

        # Clean up temp files
        for f in [video_path, audio_path]:
            Path(f).unlink(missing_ok=True)

        log.info(f"🎉 Done! Posted: {facts_data['title']}")
        log.info(f"📊 Total posted: {tracker['total_posted']} | Topics remaining: {len(TOPICS) - len(tracker['used_topics'])}")

    except Exception as e:
        log.error(f"❌ Pipeline failed: {e}", exc_info=True)
        raise


def setup():
    log.info("🔧 Running setup...")
    youtube = get_youtube_service()
    response = youtube.channels().list(part="snippet", mine=True).execute()
    if response["items"]:
        name = response["items"][0]["snippet"]["title"]
        log.info(f"✅ Connected to YouTube channel: {name}")
    log.info("✅ Setup complete! Run with --run to test.")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube Fun Facts Shorts Poster")
    parser.add_argument("--setup",    action="store_true", help="First-time setup & auth")
    parser.add_argument("--run",      action="store_true", help="Generate and post one Short now")
    parser.add_argument("--stats",    action="store_true", help="Show posting stats")
    args = parser.parse_args()

    if args.setup:
        setup()
    elif args.run:
        run_daily()
    elif args.stats:
        tracker = load_tracker()
        print(f"\nTotal posted: {tracker['total_posted']}")
        print(f"Topics used:  {len(tracker['used_topics'])}/{len(TOPICS)}")
        if tracker["posted"]:
            print(f"Last post:    {tracker['posted'][-1]['title']}")
    else:
        parser.print_help()
