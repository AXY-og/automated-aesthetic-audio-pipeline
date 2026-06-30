import subprocess
import glob
import os
import shutil
import numpy as np
import pyrubberband as pyrb
import yt_dlp
from pedalboard import Pedalboard, Reverb
from scipy.signal import butter, sosfilt, sosfilt_zi
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
    """
    Slow down audio by resampling — pitch drops naturally with the speed
    reduction, giving the classic vinyl / slowed-and-reverbed sound.

    speed < 1.0 → slower + lower pitch (e.g. 0.85 = 85% speed)
    """
    with AudioFile(input_path) as f:
        audio = f.read(f.frames)  # shape: (channels, samples)
        sr = f.samplerate

    from scipy.signal import resample

    # Number of output samples = original / speed  (more samples = slower)
    n_out = int(audio.shape[1] / speed)
    channels = []
    for ch in range(audio.shape[0]):
        channels.append(resample(audio[ch], n_out).astype(np.float32))
    slowed = np.stack(channels, axis=0)

    with AudioFile(output_path, "w", sr, slowed.shape[0]) as f:
        f.write(slowed)


def _crossover_split(audio, sr, cutoff_hz=180, order=4):
    """
    Split audio into low-pass (below cutoff) and high-pass (above cutoff)
    using a Butterworth crossover filter.
    Returns (low, high) numpy arrays with the same shape as input.
    """
    nyquist = sr / 2.0
    normalized_cutoff = cutoff_hz / nyquist

    # Design Butterworth low-pass and high-pass filters
    sos_low = butter(order, normalized_cutoff, btype='low', output='sos')
    sos_high = butter(order, normalized_cutoff, btype='high', output='sos')

    # Apply filters to each channel
    low = sosfilt(sos_low, audio, axis=-1)
    high = sosfilt(sos_high, audio, axis=-1)

    return low.astype(np.float32), high.astype(np.float32)


def _biquad_peak(sr, center_hz, gain_db, q=0.707):
    """
    Design a biquad peaking EQ filter.
    Returns second-order sections (sos) array.
    """
    A = 10 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * center_hz / sr
    alpha = np.sin(w0) / (2.0 * q)

    b0 = 1.0 + alpha * A
    b1 = -2.0 * np.cos(w0)
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * np.cos(w0)
    a2 = 1.0 - alpha / A

    # Normalize
    sos = np.array([[b0/a0, b1/a0, b2/a0, 1.0, a1/a0, a2/a0]])
    return sos


