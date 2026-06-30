"""
Thumbnail generator module for Xenia Pipeline.
Generates a styled 1920x1080 thumbnail using a linear gradient derived from album art,
a centered square cover image with a drop shadow/glow, and Moontime font styling.
"""

import os
import re
import json
import subprocess
import tempfile
import urllib.request
import urllib.parse
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter


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


def get_pop_gradient(bg_color):
    """
    Compute a text gradient (text_start, text_end) that has maximum contrast 
    and pops out vividly against the given background color.
    """
    import colorsys
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
    """Prompt the user manually to enter two hex colors if extraction fails."""
    print("\n⚠️ Color extraction failed or could not be completed.")
    print("Please enter two dominant colors manually to generate the background gradient.")
    while True:
        c1 = input("  Enter first hex color (e.g. #ff0055 or ff0055): ").strip().lstrip('#')
        c2 = input("  Enter second hex color (e.g. #00ffaa or 00ffaa): ").strip().lstrip('#')
        try:
            color1 = (int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16))
            color2 = (int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16))
            return color1, color2
        except Exception:
            print("  ❌ Invalid hex color formats. Please use 6-character hex codes (e.g. #1a2b3c).")


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


def extract_colors(art_path):
    """
    Step 1: Extract two vibrant dominant colors using ColorThief.
    Skips near-black and near-white background colors and picks a pair of
    high-contrast/complementary hues (at least 90 degrees hue difference) if possible.
    """
    print("\n[Step 1] Extracting dominant colors from album art...")
    if not art_path:
        return prompt_hex_colors()

    try:
        import colorsys
        from colorthief import ColorThief
        color_thief = ColorThief(art_path)
        palette = color_thief.get_palette(color_count=8, quality=1)

        def get_hsv(rgb):
            r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
            return colorsys.rgb_to_hsv(r, g, b)

        # Filter candidates: keep non-neutral, vibrant colors
        vibrant_candidates = []
        for c in palette:
            h, s, v = get_hsv(c)
            # Filter out near-blacks (v < 0.12), near-whites (v > 0.95), and grayscale (s < 0.15)
            if v >= 0.12 and v <= 0.95 and s >= 0.15:
                vibrant_candidates.append((c, h, s, v))

        if not vibrant_candidates:
            # Fallback to absolute dominant colors if everything is neutral/monochromatic
            color1 = palette[0]
            color2 = palette[1] if len(palette) > 1 else color1
            print(f"  ↳ Color 1 (RGB): {color1}")
            print(f"  ↳ Color 2 (RGB): {color2}")
            return color1, color2

        color1, h1, s1, v1 = vibrant_candidates[0]
        color2 = None

        # Look for a secondary color with a circular hue difference >= 0.25 (90 degrees)
        for c, h2, s2, v2 in vibrant_candidates[1:]:
            hue_diff = min(abs(h1 - h2), 1.0 - abs(h1 - h2))
            if hue_diff >= 0.25:
                color2 = c
                break

        # Fallback 1: at least 0.15 hue difference (54 degrees)
        if not color2:
            for c, h2, s2, v2 in vibrant_candidates[1:]:
                hue_diff = min(abs(h1 - h2), 1.0 - abs(h1 - h2))
                if hue_diff >= 0.15:
                    color2 = c
                    break

        # Fallback 2: highest RGB distance
        if not color2:
            max_dist = -1
            for c, h2, s2, v2 in vibrant_candidates[1:]:
                dist = sum(abs(c[i] - color1[i]) for i in range(3))
                if dist > max_dist:
                    max_dist = dist
                    color2 = c

        if not color2:
            color2 = vibrant_candidates[1][0] if len(vibrant_candidates) > 1 else color1

        print(f"  ↳ Color 1 (RGB): {color1}")
        print(f"  ↳ Color 2 (RGB): {color2}")
        return color1, color2
    except Exception as e:
        print(f"  ⚠️ Color Thief extraction failed: {e}")
        return prompt_hex_colors()


def darken_color(rgb, factor=0.5):
    """Darken RGB color by a factor (e.g. 0.5 reduces brightness by 50%)."""
    return (int(rgb[0] * factor), int(rgb[1] * factor), int(rgb[2] * factor))


def vibrant_boost(rgb):
    """Boost the saturation and brightness of a color to make it extremely vivid."""
    import colorsys
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    s = max(s, 0.93)
    v = max(v, 0.93)
    r_new, g_new, b_new = colorsys.hsv_to_rgb(h, s, v)
    return (int(r_new * 255), int(g_new * 255), int(b_new * 255))


