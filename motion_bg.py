import os
import sys
import json
import time
import math
import subprocess
import numpy as np
import soundfile as sf
from scipy.ndimage import gaussian_filter1d
from scipy.signal import butter, sosfilt
from PIL import Image, ImageEnhance
import tempfile


def apply_color_adjustments_to_frame(img, color_adj):
    if not color_adj:
        return img
        
    from PIL import Image, ImageEnhance, ImageFilter
    import random
    
    color_grade = color_adj.get("color_grade", "none")
    maroon_intensity = color_adj.get("maroon_intensity", 35)
    purple_intensity = color_adj.get("purple_intensity", 35)
    filter_intensity = color_adj.get("filter_intensity", 50)
    saturation = color_adj.get("saturation", 100)
    contrast = color_adj.get("contrast", 100)
    vignette = color_adj.get("vignette", 0)
    glow = color_adj.get("glow", 0)
    sparkles = color_adj.get("sparkles", 0)
    
    # ── Helpers ──
    def _apply_curves(image, r_curve=None, g_curve=None, b_curve=None):
        r, g, b = image.split()
        if r_curve:
            r = r.point(r_curve)
        if g_curve:
            g = g.point(g_curve)
        if b_curve:
            b = b.point(b_curve)
        return Image.merge("RGB", (r, g, b))
        
    def _add_vignette(image, strength=0.6):
        w, h = image.size
        cx, cy = w / 2.0, h / 2.0
        y_coords = np.arange(h, dtype=np.float64)
        x_coords = np.arange(w, dtype=np.float64)
        yy, xx = np.meshgrid(y_coords, x_coords, indexing='ij')
        dx = (xx - cx) / cx
        dy = (yy - cy) / cy
        dist = np.sqrt(dx ** 2 + dy ** 2)
        max_d = dist.max()
        if max_d > 0:
            dist = dist / max_d
        threshold = 0.55
        falloff = np.clip((dist - threshold) / (1.0 - threshold), 0.0, 1.0)
        falloff = falloff ** 3.0
        vignette_alpha = np.clip(falloff * strength * 255, 0, 255).astype(np.uint8)
        mask = Image.fromarray(vignette_alpha, mode="L")
        blur_radius = max(8, int(min(w, h) * 0.05))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        dark = Image.new("RGB", (w, h), (0, 0, 0))
        return Image.composite(dark, image, mask)
        
    def _add_glow(image, amount=0.0):
        if amount <= 0:
            return image
        w, h = image.size
        blur_radius = max(1, int(amount * 35 * (w / 1000.0)))
        blurred = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        bright_blurred = ImageEnhance.Brightness(blurred).enhance(1.25)
        return Image.blend(image, bright_blurred, alpha=amount * 0.45)
        
    def _add_sparkles(image, intensity=0.0):
        if intensity <= 0:
            return image
        from PIL import ImageDraw
        gray = image.convert("L")
        w, h = image.size
        arr = np.array(gray)
        threshold = int(248 - intensity * 15)
        y_indices, x_indices = np.where(arr >= threshold)
        if len(x_indices) == 0:
            return image
        candidates = list(zip(x_indices, y_indices))
        # Keep deterministic subset to avoid flickering sparkles across frames
        candidates = candidates[:int(10 + intensity * 45)]
        
        result = image.copy()
        draw = ImageDraw.Draw(result, "RGBA")
        base_size = int(15 + intensity * 25 * (w / 1000.0))
        for cx, cy in candidates:
            try:
                original_pixel = image.getpixel((cx, cy))
            except Exception:
                continue
            r = min(255, int(original_pixel[0] * 0.2 + 255 * 0.8))
            g = min(255, int(original_pixel[1] * 0.2 + 255 * 0.8))
            b = min(255, int(original_pixel[2] * 0.2 + 255 * 0.8))
            glint_size = int(base_size)
            if glint_size <= 0:
                continue
            core_r = max(2, glint_size // 6)
            draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=(255, 255, 255, 255))
            for offset in range(1, glint_size):
                alpha = int(255 * (1.0 - offset / float(glint_size)) ** 1.8)
                if alpha <= 0:
                    continue
                thickness = max(1, (glint_size - offset) // 8)
                draw.line([(cx - offset, cy), (cx + offset, cy)], fill=(r, g, b, alpha), width=thickness)
                draw.line([(cx, cy - offset), (cx, cy + offset)], fill=(r, g, b, alpha), width=thickness)
        return result

    # 1. Apply preset color filter
    t = filter_intensity / 100.0
    
    if color_grade == "bw":
        img = img.convert("L").convert("RGB")
    elif color_grade == "maroon":
        alpha = maroon_intensity / 100.0 * 0.6
        overlay = Image.new("RGB", img.size, (100, 10, 20))
        blended = Image.blend(img, overlay, alpha=max(0.0, min(alpha, 1.0)))
        img = ImageEnhance.Contrast(blended).enhance(1.15)
    elif color_grade == "purple":
        alpha = purple_intensity / 100.0 * 0.6
        overlay = Image.new("RGB", img.size, (80, 20, 120))
        blended = Image.blend(img, overlay, alpha=max(0.0, min(alpha, 1.0)))
        img = ImageEnhance.Contrast(blended).enhance(1.15)
    elif color_grade == "grain":
        img = ImageEnhance.Color(img).enhance(0.85 + t * 0.1)
        img = ImageEnhance.Contrast(img).enhance(1.05 + t * 0.1)
    elif color_grade == "faded":
        fade_floor = int(20 + t * 40)
        lut = [int(fade_floor + (255 - fade_floor) * (i / 255.0)) for i in range(256)]
        img = _apply_curves(img, lut, lut, lut)
        img = ImageEnhance.Color(img).enhance(0.55 + (1 - t) * 0.3)
        img = ImageEnhance.Contrast(img).enhance(0.85)
    elif color_grade == "golden":
        alpha = t * 0.3
        warm_overlay = Image.new("RGB", img.size, (255, 180, 80))
        blended = Image.blend(img, warm_overlay, alpha=max(0.0, min(alpha, 1.0)))
        img = ImageEnhance.Color(blended).enhance(1.15 + t * 0.2)
        img = ImageEnhance.Brightness(img).enhance(1.05 + t * 0.08)
        img = ImageEnhance.Contrast(img).enhance(1.08)
    elif color_grade == "cool":
        alpha = t * 0.25
        cool_overlay = Image.new("RGB", img.size, (100, 140, 200))
        blended = Image.blend(img, cool_overlay, alpha=max(0.0, min(alpha, 1.0)))
        img = ImageEnhance.Color(blended).enhance(0.75 + (1 - t) * 0.2)
        img = ImageEnhance.Contrast(img).enhance(1.1)
    elif color_grade == "sepia":
        grey = img.convert("L")
        sepia_r = grey.point(lambda p: min(255, int(p * (1.0 + 0.30 * t))))
        sepia_g = grey.point(lambda p: min(255, int(p * (1.0 + 0.05 * t))))
        sepia_b = grey.point(lambda p: max(0,   int(p * (1.0 - 0.20 * t))))
        sepia = Image.merge("RGB", (sepia_r, sepia_g, sepia_b))
        blended = Image.blend(img, sepia, alpha=0.4 + t * 0.4)
        img = ImageEnhance.Contrast(blended).enhance(1.05)
    elif color_grade == "matte":
        fade_floor = int(30 + t * 35)
        ceiling = int(245 - t * 15)
        lut = [int(fade_floor + (ceiling - fade_floor) * (i / 255.0)) for i in range(256)]
        blended = _apply_curves(img, lut, lut, lut)
        blended = ImageEnhance.Color(blended).enhance(0.7 + (1 - t) * 0.2)
        blended = ImageEnhance.Contrast(blended).enhance(0.9)
        warm = Image.new("RGB", blended.size, (240, 220, 200))
        img = Image.blend(blended, warm, alpha=t * 0.08)
    elif color_grade == "softpink":
        alpha = t * 0.2
        pink_overlay = Image.new("RGB", img.size, (255, 180, 200))
        blended = Image.blend(img, pink_overlay, alpha=max(0.0, min(alpha, 1.0)))
        img = ImageEnhance.Brightness(blended).enhance(1.08 + t * 0.06)
        img = ImageEnhance.Color(img).enhance(0.85 + t * 0.1)
        img = ImageEnhance.Contrast(img).enhance(0.95)
    elif color_grade == "teal":
        r_lut = [min(255, max(0, int(i * (0.9 + t * 0.15) + t * 10))) for i in range(256)]
        g_lut = [min(255, max(0, int(i * (0.95 + t * 0.05) + t * 5))) for i in range(256)]
        b_lut = [min(255, max(0, int(i * (1.0 + t * 0.08) + t * 15))) for i in range(256)]
        blended = _apply_curves(img, r_lut, g_lut, b_lut)
        img = ImageEnhance.Contrast(blended).enhance(1.15 + t * 0.1)
        img = ImageEnhance.Color(img).enhance(0.8 + t * 0.15)
    elif color_grade == "analog":
        r_lut = [min(255, max(0, int(i * (1.0 + t * 0.12)))) for i in range(256)]
        g_lut = [min(255, max(0, int(i * (1.0 + t * 0.04) - t * 5))) for i in range(256)]
        b_lut = [min(255, max(0, int(i * (0.95 - t * 0.05)))) for i in range(256)]
        blended = _apply_curves(img, r_lut, g_lut, b_lut)
        fade_floor = int(t * 18)
        if fade_floor > 0:
            lift_lut = [max(fade_floor, i) for i in range(256)]
            blended = _apply_curves(blended, lift_lut, lift_lut, lift_lut)
        img = ImageEnhance.Color(blended).enhance(1.1 + t * 0.15)
        img = ImageEnhance.Contrast(blended).enhance(1.08)
    elif color_grade == "cinema":
        shadow_lut = [max(0, int(i * (1.0 - t * 0.15))) for i in range(256)]
        blended = _apply_curves(img, shadow_lut, shadow_lut, shadow_lut)
        r_lut = [min(255, max(0, int(i + (i / 255.0) * t * 15 - (1 - i / 255.0) * t * 8))) for i in range(256)]
        g_lut = list(range(256))
        b_lut = [min(255, max(0, int(i - (i / 255.0) * t * 10 + (1 - i / 255.0) * t * 12))) for i in range(256)]
        blended = _apply_curves(blended, r_lut, g_lut, b_lut)
        img = ImageEnhance.Color(blended).enhance(0.7 + (1 - t) * 0.2)
        img = ImageEnhance.Contrast(blended).enhance(1.2 + t * 0.15)

    # 2. Saturation Slider
    if saturation != 100:
        img = ImageEnhance.Color(img).enhance(saturation / 100.0)
        
    # 3. Contrast Slider
    if contrast != 100:
        img = ImageEnhance.Contrast(img).enhance(contrast / 100.0)
        
    # 4. Glow Slider
    if glow > 0:
        img = _add_glow(img, glow / 100.0)
        
    # 5. Vignette Slider
    if vignette > 0:
        img = _add_vignette(img, vignette / 100.0 * 0.85)
        
    # 6. Sparkles Slider
    if sparkles > 0:
        img = _add_sparkles(img, sparkles / 100.0)
        
    return img


def extract_center_video_frames(video_path, target_size=660, fps=30, crop_info=None):
    """Extract all frames from a video/GIF, square-crop (or use crop_info), and resize to target_size.

    Returns a list of PIL RGBA Images ready to be pasted at the center position.
    Uses FFmpeg to extract frames at the target fps. For GIFs, uses PIL directly.
    """
    ext = os.path.splitext(video_path)[1].lower()

    # Define crop helper for a single frame
    def process_frame(frame):
        if crop_info:
            rot = crop_info.get("rotation", 0)
            if rot % 360 != 0:
                frame = frame.rotate(-rot, expand=True)
            x1 = crop_info.get("x1", 0)
            y1 = crop_info.get("y1", 0)
            x2 = crop_info.get("x2", frame.width)
            y2 = crop_info.get("y2", frame.height)
            frame = frame.crop((x1, y1, x2, y2))
            
            # Apply color grading and slider adjustments!
            color_adj = crop_info.get("color_adjustments")
            if color_adj:
                # convert RGBA to RGB for processing
                alpha = None
                if frame.mode == "RGBA":
                    r, g, b, alpha = frame.split()
                    frame = Image.merge("RGB", (r, g, b))
                
                frame = apply_color_adjustments_to_frame(frame, color_adj)
                
                # put alpha back if it existed
                if alpha:
                    frame = frame.convert("RGBA")
                    frame.putalpha(alpha)
        else:
            w, h = frame.size
            min_dim = min(w, h)
            left = (w - min_dim) // 2
            top = (h - min_dim) // 2
            frame = frame.crop((left, top, left + min_dim, top + min_dim))
        
        return frame.resize((target_size, target_size), Image.Resampling.LANCZOS)

    if ext == ".gif":
        # Use PIL to extract GIF frames natively
        frames = []
        gif = Image.open(video_path)
        try:
            while True:
                frame = gif.copy().convert("RGBA")
                frame = process_frame(frame)
                frames.append(frame)
                gif.seek(gif.tell() + 1)
        except EOFError:
            pass
        print(f"  ↳ Extracted {len(frames)} frames from GIF")
        return frames

    # For video files, use FFmpeg to dump frames to a temp directory
    tmp_dir = tempfile.mkdtemp(prefix="xenia_frames_")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps={fps}",
            "-q:v", "2",
            os.path.join(tmp_dir, "frame_%05d.png")
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)

        # Load all frames
        frame_files = sorted([
            f for f in os.listdir(tmp_dir)
            if f.startswith("frame_") and f.endswith(".png")
        ])

        frames = []
        for fname in frame_files:
            frame = Image.open(os.path.join(tmp_dir, fname)).convert("RGBA")
            frame = process_frame(frame)
            frames.append(frame)

        print(f"  ↳ Extracted {len(frames)} frames from video at {fps}fps")
        return frames
    finally:
        # Clean up temp directory
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

