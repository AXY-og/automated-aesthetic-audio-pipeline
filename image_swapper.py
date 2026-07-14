#!/usr/bin/env python3
"""
image_swapper.py — Swap the background image of an existing video in output/.

Allows selecting a video, choosing a new image, cropping it to the video's aspect ratio,
and generating a new video by combining the cropped image with the original audio/subtitles.
"""

import os
import sys
import shutil
import subprocess
from PIL import Image
import cropper
import lyrics

INPUT_DIR = "input"
OUTPUT_DIR = "output"
TEMP_DIR = "temp"


def get_encoding_profile(width):
    if width >= 3840:
        return {"bitrate": "60M", "maxrate": "75M", "bufsize": "150M"}
    elif width >= 2560:
        return {"bitrate": "35M", "maxrate": "45M", "bufsize": "90M"}
    else:
        return {"bitrate": "20M", "maxrate": "25M", "bufsize": "50M"}


def select_video():
    """Scan output/ for .mp4 files and let the user select one."""
    if not os.path.exists(OUTPUT_DIR):
        print(f"❌ Error: Output directory '{OUTPUT_DIR}' does not exist.")
        return None

    videos = [f for f in os.listdir(OUTPUT_DIR) if f.lower().endswith(".mp4")]
    videos.sort()

    if not videos:
        print(f"⚠️ No video files (.mp4) found in '{OUTPUT_DIR}/'.")
        return None

    print("\nAvailable videos in output/:")
    for idx, f in enumerate(videos, 1):
        print(f"  {idx}) {f}")

    while True:
        try:
            sel = input(f"Select a video file (1-{len(videos)}): ").strip()
            if not sel:
                continue
            sel_idx = int(sel) - 1
            if 0 <= sel_idx < len(videos):
                return os.path.join(OUTPUT_DIR, videos[sel_idx])
            print(f"Please enter a number between 1 and {len(videos)}.")
        except ValueError:
            print("Invalid input. Please enter a valid number.")