def create_linear_gradient(width, height, color_top, color_bottom):
    """
    Step 2: Generate a top-to-bottom linear gradient canvas.
    """
    print("[Step 2] Generating background top-to-bottom linear gradient...")
    # Create a 1D vertical gradient of shape (height, 1)
    t = np.linspace(0, 1, height)[:, None]
    
    r = (1 - t) * color_top[0] + t * color_bottom[0]
    g = (1 - t) * color_top[1] + t * color_bottom[1]
    b = (1 - t) * color_top[2] + t * color_bottom[2]
    
    # Stack to shape (height, 1, 3)
    column = np.stack([r, g, b], axis=-1).astype(np.uint8)
    
    # Broadcast across width
    rgb = np.tile(column, (1, width, 1))
    return Image.fromarray(rgb)


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
        
    # Draw a dark outline on the main canvas first for contrast/readability
    canvas_draw = ImageDraw.Draw(canvas)
    # Use a dark outline (stroke_width + 3 for a nice defined boundary)
    canvas_draw.text((center_x, y), text, font=font, fill=(15, 15, 15), anchor=anchor, stroke_width=stroke_width + 3, stroke_fill=(15, 15, 15))

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


def generate_thumbnail(youtube_url, pinterest_image_path, output_path):
    """
    Main entry point: Generates a 1920x1080 thumbnail image.
    """
    print("\n" + "=" * 55)
    print("  XENIA THUMBNAIL GENERATOR")
    print("=" * 55)

    # Step 0: Metadata extraction
    title, artist, thumb_url = extract_metadata(youtube_url)

    # Try fetching the actual album cover from iTunes for color extraction
    album_cover_path = fetch_album_cover(artist, title)

    # Download YouTube thumbnail as fallback for color extraction
    temp_art = download_thumbnail(thumb_url)

    # Use album cover for colors if available, otherwise fall back to YouTube thumbnail
    color_source = album_cover_path or temp_art

    # Step 1: Color extraction (from album cover)
    c1, c2 = extract_colors(color_source)

    # Boost extracted colors to match the vivid, prominent album cover colors
    boosted_c1 = vibrant_boost(c1)
    boosted_c2 = vibrant_boost(c2)

    # Use very minimal darkening so the background remains highly vibrant
    darkened_c1 = darken_color(boosted_c1, 0.92)
    darkened_c2 = darken_color(boosted_c2, 0.92)

    # Step 2: Linear gradient background (top to bottom)
    canvas = create_linear_gradient(1920, 1080, darkened_c1, darkened_c2)

    # Step 3: Central image & soft glow
    print("\n[Step 3] Processing central image and glow...")
    
    # Automatically look for Pinterest image in input/ if not provided
    chosen_center_image = pinterest_image_path
    if not chosen_center_image or not os.path.exists(chosen_center_image):
        import glob
        input_images = []
        for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG"]:
            input_images.extend(glob.glob(f"input/*.{ext}"))
        if input_images:
            chosen_center_image = input_images[0]
            print(f"  ↳ Found Pinterest image in input/: {os.path.basename(chosen_center_image)}")

    if not chosen_center_image or not os.path.exists(chosen_center_image):
        if temp_art and os.path.exists(temp_art):
            print("  ↳ No Pinterest image found in input/. Using downloaded YouTube thumbnail as central image.")
            chosen_center_image = temp_art
        else:
            raise FileNotFoundError("No Pinterest cover image found in input/ and no YouTube fallback available.")

    try:
        pint_img = Image.open(chosen_center_image)
    except Exception as e:
        print(f"  ❌ Failed to open center image '{chosen_center_image}': {e}")
        raise

    central_img = center_crop_to_square(pint_img, target_size=660)

    # Draw glow behind the image
    glow_img = create_glow_image(size=800, inner_size=660, spread=16, blur=30, max_alpha=0.5)

    # Paste glow centered at (960, 540)
    glow_x = (1920 - 800) // 2
    glow_y = (1080 - 800) // 2
    canvas.paste(glow_img, (glow_x, glow_y), glow_img)

    # Paste central square image centered at (960, 540)
    central_x = (1920 - 660) // 2
    central_y = (1080 - 660) // 2
    canvas.paste(central_img, (central_x, central_y))

    # Step 4: Font setup
    print("\n[Step 4 & 5] Computing text colors and typography...")
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

    # Step 5: Text color calculation
    # Compute contrast-maximizing gradients for the title (against darkened_c1)
    # and the artist (against darkened_c2) to ensure high visibility and clean pop.
    title_start, title_end = get_pop_gradient(darkened_c1)
    artist_start, artist_end = get_pop_gradient(darkened_c2)

    # Step 6: Text Layout
    print("[Step 6] Drawing text layout...")
    # Centre text vertically within the margin between image edge and canvas edge.
    # Top margin: Y 0–210  → centre = 105   (anchor "mm" = vertical+horizontal centre)
    # Bottom margin: Y 870–1080 → centre = 975
    draw_gradient_text(canvas, title, title_font, 960, 105, title_start, title_end, anchor="mm", stroke_width=1)
    draw_gradient_text(canvas, artist, artist_font, 960, 975, artist_start, artist_end, anchor="mm", stroke_width=1)

    # Step 7: Save output
    print(f"\n[Step 7] Saving final thumbnail to {output_path}...")
    try:
        # Ensure parent directories exist
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        canvas.save(output_path, "PNG")
        print("  ✅ Thumbnail generation complete!")
    finally:
        # Clean up temp thumbnail if downloaded
        if temp_art and temp_art != thumb_url and os.path.exists(temp_art):
            try:
                os.remove(temp_art)
            except Exception:
                pass
    return output_path