def analyze_audio(audio_path, fps=30):
    """
    Load processed audio, compute RMS energy per frame, smooth it,
    and normalize it to [0.0, 1.0].
    Also detects drum kicks using low-pass filtering and returns a decaying pulse envelope.
    """
    print(f"[Motion BG] Analyzing audio for envelope syncing: {os.path.basename(audio_path)}")
    try:
        data, samplerate = sf.read(audio_path)
        # Convert to mono if multi-channel
        if len(data.shape) > 1:
            data = data.mean(axis=1)

        # Number of samples per frame
        spf = int(samplerate / fps)
        num_frames = int(len(data) / spf)

        # Compute full-range RMS for smooth motion
        rms = []
        for i in range(num_frames):
            start = i * spf
            end = start + spf
            frame_data = data[start:end]
            if len(frame_data) == 0:
                rms.append(0.0)
            else:
                rms.append(np.sqrt(np.mean(frame_data ** 2)))

        rms = np.array(rms)
        # Smooth envelope using a Gaussian filter for fluid motions
        rms = gaussian_filter1d(rms, sigma=6.0)

        # Normalize full-range RMS to [0.0, 1.0]
        r_max = rms.max()
        r_min = rms.min()
        if r_max > r_min:
            rms = (rms - r_min) / (r_max - r_min)
        else:
            rms = np.zeros_like(rms)

        # --- Kick drum detection (lowpass filter < 80Hz + first difference onset detection) ---
        try:
            # Lowpass filter to isolate fundamental kick frequencies
            nyquist = 0.5 * samplerate
            cutoff = 80.0
            normal_cutoff = cutoff / nyquist
            sos = butter(4, normal_cutoff, btype='low', output='sos')
            low_filtered = sosfilt(sos, data)
            
            rms_low = []
            for i in range(num_frames):
                start = i * spf
                end = start + spf
                frame_data = low_filtered[start:end]
                if len(frame_data) == 0:
                    rms_low.append(0.0)
                else:
                    rms_low.append(np.sqrt(np.mean(frame_data ** 2)))
            
            rms_low = np.array(rms_low)
            
            # Smooth slightly to remove noise but keep attack transients sharp
            rms_low = gaussian_filter1d(rms_low, sigma=1.0)
            
            # Compute positive difference (onset strength) to detect attacks (onset transient)
            # This ignores sustained sub-bass notes or vocal hums
            rms_diff = np.diff(rms_low, prepend=0.0)
            rms_diff = np.clip(rms_diff, 0.0, None)  # only keep positive rises
            
            # Normalize the difference/onset envelope (if max rise exceeds noise floor)
            rd_max = rms_diff.max()
            if rd_max > 0.005:
                rms_diff = rms_diff / rd_max
            else:
                rms_diff = np.zeros_like(rms_diff)
                
            # Detect local peaks (kicks) on the difference envelope
            kicks = np.zeros(num_frames)
            window = 4  # minimum distance between kicks (at 30 fps, 4 frames is ~133ms)
            for i in range(window, num_frames - window):
                val = rms_diff[i]
                # Check if local maximum in the window
                is_local_max = all(val >= rms_diff[i + j] for j in range(-window, window + 1))
                # Threshold for kick peak strength on the difference envelope
                if is_local_max and val > 0.30:
                    kicks[i] = 1.0
                    
            # Generate decaying pulse envelope
            kick_pulse = np.zeros(num_frames)
            current_val = 0.0
            decay = 0.82  # decay factor per frame (~200ms decay time)
            for i in range(num_frames):
                if kicks[i] > 0:
                    current_val = 1.0
                else:
                    current_val *= decay
                kick_pulse[i] = current_val
                
        except Exception as e_kick:
            print(f"  ⚠️ Kick detection failed: {e_kick}. Falling back to standard envelope for pulses.")
            kick_pulse = rms.copy()

        return rms, kick_pulse
    except Exception as e:
        print(f"  ⚠️ Audio envelope extraction failed: {e}. Using constant zero energy.")
        return None

