"""
Xenia Pipeline — Main Entry Point

Combines the audio effects engine (fx.py) with an automated
YouTube upload pipeline.

Usage:
    python pipeline.py
"""

import os
import sys
import re
import json
import urllib.request
import urllib.parse
import html
from datetime import datetime, date
from zoneinfo import ZoneInfo
import fx
from uploader import authenticate, upload_video, sanitize_tags

METADATA_FILE = "upload_metadata.json"


# ── Phase 1: Video Generation ─────────────────────────────────────────

def phase_video_generation():
    """Run the fx.py effects pipeline and return result dict."""
    print("=" * 55)
    print("  PHASE 1 — VIDEO GENERATION")
    print("=" * 55)

    skip = input("\n  Skip all audio effects? (y/n) [default n]: ").strip().lower()
    skip_effects = skip == "y"

    result = fx.main(skip_effects=skip_effects)

    if not result or not os.path.exists(result["video_path"]):
        print("\nVideo generation failed.")
        sys.exit(1)

    print(f"\n✅ Video generated successfully: {result['video_path']}")
    proceed = input("\nDo you want to proceed with uploading to YouTube? (y/n): ").strip().lower()

    if proceed != "y":
        print("Exiting. Your video is ready at:", result["video_path"])
        sys.exit(0)

    return result


# ── Lyrics fetcher ────────────────────────────────────────────────────

