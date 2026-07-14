#!/usr/bin/env python3
"""
pipeline_headless.py — Headless entry point for Xenia Mobile GitHub Actions workflow.

Loads a job configuration JSON, downloads YouTube audio and Pinterest media,
applies crops, rotation, color filters, processes audio effects, headlessly
generates thumbnails, renders rhythmic motion video, burns subtitles, and uploads to YouTube.
"""

import os
import sys
import json
import re
import glob
import shutil
import urllib.request
import urllib.parse
from PIL import Image, ImageDraw, ImageFont

# Import pipeline modules
import fx
import thumbnail
import motion_bg
import lyrics
import pipeline
from uploader import authenticate, upload_video

# Configurations
INPUT_DIR = "input"
OUTPUT_DIR = "output"
TEMP_DIR = "temp"
RESULT_FILE = os.path.join(OUTPUT_DIR, "result.json")

def cleanup_dirs():
    """Wipe temp and clean up directories."""
    print("🧹 Cleaning input, output, and temp directories...")
    for folder in [INPUT_DIR, OUTPUT_DIR, TEMP_DIR]:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f"  ⚠️ Failed to delete {file_path}: {e}")
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

def burn_text_overlays(img, text_overlays):
    """Draw text overlays directly on the cropped square image."""
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # Look for common system fonts or local assets folder font fallbacks
    common_fonts = [
        "assets/fonts/UnifrakturCook.ttf",
        "assets/fonts/Moontime.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Apple Chancery.ttf"
    ]
    fallback_font_path = None
    for path in common_fonts:
        if os.path.exists(path):
            fallback_font_path = path
            break

    for t in text_overlays:
        text = t.get("text", "")
        if not text:
            continue

        size = max(12, int(t.get("size", 36)))
        pil_font = None
        if fallback_font_path:
            try:
                pil_font = ImageFont.truetype(fallback_font_path, size)
            except Exception:
                pass
        if not pil_font:
            pil_font = ImageFont.load_default()

        # Parse color
        color_hex = t.get("color", "#ffffff").lstrip("#")
        try:
            color_rgb = (int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16))
        except Exception:
            color_rgb = (255, 255, 255)

        x = t.get("x", 0)
        y = t.get("y", 0)

        # Center horizontally if toggled
        anchor = "lt"
        if t.get("center_x", False):
            x = w / 2.0
            anchor = "mt"

        draw.text((int(x), int(y)), text, fill=color_rgb, font=pil_font, anchor=anchor)

