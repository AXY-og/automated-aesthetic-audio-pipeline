"""
Thumbnail generator module for Xenia Pipeline.
Generates a styled 1920x1080 thumbnail using a blurred version of the Pinterest
image as the background, a centered square cover image with optional brightness
glow, and Moontime / UnifrakturCook font styling.
"""

import os
import re
import json
import subprocess
import tempfile
import urllib.request
import urllib.parse
import colorsys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageChops

# Video/GIF extensions recognized as animated center media
VIDEO_EXTENSIONS = {"mp4", "mov", "webm", "gif", "avi", "mkv"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "JPG", "JPEG", "PNG"}


def is_video_file(path):
    """Return True if the file extension indicates a video or GIF."""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext in VIDEO_EXTENSIONS


def extract_first_frame(video_path):
    """Extract the first frame from a video or GIF and return it as a PIL Image.

    Uses FFmpeg to grab frame 0 and writes it to a temp PNG file.
    For GIFs, PIL is used directly since it handles them natively.
    """
    ext = os.path.splitext(video_path)[1].lower()

    # PIL can natively read GIF frames
    if ext == ".gif":
        img = Image.open(video_path)
        img.seek(0)
        return img.convert("RGB")

    # For video files, use FFmpeg
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            tmp_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return Image.open(tmp_path).convert("RGB")
    finally:
        # Clean up temp file after loading into PIL
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def strip_features(name):
    """Remove featured artist indicators and return only the primary artist.

    Handles: 'Artist, Other', 'Artist feat. Other', 'Artist ft. Other',
             'Artist (feat. Other)', 'Artist & Other', 'Artist x Other', etc.
    """
    if not name:
        return name
    # Remove parenthesized/bracketed feat blocks first
    name = re.sub(r'\s*[\(\[](?:feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]', '', name, flags=re.IGNORECASE).strip()
    # Split on feat./ft./featuring outside parens
    name = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', name, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    # Split on comma — keep only the first artist
    name = name.split(',')[0].strip()
    # Split on ' & ' or ' x ' — keep only the first artist
    name = re.split(r'\s+[&x]\s+', name, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return name


def fetch_album_cover(artist, title):
    """Fetch the actual album cover art from iTunes Search API.

    Returns path to a temporary image file, or None on failure.
    """
    try:
        # Clean artist for search (use primary artist only)
        search_artist = strip_features(artist)
        query = f"{search_artist} {title}"
        encoded = urllib.parse.quote(query)
        url = f"https://itunes.apple.com/search?term={encoded}&entity=song&limit=5"

        req = urllib.request.Request(url, headers={
            "User-Agent": "xenia-pipeline/1.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        results = data.get("results", [])
        if not results:
            print(f"  ⚠️ No album cover found on iTunes for \"{search_artist} - {title}\"")
            return None

        # Get the highest-resolution artwork URL (replace 100x100 with 600x600)
        artwork_url = results[0].get("artworkUrl100", "")
        if not artwork_url:
            return None
        artwork_url = artwork_url.replace("100x100bb", "600x600bb")

        print(f"  ↳ Found album cover: {results[0].get('trackName', '')} — {results[0].get('artistName', '')}")

        # Download to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_path = temp_file.name
        temp_file.close()

        req = urllib.request.Request(artwork_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        with urllib.request.urlopen(req, timeout=10) as response:
            with open(temp_path, "wb") as out_file:
                out_file.write(response.read())

        return temp_path
    except Exception as e:
        print(f"  ⚠️ Failed to fetch album cover from iTunes: {e}")
        return None


# ── Text color helpers ────────────────────────────────────────────────


def get_pop_gradient(bg_color):
    """
    Compute a text gradient (text_start, text_end) that has maximum contrast
    and pops out vividly against the given background color.
    """
    r, g, b = bg_color[0] / 255.0, bg_color[1] / 255.0, bg_color[2] / 255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)

    # Complementary hue (180 degrees shift)
    h_comp = (h + 0.5) % 1.0

    # For text_start: highly saturated/vibrant contrast color, very bright (V=1.0)
    s_start = 0.40  # 40% saturation for a beautiful vivid tint
    v_start = 1.0
    r_start, g_start, b_start = colorsys.hsv_to_rgb(h_comp, s_start, v_start)
    text_start = (int(r_start * 255), int(g_start * 255), int(b_start * 255))

    # For text_end: clean off-white with just a subtle touch of the complementary tint
    s_end = 0.05
    v_end = 0.98
    r_end, g_end, b_end = colorsys.hsv_to_rgb(h_comp, s_end, v_end)
    text_end = (int(r_end * 255), int(g_end * 255), int(b_end * 255))

    return text_start, text_end


def prompt_hex_colors():
    """Prompt the user manually to enter two hex colors for a flat gradient background."""
    print("\n⚠️ No image available for blurred background.")
    print("Please enter two colors manually for a flat gradient background.")
    while True:
        c1 = input("  Enter first hex color (e.g. #ff0055 or ff0055): ").strip().lstrip('#')
        c2 = input("  Enter second hex color (e.g. #00ffaa or 00ffaa): ").strip().lstrip('#')
        try:
            color1 = (int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16))
            color2 = (int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16))
            return color1, color2
        except Exception:
            print("  ❌ Invalid hex color formats. Please use 6-character hex codes (e.g. #1a2b3c).")

def prompt_text_color():
    """Prompt the user manually to enter a text color, or press Enter for auto contrast."""
    print("\nSelect text color option:")
    print("  1) Auto contrast pop gradient [Default]")
    print("  2) Custom hex color")
    choice = input("Enter 1 or 2 [default 1]: ").strip()
    if choice == "2":
        while True:
            c = input("  Enter text hex color (e.g. #ffffff or ffffff): ").strip().lstrip('#')
            try:
                # Convert to RGB
                color = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
                return color
            except Exception:
                print("  ❌ Invalid hex color. Please use a 6-character hex code.")
    return None


# ── Metadata extraction ──────────────────────────────────────────────


def extract_metadata(youtube_url):
    """
    Step 0: Extract title, artist, and thumbnail URL from YouTube video.
    Falls back to manual prompt on failure.
    """
    if not youtube_url:
        print("\n[Step 0] No YouTube URL provided.")
        title = input("  Enter song title (or press Enter to skip styled thumbnail): ").strip()
        if not title:
            raise ValueError("No title provided. Skipping styled thumbnail.")
        artist = input("  Enter artist name: ").strip()
        if not artist:
            raise ValueError("No artist provided. Skipping styled thumbnail.")
        artist = strip_features(artist)
        thumbnail_url = input("  Enter album art image path or URL (or press Enter to skip): ").strip()
        return title, artist, thumbnail_url

    print(f"\n[Step 0] Extracting metadata from YouTube URL: {youtube_url}...")
    try:
        # Run yt-dlp to get JSON metadata (no-playlist and timeout to prevent hanging)
        cmd = ["yt-dlp", "--no-playlist", "--dump-json", youtube_url]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
        info = json.loads(proc.stdout)

        title = info.get("title", "").strip()
        artist = info.get("artist", "").strip()

        # Parse from title if artist key is absent/empty (common for "Artist - Title")
        if not artist and " - " in title:
            parts = title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif not artist:
            artist = "Unknown Artist"

        # Clean title and artist from common video suffixes/parentheses
        clean_regex = r'\s*[\(\[][^\]\)]*(official|video|lyric|lyrics|audio|slowed|reverb|8d|music|clip|prod|remix|hd|4k)[^\]\)]*[\)\]]'
        title = re.sub(clean_regex, '', title, flags=re.IGNORECASE).strip()
        artist = re.sub(clean_regex, '', artist, flags=re.IGNORECASE).strip()

        # Extract best thumbnail URL
        thumbnail_url = info.get("thumbnail", "")
        thumbnails = info.get("thumbnails", [])
        if thumbnails:
            valid_thumbs = [t for t in thumbnails if t.get("width") and t.get("height")]
            if valid_thumbs:
                best_thumb = max(valid_thumbs, key=lambda t: t.get("width", 0))
                thumbnail_url = best_thumb.get("url", thumbnail_url)

        # Strip featured artists — keep only primary artist
        artist = strip_features(artist)

        print(f"  ↳ Extracted Title  : \"{title}\"")
        print(f"  ↳ Extracted Artist : \"{artist}\"")
        return title, artist, thumbnail_url

    except Exception as e:
        print(f"  ⚠️ Failed to extract metadata automatically: {e}")
        title = input("  Enter song title (or press Enter to skip styled thumbnail): ").strip()
        if not title:
            raise ValueError("No title provided. Skipping styled thumbnail.")
        artist = input("  Enter artist name: ").strip()
        if not artist:
            raise ValueError("No artist provided. Skipping styled thumbnail.")
        artist = strip_features(artist)
        thumbnail_url = input("  Enter album art image path or URL (or press Enter to skip): ").strip()
        return title, artist, thumbnail_url


def download_thumbnail(url_or_path):
    """Download a remote thumbnail to a temp file, or return local path if valid."""
    if not url_or_path:
        return None
    if url_or_path.startswith(("http://", "https://")):
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            temp_path = temp_file.name
            temp_file.close()

            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            req = urllib.request.Request(url_or_path, headers=headers)
            with urllib.request.urlopen(req) as response:
                with open(temp_path, "wb") as out_file:
                    out_file.write(response.read())
            return temp_path
        except Exception as e:
            print(f"  ⚠️ Failed to download album art from URL: {e}")
            return None
    else:
        if os.path.exists(url_or_path):
            return url_or_path
        return None


# ── Background generation ────────────────────────────────────────────


def create_blurred_bg(image_path, width=1920, height=1080, blur_radius=70, darken=0.55):
    """
    Create an aesthetic blurred background from the source image.
    Scales the image to cover target canvas (16:9), applies heavy Gaussian blur,
    increases brightness and saturation, generates a bloom pass for glowing highlights,
    and applies a radial vignette that fades to black toward the edges.
    """
    print("[Step 1] Creating blurred image background...")
    img = Image.open(image_path).convert("RGB")
    img_w, img_h = img.size

    # 1. Scale to cover the target dimensions (crop-to-fill)
    target_ratio = width / height
    img_ratio = img_w / img_h

    if img_ratio > target_ratio:
        # Image is wider — scale by height, crop width
        new_h = height
        new_w = int(img_w * (height / img_h))
    else:
        # Image is taller — scale by width, crop height
        new_w = width
        new_h = int(img_h * (width / img_w))

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Center crop
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    img = img.crop((left, top, left + width, top + height))

    # 2. Apply a strong Gaussian blur
    img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # 3. Increase brightness and saturation
    # Apply user-specified darkening factor or default boost if darken is 1.0 (e.g. from motion_bg.py)
    brightness_factor = 1.25 if darken >= 1.0 else (darken * 2.0)
    img = ImageEnhance.Brightness(img).enhance(brightness_factor)
    img = ImageEnhance.Color(img).enhance(1.4)

    # 4. Generate a bloom pass
    # Convert a copy to grayscale for luminance mask
    luminance = img.convert("L")
    # Threshold for bright pixels (threshold of 180 out of 255)
    threshold = 180
    bright_mask = luminance.point(lambda p: 255 if p > threshold else 0)
    
    # Create an image containing only the bright pixels
    bright_pixels = Image.new("RGB", img.size, (0, 0, 0))
    bright_pixels.paste(img, mask=bright_mask)
    
    # Blur the bright pixels with a larger radius (blur_radius * 1.5)
    bloom_radius = int(blur_radius * 1.5)
    bright_blurred = bright_pixels.filter(ImageFilter.GaussianBlur(radius=bloom_radius))
    
    # Blend back over the background using 'screen' blend mode
    img = ImageChops.screen(img, bright_blurred)

    # 5. Apply radial vignette fading to black at edges
    cx, cy = width / 2.0, height / 2.0
    y_coords = np.arange(height, dtype=np.float64)
    x_coords = np.arange(width, dtype=np.float64)
    yy, xx = np.meshgrid(y_coords, x_coords, indexing='ij')

    # Distance from center
    dx = (xx - cx) / cx
    dy = (yy - cy) / cy
    dist = np.sqrt(dx ** 2 + dy ** 2)
    dist = dist / dist.max()  # normalize to 1.0 at corner

    # Vignette curve: clear center, fade to black towards edges
    threshold_v = 0.40
    v_strength = 0.80  # strength of vignette (0.0 to 1.0)
    
    falloff = np.clip((dist - threshold_v) / (1.0 - threshold_v), 0.0, 1.0)
    falloff = falloff ** 2.0
    
    # Map to mask alpha
    vignette_alpha = np.clip(falloff * v_strength * 255, 0, 255).astype(np.uint8)
    
    # Blur the vignette mask for smooth edges
    vignette_mask = Image.fromarray(vignette_alpha, mode="L")
    vignette_mask = vignette_mask.filter(ImageFilter.GaussianBlur(radius=30))
    
    # Blend black color using the vignette mask
    black_img = Image.new("RGB", img.size, (0, 0, 0))
    img = Image.composite(black_img, img, vignette_mask)

    print(f"  ↳ Blurred background with bloom and vignette created ({width}x{height}, blur={blur_radius})")
    return img


def create_flat_gradient(width, height, color_top, color_bottom):
    """
    Fallback: Generate a flat top-to-bottom linear gradient canvas
    when no image is available.
    """
    print("[Step 1] Generating flat gradient background (fallback)...")
    t = np.linspace(0, 1, height)[:, None]

    r = (1 - t) * color_top[0] + t * color_bottom[0]
    g = (1 - t) * color_top[1] + t * color_bottom[1]
    b = (1 - t) * color_top[2] + t * color_bottom[2]

    column = np.stack([r, g, b], axis=-1).astype(np.uint8)
    rgb = np.tile(column, (1, width, 1))
    return Image.fromarray(rgb)


# ── Brightness glow ──────────────────────────────────────────────────


def create_brightness_glow(center_img, canvas_size=(1920, 1080), scale=1.6,
                           blur_radius=45, brightness=1.5, saturation=1.4):
    """
    Create a luminous aura/halo behind the center image.
    Takes the center image, scales it up, blurs heavily, boosts brightness
    and saturation, and returns as an RGBA image ready to composite.
    """
    w, h = center_img.size
    glow_w = int(w * scale)
    glow_h = int(h * scale)

    # Scale up the center image
    glow = center_img.copy().resize((glow_w, glow_h), Image.Resampling.LANCZOS)

    # Apply heavy blur
    glow = glow.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # Boost brightness
    glow = ImageEnhance.Brightness(glow).enhance(brightness)

    # Boost saturation
    glow = ImageEnhance.Color(glow).enhance(saturation)

    # Create a smooth radial feathering mask using Gaussian blur
    # Start with a solid white circle in the middle (size of original image)
    mask = Image.new("L", (glow_w, glow_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    margin = (glow_w - w) // 2
    
    # Draw a solid circle centered, then blur it to create a perfect radial fade
    mask_draw.ellipse([margin, margin, glow_w - margin, glow_h - margin], fill=200)
    
    # Blur the mask heavily to create a soft, natural radial falloff
    mask = mask.filter(ImageFilter.GaussianBlur(radius=60))

    glow_rgba = glow.convert("RGBA")
    glow_rgba.putalpha(mask)

    # Create canvas-sized RGBA to paste glow centered
    result = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    paste_x = (canvas_size[0] - glow_w) // 2
    paste_y = (canvas_size[1] - glow_h) // 2
    result.paste(glow_rgba, (paste_x, paste_y), glow_rgba)

    return result


# ── Vignette overlay ─────────────────────────────────────────────────


def create_vignette_overlay(width=1920, height=1080, strength=0.55):
    """
    Create a feathered optical-falloff vignette (darkest at corners, clear center).
    Simulates real camera lens vignetting — the falloff is strongest at the four
    corners and feathers naturally along the edges, leaving a large clear center.
    Returns an RGBA image to composite on top of the final canvas.
    """
    cx, cy = width / 2.0, height / 2.0

    # Build coordinate grids
    y_coords = np.arange(height, dtype=np.float64)
    x_coords = np.arange(width, dtype=np.float64)
    yy, xx = np.meshgrid(y_coords, x_coords, indexing='ij')

    # Normalized distance from center: 0 at center, 1 at corners
    # Using squared-elliptical distance so corners get hit hardest
    dx = (xx - cx) / cx
    dy = (yy - cy) / cy
    dist = np.sqrt(dx ** 2 + dy ** 2)  # max ≈ 1.414 at corners

    # Normalize so corners = 1.0
    dist = dist / dist.max()

    # Dead-zone: keep center ~60% completely clear, then feather out
    threshold = 0.55
    falloff = np.clip((dist - threshold) / (1.0 - threshold), 0.0, 1.0)

    # High power curve for gentle, natural feathering (optical falloff feel)
    falloff = falloff ** 3.0

    # Scale to alpha values
    vignette = np.clip(falloff * strength * 255, 0, 255).astype(np.uint8)

    # Apply Gaussian blur to the mask itself for extra smooth feathering
    mask = Image.fromarray(vignette, mode="L")
    mask = mask.filter(ImageFilter.GaussianBlur(radius=40))

    # Create black RGBA overlay with vignette as alpha
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    black_layer = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    overlay = Image.composite(black_layer, overlay, mask)

    return overlay


# ── Drop shadow for center image ─────────────────────────────────────


def create_glow_image(size=800, inner_size=660, spread=16, blur=30, max_alpha=0.5):
    """Generate a soft black box shadow glow using true Gaussian blur."""
    # Create L mask image
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)

    # Shadow box dimensions
    shadow_w = inner_size + 2 * spread

    # Calculate box coordinates centered on size x size canvas
    offset = (size - shadow_w) // 2
    x0 = offset
    y0 = offset
    x1 = size - offset
    y1 = size - offset

    # Fill solid center with max_alpha intensity
    intensity = int(max_alpha * 255)
    mask_draw.rectangle([x0, y0, x1, y1], fill=intensity)

    # Apply Gaussian blur
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))

    # Convert mask to RGBA black shadow image
    rgba = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    black = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    rgba = Image.composite(black, rgba, mask)
    return rgba


