import os
import sys
import json
import time
import math
import subprocess
import numpy as np
import soundfile as sf
from scipy.ndimage import gaussian_filter1d
from PIL import Image, ImageEnhance

def analyze_audio(audio_path, fps=30):
    """
    Load processed audio, compute RMS energy per frame, smooth it,
    and normalize it to [0.0, 1.0].
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

        # Normalize to [0.0, 1.0]
        r_max = rms.max()
        r_min = rms.min()
        if r_max > r_min:
            rms = (rms - r_min) / (r_max - r_min)
        else:
            rms = np.zeros_like(rms)

        return rms
    except Exception as e:
        print(f"  ⚠️ Audio envelope extraction failed: {e}. Using constant zero energy.")
        return None

def render_motion_video(audio_path, output_path, profile, config_path, fps=30):
    """
    Generate dynamic background motion, composite the pre-rendered overlay on top,
    and stream directly to FFmpeg.
    """
    print(f"\n[Motion BG] Starting rhythmic video generation...")
    
    # 1. Load config and overlay
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Missing thumbnail config file: {config_path}")
        
    with open(config_path, "r") as f:
        config = json.load(f)
        
    center_image_path = config["center_image"]
    overlay_image_path = config["overlay_image"]
    
    if not os.path.exists(center_image_path):
        raise FileNotFoundError(f"Center image not found: {center_image_path}")
    if not os.path.exists(overlay_image_path):
        raise FileNotFoundError(f"Overlay image not found: {overlay_image_path}")

    # Load static transparent overlay
    overlay = Image.open(overlay_image_path).convert("RGBA")
    
    # 2. Extract audio RMS envelope
    energy = analyze_audio(audio_path, fps=fps)
    if energy is None:
        # Fallback to zeros (no sync, only smooth sinusoidal drift)
        energy = np.zeros(int(300 * fps)) # 5 minutes default
        
    total_frames = len(energy)
    duration = total_frames / fps
    print(f"  ↳ Total frames: {total_frames} ({duration:.1f}s at {fps} fps)")

    # 3. Create oversized blurred background
    # 1.15x oversize gives us margin to pan and zoom
    oversize_w, oversize_h = 2208, 1242
    print(f"  ↳ Pre-generating oversized blurred background ({oversize_w}x{oversize_h})...")
    from thumbnail import create_blurred_bg
    bg_oversized = create_blurred_bg(center_image_path, width=oversize_w, height=oversize_h, darken=1.0)
    bg_oversized = bg_oversized.convert("RGB")

    # 4. Configure FFmpeg subprocess
    tw = profile["width"]
    th = profile["height"]
    vf = f"scale={tw}:{th}"
    
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", "1920x1080",
        "-r", str(fps),
        "-i", "-", # stdin
        "-i", audio_path,
        "-vf", f"format=yuv420p,{vf}",
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

    print(f"  ↳ Initializing FFmpeg video stream...")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # 5. Render loop
    print(f"  ↳ Rendering frame sequence with motion profiles...")
    
    # Pre-calculate constants
    cx, cy = oversize_w / 2.0, oversize_h / 2.0
    start_time = time.time()
    
    try:
        for n in range(total_frames):
            t = n / float(fps)
            e = energy[n]
            
            # --- Dynamic motion transforms ---
            # 1. Slow organic panning drift
            drift_x = math.sin(t * 0.20) * 80.0
            drift_y = math.cos(t * 0.15) * 50.0
            
            # 2. Breathing zoom modulated by slow sinwave + beat energy
            zoom = 1.05 + math.sin(t * 0.30) * 0.02 + e * 0.03
            
            # 3. Dynamic brightness & saturation pulses
            brightness = 0.55 + e * 0.12
            saturation = 1.30 + e * 0.20
            
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
            frame = frame.resize((1920, 1080), Image.Resampling.BILINEAR)
            
            # Apply color adjustments
            if brightness != 1.0:
                frame = ImageEnhance.Brightness(frame).enhance(brightness)
            if saturation != 1.0:
                frame = ImageEnhance.Color(frame).enhance(saturation)
                
            # Composite transparent overlay (center image, glow, shadow, text, vignette)
            frame_rgba = frame.convert("RGBA")
            frame_rgba.paste(overlay, (0, 0), overlay)
            
            # Convert back to raw RGB bytes
            rgb_frame = frame_rgba.convert("RGB")
            
            # Write to FFmpeg stdin pipe
            proc.stdin.write(rgb_frame.tobytes())
            
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