def prepare_pinterest_media_headless(pinterest_path, crop_info, text_overlays=None):
    """Rotate, crop, apply color adjustments, and bake text overlays onto center media."""
    print(f"\n[Headless] Cropping and adjusting Pinterest media: {pinterest_path}")
    
    ext = os.path.splitext(pinterest_path)[1].lstrip(".").lower()
    is_video = ext in ["gif", "mp4", "mov", "webm", "avi", "mkv"]
    
    temp_img_path = pinterest_path
    if is_video:
        import subprocess
        color_adj = crop_info.get("color_adjustments", {})
        time_seconds = color_adj.get("selected_frame_time", 0.0)
        
        extracted_path = os.path.join(TEMP_DIR, "_extracted_frame.png")
        if os.path.exists(extracted_path):
            try:
                os.unlink(extracted_path)
            except Exception:
                pass
                
        success = False
        if ext == "gif":
            try:
                gif_img = Image.open(pinterest_path)
                gif_img.seek(0)
                gif_img.convert("RGB").save(extracted_path, "PNG")
                success = True
            except Exception:
                pass
                
        if not success:
            try:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(time_seconds),
                    "-i", pinterest_path,
                    "-frames:v", "1",
                    "-q:v", "2",
                    extracted_path
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                success = True
            except Exception as e:
                print(f"  ⚠️ FFmpeg extraction failed: {e}")
                
        if success and os.path.exists(extracted_path):
            temp_img_path = extracted_path
        else:
            print("  ❌ Could not extract frame from video, falling back to original file.")
            
    img = Image.open(temp_img_path)
    
    # Apply rotation
    rotation = crop_info.get("rotation", 0)
    if rotation % 360 != 0:
        img = img.rotate(-rotation, expand=True)

    # Apply crop box bounds
    x1 = crop_info.get("x1", 0)
    y1 = crop_info.get("y1", 0)
    x2 = crop_info.get("x2", img.width)
    y2 = crop_info.get("y2", img.height)
    
    # Clamp coordinates
    x1 = max(0, min(x1, img.width))
    y1 = max(0, min(y1, img.height))
    x2 = max(x1 + 10, min(x2, img.width))
    y2 = max(y1 + 10, min(y2, img.height))

    cropped = img.crop((x1, y1, x2, y2))

    # Apply preset color filter and manual adjustments
    color_adj = crop_info.get("color_adjustments")
    if color_adj:
        alpha = None
        if cropped.mode == "RGBA":
            r, g, b, alpha = cropped.split()
            cropped = Image.merge("RGB", (r, g, b))

        cropped = motion_bg.apply_color_adjustments_to_frame(cropped, color_adj)

        if alpha is not None:
            cropped = cropped.convert("RGBA")
            cropped.putalpha(alpha)

    # Burn custom text overlays
    if text_overlays:
        burn_text_overlays(cropped, text_overlays)

    if is_video:
        out_path = os.path.join(INPUT_DIR, "_center_first_frame.png")
    else:
        out_path = os.path.join(INPUT_DIR, "pinterest_download.png")

    cropped.save(out_path, "PNG")
    print(f"  ✅ Headless media preparation saved to: {out_path}")
    return out_path

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline_headless.py <config.json>")
        sys.exit(1)

    config_file = sys.argv[1]
    if not os.path.exists(config_file):
        print(f"❌ Error: Config file not found at: {config_file}")
        sys.exit(1)

    with open(config_file, "r") as f:
        config = json.load(f)

    job_id = config.get("job_id", "headless-job")
    print(f"\n=======================================================")
    print(f"  STARTING Headless Xenia Render (Job ID: {job_id})")
    print(f"=======================================================\n")

    # Clean directories before running
    cleanup_dirs()

    # Write initial result file to indicate job started
    result_data = {
        "job_id": job_id,
        "status": "failed",
        "error": "Job timed out or failed before writing final output.",
        "youtube_video_id": None,
        "youtube_url": None,
        "thumbnail_note": None
    }
    with open(RESULT_FILE, "w") as f:
        json.dump(result_data, f, indent=2)

    try:
        # 1. Download YouTube audio
        youtube_url = config["youtube_url"]
        print(f"\n[Step 1] Downloading YouTube audio: {youtube_url}")
        audio_path, yt_meta = fx.download_youtube_audio(youtube_url)
        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError("YouTube audio download failed.")

        # Confirm artist/song names
        artist_name = config.get("confirmed_artist") or yt_meta.get("artist") or "Unknown Artist"
        song_name = config.get("confirmed_song") or yt_meta.get("yt_title") or "Unknown Song"
        print(f"  ↳ Verified Artist: {artist_name}")
        print(f"  ↳ Verified Song  : {song_name}")

        # 2. Download Pinterest media
        pinterest_url = config.get("pinterest_url")
        if not pinterest_url:
            raise ValueError("Pinterest Pin URL is required in headless payload.")
        
        print(f"\n[Step 2] Resolving Pinterest media: {pinterest_url}")
        pinterest_raw = fx.download_pinterest_media(pinterest_url)
        if not pinterest_raw or not os.path.exists(pinterest_raw):
            raise FileNotFoundError("Pinterest media download failed.")

        # 3. Apply crops, rotations, color grades, and text overlays
        crop_info = config.get("crop_info", {})
        text_overlays = config.get("text_overlays", [])
        center_image_path = prepare_pinterest_media_headless(pinterest_raw, crop_info, text_overlays)

        # Write .crop.json next to center image
        crop_json_path = center_image_path + ".crop.json"
        with open(crop_json_path, "w") as f:
            json.dump(crop_info, f, indent=2)

        # 4. Audio processing chain
        print("\n[Step 3] Processing audio effects...")
        current_audio = audio_path
        effects = config.get("effects", [])
        settings = config.get("effect_settings", {})
        step = 0

        if "slow" in effects:
            print("  Applying slow...")
            out = os.path.join(TEMP_DIR, f"{step}_slow.wav")
            fx.apply_slow(current_audio, out, **settings.get("slow", {"speed": 0.85}))
            current_audio, step = out, step + 1

        if "reverb" in effects:
            print("  Applying reverb...")
            out = os.path.join(TEMP_DIR, f"{step}_reverb.wav")
            fx.apply_reverb(current_audio, out, **settings.get("reverb", {
                "room_size": 0.75, "damping": 0.5, "wet_level": 0.25, "dry_level": 0.7
            }))
            current_audio, step = out, step + 1

        if "8d" in effects:
            print("  Applying 8D...")
            out = os.path.join(TEMP_DIR, f"{step}_8d.wav")
            fx.apply_8d(current_audio, out, **settings.get("8d", {"hz": 0.125}))
            current_audio, step = out, step + 1

        # 5. Generate Styled Thumbnail
        print("\n[Step 4] Headlessly generating styled thumbnail...")
        audio_filename = os.path.splitext(os.path.basename(audio_path))[0]
        thumb_output_path = os.path.join(OUTPUT_DIR, f"{audio_filename}.png")
        
        # Clean effects display string
        parts = []
        if "slow" in effects:
            parts.append("slowed")
        if "reverb" in effects:
            parts.append("reverb")
        if "8d" in effects:
            parts.append("8d")
        effects_str = " + ".join(parts)

        # Parse text overlay layout settings
        # Look for centered text if layout stacked is requested
        layout_choice = "1"
        if len(text_overlays) > 0:
            # If user has custom text overlays, layout choices might be custom
            pass

        thumbnail.generate_thumbnail(
            youtube_url=youtube_url,
            pinterest_image_path=center_image_path,
            output_path=thumb_output_path,
            title=song_name,
            artist=artist_name,
            effects=effects_str,
            use_glow=True,       # default true
            use_vignette=True,   # default true
            custom_text_rgb=None,
            font_choice="1",     # default Moontime
            layout_choice=layout_choice,
            crop_info=crop_info,
            interactive=False
        )

        # 6. Render final video
        print("\n[Step 5] Rendering output video...")
        video_output_path = os.path.join(OUTPUT_DIR, f"{audio_filename}.mp4")
        
        # Load resolution profile
        res_profile_key = config.get("resolution", "4k").lower()
        res_mapping = {"4k": "1", "1440p": "2", "1080p": "3"}
        res_choice = res_mapping.get(res_profile_key, "1")
        profile = fx.RESOLUTION_PROFILES[res_choice]
        print(f"  ↳ Resolution profile: {profile['label']}")

        use_motion = config.get("use_motion_background", True)
        if use_motion:
            # Rhythmic audio-reactive motion render
            motion_bg.render_motion_video(
                audio_path=current_audio,
                output_path=video_output_path,
                profile=profile,
                config_path=thumb_output_path + ".config.json"
            )
        else:
            # Static image render
            fx.combine(
                image_path=center_image_path,
                audio_path=current_audio,
                output_path=video_output_path,
                profile=profile,
                youtube_url=youtube_url,
                existing_thumb=thumb_output_path,
                use_motion=False
            )

        # 7. Sync & Burn Subtitles
        burn_subtitles = config.get("burn_subtitles", False)
        if burn_subtitles:
            print("\n[Step 6] Searching lyrics and burning subtitles...")
            lyrics_res = lyrics.get_lyrics(artist_name, song_name)
            if lyrics_res and "syncedLyrics" in lyrics_res:
                print("  ✅ Synced lyrics found!")
                subbed_path = os.path.join(OUTPUT_DIR, f"{audio_filename}_subbed.mp4")
                speed_factor = settings.get("slow", {}).get("speed", 1.0)
                success = lyrics.burn_subtitles_from_lrc(
                    video_path=video_output_path,
                    lrc_text=lyrics_res["syncedLyrics"],
                    output_path=subbed_path,
                    speed_factor=speed_factor
                )
                if success and os.path.exists(subbed_path):
                    print(f"  ✅ Subtitles burned successfully: {subbed_path}")
                    video_output_path = subbed_path
                else:
                    print("  ❌ Subtitle burning failed. Falling back to clean video.")
            else:
                print("  ❌ Synced lyrics not found. Skipping subtitle burn.")

        # 8. Setup Metadata
        print("\n[Step 7] Generating YouTube Upload Metadata...")
        privacy_status = config.get("privacy_status", "unlisted")
        publish_at = config.get("publish_at_utc") # ISO 8601 UTC date string
        
        metadata = pipeline.phase_metadata(
            source_url=youtube_url,
            artist_name=artist_name,
            song_name=song_name,
            effects=effects,
            artist_link=yt_meta.get("channel_url", ""),
            original_link=youtube_url,
            privacy_status=privacy_status,
            publish_at=publish_at,
            interactive=False
        )

        # 9. Upload to YouTube
        print("\n[Step 8] Starting automated YouTube upload...")
        youtube = authenticate()
        video_id = upload_video(youtube, video_output_path, metadata)

        if video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"\n🎉 SUCCESS! Upload complete.")
            print(f"🔗 YouTube Video Link: {url}")
            
            # Write success result JSON
            result_data = {
                "job_id": job_id,
                "status": "success",
                "error": None,
                "youtube_video_id": video_id,
                "youtube_url": url,
                "thumbnail_note": "uploaded automatically via uploader.upload_thumbnail"
            }
        else:
            raise RuntimeError("YouTube API video upload failed.")

    except Exception as e:
        import traceback
        err_msg = str(e)
        print(f"\n❌ Headless render job failed with error: {err_msg}")
        traceback.print_exc()
        result_data = {
            "job_id": job_id,
            "status": "failed",
            "error": err_msg,
            "youtube_video_id": None,
            "youtube_url": None,
            "thumbnail_note": None
        }

    # Write final result JSON
    with open(RESULT_FILE, "w") as f:
        json.dump(result_data, f, indent=2)
    print("\nResult JSON saved to output/result.json.")

if __name__ == "__main__":
    main()