def _fetch_lyrics(artist, song):
    """
    Fetch plain (non-timestamped) lyrics from lrclib.net.
    Falls back to manual paste if not found.
    """
    try:
        safe_artist = urllib.parse.quote(artist)
        safe_song = urllib.parse.quote(song)
        url = f"https://lrclib.net/api/get?artist_name={safe_artist}&track_name={safe_song}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "xenia-pipeline (https://github.com/AXY-og/automated-aesthetic-audio-pipeline)"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            lyrics = data.get("plainLyrics", "").strip()
            if lyrics:
                return lyrics
    except Exception:
        pass

    return None


# ── Phase 2: Metadata (automated) ────────────────────────────────────

def phase_metadata(source_url, artist_name="", song_name="", effects=None,
                   artist_link="", original_link=""):
    """Build upload metadata automatically from scraped info."""
    print("\n" + "=" * 55)
    print("  PHASE 2 — UPLOAD METADATA")
    print("=" * 55)

    original_link = original_link or source_url or ""

    if not song_name or not artist_name:
        print("  ⚠️  Song name and artist name are required.")
        sys.exit(1)

    print(f"\n  Song:   {song_name}")
    print(f"  Artist: {artist_name}")
    if artist_link:
        print(f"  Channel: {artist_link}")

    # ── Fetch lyrics ──
    print(f"\n  Searching lyrics for \"{artist_name} - {song_name}\"...")
    lyrics = _fetch_lyrics(artist_name, song_name)
    if lyrics:
        print("  ✅ Lyrics found!")
    else:
        print("  ⚠️  Could not auto-fetch lyrics. Using placeholder.")
        lyrics = "(Lyrics not available)"

    # ── Build effects label ──
    effect_display = {"slow": "slowed", "reverb": "reverbed", "8d": "8d"}
    if effects:
        effects_label = " + ".join(effect_display.get(e, e) for e in effects)
    else:
        effects_label = "slowed + reverbed + 8d"

    # ── Build title ──
    title = f"{song_name} ({effects_label}) | {artist_name}"

    # ── Build description ──
    description = f"""🎧 Please wear headphones for the full 8D experience. Close your eyes and drift.

{song_name} ({effects_label}) | {artist_name}

Support the Original Artist:
Original Song: {artist_name} - {song_name}
Listen here: {original_link}
Follow {artist_name}: {artist_link}

Visuals:
Artwork is not mine. If you are the artist, please DM me for proper credit or removal.

Lyrics:
{lyrics}

Tags:
#{artist_name.replace(' ', '')} #{song_name.replace(' ', '')} #slowedandreverb #8daudio #slowed #lofi #aesthetic #8dmusic #vibes

Disclaimer:
I do not own the music or the artwork used in this video. All rights belong to their respective owners. This video is purely fan-made for entertainment and immersive listening purposes. If any producer, label, or artist has an issue with this upload, please contact me directly at fakexenia123@gmail.com  I will remove it immediately."""

    # ── Build tags list ──
    # Default tags (always present on every video)
    default_tags = [
        "music", "audio", "reverbed", "8d", "slowed",
        "slow", "reverb", "song", "songs", "hot", "sexy",
        "hot audio", "sexy audio", "lofi", "beat", "lofi girl",
        "surround", "sound", "surround sound",
        "track", "chick", "art", "pic", "guitar", "vibes",
        "slowedandreverbed", "slowandreverb", "xenia", "aesthetic",
        "experience",
    ]

    # Per-video dynamic tags
    video_tags = [
        artist_name, song_name,
        f"{artist_name} slowed", f"{song_name} slowed",
        f"{artist_name} {song_name}", f"{artist_name} 8d",
        f"{song_name} slowed reverb",
        "slowed and reverb", "8d audio", "8d music", "headphones",
    ]

    # Merge and deduplicate (preserving order)
    seen = set()
    tags = []
    for t in video_tags + default_tags:
        key = t.lower().strip()
        if key and key not in seen:
            seen.add(key)
            tags.append(t)

    tags = sanitize_tags(tags)

    metadata = {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": "10",         # Music
        "privacy_status": "unlisted",
    }

    # ── Save to file ──
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    # ── Display ──
    print("\n  ┌─────────────────────────────────────────────")
    print(f"  │ Title:          {metadata['title']}")
    desc_lines = description.split("\n")
    print(f"  │ Description:    {desc_lines[0]}")
    print(f"  │                 ... ({len(desc_lines)} lines total)")
    print(f"  │ Tags:           {', '.join(tags[:6])}...")
    print(f"  │ Category ID:    {metadata['category_id']}")
    print(f"  │ Privacy Status: {metadata['privacy_status']}")
    print("  └─────────────────────────────────────────────\n")

    # ── Let user review ──
    choice = input("  Privacy status — (p)ublic / (u)nlisted / p(r)ivate [default unlisted]: ").strip().lower()
    if choice.startswith("p") and not choice.startswith("pr"):
        metadata["privacy_status"] = "public"
    elif choice.startswith("r") or choice.startswith("pr"):
        metadata["privacy_status"] = "private"
    # else stays unlisted

    # ── Schedule ──
    publish_at = _prompt_schedule()
    if publish_at:
        metadata["publish_at"] = publish_at
        # YouTube requires private status for scheduled publishing
        metadata["privacy_status"] = "private"
        print(f"  📅 Scheduled for: {publish_at}")
        print(f"     (privacy auto-set to 'private' — YouTube will publish it at the scheduled time)")

    # Re-save with final settings
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\n  ✅ Metadata ready. Privacy: {metadata['privacy_status']}")
    return metadata


def _prompt_schedule():
    """
    Ask the user if they want to schedule the upload.
    Returns an ISO 8601 UTC datetime string, or None for immediate upload.
    """
    print()
    schedule = input("  Schedule upload? (y/n) [default y]: ").strip().lower()
    if schedule == "n":
        return None

    # ── Pick timezone ──
    print("\n  Pick a timezone for 7:30 PM:")
    print("    1) 🇺🇸 New York (ET)")
    print("    2) 🇮🇳 IST")

    while True:
        tz_choice = input("  Enter 1 or 2: ").strip()
        if tz_choice == "1":
            tz = ZoneInfo("America/New_York")
            tz_label = "New York (ET)"
            break
        elif tz_choice == "2":
            tz = ZoneInfo("Asia/Kolkata")
            tz_label = "IST"
            break
        else:
            print("  ⚠️  Please enter 1 or 2.")

    # ── Pick date ──
    today_str = date.today().isoformat()
    while True:
        date_input = input(f"  Date (YYYY-MM-DD) [default {today_str}]: ").strip()
        if not date_input:
            date_input = today_str

        try:
            chosen_date = date.fromisoformat(date_input)
        except ValueError:
            print(f"  ⚠️  Invalid date '{date_input}'. Use YYYY-MM-DD format.")
            continue

        if chosen_date < date.today():
            print(f"  ⚠️  Date '{date_input}' is in the past. Please pick today or a future date.")
            continue

        break

    # ── Build datetime at 7:30 PM in chosen timezone, convert to UTC ──
    local_dt = datetime(chosen_date.year, chosen_date.month, chosen_date.day,
                        19, 30, 0, tzinfo=tz)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))

    print(f"\n  → {chosen_date.isoformat()} at 7:30 PM {tz_label}")
    print(f"  → UTC: {utc_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.0Z")


# ── Phase 3: YouTube Upload ───────────────────────────────────────────