def render_motion_video(audio_path, output_path, profile, config_path, fps=30):
    """
    Generate dynamic background motion, composite the pre-rendered overlay on top,
    and stream directly to FFmpeg.
    
    Supports both static center images (fast FFmpeg overlay path) and animated
    center videos/GIFs (per-frame Python compositing with looping).
    """
    print(f"\n[Motion BG] Starting rhythmic video generation...")
    
    # 1. Load config and overlay
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Missing thumbnail config file: {config_path}")
        
    with open(config_path, "r") as f:
        config = json.load(f)
        
    center_image_path = config["center_image"]
    overlay_image_path = config["overlay_image"]
    center_video_path = config.get("center_video")  # None for static images
    overlay_no_center_path = config.get("overlay_no_center")  # None for static images
    
    if not os.path.exists(center_image_path):
        raise FileNotFoundError(f"Center image not found: {center_image_path}")
    if not os.path.exists(overlay_image_path):
        raise FileNotFoundError(f"Overlay image not found: {overlay_image_path}")

    # Determine if we're in video center mode
    use_video_center = (
        center_video_path is not None
        and os.path.exists(center_video_path)
        and overlay_no_center_path is not None
        and os.path.exists(overlay_no_center_path)
    )

    if use_video_center:
        print(f"  ↳ Video center mode: {os.path.basename(center_video_path)}")
        print(f"  ↳ Pre-extracting center video frames...")
        center_frames = extract_center_video_frames(center_video_path, target_size=660, fps=fps, crop_info=config.get("crop_info"))
        if not center_frames:
            print("  ⚠️ No frames extracted from center video. Falling back to static mode.")
            use_video_center = False
        else:
            # Load the no-center overlay (has text, glow, shadow, vignette but NOT center image)
            overlay_nc = Image.open(overlay_no_center_path).convert("RGBA")
            # Pre-calculate center paste position
            central_x = (1920 - 660) // 2
            central_y = (1080 - 660) // 2

    # Load static transparent overlay (used for static path or fallback)
    overlay = Image.open(overlay_image_path).convert("RGBA")
    
    # 2. Extract audio RMS envelope
    audio_analysis = analyze_audio(audio_path, fps=fps)
    if audio_analysis is None:
        # Fallback to zeros (no sync, only smooth sinusoidal drift)
        smooth_env = np.zeros(int(300 * fps)) # 5 minutes default
        kick_pulse = np.zeros(int(300 * fps))
    else:
        smooth_env, kick_pulse = audio_analysis
        
    total_frames = len(smooth_env)
    if "_test.mp4" in output_path:
        total_frames = min(total_frames, 15 * fps)
        print(f"  🧪 Capping render to 15 seconds ({total_frames} frames) for test snippet.")
    duration = total_frames / fps
    print(f"  ↳ Total frames: {total_frames} ({duration:.1f}s at {fps} fps)")

    # 3. Create oversized blurred background
    # 1.15x oversize gives us margin to pan and zoom
    oversize_w, oversize_h = 2208, 1242
    print(f"  ↳ Pre-generating oversized blurred background ({oversize_w}x{oversize_h})...")
    from thumbnail import create_blurred_bg
    bg_oversized = create_blurred_bg(center_image_path, width=oversize_w, height=oversize_h, darken=1.0)
    bg_oversized = bg_oversized.convert("RGB")

    # Pre-calculate grayscale background and black frame once to optimize CPU blend calls in loop
    bg_oversized_gray = bg_oversized.convert("L").convert("RGB")
    black_frame = Image.new("RGB", (1920, 1080), 0)

    # 4. Configure FFmpeg subprocess
    tw = profile["width"]
    th = profile["height"]
    vf = f"scale={tw}:{th}"
    
    # Check if we can use Apple hardware acceleration on macOS
    import platform
    if platform.system() == "Darwin":
        print("  ↳ Using hardware-accelerated H.264 VideoToolbox encoder")
        vcodec = "h264_videotoolbox"
        codec_args = [
            "-c:v", vcodec,
            "-b:v", profile["bitrate"],
        ]
    else:
        vcodec = "libx264"
        codec_args = [
            "-c:v", vcodec, "-preset", "ultrafast", "-tune", "stillimage",
            "-b:v", profile["bitrate"],
            "-maxrate", profile["maxrate"],
            "-bufsize", profile["bufsize"],
        ]

    if use_video_center:
        # Video center mode: overlay_no_center is composited via FFmpeg,
        # but center frames are pasted per-frame in Python before writing to stdin
        ffmpeg_overlay_path = overlay_no_center_path
    else:
        # Static center mode: full overlay (with center image baked in) via FFmpeg
        ffmpeg_overlay_path = overlay_image_path

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", "1920x1080",
        "-r", str(fps),
        "-i", "-", # Input 0 (stdin)
        "-i", audio_path, # Input 1
        "-i", ffmpeg_overlay_path, # Input 2
        "-filter_complex", f"[0:v][2:v]overlay=0:0,format=yuv420p,{vf}[outv]",
        "-map", "[outv]",
        "-map", "1:a",
    ] + codec_args + [
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path
    ]

    print(f"  ↳ Initializing FFmpeg video stream...")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # 5. Render loop
    if use_video_center:
        num_center_frames = len(center_frames)
        print(f"  ↳ Rendering with animated center ({num_center_frames} frames, looping)...")
    else:
        print(f"  ↳ Rendering frame sequence with motion profiles...")
    
    # Pre-calculate constants
    cx, cy = oversize_w / 2.0, oversize_h / 2.0
    start_time = time.time()
    
    try:
        for n in range(total_frames):
            t = n / float(fps)
            e = smooth_env[n]
            pulse = kick_pulse[n]
            
            # --- Dynamic motion transforms ---
            # 1. Slow organic panning drift
            drift_x = math.sin(t * 0.20) * 80.0
            drift_y = math.cos(t * 0.15) * 50.0
            
            # 2. Breathing zoom modulated by slow sinwave + beat energy (now with kick pulse bump!)
            zoom = 1.05 + math.sin(t * 0.30) * 0.02 + pulse * 0.04
            
            # 3. Dynamic brightness & saturation pulses synced to drum kick
            brightness = 0.55 + pulse * 0.15
            saturation = 1.30 + pulse * 0.20
            
            # Determine crop box dimensions
            crop_w = 1920.0 / zoom
            crop_h = 1080.0 / zoom
            
            # Crop box coordinates centered around drifted position
            x0 = cx + drift_x - crop_w / 2.0
            y0 = cy + drift_y - crop_h / 2.0
            x1 = x0 + crop_w
            y1 = y0 + crop_h
            
            # Crop and resize back to target viewport (1920x1080)
            frame = bg_oversized.crop((int(x0), int(y0), int(x1), int(y1)))
            frame = frame.resize((1920, 1080), Image.Resampling.NEAREST)
            
            # Apply color adjustments using pre-calculated frames and fast Image.blend
            if brightness != 1.0:
                frame = Image.blend(frame, black_frame, 1.0 - brightness)
                
            if saturation != 1.0:
                frame_gray = bg_oversized_gray.crop((int(x0), int(y0), int(x1), int(y1)))
                frame_gray = frame_gray.resize((1920, 1080), Image.Resampling.NEAREST)
                frame = Image.blend(frame_gray, frame, saturation)

            # If video center mode, paste the current center frame onto the background
            if use_video_center:
                center_frame = center_frames[n % num_center_frames]
                frame.paste(center_frame, (central_x, central_y), center_frame)
                
            # Write to FFmpeg stdin pipe (static overlay composite is handled by FFmpeg in C)
            proc.stdin.write(frame.tobytes())
            
            # Progress update every 100 frames
            if n % 100 == 0 or n == total_frames - 1:
                elapsed = time.time() - start_time
                fps_render = (n + 1) / elapsed if elapsed > 0 else 0
                eta = (total_frames - n) / fps_render if fps_render > 0 else 0
                pct = (n + 1) / float(total_frames) * 100
                sys.stdout.write(f"\r    [Progress] Frame {n+1}/{total_frames} ({pct:.1f}%) | Speed: {fps_render:.1f} fps | ETA: {eta:.1f}s   ")
                sys.stdout.flush()
                
        print("\n  ↳ Closing stream and finalizing video encoding...")
        proc.stdin.close()
        
    except Exception as e:
        print(f"\n  ❌ Render error occurred: {e}")
        proc.kill()
        raise
        
    # Wait for FFmpeg process to finish
    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        print(f"\n  ❌ FFmpeg failed (exit {proc.returncode}):")
        print(stderr.decode(errors="replace"))
        raise subprocess.CalledProcessError(proc.returncode, cmd)
        
    total_time = time.time() - start_time
    print(f"  ✅ Rhythmic motion background video generated in {total_time:.1f}s!")