def get_video_dimensions(video_path):
    """Get the width and height of a video file using ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            video_path
        ], capture_output=True, text=True, check=True)
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except Exception as e:
        print(f"❌ Error reading video dimensions via ffprobe: {e}")
        raise


def select_image():
    """Scan input/ for image files and let the user select one, or input a path manually."""
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)

    images = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    images.sort()

    if images:
        print("\nAvailable images in input/:")
        for idx, f in enumerate(images, 1):
            print(f"  {idx}) {f}")
        print("  0) Enter path manually")

        while True:
            try:
                sel = input(f"Select an image (0-{len(images)}): ").strip()
                if not sel:
                    continue
                sel_idx = int(sel)
                if sel_idx == 0:
                    break
                if 1 <= sel_idx <= len(images):
                    return os.path.join(INPUT_DIR, images[sel_idx - 1])
                print(f"Please enter a number between 0 and {len(images)}.")
            except ValueError:
                print("Invalid input. Please enter a valid number.")

    # Manual path prompt if selected 0 or no images found
    while True:
        path = input("\nEnter exact path to image manually: ").strip()
        if not path:
            continue
        if os.path.exists(path):
            return path
        print(f"❌ File '{path}' does not exist. Please check the path and try again.")


def main():
    print("=======================================================")
    print("  IMAGE SWAPPER UTILITY")
    print("=======================================================")

    # 1. Select video
    video_path = select_video()
    if not video_path:
        print("Exiting.")
        sys.exit(0)

    # 2. Get video dimensions
    try:
        video_w, video_h = get_video_dimensions(video_path)
    except Exception:
        print("Could not retrieve video dimensions. Exiting.")
        sys.exit(1)

    video_ratio = video_w / video_h
    # Determine suggested aspect ratio mode
    if abs(video_ratio - (16/9)) < abs(video_ratio - 1.0):
        suggested_mode = "16:9"
    else:
        suggested_mode = "1:1"

    print(f"\nOriginal video resolution: {video_w}x{video_h} (Aspect Ratio: {video_ratio:.3f})")
    print(f"Suggested crop mode: {suggested_mode}")

    # Ask user to confirm aspect ratio mode
    crop_mode_input = input(f"Confirm crop mode - (1) 1:1, (2) 16:9 [default {suggested_mode}]: ").strip()
    if crop_mode_input == "1":
        crop_mode = "1:1"
    elif crop_mode_input == "2":
        crop_mode = "16:9"
    else:
        crop_mode = suggested_mode

    # 3. Select image
    image_path = select_image()

    # Create temp directory
    os.makedirs(TEMP_DIR, exist_ok=True)

    # 4. Copy image to temp and open cropper
    ext = os.path.splitext(image_path)[1]
    temp_crop_path = os.path.join(TEMP_DIR, f"swap_crop{ext}")
    shutil.copy2(image_path, temp_crop_path)

    print(f"\nOpening image cropper for '{os.path.basename(image_path)}'...")
    cropped_image = cropper.crop_image(temp_crop_path, mode=crop_mode)

    if not os.path.exists(cropped_image):
        print("❌ Error: Cropped image not found. Cropping may have been aborted.")
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        sys.exit(1)

    # 5. Determine scaling and padding filter
    with Image.open(cropped_image) as img:
        img_w, img_h = img.size

    img_ratio = img_w / img_h

    if abs(img_ratio - video_ratio) < 0.05:
        print(f"  ↳ Scaling directly to {video_w}x{video_h} (no padding)")
        vf = f"scale={video_w}:{video_h}"
    else:
        print(f"  ↳ Scaling to {video_w}x{video_h} with black padding")
        vf = (f"scale={video_w}:{video_h}:force_original_aspect_ratio=decrease,"
              f"pad={video_w}:{video_h}:(ow-iw)/2:(oh-ih)/2:black")

    # 6. Run FFmpeg to construct the new video
    temp_video_path = os.path.join(TEMP_DIR, "swapped_temp.mp4")
    if os.path.exists(temp_video_path):
        os.remove(temp_video_path)

    print("\nRunning FFmpeg to swap image stream...")
    # FFmpeg command structure:
    # - loop 1: loop the image input
    # - shortest: finish when the shortest input (the original video's audio) ends
    # - map 0:v:0: take the video from the looped image input
    # - map 1:a:0: take the audio from the original video
    # - map 1:s?: copy optional soft subtitles if they exist
    # - c:a copy: copy audio directly without re-encoding
    # - c:s copy: copy soft subtitles directly
    profile = get_encoding_profile(video_w)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", cropped_image,
        "-i", video_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map", "1:s?",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-b:v", profile["bitrate"],
        "-maxrate", profile["maxrate"],
        "-bufsize", profile["bufsize"],
        "-c:a", "copy",
        "-c:s", "copy",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-shortest",
        temp_video_path
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ FFmpeg failed with exit code {e.returncode}")
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        sys.exit(1)

    # 7. Optionally re-burn subtitles
    burn_subs = False
    suggest_burn = "_subbed" in video_path.lower()
    
    prompt_msg = "\nWould you like to burn/re-add subtitles onto this new video? (y/n)"
    if suggest_burn:
        prompt_msg += " [default y]: "
    else:
        prompt_msg += " [default n]: "
        
    choice = input(prompt_msg).strip().lower()
    if choice == "y" or (choice == "" and suggest_burn):
        burn_subs = True

    if burn_subs:
        # Extract base name without extension and parse artist/title suggestions
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        if base_name.lower().endswith("_subbed"):
            base_name = base_name[:-7]
            
        suggested_artist = ""
        suggested_title = ""
        if " - " in base_name:
            parts = base_name.split(" - ", 1)
            suggested_artist = parts[0].strip()
            suggested_title = parts[1].strip()
            
            # Clean up suggested title from common pipeline suffixes
            for suffix in ["(official lyric video)", "(official video)", "(lyrics)", "(lyric video)", "(slowed + reverbed + 8d)"]:
                if suggested_title.lower().endswith(suffix):
                    suggested_title = suggested_title[:-len(suffix)].strip()

        print("\n--- Subtitle Setup ---")
        artist = input(f"Artist name [default: {suggested_artist}]: ").strip()
        if not artist:
            artist = suggested_artist

        title = input(f"Song title [default: {suggested_title}]: ").strip()
        if not title:
            title = suggested_title

        try:
            speed_raw = input("Playback speed factor (e.g. 0.85 for slowed, Enter for 0.85): ").strip()
            speed_factor = float(speed_raw) if speed_raw else 0.85
        except ValueError:
            print("Invalid float entered. Defaulting to 0.85.")
            speed_factor = 0.85

        print(f"\nSearching lyrics for \"{artist} - {title}\"...")
        lyrics_res = lyrics.get_lyrics(artist, title)
        if lyrics_res and "syncedLyrics" in lyrics_res:
            print("✅ Synced lyrics found!")
            subbed_temp_path = os.path.join(TEMP_DIR, "subbed_temp.mp4")
            
            print("Burning subtitles onto the video...")
            success = lyrics.burn_subtitles_from_lrc(
                temp_video_path,
                lyrics_res["syncedLyrics"],
                subbed_temp_path,
                speed_factor=speed_factor
            )
            if success and os.path.exists(subbed_temp_path):
                os.remove(temp_video_path)
                shutil.move(subbed_temp_path, temp_video_path)
                print("✅ Subtitles burned successfully!")
                
                # Maintain naming convention: append _subbed if not already present
                if not video_path.lower().endswith("_subbed.mp4"):
                    dest_dir = os.path.dirname(video_path)
                    dest_base = os.path.splitext(os.path.basename(video_path))[0]
                    video_path = os.path.join(dest_dir, f"{dest_base}_subbed.mp4")
            else:
                print("❌ Failed to burn subtitles. Proceeding with image swap only.")
        else:
            print("❌ Could not find synced lyrics. Proceeding with image swap only.")

    # 8. Replace the original video atomically
    if os.path.exists(temp_video_path):
        shutil.move(temp_video_path, video_path)
        print(f"\n✅ Successfully swapped background image of '{os.path.basename(video_path)}'!")

        # Save/update 16:9 thumbnail
        try:
            import thumbnail
            thumb_path = os.path.splitext(video_path)[0] + ".png"
            thumbnail.generate_thumbnail(None, cropped_image, thumb_path)
            print(f"✅ Saved premium 16:9 thumbnail to {os.path.basename(thumb_path)}")
        except Exception as e:
            print(f"⚠️ Styled thumbnail generation failed ({e}). Falling back to simple thumbnail...")
            try:
                img = Image.open(cropped_image)
                img_w, img_h = img.size
                img_ratio = img_w / img_h
                
                target_w, target_h = 1920, 1080
                target_ratio = 16.0 / 9.0
                if abs(img_ratio - target_ratio) < 0.05:
                    thumb = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                else:
                    scale = min(target_w / img_w, target_h / img_h)
                    new_w = int(img_w * scale)
                    new_h = int(img_h * scale)
                    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    
                    thumb = Image.new("RGB", (target_w, target_h), (0, 0, 0))
                    x = (target_w - new_w) // 2
                    y = (target_h - new_h) // 2
                    thumb.paste(resized, (x, y))
                    
                thumb_path = os.path.splitext(video_path)[0] + ".png"
                thumb.save(thumb_path, "PNG")
                print(f"✅ Saved fallback 16:9 thumbnail to {os.path.basename(thumb_path)}")
            except Exception as e2:
                print(f"⚠️ Warning: Failed to generate fallback 16:9 thumbnail: {e2}")

    # Clean up
    shutil.rmtree(TEMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