def _biquad_lowshelf(sr, center_hz, gain_db, q=0.707):
    """
    Design a biquad low-shelf filter.
    Returns second-order sections (sos) array.
    """
    A = 10 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * center_hz / sr
    alpha = np.sin(w0) / (2.0 * q)

    b0 = A * ((A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha)
    b1 = 2 * A * ((A - 1) - (A + 1) * np.cos(w0))
    b2 = A * ((A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)
    a0 = (A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha
    a1 = -2 * ((A - 1) + (A + 1) * np.cos(w0))
    a2 = (A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha

    sos = np.array([[b0/a0, b1/a0, b2/a0, 1.0, a1/a0, a2/a0]])
    return sos


def _bass_boost(audio, sr):
    """
    Dual-band bass boost for the dry bass band:
    - Bell at ~100Hz, +4dB: punch / attack restoration
    - Low-shelf at ~55Hz, +3dB: body / sub weight
    """
    # Bell boost at 100Hz for punch
    sos_bell = _biquad_peak(sr, center_hz=100, gain_db=4.0, q=1.0)
    boosted = sosfilt(sos_bell, audio, axis=-1)

    # Low-shelf boost at 55Hz for sub weight
    sos_shelf = _biquad_lowshelf(sr, center_hz=55, gain_db=3.0, q=0.707)
    boosted = sosfilt(sos_shelf, boosted, axis=-1)

    print(f"  ↳ Bass boost: bell +4dB @ 100Hz, low-shelf +3dB @ 55Hz")
    return boosted.astype(np.float32)


def apply_reverb(input_path, output_path, room_size, damping, wet_level, dry_level):
    """
    Apply reverb with a bass-preserving crossover.

    Instead of feeding the full signal into reverb:
    - Split at ~180Hz crossover
    - Apply reverb only to the high-pass (mids/highs)
    - Mix the untouched low-pass (sub-bass) back in

    This keeps bass dry and punchy while mids/highs get the reverb wash.
    """
    with AudioFile(input_path) as f:
        audio = f.read(f.frames)
        sr = f.samplerate

    # Split signal at crossover frequency
    CROSSOVER_HZ = 180
    low, high = _crossover_split(audio, sr, cutoff_hz=CROSSOVER_HZ)
    print(f"  ↳ Crossover split at {CROSSOVER_HZ}Hz (bass stays dry)")

    # Apply reverb only to the high-pass portion (mids/highs)
    board = Pedalboard([Reverb(
        room_size=room_size,
        damping=damping,
        wet_level=wet_level,
        dry_level=dry_level
    )])

    high_reverbed = board(high, sr)

    # Bass boost on the dry low band before recombining
    low_boosted = _bass_boost(low, sr)

    # Recombine: boosted dry bass + reverbed mids/highs
    effected = low_boosted + high_reverbed

    # Detect peak and soft limit to prevent clipping
    pre_peak = np.max(np.abs(effected))
    print(f"  ↳ Reverb peak amplitude: {pre_peak:.4f}")

    threshold = 0.9
    effected = np.tanh(effected / threshold) * threshold

    post_peak = np.max(np.abs(effected))
    print(f"  ↳ Reverb peak after soft limit: {post_peak:.4f}")

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


RESOLUTION_PROFILES = {
    "1": {"label": "4K (3840x2160) [Default/Recommended]", "width": 3840, "height": 2160, "bitrate": "60M", "maxrate": "75M", "bufsize": "150M"},
    "2": {"label": "1440p (2560x1440)", "width": 2560, "height": 1440, "bitrate": "35M", "maxrate": "45M", "bufsize": "90M"},
    "3": {"label": "1080p (1920x1080)", "width": 1920, "height": 1080, "bitrate": "20M", "maxrate": "25M", "bufsize": "50M"},
}


def combine(image_path, audio_path, output_path, profile, youtube_url=None):
    # Generate 16:9 thumbnail in output/ first so we can use it as the video background!
    thumb_path = os.path.splitext(output_path)[0] + ".png"
    try:
        import thumbnail
        thumbnail.generate_thumbnail(youtube_url, image_path, thumb_path)
        print(f"  ✅ Generated premium 16:9 thumbnail: {os.path.basename(thumb_path)}")
        video_bg = thumb_path
    except Exception as e:
        print(f"  ⚠️ Styled thumbnail generation failed ({e}). Falling back to simple thumbnail...")
        try:
            from PIL import Image
            img = Image.open(image_path)
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
                
            thumb.save(thumb_path, "PNG")
            print(f"  ✅ Saved fallback 16:9 thumbnail to {os.path.basename(thumb_path)}")
            video_bg = thumb_path
        except Exception as e2:
            print(f"  ⚠️ Warning: Failed to generate fallback 16:9 thumbnail: {e2}")
            video_bg = image_path

    # Determine dimensions of the background image to be used for video
    w, h = _get_image_dimensions(video_bg)
    ratio = w / h
    target_ratio = 16.0 / 9.0
    tw = profile["width"]
    th = profile["height"]

    if abs(ratio - target_ratio) < 0.05:
        print(f"  ↳ Scaling background directly to {tw}x{th} (no padding)")
        vf = f"scale={tw}:{th}"
    else:
        print(f"  ↳ Scaling background to {tw}x{th} with black padding")
        vf = (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
              f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:black")

    # Prepend format conversion to handle PNGs with alpha / 16-bit depth
    vf = f"format=yuv420p,{vf}"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", video_bg,
        "-i", audio_path,
        "-vf", vf,
        "-af", "alimiter=level_in=1:level_out=0.9:limit=0.9:attack=5:release=50",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-b:v", profile["bitrate"],
        "-maxrate", profile["maxrate"],
        "-bufsize", profile["bufsize"],
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path
    ]

    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        print(f"\n  ❌ FFmpeg failed (exit {proc.returncode}):")
        print(proc.stderr.decode(errors="replace"))
        proc.check_returncode()  # raise CalledProcessError


def download_youtube_audio(url):
    """Download audio from a YouTube URL as high-quality WAV into INPUT_DIR.

    Returns (filepath, yt_meta) where yt_meta is a dict with keys:
        yt_title, artist, channel, channel_url
    """
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
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "audio")

    yt_meta = {
        "yt_title": title,
        "artist": info.get("artist", "") or info.get("uploader", ""),
        "channel": info.get("channel", "") or info.get("uploader", ""),
        "channel_url": info.get("channel_url", "") or info.get("uploader_url", ""),
    }

    # yt-dlp names the output <title>.wav
    downloaded = os.path.join(INPUT_DIR, f"{title}.wav")
    if os.path.exists(downloaded):
        return downloaded, yt_meta
    # Fallback: find whatever wav just appeared
    return find_file(INPUT_DIR, ["wav"]), yt_meta


def main(skip_effects=False):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    source_url = None

    # ── Audio source ──
    url = input("\nYouTube URL (or press Enter to use local file): ").strip()
    if url:
        source_url = url
        yt_meta = {}
        print("\nDownloading audio from YouTube...")
        audio, yt_meta = download_youtube_audio(url)
        if not audio:
            print("Download failed — no audio file produced.")
            return None
        print(f"  ✅ Downloaded: {os.path.basename(audio)}")
        
        # Auto-generate thumbnail immediately after download
        try:
            import thumbnail
            name = os.path.splitext(os.path.basename(audio))[0]
            thumb_path = os.path.join(OUTPUT_DIR, f"{name}.png")
            # Look for a local image in input/ (e.g. Pinterest image)
            local_image = find_file(INPUT_DIR, ["jpg", "jpeg", "png"])
            
            # Generate the thumbnail (uses YouTube thumbnail as fallback if no local image is present)
            thumbnail.generate_thumbnail(source_url, local_image, thumb_path)
            print(f"  ✅ Automatically generated styled thumbnail: {os.path.basename(thumb_path)}")
        except Exception as e:
            print(f"  ⚠️ Automatic thumbnail generation failed: {e}")
    else:
        audio = find_file(INPUT_DIR, ["mp3", "wav", "flac"])
        if not audio:
            print("No audio file found in input/")
            return None

    used_auto_thumbnail = False
    image = find_file(INPUT_DIR, ["jpg", "jpeg", "png"])
    if not image:
        # Check if we generated a thumbnail that we can use as the video background
        name = os.path.splitext(os.path.basename(audio))[0]
        thumb_path = os.path.join(OUTPUT_DIR, f"{name}.png")
        if os.path.exists(thumb_path):
            print(f"  No Pinterest image found in input/. Using generated thumbnail as video background: {os.path.basename(thumb_path)}")
            image = thumb_path
            used_auto_thumbnail = True
        else:
            print("No image file found in input/")
            return None

    print(f"\nAudio : {os.path.basename(audio)}")
    print(f"Image : {os.path.basename(image)}")

    # ── Interactive 1:1 crop ──
    if not used_auto_thumbnail:
        print("\nOpening image cropper (crop to 1:1)...")
        image = crop_to_square(image)
    else:
        print("\nUsing generated thumbnail directly as video background (skipping cropper).")

    if skip_effects:
        print("\n⏭  Skipping all effects (raw audio will be used)")
        effects = []
        settings = {}
    else:
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
                "wet_level": prompt_float("Wet level 0.0-1.0", 0.25),
                "dry_level": prompt_float("Dry level 0.0-1.0", 0.70),
            }

        if "8d" in effects:
            print("\n[8D]")
            settings["8d"] = {
                "hz": prompt_float("Pan speed Hz", 0.125)
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

    # Prompt for resolution
    print("\nSelect target video resolution:")
    print("  1) 4K (3840x2160) [Default/Recommended]")
    print("  2) 1440p (2560x1440)")
    print("  3) 1080p (1920x1080)")
    res_choice = input("Enter 1, 2 or 3: ").strip()
    if res_choice not in RESOLUTION_PROFILES:
        res_choice = "1"
    profile = RESOLUTION_PROFILES[res_choice]
    print(f"  ↳ Selected: {profile['label']}")

    # Combine
    name = os.path.splitext(os.path.basename(audio))[0]
    output_path = os.path.join(OUTPUT_DIR, f"{name}.mp4")

    print("Combining audio and image...")
    combine(image, current, output_path, profile, youtube_url=source_url)

    shutil.rmtree(TEMP_DIR)
    print(f"\nDone → {output_path}")
    speed_factor = settings.get("slow", {}).get("speed", 1.0)
    yt_meta = locals().get("yt_meta", {})
    return {
        "video_path": output_path,
        "source_url": source_url,
        "speed_factor": speed_factor,
        "effects": effects,
        "yt_meta": yt_meta,
    }


if __name__ == "__main__":
    main()