def phase_upload(video_path, metadata):
    """Authenticate and upload the video to YouTube."""
    print("\n" + "=" * 55)
    print("  PHASE 3 — YOUTUBE UPLOAD")
    print("=" * 55)

    print("\n  Authenticating with YouTube...")
    youtube = authenticate()

    print(f"\n  Uploading: {os.path.basename(video_path)}")
    video_id = upload_video(youtube, video_path, metadata)

    if video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
        print("\n" + "=" * 55)
        print(f"  ✅ Upload complete!")
        print(f"  🔗 {url}")
        print("=" * 55 + "\n")
    else:
        print("\n  ❌ Upload failed. Please check the error above and try again.")
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    result = phase_video_generation()
    video_path = result["video_path"]
    source_url = result.get("source_url", "")
    speed_factor = result.get("speed_factor", 1.0)
    effects = result.get("effects", [])
    yt_meta = result.get("yt_meta", {})

    # ── Parse artist / song from scraped YouTube title ──
    artist_name = yt_meta.get("artist", "")
    song_name = ""
    yt_title = yt_meta.get("yt_title", "")

    if yt_title:
        # Same parsing logic as thumbnail.py — split "Artist - Title"
        if not artist_name and " - " in yt_title:
            parts = yt_title.split(" - ", 1)
            artist_name = parts[0].strip()
            song_name = parts[1].strip()
        elif " - " in yt_title:
            song_name = yt_title.split(" - ", 1)[1].strip()
        else:
            song_name = yt_title

        # Clean common video suffixes
        clean_regex = r'\s*[\(\[][^\]\)]*(official|video|lyric|lyrics|audio|slowed|reverb|8d|music|clip|prod|remix|hd|4k)[^\]\)]*[\)\]]'
        song_name = re.sub(clean_regex, '', song_name, flags=re.IGNORECASE).strip()
        artist_name = re.sub(clean_regex, '', artist_name, flags=re.IGNORECASE).strip()

    channel_url = yt_meta.get("channel_url", "")

    # ── Phase 1.5: Subtitles / Lyrics ─────────────────────────────────────
    print("\n" + "=" * 55)
    print("  PHASE 1.5 — SUBTITLES / LYRICS")
    print("=" * 55)

    if artist_name and song_name:
        print(f"\n  Detected: {artist_name} - {song_name}")

    burn_subs = input("\nDo you want to burn synced subtitles onto the video? (y/n) [default n]: ").strip().lower()
    if burn_subs == "y":
        if not artist_name or not song_name:
            print()
            artist_name = artist_name or input("  Artist name: ").strip()
            song_name = song_name or input("  Song name:   ").strip()

        # Speed factor from Phase 1
        print(f"\n  Detected speed factor from Phase 1: {speed_factor}")
        speed_raw = input(f"    Playback speed factor [default {speed_factor}]: ").strip()
        try:
            chosen_speed = float(speed_raw) if speed_raw else speed_factor
        except ValueError:
            print(f"    Invalid float, using default: {speed_factor}")
            chosen_speed = speed_factor

        import lyrics
        print(f"\n  Searching synced lyrics for \"{artist_name} - {song_name}\"...")
        lyrics_res = lyrics.get_lyrics(artist_name, song_name)
        if lyrics_res and "syncedLyrics" in lyrics_res:
            print("  ✅ Synced lyrics found!")
            # Determine subbed path
            dest_dir = os.path.dirname(video_path)
            dest_base = os.path.splitext(os.path.basename(video_path))[0]
            subbed_video_path = os.path.join(dest_dir, f"{dest_base}_subbed.mp4")

            print("  Burning subtitles onto the video...")
            success = lyrics.burn_subtitles_from_lrc(
                video_path,
                lyrics_res["syncedLyrics"],
                subbed_video_path,
                speed_factor=chosen_speed
            )
            if success and os.path.exists(subbed_video_path):
                print(f"  ✅ Subtitles burned successfully: {subbed_video_path}")
                video_path = subbed_video_path
            else:
                print("  ❌ Subtitle burning failed. Proceeding with clean video.")
        else:
            print("  ❌ Synced lyrics not found. Proceeding with clean video.")

    metadata = phase_metadata(
        source_url,
        artist_name=artist_name,
        song_name=song_name,
        effects=effects,
        artist_link=channel_url,
        original_link=source_url,
    )

    phase_upload(video_path, metadata)


if __name__ == "__main__":
    main()
