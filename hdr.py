"""
HDR-style Enhancement Module for Xenia Pipeline.

Provides a local-contrast + tone enhancement effect (similar to Lightroom's
"Clarity" combined with saturation boost and micro-contrast sharpening).
This is NOT true HDR metadata -- it visually reads as sharper, more detailed,
and more "popped."

Processing chain:
  1. Local contrast / clarity boost (midtone edge contrast via unsharp-mask
     on luminance at a large radius)
  2. Mild saturation increase (~15%)
  3. Unsharp mask for detail sharpening (small radius, tight)
  4. Highlight rolloff / shadow lift to simulate dynamic-range compression

All operations use PIL/Pillow only -- no OpenCV dependency required.
"""

from PIL import Image, ImageEnhance, ImageFilter
import numpy as np


def apply_hdr_effect(img, strength=1.0):
    """
    Apply an HDR-style local-contrast + tone enhancement to a PIL Image.

    Parameters
    ----------
    img : PIL.Image.Image
        Input image (RGB or RGBA). If RGBA, the alpha channel is preserved
        unchanged and only the RGB data is processed.
    strength : float
        Multiplier for the overall effect intensity.  1.0 = full default
        processing; 0.5 = half-strength; 0.0 = no-op passthrough.

    Returns
    -------
    PIL.Image.Image
        Enhanced image in the same mode as the input.
    """
    if strength <= 0.0:
        return img

    # Preserve alpha channel if present
    alpha = None
    if img.mode == "RGBA":
        r, g, b, alpha = img.split()
        img = Image.merge("RGB", (r, g, b))
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # -- Step 1: Local Contrast / Clarity Boost --
    # Large-radius unsharp mask on the luminance channel enhances midtone
    # edge contrast (the "clarity" look). We blend the result back at the
    # requested strength.
    clarity_img = _apply_clarity(img, radius=20, amount=0.45 * strength)

    # -- Step 2: Mild Saturation Increase (~15%) --
    sat_factor = 1.0 + 0.15 * strength
    saturated = ImageEnhance.Color(clarity_img).enhance(sat_factor)

    # -- Step 3: Detail Sharpening (small-radius unsharp mask) --
    sharp_amount = 0.6 * strength
    sharpened = _apply_unsharp_mask(saturated, radius=2, percent=120, threshold=3,
                                     amount=sharp_amount)

    # -- Step 4: Highlight Rolloff / Shadow Lift --
    # Compress the tonal range slightly: lift deep blacks and roll off
    # blown highlights to simulate dynamic range compression.
    result = _apply_tone_compression(sharpened, shadow_lift=12 * strength,
                                      highlight_rolloff=8 * strength)

    # Restore alpha if it was present
    if alpha is not None:
        result = result.convert("RGBA")
        result.putalpha(alpha)

    return result


# -- Internal Helpers --


def _apply_clarity(img, radius=20, amount=0.45):
    """
    Clarity boost via large-radius unsharp mask on the luminance channel.

    This increases midtone edge contrast without blowing highlights or
    crushing shadows (unlike a simple contrast slider).
    """
    if amount <= 0:
        return img

    # Work on luminance to avoid hue shifts
    arr = np.array(img, dtype=np.float32)

    # Convert to luminance (Rec. 709)
    lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]

    # Low-pass: heavy blur of the luminance
    lum_pil = Image.fromarray(lum.astype(np.uint8), mode="L")
    lum_blurred = lum_pil.filter(ImageFilter.GaussianBlur(radius=radius))
    lum_blur_arr = np.array(lum_blurred, dtype=np.float32)

    # High-pass detail = original luminance - blurred luminance
    detail = lum - lum_blur_arr

    # Add the detail back into each channel, scaled by amount
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] + detail * amount, 0, 255)

    return Image.fromarray(arr.astype(np.uint8), mode="RGB")


def _apply_unsharp_mask(img, radius=2, percent=120, threshold=3, amount=1.0):
    """
    Standard unsharp mask for micro-detail sharpening.
    Blended at `amount` strength against the original.
    """
    if amount <= 0:
        return img

    sharpened = img.filter(ImageFilter.UnsharpMask(
        radius=radius, percent=percent, threshold=threshold
    ))

    if amount >= 1.0:
        return sharpened

    # Partial blend
    return Image.blend(img, sharpened, amount)


def _apply_tone_compression(img, shadow_lift=12.0, highlight_rolloff=8.0):
    """
    Compress tonal extremes to simulate HDR dynamic range mapping:
    - Lift deep shadows (blacks become dark grey)
    - Roll off bright highlights (whites become slightly muted)

    Both values are in 0-255 pixel units at full strength.
    """
    if shadow_lift <= 0 and highlight_rolloff <= 0:
        return img

    shadow_lift = max(0.0, min(shadow_lift, 60.0))
    highlight_rolloff = max(0.0, min(highlight_rolloff, 60.0))

    # Build a lookup table that maps [0, 255] -> [shadow_lift, 255 - highlight_rolloff]
    out_min = shadow_lift
    out_max = 255.0 - highlight_rolloff

    # Apply a gentle S-curve within the compressed range for natural feel
    lut = []
    for i in range(256):
        t = i / 255.0
        # Mild S-curve: keeps midtones punchy while compressing extremes
        s = t * t * (3.0 - 2.0 * t)  # smoothstep
        val = out_min + s * (out_max - out_min)
        lut.append(int(max(0, min(255, val))))

    # Apply the same curve to all three channels
    return img.point(lut * 3)
