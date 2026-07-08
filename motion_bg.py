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