# ── Image utilities ──────────────────────────────────────────────────


def center_crop_to_square(img, target_size=660):
    """Crop the source image into a centered square and resize."""
    w, h = img.size
    min_dim = min(w, h)
    left = (w - min_dim) // 2
    top = (h - min_dim) // 2
    right = left + min_dim
    bottom = top + min_dim
    cropped = img.crop((left, top, right, bottom))
    return cropped.resize((target_size, target_size), Image.Resampling.LANCZOS)


# ── Typography ───────────────────────────────────────────────────────


def get_scaled_font(font_path, text, max_width=1700, default_size=200):
    """Load script font and automatically scale down to prevent horizontal overflow."""
    size = default_size
    font = ImageFont.truetype(font_path, size)
    bbox = font.getbbox(text)
    w = bbox[2] - bbox[0]
    while w > max_width and size > 60:
        size -= 5
        font = ImageFont.truetype(font_path, size)
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
    return font


def draw_gradient_text(canvas, text, font, center_x, y, start_color, end_color, anchor="ms", stroke_width=1):
    """Draw text with a subtle warm-to-light horizontal gradient using the specified anchor."""
    # Create a dummy image to measure the text box bounds locally
    dummy = Image.new("L", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy)
    bbox = dummy_draw.textbbox((0, 0), text, font=font, anchor=anchor, stroke_width=stroke_width)

    left, top, right, bottom = bbox
    text_w = right - left
    text_h = bottom - top

    if text_w <= 0 or text_h <= 0:
        return

    # Create a local mask image with a safe margin to prevent clipping script flourishes
    margin = 50
    mask_w = text_w + 2 * margin
    mask_h = text_h + 2 * margin

    mask = Image.new("L", (mask_w, mask_h), 0)
    mask_draw = ImageDraw.Draw(mask)

    # Draw the text on the local mask, shifted so it aligns inside the mask bounds
    mask_draw.text((margin - left, margin - top), text, fill=255, font=font, anchor=anchor, stroke_width=stroke_width)

    # Create the gradient of the exact mask size
    gradient = Image.new("RGB", (mask_w, mask_h))
    grad_draw = ImageDraw.Draw(gradient)
    for x_pixel in range(mask_w):
        ratio = x_pixel / float(mask_w - 1) if mask_w > 1 else 0
        r = int((1 - ratio) * start_color[0] + ratio * end_color[0])
        g = int((1 - ratio) * start_color[1] + ratio * end_color[1])
        b = int((1 - ratio) * start_color[2] + ratio * end_color[2])
        grad_draw.line([(x_pixel, 0), (x_pixel, mask_h)], fill=(r, g, b))

    # Paste the gradient onto the canvas using the mask
    paste_x = center_x + left - margin
    paste_y = y + top - margin

    canvas.paste(gradient, (int(paste_x), int(paste_y)), mask)


