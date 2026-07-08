import subprocess
import glob
import os
import shutil
import re
import numpy as np
import json
import pyrubberband as pyrb
import yt_dlp
from pedalboard import Pedalboard, Reverb
from scipy.signal import butter, sosfilt, sosfilt_zi
from pedalboard.io import AudioFile
from cropper import crop_to_square

def open_file(filepath):
    import platform
    if platform.system() == "Darwin":
        try:
            subprocess.Popen(["open", filepath])
            print(f"  ↳ Auto-opened: {os.path.basename(filepath)}")
        except Exception as e:
            print(f"  ⚠️ Warning: Could not auto-open test video: {e}")


def download_pinterest_media(url):
    """
    Download a Pinterest image or video from a pin URL.
    Supports pin.it short links and standard pinterest.com/pin/ URLs.
    Downloads the high-resolution original image/video into INPUT_DIR.
    """
    import urllib.request
    import urllib.parse
    import json
    
    print(f"\n[Pinterest] Downloading media from URL: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    # Resolve redirect if pin.it
    if "pin.it" in url or "/pin/" not in url:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                url = response.geturl()
                print(f"  ↳ Resolved short URL to: {url}")
        except Exception as e:
            print(f"  ⚠️ Redirect resolution failed: {e}")
            
    # Download HTML
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  ❌ Failed to download Pinterest page HTML: {e}")
        return None

    # Search for all application/json script tags
    script_matches = re.finditer(r'<script([^>]*)>(.*?)</script>', html, re.DOTALL)
    
    image_urls = set()
    video_urls = set()
    
    def find_urls_recursive(val):
        if isinstance(val, str):
            if "i.pinimg.com/originals/" in val:
                image_urls.add(val)
            elif ".mp4" in val or "/videos/" in val or "/v/" in val:
                if val.startswith("http"):
                    video_urls.add(val)
        elif isinstance(val, dict):
            for v in val.values():
                find_urls_recursive(v)
        elif isinstance(val, list):
            for v in val:
                find_urls_recursive(v)

    # Try to load and parse JSON from script tags
    for m in script_matches:
        attrs = m.group(1)
        content = m.group(2)
        if "application/json" in attrs or "initialReduxState" in content or "__PWS_INITIAL_PROPS__" in attrs:
            try:
                data = json.loads(content)
                find_urls_recursive(data)
            except Exception:
                pass

    # If JSON parsing did not find any media, use regex fallback on raw HTML
    if not image_urls and not video_urls:
        originals = re.findall(r'https://i\.pinimg\.com/originals/[a-zA-Z0-9_/.-]+', html)
        for orig in originals:
            image_urls.add(orig)
            
        mp4s = re.findall(r'https?://[a-zA-Z0-9_/.-]+\.mp4[a-zA-Z0-9_/?=.-]*', html)
        for mp4 in mp4s:
            video_urls.add(mp4)

    # Resolve selection
    chosen_url = None
    is_video = False
    
    if video_urls:
        # Prioritize 720p or mp4 video files
        sorted_videos = sorted(list(video_urls), key=lambda x: ("720p" in x or "720" in x or "v720p" in x), reverse=True)
        chosen_url = sorted_videos[0]
        is_video = True
        print(f"  ↳ Selected video URL: {chosen_url}")
    elif image_urls:
        chosen_url = list(image_urls)[0]
        print(f"  ↳ Selected original image URL: {chosen_url}")
        
    if not chosen_url:
        print("  ❌ No images or videos could be extracted from Pinterest page.")
        return None

    # Download file
    ext = os.path.splitext(urllib.parse.urlparse(chosen_url).path)[1]
    if not ext:
        ext = ".mp4" if is_video else ".png"
    
    # Save into INPUT_DIR
    filepath = os.path.join(INPUT_DIR, f"pinterest_download{ext}")
    
    # Remove existing download if any
    if os.path.exists(filepath):
        try:
            os.unlink(filepath)
        except Exception:
            pass
            
    req = urllib.request.Request(chosen_url, headers=headers)
    try:
        print(f"  ↳ Downloading to {filepath}...")
        with urllib.request.urlopen(req, timeout=30) as response, open(filepath, 'wb') as out_file:
            out_file.write(response.read())
        print(f"  ✅ Download complete: {os.path.basename(filepath)}")
        return filepath
    except Exception as e:
        print(f"  ❌ Failed to download file: {e}")
        return None


INPUT_DIR = "input"
OUTPUT_DIR = "output"
TEMP_DIR = "temp"


def find_file(directory, extensions):
    for ext in extensions:
        matches = glob.glob(os.path.join(directory, f"*.{ext}"))
        if matches:
            return matches[0]
    return None


STORED_LINKS_PATH = "stored_links.json"

def load_stored_links():
    if os.path.exists(STORED_LINKS_PATH):
        try:
            with open(STORED_LINKS_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️ Error loading stored links: {e}")
    return {"youtube_url": "", "pinterest_url": ""}

def save_stored_link(key, value):
    links = load_stored_links()
    links[key] = value
    try:
        dir_name = os.path.dirname(STORED_LINKS_PATH)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(STORED_LINKS_PATH, "w") as f:
            json.dump(links, f, indent=2)
    except Exception as e:
        print(f"  ⚠️ Error saving stored links: {e}")


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


def combine(image_path, audio_path, output_path, profile, youtube_url=None, existing_thumb=None, use_motion=None):
    # Use the pre-generated thumbnail if available (avoids double generation)
    thumb_path = os.path.splitext(output_path)[0] + ".png"

    # Prompt for rhythmic background motion if not pre-configured
    if use_motion is None:
        use_motion_choice = input("  Use rhythmic background motion? (y/n) [default y]: ").strip().lower()
        use_motion = use_motion_choice != "n"
        
    if use_motion:
        config_path = (existing_thumb + ".config.json") if existing_thumb else (thumb_path + ".config.json")
        if os.path.exists(config_path):
            try:
                import motion_bg
                motion_bg.render_motion_video(audio_path, output_path, profile, config_path)
                return
            except Exception as e:
                print(f"  ⚠️ Motion background rendering failed ({e}). Falling back to static video...")
        else:
            print("  ⚠️ Thumbnail configuration file missing. Falling back to static video...")

    if existing_thumb and os.path.exists(existing_thumb):
        # Thumbnail was already generated earlier — just use it
        video_bg = existing_thumb
        # Copy to expected thumb_path location if different
        if os.path.abspath(existing_thumb) != os.path.abspath(thumb_path):
            import shutil as _shutil
            _shutil.copy2(existing_thumb, thumb_path)
        print(f"  ✅ Using pre-generated thumbnail: {os.path.basename(video_bg)}")
    elif os.path.exists(thumb_path):
        video_bg = thumb_path
        print(f"  ✅ Using existing thumbnail: {os.path.basename(thumb_path)}")
    else:
        # No pre-generated thumbnail — create a simple scaled fallback
        print("  ⚠️ No pre-generated thumbnail found. Creating simple fallback...")
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


def render_with_tweaking_loop(image_path, audio_path, output_path, profile, source_url, 
                              existing_thumb_path, use_motion, test_mode, title, artist,
                              raw_audio_path=None, effects=None, settings=None):
    """
    Unified rendering function that always renders a 15-second test video first,
    opens it automatically for review, and lets the user tweak background/filters in the GUI
    and audio effects in the console before generating the final output.
    """
    import os
    import json
    
    # Enforce test output path first
    base_no_ext = os.path.splitext(output_path)[0]
    test_output_path = base_no_ext + "_test.mp4"
    
    # Check if the existing_thumb exists, otherwise determine thumb_base
    thumb_base = existing_thumb_path if existing_thumb_path else (base_no_ext + ".png")
    
    print("\n" + "=" * 55)
    if test_mode == "y":
        print(f"  Generating test video: {os.path.basename(test_output_path)}")
        combine(image_path, audio_path, test_output_path, profile, youtube_url=source_url,
                existing_thumb=thumb_base if os.path.exists(thumb_base) else None, use_motion=use_motion)
    else:
        print(f"  Generating 15-second test snippet for review first...")
        combine(image_path, audio_path, test_output_path, profile, youtube_url=source_url,
                existing_thumb=thumb_base if os.path.exists(thumb_base) else None, use_motion=use_motion)

    # Auto-open the test video
    open_file(test_output_path)
    
    # Loop for user feedback and tweaking
    while True:
        print("\n" + "=" * 55)
        print("  TEST VIDEO REVIEW & TWEAKING")
        print("=" * 55)
        print("Select an option:")
        print("  1) Satisfied / Continue [Default]")
        print("  2) Tweak colors, background, or text in cropper GUI")
        print("  3) Re-play the test video")
        if raw_audio_path:
            print("  4) Tweak audio effects (slow, reverb, 8d settings)")
            
        choice = input("Enter 1, 2, 3, or 4 [default 1]: ").strip()
        
        if not choice or choice == "1":
            break
        elif choice == "3":
            print(f"  Re-playing test video: {os.path.basename(test_output_path)}")
            open_file(test_output_path)
            continue
        elif choice == "2":
            # Determine config.json path
            config_path = thumb_base + ".config.json"
            if not os.path.exists(config_path):
                print("  ⚠️ Configuration file not found. Cannot tweak settings.")
                continue
                
            try:
                with open(config_path, "r") as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f"  ⚠️ Failed to read config JSON: {e}")
                continue
                
            crop_target = cfg["center_image"]
            
            # Restore the original uncropped image from backup if it exists
            backup_path = crop_target + ".original_backup.png"
            if os.path.exists(backup_path):
                import shutil
                shutil.copy2(backup_path, crop_target)
                print(f"  ↳ Restored original uncropped image for tweaking: {os.path.basename(crop_target)}")
            
            print(f"\n  Opening cropper GUI on: {os.path.basename(crop_target)}")
            
            # Open cropper on target image
            from cropper import crop_to_square
            crop_to_square(crop_target)
            
            # Regenerate thumbnail using original image/video path
            import thumbnail
            orig_media = cfg.get("center_video") or cfg["center_image"]
            print("\n  Regenerating styled thumbnail overlays with updated tweaks...")
            thumbnail.generate_thumbnail(
                source_url,
                orig_media,
                thumb_base,
                title=title,
                artist=artist
            )
            
            # Re-render 15s test video with updated configuration
            print("\n  Re-rendering 15-second test video with updated tweaks...")
            combine(image_path, audio_path, test_output_path, profile, youtube_url=source_url,
                    existing_thumb=thumb_base if os.path.exists(thumb_base) else None, use_motion=use_motion)
            
            # Auto-open the updated test video
            open_file(test_output_path)
            
        elif choice == "4" and raw_audio_path:
            print("\nAvailable effects: s: slow, r: reverb, 8: 8d")
            raw = input("Which effects? (e.g. sr8, rs, or Enter for all): ").strip().lower()
            if not raw:
                effects = ["slow", "reverb", "8d"]
            else:
                effects = []
                if "s" in raw:
                    effects.append("slow")
                if "r" in raw:
                    effects.append("reverb")
                if "8" in raw:
                    effects.append("8d")

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

            # Run processing chain
            print("\nRe-processing audio effects...")
            current_tweak = raw_audio_path
            step_tweak = 100
            
            # Clean up old tweaked files if any
            import glob
            for f in glob.glob(os.path.join(TEMP_DIR, "tweak_*.wav")):
                try:
                    os.unlink(f)
                except Exception:
                    pass

            if "slow" in effects:
                print("Applying slow...")
                out = os.path.join(TEMP_DIR, f"tweak_{step_tweak}_slow.wav")
                apply_slow(current_tweak, out, **settings["slow"])
                current_tweak, step_tweak = out, step_tweak + 1

            if "reverb" in effects:
                print("Applying reverb...")
                out = os.path.join(TEMP_DIR, f"tweak_{step_tweak}_reverb.wav")
                apply_reverb(current_tweak, out, **settings["reverb"])
                current_tweak, step_tweak = out, step_tweak + 1

            if "8d" in effects:
                print("Applying 8D...")
                out = os.path.join(TEMP_DIR, f"tweak_{step_tweak}_8d.wav")
                apply_8d(current_tweak, out, **settings["8d"])
                current_tweak, step_tweak = out, step_tweak + 1

            # Update audio_path variable for final rendering
            audio_path = current_tweak
            print("  ✅ Audio effects re-processed successfully!")

            # Re-render 15s test video with updated configuration
            print("\n  Re-rendering 15-second test video with updated tweaks...")
            combine(image_path, audio_path, test_output_path, profile, youtube_url=source_url,
                    existing_thumb=thumb_base if os.path.exists(thumb_base) else None, use_motion=use_motion)
            
            # Auto-open the updated test video
            open_file(test_output_path)

    # Proceed to finalize final output path
    if test_mode == "y":
        final_path = test_output_path
    else:
        print(f"\n  Proceeding to render the full-length video: {os.path.basename(output_path)}")
        combine(image_path, audio_path, output_path, profile, youtube_url=source_url,
                existing_thumb=thumb_base if os.path.exists(thumb_base) else None, use_motion=use_motion)
        final_path = output_path
                
    # Clean up temporary test video file to keep directory clean
    if os.path.exists(test_output_path) and test_mode != "y":
        try:
            os.unlink(test_output_path)
        except Exception:
            pass
            
    return {
        "output_path": final_path,
        "audio_path": audio_path,
        "effects": effects,
        "settings": settings
    }


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
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios"]
            }
        },
        "nocheckcertificate": True,
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


