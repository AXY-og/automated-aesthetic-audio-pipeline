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


def play_notification_sound():
    """Play a short system alert sound on macOS, falling back to a terminal bell."""
    try:
        import subprocess
        if sys.platform == "darwin":
            sound_path = "/System/Library/Sounds/Glass.aiff"
            if os.path.exists(sound_path):
                subprocess.Popen(["afplay", sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["afplay", "/System/Library/Sounds/Ping.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print("\a", end="", flush=True)
    except Exception:
        print("\a", end="", flush=True)


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
    play_notification_sound()
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
        print()
        if not artist_name:
            artist_name = input("  Enter Artist name: ").strip()
        if not song_name:
            song_name = input("  Enter Song name:   ").strip()

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
    if effects is not None:
        if effects:
            effects_label = " + ".join(effect_display.get(e, e) for e in effects)
        else:
            effects_label = "clean"
    else:
        effects_label = "slowed + reverbed + 8d"

    # ── Determine if 8D was applied ──
    has_8d = (effects is None) or ("8d" in effects)

    # ── Build title ──
    title = f"{song_name} ({effects_label}) | {artist_name}"

    # ── Build description ──
    desc_intro = "🎧 Please wear headphones for the full 8D experience. Close your eyes and drift." if has_8d else "Close your eyes and drift."
    desc_tags = (f"#{artist_name.replace(' ', '')} #{song_name.replace(' ', '')} #slowedandreverb #8daudio #slowed #lofi #aesthetic #8dmusic #vibes" 
                 if has_8d else 
                 f"#{artist_name.replace(' ', '')} #{song_name.replace(' ', '')} #slowedandreverb #slowed #lofi #aesthetic #vibes")

    description = f"""{desc_intro}

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
{desc_tags}

Disclaimer:
I do not own the music or the artwork used in this video. All rights belong to their respective owners. This video is purely fan-made for entertainment and immersive listening purposes. If any producer, label, or artist has an issue with this upload, please contact me directly at fakexenia123@gmail.com  I will remove it immediately."""

    # ── Build tags list ──
    # Default tags (always present on every video)
    default_tags = [
        "music", "audio", "reverbed", "slowed",
        "slow", "reverb", "song", "songs", "hot", "sexy",
        "hot audio", "sexy audio", "lofi", "beat", "lofi girl",
        "surround", "sound", "surround sound",
        "track", "chick", "art", "pic", "guitar", "vibes",
        "slowedandreverbed", "slowandreverb", "xenia", "aesthetic",
    ]
    if has_8d:
        default_tags.extend(["8d", "experience"])

    # Per-video dynamic tags
    video_tags = [
        artist_name, song_name,
        f"{artist_name} slowed", f"{song_name} slowed",
        f"{artist_name} {song_name}",
        f"{song_name} slowed reverb",
        "slowed and reverb",
    ]
    if has_8d:
        video_tags.extend([
            f"{artist_name} 8d",
            "8d audio", "8d music", "headphones",
        ])

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
        "effects": effects,
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

    # ── Pick time ──
    default_time = "19:30"
    while True:
        time_input = input(f"  Time (HH:MM in 24h format) [default {default_time}]: ").strip()
        if not time_input:
            time_input = default_time

        try:
            parts = time_input.split(":")
            if len(parts) != 2:
                raise ValueError()
            hour = int(parts[0])
            minute = int(parts[1])
            if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                raise ValueError()
            break
        except ValueError:
            print(f"  ⚠️  Invalid time '{time_input}'. Use HH:MM in 24-hour format (e.g. 19:30).")

    # ── Build datetime in chosen timezone, convert to UTC ──
    local_dt = datetime(chosen_date.year, chosen_date.month, chosen_date.day,
                        hour, minute, 0, tzinfo=tz)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))

    print(f"\n  → {chosen_date.isoformat()} at {time_input} {tz_label}")
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

        # Clean up input and output directories
        print("🧹 Cleaning up input and output directories...")
        import shutil
        for folder in ["input", "output"]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    try:
                        if os.path.isfile(file_path) or os.path.islink(file_path):
                            os.unlink(file_path)
                            print(f"  Deleted file: {filename}")
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                            print(f"  Deleted folder: {filename}")
                    except Exception as e:
                        print(f"  ⚠️ Failed to delete {file_path}: {e}")
        print("  ✅ Cleanup complete.\n")
    else:
        print("\n  ❌ Upload failed. Please check the error above and try again.")
        sys.exit(1)


def run_batch_pipeline():
    print("\n=======================================================")
    print("  XENIA BATCH QUEUE GENERATOR")
    print("=======================================================")
    
    try:
        num_songs = int(input("How many songs would you like to queue? ").strip())
    except ValueError:
        print("Invalid number. Exiting.")
        return

    if num_songs <= 0:
        print("Number of songs must be greater than 0. Exiting.")
        return

    queued_tasks = []

    for i in range(1, num_songs + 1):
        print(f"\n" + "=" * 55)
        print(f"  CONFIGURING SONG {i} OF {num_songs}")
        print("=" * 55)

        # 1. Run interactive configuration
        import fx
        task_config = fx.main(interactive_only=True)
        if not task_config:
            print(f"❌ Configuration for song {i} failed. Exiting.")
            return

        # 2. Isolate files by renaming/moving them to prevent collisions
        import shutil
        import json
        
        batch_audio = os.path.join("input", f"batch_{i}_audio.wav")
        batch_image = os.path.join("input", f"batch_{i}_image.png")
        batch_thumb = os.path.join("output", f"batch_{i}_audio.png")
        
        print(f"\n  Isolating assets for batch item {i}...")
        
        # Move raw downloaded audio
        if os.path.exists(task_config["audio_path"]):
            shutil.move(task_config["audio_path"], batch_audio)
        else:
            print(f"  ⚠️ Audio file not found at: {task_config['audio_path']}")
            
        # Move cropped square image
        if os.path.exists(task_config["image_path"]):
            shutil.move(task_config["image_path"], batch_image)
        else:
            print(f"  ⚠️ Cropped image file not found at: {task_config['image_path']}")
            
        # Move generated styled thumbnail
        if os.path.exists(task_config["thumbnail_path"]):
            shutil.move(task_config["thumbnail_path"], batch_thumb)
        else:
            print(f"  ⚠️ Thumbnail file not found at: {task_config['thumbnail_path']}")

        # Move config.json and overlay.png if they exist
        old_config = task_config["thumbnail_path"] + ".config.json"
        new_config = batch_thumb + ".config.json"
        if os.path.exists(old_config):
            shutil.move(old_config, new_config)
            
        old_overlay = task_config["thumbnail_path"] + ".overlay.png"
        new_overlay = batch_thumb + ".overlay.png"
        if os.path.exists(old_overlay):
            shutil.move(old_overlay, new_overlay)

        # Update the configuration file contents to point to new paths
        if os.path.exists(new_config):
            try:
                with open(new_config, "r") as f:
                    cfg_data = json.load(f)
                cfg_data["center_image"] = os.path.abspath(batch_image)
                cfg_data["overlay_image"] = os.path.abspath(new_overlay)
                with open(new_config, "w") as f:
                    json.dump(cfg_data, f, indent=2)
            except Exception as e:
                print(f"  ⚠️ Failed to update configuration JSON paths: {e}")

        # Update task paths
        task_config["audio_path"] = batch_audio
        task_config["image_path"] = batch_image
        task_config["thumbnail_path"] = batch_thumb

        # Clean up any leftover temp files in input directory (like download leftovers)
        # to ensure the input folder is clean for the next song's Pinterest image
        for filename in os.listdir("input"):
            file_path = os.path.join("input", filename)
            if os.path.isfile(file_path) and not filename.startswith("batch_"):
                try:
                    os.unlink(file_path)
                except Exception:
                    pass

        # 3. Parse details for subtitles & metadata
        yt_meta = task_config["yt_meta"]
        if "confirmed_artist" in yt_meta and "confirmed_song" in yt_meta:
            artist_name = yt_meta["confirmed_artist"]
            song_name = yt_meta["confirmed_song"]
        else:
            artist_name = yt_meta.get("artist", "")
            song_name = ""
            yt_title = yt_meta.get("yt_title", "")
            if yt_title:
                if not artist_name and " - " in yt_title:
                    parts = yt_title.split(" - ", 1)
                    artist_name = parts[0].strip()
                    song_name = parts[1].strip()
                elif " - " in yt_title:
                    song_name = yt_title.split(" - ", 1)[1].strip()
                else:
                    song_name = yt_title
                # Clean video suffixes
                clean_regex = r'\s*[\(\[][^\]\)]*(official|video|lyric|lyrics|audio|slowed|reverb|8d|music|clip|prod|remix|hd|4k)[^\]\)]*[\)\]]'
                song_name = re.sub(clean_regex, '', song_name, flags=re.IGNORECASE).strip()
                artist_name = re.sub(clean_regex, '', artist_name, flags=re.IGNORECASE).strip()
                from thumbnail import strip_features
                artist_name = strip_features(artist_name)

        # Prompt for subtitles choice
        print("\n  Subtitles / Synced Lyrics Configuration:")
        burn_subs_choice = input("  Do you want to burn synced subtitles onto the video? (y/n) [default n]: ").strip().lower()
        burn_subs = burn_subs_choice == "y"
        
        chosen_speed = 1.0
        if burn_subs:
            speed_factor = task_config.get("settings", {}).get("slow", {}).get("speed", 1.0)
            print(f"  ↳ Detected speed factor: {speed_factor}")
            speed_raw = input(f"  Playback speed factor [default {speed_factor}]: ").strip()
            try:
                chosen_speed = float(speed_raw) if speed_raw else speed_factor
            except ValueError:
                chosen_speed = speed_factor

        # 4. Phase metadata setup
        print("\n  YouTube Upload Metadata Configuration:")
        metadata = phase_metadata(
            task_config["source_url"],
            artist_name=artist_name,
            song_name=song_name,
            effects=task_config["effects"],
            artist_link=yt_meta.get("channel_url", ""),
            original_link=task_config["source_url"],
        )

        task_config["burn_subs"] = burn_subs
        task_config["chosen_speed"] = chosen_speed
        task_config["artist_name"] = artist_name
        task_config["song_name"] = song_name
        task_config["metadata"] = metadata

        queued_tasks.append(task_config)
        print(f"✅ Configured Song {i} successfully and added to queue.")

    # Loop to process the configured queue
    print("\n" + "=" * 55)
    print("  STARTING BATCH PROCESSING LOOP")
    print("=" * 55 + "\n")

    for idx, task in enumerate(queued_tasks, 1):
        artist_name = task["artist_name"]
        song_name = task["song_name"]
        
        print(f"\n" + "─" * 55)
        print(f"  PROCESSING TASK {idx} OF {num_songs}: {artist_name} - {song_name}")
        print("─" * 55)

        try:
            # 1. Heavy video generation
            import fx
            gen_res = fx.execute_task(task)
            video_path = gen_res["video_path"]

            # 2. Burn subtitles if enabled
            if task["burn_subs"]:
                import lyrics
                print(f"\n  Searching synced lyrics for \"{artist_name} - {song_name}\"...")
                lyrics_res = lyrics.get_lyrics(artist_name, song_name)
                if lyrics_res and "syncedLyrics" in lyrics_res:
                    print("  ✅ Synced lyrics found!")
                    dest_dir = os.path.dirname(video_path)
                    dest_base = os.path.splitext(os.path.basename(video_path))[0]
                    subbed_video_path = os.path.join(dest_dir, f"{dest_base}_subbed.mp4")

                    print("  Burning subtitles onto the video...")
                    success = lyrics.burn_subtitles_from_lrc(
                        video_path,
                        lyrics_res["syncedLyrics"],
                        subbed_video_path,
                        speed_factor=task["chosen_speed"]
                    )
                    if success and os.path.exists(subbed_video_path):
                        print(f"  ✅ Subtitles burned successfully: {subbed_video_path}")
                        video_path = subbed_video_path
                    else:
                        print("  ❌ Subtitle burning failed. Proceeding with clean video.")
                else:
                    print("  ❌ Synced lyrics not found. Proceeding with clean video.")

            # 3. Upload video
            from uploader import authenticate, upload_video
            youtube = authenticate()
            video_id = upload_video(youtube, video_path, task["metadata"])

            if video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
                print("\n" + "=" * 55)
                print(f"  ✅ Task {idx} Upload complete!")
                print(f"  🔗 {url}")
                print("=" * 55 + "\n")
            else:
                print(f"\n  ❌ Task {idx} upload failed.")

        except Exception as e:
            print(f"\n  ❌ Task {idx} failed with error: {e}")

        # 4. Immediate cleanup of files for this index to keep disk space minimal
        print(f"🧹 Cleaning up assets for task {idx}...")
        batch_prefix = f"batch_{idx}_"
        for folder in ["input", "output"]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    if filename.startswith(batch_prefix):
                        file_path = os.path.join(folder, filename)
                        try:
                            if os.path.isfile(file_path) or os.path.islink(file_path):
                                os.unlink(file_path)
                            elif os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                        except Exception as e:
                            print(f"  ⚠️ Failed to delete {file_path}: {e}")

    print("\n=======================================================")
    print("  ALL BATCH TASKS COMPLETED SUCCESSFULLY!")
    print("=======================================================\n")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("=======================================================")
    print("  XENIA AUTOMATED PIPELINE")
    print("=======================================================")
    print("Select pipeline mode:")
    print("  1) Single song generator [Default]")
    print("  2) Batch queue generator")
    mode = input("Enter 1 or 2 [default 1]: ").strip()
    
    if mode == "2":
        run_batch_pipeline()
    else:
        # Original single song flow
        result = phase_video_generation()
        video_path = result["video_path"]
        source_url = result.get("source_url", "")
        speed_factor = result.get("speed_factor", 1.0)
        effects = result.get("effects", [])
        yt_meta = result.get("yt_meta", {})

        # ── Parse artist / song from scraped YouTube title ──
        if "confirmed_artist" in yt_meta and "confirmed_song" in yt_meta:
            artist_name = yt_meta["confirmed_artist"]
            song_name = yt_meta["confirmed_song"]
        else:
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

                # Strip featured artists — keep only the primary artist
                from thumbnail import strip_features
                artist_name = strip_features(artist_name)

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