# ── Average color helper ─────────────────────────────────────────────


def _avg_color_of_region(canvas, x, y, w, h):
    """Sample the average RGB color from a rectangular region of the canvas."""
    region = canvas.crop((x, y, x + w, y + h))
    arr = np.array(region)
    avg_r = int(arr[:, :, 0].mean())
    avg_g = int(arr[:, :, 1].mean())
    avg_b = int(arr[:, :, 2].mean())
    return (avg_r, avg_g, avg_b)


# ── Main generation ──────────────────────────────────────────────────


def generate_thumbnail(youtube_url, pinterest_image_path, output_path, title=None, artist=None):
    """
    Main entry point: Generates a 1920x1080 thumbnail image.
    Uses a blurred version of the Pinterest image as the background.
    """
    print("\n" + "=" * 55)
    print("  XENIA THUMBNAIL GENERATOR")
    print("=" * 55)

    # Step 0: Metadata extraction
    if not title or not artist:
        extracted_title, extracted_artist, thumb_url = extract_metadata(youtube_url)
        title = title or extracted_title
        artist = artist or extracted_artist
    else:
        # If title and artist are passed in, get the thumbnail URL fallback if available
        thumb_url = None
        if youtube_url:
            try:
                cmd = ["yt-dlp", "--no-playlist", "--dump-json", youtube_url]
                proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
                info = json.loads(proc.stdout)
                thumb_url = info.get("thumbnail", "")
                thumbnails = info.get("thumbnails", [])
                if thumbnails:
                    valid_thumbs = [t for t in thumbnails if t.get("width") and t.get("height")]
                    if valid_thumbs:
                        best_thumb = max(valid_thumbs, key=lambda t: t.get("width", 0))
                        thumb_url = best_thumb.get("url", thumb_url)
            except Exception:
                pass

    # Download YouTube thumbnail as fallback
    temp_art = download_thumbnail(thumb_url)

    # ── Determine center media (image or video/GIF) ──
    print("\n[Step 0b] Locating center media...")
    chosen_center_image = pinterest_image_path
    center_video_path = None  # non-None when center is animated video/GIF

    if not chosen_center_image or not os.path.exists(chosen_center_image):
        import glob
        # First, look for static images
        input_images = []
        for ext in IMAGE_EXTENSIONS:
            input_images.extend(glob.glob(f"input/*.{ext}"))
        if input_images:
            chosen_center_image = input_images[0]
            print(f"  ↳ Found image in input/: {os.path.basename(chosen_center_image)}")

    # If no static image found, look for video/GIF files
    if not chosen_center_image or not os.path.exists(chosen_center_image):
        import glob
        input_videos = []
        for ext in VIDEO_EXTENSIONS:
            input_videos.extend(glob.glob(f"input/*.{ext}"))
        if input_videos:
            center_video_path = input_videos[0]
            print(f"  ↳ Found video/GIF center media in input/: {os.path.basename(center_video_path)}")
            first_frame_path = os.path.join("input", "_center_first_frame.png")
            if os.path.exists(first_frame_path):
                print(f"  ↳ Reusing pre-extracted/cropped first frame: {os.path.basename(first_frame_path)}")
            else:
                print(f"  ↳ Extracting first frame for thumbnail generation...")
                first_frame = extract_first_frame(center_video_path)
                first_frame.save(first_frame_path, "PNG")
                print(f"  ✅ First frame extracted ({first_frame.size[0]}x{first_frame.size[1]})")
            chosen_center_image = first_frame_path
    elif chosen_center_image and is_video_file(chosen_center_image):
        # The pinterest_image_path itself is a video/GIF
        center_video_path = chosen_center_image
        print(f"  ↳ Center media is a video/GIF: {os.path.basename(center_video_path)}")
        first_frame_path = os.path.join("input", "_center_first_frame.png")
        if os.path.exists(first_frame_path):
            print(f"  ↳ Reusing pre-extracted/cropped first frame: {os.path.basename(first_frame_path)}")
        else:
            print(f"  ↳ Extracting first frame for thumbnail generation...")
            first_frame = extract_first_frame(center_video_path)
            first_frame.save(first_frame_path, "PNG")
            print(f"  ✅ First frame extracted ({first_frame.size[0]}x{first_frame.size[1]})")
        chosen_center_image = first_frame_path

    if not chosen_center_image or not os.path.exists(chosen_center_image):
        if temp_art and os.path.exists(temp_art):
            print("  ↳ No center media found. Using downloaded YouTube thumbnail.")
            chosen_center_image = temp_art
        else:
            raise FileNotFoundError("No center media (image or video) found in input/ and no YouTube fallback available.")

    try:
        pint_img = Image.open(chosen_center_image)
    except Exception as e:
        print(f"  ❌ Failed to open center image '{chosen_center_image}': {e}")
        raise

    # For blurred background, use center_video first frame or the static image
    bg_source_image = chosen_center_image

    # ── Step 1: Background ──
    # Use blurred center image as the background (always aesthetic)
    if bg_source_image and os.path.exists(bg_source_image):
        canvas = create_blurred_bg(bg_source_image)
    else:
        # Absolute fallback: flat gradient from manual hex colors
        c1, c2 = prompt_hex_colors()
        canvas = create_flat_gradient(1920, 1080, c1, c2)

    # Convert canvas to RGBA for compositing
    canvas = canvas.convert("RGBA")

    # ── Optional effects prompt ──
    print("\nOptional thumbnail effects:")
    glow_choice = input("  Add brightness glow behind center image? (y/n) [default n]: ").strip().lower()
    use_glow = glow_choice == "y"

    vignette_choice = input("  Add subtle vignette to edges? (y/n) [default n]: ").strip().lower()
    use_vignette = vignette_choice == "y"

    # ── Text color prompt ──
    custom_text_rgb = prompt_text_color()

    # ── Step 2: Center image ──
    print("\n[Step 2] Processing central image...")
    central_img = center_crop_to_square(pint_img, target_size=660)

    # ── Step 3: Brightness glow (optional) ──
    if use_glow:
        print("[Step 3] Adding brightness glow...")
        glow_layer = create_brightness_glow(central_img, canvas_size=(1920, 1080))
        canvas = Image.alpha_composite(canvas, glow_layer)
        print("  ↳ Brightness glow applied")
    else:
        print("[Step 3] Brightness glow: skipped")

    # ── Drop shadow behind center image ──
    glow_img = create_glow_image(size=800, inner_size=660, spread=16, blur=30, max_alpha=0.5)
    glow_x = (1920 - 800) // 2
    glow_y = (1080 - 800) // 2
    canvas.paste(glow_img, (glow_x, glow_y), glow_img)

    # ── Paste center image ──
    central_x = (1920 - 660) // 2
    central_y = (1080 - 660) // 2
    central_rgba = central_img.convert("RGBA")
    canvas.paste(central_rgba, (central_x, central_y), central_rgba)

    # ── Step 4: Font setup ──
    print("\n[Step 4] Typography setup...")
    print("Select typography font:")
    print("  1) Moontime (Elegant Cursive) [Default]")
    print("  2) UnifrakturCook (Vintage Blackletter)")
    print("  3) Rock Salt (Handwritten Brush)")
    font_choice = input("Enter 1, 2, or 3 [default 1]: ").strip()

    if font_choice == "2":
        font_path = "assets/fonts/UnifrakturCook.ttf"
        if not os.path.exists(font_path):
            print("  ⚠️ UnifrakturCook font file missing. Using Moontime.")
            font_path = "assets/fonts/Moontime.ttf"
    elif font_choice == "3":
        font_path = "assets/fonts/RockSalt.ttf"
        if not os.path.exists(font_path):
            print("  ⚠️ RockSalt font file missing. Using Moontime.")
            font_path = "assets/fonts/Moontime.ttf"
    else:
        font_path = "assets/fonts/Moontime.ttf"

    if not os.path.exists(font_path):
        # Fallback to system cursive or default if font file is missing
        font_path = "/System/Library/Fonts/Supplemental/Apple Chancery.ttf"
        if not os.path.exists(font_path):
            font_path = None

    # Load scaled fonts (using larger size of 200px)
    if font_path:
        title_font = get_scaled_font(font_path, title, max_width=1700, default_size=200)
        artist_font = get_scaled_font(font_path, artist, max_width=1700, default_size=200)
    else:
        # Fallback if no font file can be loaded
        title_font = ImageFont.load_default()
        artist_font = ImageFont.load_default()

    # ── Step 5: Text color calculation ──
    print("[Step 5] Computing text colors...")
    # Convert canvas to RGB for color sampling
    canvas_rgb = canvas.convert("RGB")

    if custom_text_rgb:
        title_start = custom_text_rgb
        title_end = custom_text_rgb
        artist_start = custom_text_rgb
        artist_end = custom_text_rgb
    else:
        # Sample the average color from the top and bottom regions of the canvas
        # where the title and artist text will be placed
        top_bg = _avg_color_of_region(canvas_rgb, 0, 0, 1920, 210)
        bottom_bg = _avg_color_of_region(canvas_rgb, 0, 870, 1920, 210)

        title_start, title_end = get_pop_gradient(top_bg)
        artist_start, artist_end = get_pop_gradient(bottom_bg)

    # ── Step 6: Assemble Overlay Canvas ──
    print("[Step 6] Assembling transparent overlay canvas...")
    overlay = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))

    # Add brightness glow behind center image
    if use_glow:
        glow_layer = create_brightness_glow(central_img, canvas_size=(1920, 1080))
        overlay = Image.alpha_composite(overlay, glow_layer)

    # Add drop shadow behind center image
    glow_img = create_glow_image(size=800, inner_size=660, spread=16, blur=30, max_alpha=0.5)
    glow_x = (1920 - 800) // 2
    glow_y = (1080 - 800) // 2
    overlay.paste(glow_img, (glow_x, glow_y), glow_img)

    # If center is a video, save the overlay WITHOUT the center image baked in
    # This version will be used by motion_bg.py for per-frame compositing
    if center_video_path:
        overlay_no_center = overlay.copy()

    # Paste center cover image (first frame for videos)
    central_rgba = central_img.convert("RGBA")
    central_x = (1920 - 660) // 2
    central_y = (1080 - 660) // 2
    overlay.paste(central_rgba, (central_x, central_y), central_rgba)

    # Draw gradient texts onto overlay
    draw_gradient_text(overlay, title, title_font, 960, 105, title_start, title_end, anchor="mm", stroke_width=1)
    draw_gradient_text(overlay, artist, artist_font, 960, 975, artist_start, artist_end, anchor="mm", stroke_width=1)

    # Also draw text onto overlay_no_center if video center
    if center_video_path:
        draw_gradient_text(overlay_no_center, title, title_font, 960, 105, title_start, title_end, anchor="mm", stroke_width=1)
        draw_gradient_text(overlay_no_center, artist, artist_font, 960, 975, artist_start, artist_end, anchor="mm", stroke_width=1)

    # Add optional vignette last
    if use_vignette:
        vignette = create_vignette_overlay(1920, 1080, strength=0.45)
        overlay = Image.alpha_composite(overlay, vignette)
        if center_video_path:
            overlay_no_center = Image.alpha_composite(overlay_no_center, vignette)

    # Composite the overlay onto the background to get final output image
    final_rgba = Image.alpha_composite(canvas, overlay)
    canvas_rgb = final_rgba.convert("RGB")

    # ── Step 7: Save output ──
    print(f"\n[Step 7] Saving final thumbnail and overlay components...")
    try:
        # Ensure parent directories exist
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        canvas_rgb.save(output_path, "PNG")
        
        # Save overlay components for video background motion module
        overlay_path = output_path + ".overlay.png"
        config_path = output_path + ".config.json"
        overlay.save(overlay_path, "PNG")

        # Build config dict
        config_data = {
            "center_image": os.path.abspath(chosen_center_image),
            "overlay_image": os.path.abspath(overlay_path),
            "center_video": None,
            "overlay_no_center": None,
            "crop_info": None,
        }

        # If crop config exists, load and embed it
        crop_json_path = chosen_center_image + ".crop.json"
        if os.path.exists(crop_json_path):
            try:
                with open(crop_json_path, "r") as f:
                    config_data["crop_info"] = json.load(f)
                print(f"  ↳ Embedded crop coordinates: {config_data['crop_info']}")
            except Exception as e:
                print(f"  ⚠️ Warning: Failed to load crop config JSON: {e}")

        # If center is a video, save the no-center overlay and video path
        if center_video_path:
            overlay_no_center_path = output_path + ".overlay_no_center.png"
            overlay_no_center.save(overlay_no_center_path, "PNG")
            config_data["center_video"] = os.path.abspath(center_video_path)
            config_data["overlay_no_center"] = os.path.abspath(overlay_no_center_path)
            print(f"  ↳ Saved overlay without center for video compositing")

        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)
            
        print("  ✅ Thumbnail and overlay generation complete!")
    finally:
        # Clean up temp thumbnail if downloaded
        if temp_art and temp_art != thumb_url and os.path.exists(temp_art):
            try:
                os.remove(temp_art)
            except Exception:
                pass
    return output_path
