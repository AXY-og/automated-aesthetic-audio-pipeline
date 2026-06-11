import subprocess
import glob
import os
import shutil
import yt_dlp
from pedalboard import Pedalboard, Reverb
from pedalboard.io import AudioFile
from cropper import crop_to_square

INPUT_DIR = "input"
OUTPUT_DIR = "output"
TEMP_DIR = "temp"


def find_file(directory, extensions):
    for ext in extensions:
        matches = glob.glob(os.path.join(directory, f"*.{ext}"))
        if matches:
            return matches[0]
    return None


def prompt_float(label, default):
    val = input(f"  {label} [default {default}]: ").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        print(f"  Invalid input, using default ({default})")
        return default


def apply_slow(input_path, output_path, speed):
    rate = int(44100 * speed)
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"asetrate={rate},aresample=44100",
        output_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_reverb(input_path, output_path, room_size, damping, wet_level, dry_level):
    with AudioFile(input_path) as f:
        audio = f.read(f.frames)
        sr = f.samplerate

    board = Pedalboard([Reverb(
        room_size=room_size,
        damping=damping,
        wet_level=wet_level,
        dry_level=dry_level
    )])

    effected = board(audio, sr)

    with AudioFile(output_path, "w", sr, effected.shape[0]) as f:
        f.write(effected)


def apply_8d(input_path, output_path, hz):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"apulsator=mode=sine:hz={hz}",
        output_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _get_image_dimensions(image_path):
    """Use ffprobe to get the width and height of an image."""
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        image_path
    ], capture_output=True, text=True, check=True)
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


def combine(image_path, audio_path, output_path):
    print("  ↳ Scaling to 1920×1080 with black padding")
    vf = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
          "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black")

    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-vf", vf,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def download_youtube_audio(url):
    """Download audio from a YouTube URL as high-quality WAV into INPUT_DIR."""
    os.makedirs(INPUT_DIR, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(INPUT_DIR, "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
        "quiet": False,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "audio")
    # yt-dlp names the output <title>.wav
    downloaded = os.path.join(INPUT_DIR, f"{title}.wav")
    if os.path.exists(downloaded):
        return downloaded
    # Fallback: find whatever wav just appeared
    return find_file(INPUT_DIR, ["wav"])


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    source_url = None

    # ── Audio source ──
    url = input("\nYouTube URL (or press Enter to use local file): ").strip()
    if url:
        source_url = url
        print("\nDownloading audio from YouTube...")
        audio = download_youtube_audio(url)
        if not audio:
            print("Download failed — no audio file produced.")
            return None
        print(f"  ✅ Downloaded: {os.path.basename(audio)}")
    else:
        audio = find_file(INPUT_DIR, ["mp3", "wav", "flac"])
        if not audio:
            print("No audio file found in input/")
            return None

    image = find_file(INPUT_DIR, ["jpg", "jpeg", "png"])
    if not image:
        print("No image file found in input/")
        return None

    print(f"\nAudio : {os.path.basename(audio)}")
    print(f"Image : {os.path.basename(image)}")

    # ── Interactive 1:1 crop ──
    print("\nOpening image cropper (crop to 1:1)...")
    image = crop_to_square(image)

    print("\nAvailable effects: slow, reverb, 8d")
    raw = input("Which effects? (comma separated, Enter for all): ").strip().lower()
    effects = ["slow", "reverb", "8d"] if not raw else [e.strip() for e in raw.split(",")]

    print(f"\nApplying: {', '.join(effects)}")

    settings = {}

    if "slow" in effects:
        print("\n[Slow]")
        settings["slow"] = {
            "speed": prompt_float("Speed 0.0-1.0", 0.85)
        }

    if "reverb" in effects:
        print("\n[Reverb]")
        settings["reverb"] = {
            "room_size": prompt_float("Room size 0.0-1.0", 0.75),
            "damping":   prompt_float("Damping   0.0-1.0", 0.50),
            "wet_level": prompt_float("Wet level 0.0-1.0", 0.35),
            "dry_level": prompt_float("Dry level 0.0-1.0", 0.70),
        }

    if "8d" in effects:
        print("\n[8D]")
        settings["8d"] = {
            "hz": prompt_float("Pan speed Hz", 0.10)
        }

    # Process chain
    current = audio
    step = 0

    if "slow" in effects:
        print("\nApplying slow...")
        out = os.path.join(TEMP_DIR, f"{step}_slow.wav")
        apply_slow(current, out, **settings["slow"])
        current, step = out, step + 1

    if "reverb" in effects:
        print("Applying reverb...")
        out = os.path.join(TEMP_DIR, f"{step}_reverb.wav")
        apply_reverb(current, out, **settings["reverb"])
        current, step = out, step + 1

    if "8d" in effects:
        print("Applying 8D...")
        out = os.path.join(TEMP_DIR, f"{step}_8d.wav")
        apply_8d(current, out, **settings["8d"])
        current, step = out, step + 1

    # Combine
    name = os.path.splitext(os.path.basename(audio))[0]
    output_path = os.path.join(OUTPUT_DIR, f"{name}.mp4")

    print("Combining audio and image...")
    combine(image, current, output_path)

    shutil.rmtree(TEMP_DIR)
    print(f"\nDone → {output_path}")
    return {"video_path": output_path, "source_url": source_url}


if __name__ == "__main__":
    main()