def main(skip_effects=False, interactive_only=False):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    source_url = None

    # ── Audio source ──
    stored = load_stored_links()
    stored_yt = stored.get("youtube_url", "")
    
    prompt = "\nYouTube URL "
    if stored_yt:
        prompt += f"[Default: {stored_yt}] "
    prompt += "(type 'local' to use local file, or press Enter for default): "
    
    url = input(prompt).strip()
    
    if not url:  # Enter
        if stored_yt:
            url = stored_yt
            print(f"  ↳ Using stored YouTube URL: {url}")
        else:
            url = ""
            print("  ↳ No stored URL. Using local file...")
    elif url.lower() == "local":
        url = ""
        print("  ↳ Bypassing stored link to use local file.")
    else:
        # New link entered
        save_stored_link("youtube_url", url)
        print(f"  ↳ Stored new YouTube URL: {url}")

    if url:
        source_url = url
        yt_meta = {}
        print("\nDownloading audio from YouTube...")
        audio, yt_meta = download_youtube_audio(url)
        if not audio:
            print("Download failed — no audio file produced.")
            return None
        print(f"  ✅ Downloaded: {os.path.basename(audio)}")
        
        # Parse artist and song name from yt_meta
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

            from thumbnail import strip_features
            artist_name = strip_features(artist_name)

        if artist_name and song_name:
            print(f"\n  Detected details from video:")
            print(f"    Artist: {artist_name}")
            print(f"    Song:   {song_name}")
            use_detected = input("\n  Use these details? (y/n) [default y]: ").strip().lower()
            if use_detected == "n":
                print()
                artist_name = ""
                while not artist_name:
                    artist_name = input("  Enter artist name: ").strip()
                song_name = ""
                while not song_name:
                    song_name = input("  Enter song/track name: ").strip()
        else:
            print("\n  ⚠️ Could not detect artist/song metadata from video.")
            artist_name = ""
            while not artist_name:
                artist_name = input("  Enter artist name: ").strip()
            song_name = ""
            while not song_name:
                song_name = input("  Enter song/track name: ").strip()

        # Update yt_meta with confirmed details
        yt_meta["confirmed_artist"] = artist_name
        yt_meta["confirmed_song"] = song_name
    else:
        audio = find_file(INPUT_DIR, ["mp3", "wav", "flac"])
        if not audio:
            print("No audio file found in input/")
            return None
        print(f"\n  No YouTube URL provided. Local file detected: {os.path.basename(audio)}")
        artist_name = ""
        while not artist_name:
            artist_name = input("  Enter artist name: ").strip()
        song_name = ""
        while not song_name:
            song_name = input("  Enter song/track name: ").strip()
            
        yt_meta = {
            "confirmed_artist": artist_name,
            "confirmed_song": song_name
        }

    # ── Resolve and crop center media ──
    stored = load_stored_links()
    stored_pin = stored.get("pinterest_url", "")
    
    prompt = "\nPinterest Pin URL "
    if stored_pin:
        prompt += f"[Default: {stored_pin}] "
    prompt += "(type 'local' to use local file, or press Enter for default): "
    
    pinterest_url = input(prompt).strip()
    
    if not pinterest_url:  # Enter
        if stored_pin:
            pinterest_url = stored_pin
            print(f"  ↳ Using stored Pinterest URL: {pinterest_url}")
            download_pinterest_media(pinterest_url)
        else:
            pinterest_url = ""
            print("  ↳ No stored URL. Using local file...")
    elif pinterest_url.lower() == "local":
        pinterest_url = ""
        print("  ↳ Bypassing stored link to use local file.")
    else:
        # New link entered
        save_stored_link("pinterest_url", pinterest_url)
        print(f"  ↳ Stored new Pinterest URL: {pinterest_url}")
        download_pinterest_media(pinterest_url)

    print("\n[Step 1] Resolving center media...")
    is_video_center = False
    used_auto_thumbnail = False
    
    # 1. Look for local Pinterest image or video/GIF in input/
    image = find_file(INPUT_DIR, ["jpg", "jpeg", "png"])
    if not image:
        image = find_file(INPUT_DIR, ["gif", "mp4", "mov", "webm", "avi", "mkv"])
        if image:
            is_video_center = True
            print(f"  ↳ Found video/GIF center media: {os.path.basename(image)}")

    # 2. If no local media, and we have a YouTube URL, download its thumbnail as fallback
    if not image and source_url:
        print("  ↳ No local image or video found. Downloading YouTube thumbnail fallback...")
        # Get thumbnail URL from yt_meta if already fetched, otherwise fetch it
        thumb_url = yt_meta.get("thumbnail")
        if not thumb_url:
            try:
                cmd = ["yt-dlp", "--no-playlist", "--dump-json", source_url]
                proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
                info = json.loads(proc.stdout)
                thumb_url = info.get("thumbnail", "")
            except Exception:
                pass
        from thumbnail import download_thumbnail
        image = download_thumbnail(thumb_url)
        if image:
            print(f"  ↳ Downloaded YouTube fallback cover: {os.path.basename(image)}")

    if not image:
        # Check if we already have a generated thumbnail we can use as a last resort
        name = os.path.splitext(os.path.basename(audio))[0]
        thumb_path = os.path.join(OUTPUT_DIR, f"{name}.png")
        if os.path.exists(thumb_path):
            print(f"  ↳ Using existing thumbnail as video background: {os.path.basename(thumb_path)}")
            image = thumb_path
            used_auto_thumbnail = True

    if not image:
        print("  ❌ Error: No cover image or video found in input/ and no YouTube fallback available.")
        return None

    print(f"\nAudio  : {os.path.basename(audio)}")
    print(f"Center : {os.path.basename(image)}{' (video/GIF)' if is_video_center else ''}")

    # 3. Interactive Crop of resolved center media
    if is_video_center:
        print("\nCenter media is a video/GIF — extracting first frame for interactive cropping...")
        from thumbnail import extract_first_frame
        first_frame = extract_first_frame(image)
        first_frame_path = os.path.join(INPUT_DIR, "_center_first_frame.png")
        first_frame.save(first_frame_path, "PNG")
        
        # Save a backup of the original uncropped first frame
        first_frame_backup_path = first_frame_path + ".original_backup.png"
        if os.path.exists(first_frame_backup_path):
            try:
                os.unlink(first_frame_backup_path)
            except Exception:
                pass
        shutil.copy2(first_frame_path, first_frame_backup_path)
        
        print("\nOpening cropper on video first frame to select custom crop/remove black bars...")
        cropped_first_frame = crop_to_square(first_frame_path)
    elif not used_auto_thumbnail:
        # Save a backup of the original uncropped static image
        static_backup_path = image + ".original_backup.png"
        if os.path.exists(static_backup_path):
            try:
                os.unlink(static_backup_path)
            except Exception:
                pass
        shutil.copy2(image, static_backup_path)
        
        print("\nOpening image cropper (crop to 1:1)...")
        image = crop_to_square(image)
    else:
        print("\nUsing generated thumbnail directly as video background (skipping cropper).")

    # ── Step 2: Generate Styled Thumbnail and Config ──
    print("\n[Step 2] Generating styled thumbnail and config...")
    name = os.path.splitext(os.path.basename(audio))[0]
    thumb_path = os.path.join(OUTPUT_DIR, f"{name}.png")
    
    try:
        import thumbnail
        thumbnail.generate_thumbnail(
            source_url, 
            image, 
            thumb_path,
            title=yt_meta.get("confirmed_song"),
            artist=yt_meta.get("confirmed_artist")
        )
        print(f"  ✅ Generated styled thumbnail and config: {os.path.basename(thumb_path)}")
    except Exception as e:
        print(f"  ⚠️ Styled thumbnail generation failed: {e}")

    if skip_effects:
        print("\n⏭  Skipping all effects (raw audio will be used)")
        effects = []
        settings = {}
    else:
        print("\nAvailable effects: s: slow, r: reverb, 8: 8d")
        raw = input("Which effects? (e.g. sr8, rs, or Enter for all): ").strip().lower()
        if not raw:
            effects = ["slow", "reverb", "8d"]
        else:
            effects = []
            if "s" in raw:
                effects.append("slow")
            if "r" in raw:
                effects.append("reverb")
            if "8" in raw:
                effects.append("8d")

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

    # Prompt for test render mode
    test_mode = input("\nRender a short test snippet (15s) instead of the full video? (y/n) [default n]: ").strip().lower()

    # Prompt for rhythmic background motion
    use_motion_choice = input("  Use rhythmic background motion? (y/n) [default y]: ").strip().lower()
    use_motion = use_motion_choice != "n"

    # Return config immediately if in interactive_only mode
    if interactive_only:
        name = os.path.splitext(os.path.basename(audio))[0]
        existing_thumb_path = os.path.join(OUTPUT_DIR, f"{name}.png")
        return {
            "audio_path": audio,
            "image_path": image,
            "thumbnail_path": existing_thumb_path,
            "artist_name": artist_name,
            "song_name": song_name,
            "effects": effects,
            "settings": settings,
            "profile": profile,
            "test_mode": test_mode,
            "use_motion": use_motion,
            "source_url": source_url,
            "yt_meta": yt_meta,
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

    name = os.path.splitext(os.path.basename(audio))[0]
    if test_mode == "y":
        output_path = os.path.join(OUTPUT_DIR, f"{name}_test.mp4")
        print(f"  🧪 Test mode enabled! Output: {os.path.basename(output_path)}")
    else:
        output_path = os.path.join(OUTPUT_DIR, f"{name}.mp4")
        
    existing_thumb_path = os.path.join(OUTPUT_DIR, f"{name}.png")

    print("Combining audio and image...")
    tweak_res = render_with_tweaking_loop(
        image, current, output_path, profile, source_url, 
        existing_thumb_path, use_motion, test_mode,
        title=yt_meta.get("confirmed_song"), artist=yt_meta.get("confirmed_artist"),
        raw_audio_path=audio, effects=effects, settings=settings
    )
    output_path = tweak_res["output_path"]
    effects = tweak_res["effects"]
    settings = tweak_res["settings"]

    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    # Clean up first frame temp files and backups if they exist
    for f in [
        os.path.join(INPUT_DIR, "_center_first_frame.png"),
        os.path.join(INPUT_DIR, "_center_first_frame.png.crop.json"),
        os.path.join(INPUT_DIR, "_center_first_frame.png.original_backup.png"),
        image + ".original_backup.png"
    ]:
        if os.path.exists(f):
            try:
                os.unlink(f)
            except Exception:
                pass

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


def execute_task(task_config):
    """Executes the heavy processing and rendering for a pre-configured task."""
    audio = task_config["audio_path"]
    image = task_config["image_path"]
    existing_thumb_path = task_config["thumbnail_path"]
    effects = task_config["effects"]
    settings = task_config["settings"]
    profile = task_config["profile"]
    test_mode = task_config["test_mode"]
    use_motion = task_config["use_motion"]
    source_url = task_config["source_url"]
    yt_meta = task_config["yt_meta"]

    os.makedirs(TEMP_DIR, exist_ok=True)
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

    name = os.path.splitext(os.path.basename(audio))[0]
    if test_mode == "y":
        output_path = os.path.join(OUTPUT_DIR, f"{name}_test.mp4")
        print(f"  🧪 Test mode enabled! Output: {os.path.basename(output_path)}")
    else:
        output_path = os.path.join(OUTPUT_DIR, f"{name}.mp4")

    print("Combining audio and image...")
    tweak_res = render_with_tweaking_loop(
        image, current, output_path, profile, source_url, 
        existing_thumb_path, use_motion, test_mode,
        title=task_config.get("song_name"), artist=task_config.get("artist_name"),
        raw_audio_path=audio, effects=effects, settings=settings
    )
    output_path = tweak_res["output_path"]
    effects = tweak_res["effects"]
    settings = tweak_res["settings"]

    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)

    # Clean up first frame temp files and backups if they exist
    for f in [
        os.path.join(INPUT_DIR, "_center_first_frame.png"),
        os.path.join(INPUT_DIR, "_center_first_frame.png.crop.json"),
        os.path.join(INPUT_DIR, "_center_first_frame.png.original_backup.png"),
        image + ".original_backup.png"
    ]:
        if os.path.exists(f):
            try:
                os.unlink(f)
            except Exception:
                pass

    print(f"\nDone → {output_path}")
    speed_factor = settings.get("slow", {}).get("speed", 1.0)
    
    return {
        "video_path": output_path,
        "source_url": source_url,
        "speed_factor": speed_factor,
        "effects": effects,
        "yt_meta": yt_meta,
    }


if __name__ == "__main__":
    main()