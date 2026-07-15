"""
cropper.py — Interactive 1:1 image cropper with rotation & zoom (Tkinter + Pillow)

Features:
  • Large canvas (1000 × 750) — image is scaled to fit, small images are upscaled
  • Drag the crop box to move it
  • Drag corner handles to resize (locked to 1:1 ratio)
  • Scroll wheel zooms the view in / out (towards cursor)
  • Rotate buttons (90° CW / CCW)
  • Edge-snapping keeps the crop box within image bounds
  • "Crop & Continue" saves the cropped square; "Skip" leaves it untouched
"""

import tkinter as tk
from tkinter import colorchooser
from PIL import Image, ImageTk, ImageFilter, ImageEnhance, ImageDraw, ImageFont, ImageOps
import os
import random

CANVAS_W, CANVAS_H = 1000, 562
MIN_CROP = 50                    # smallest allowed crop (image px)
SNAP_THRESHOLD = 12              # canvas px — snap distance to edges

# Instagram Stories–style fonts (macOS paths)
INSTAGRAM_FONTS = {
    "Classic":     "/System/Library/Fonts/HelveticaNeue.ttc",
    "Modern":      "/System/Library/Fonts/Avenir Next.ttc",
    "Neon":        "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc",
    "Typewriter":  "/System/Library/Fonts/Supplemental/AmericanTypewriter.ttc",
    "Strong":      "/System/Library/Fonts/Supplemental/Futura.ttc",
    "Serif":       "/System/Library/Fonts/Supplemental/Didot.ttc",
    "Handwritten": "/System/Library/Fonts/MarkerFelt.ttc",
    "Elegant":     "/System/Library/Fonts/Supplemental/Copperplate.ttc",
}

# Tkinter display-font mapping (family names for canvas text)
_TK_FONT_FAMILIES = {
    "Classic":     "Helvetica Neue",
    "Modern":      "Avenir Next",
    "Neon":        "Snell Roundhand",
    "Typewriter":  "American Typewriter",
    "Strong":      "Futura",
    "Serif":       "Didot",
    "Handwritten": "Marker Felt",
    "Elegant":     "Copperplate",
}


class CropperApp:
    def __init__(self, image_path, default_mode="1:1", crop_json=None):
        self.image_path = image_path
        self.crop_json = crop_json
        
        # Determine if it's a video/GIF file
        ext = os.path.splitext(image_path)[1].lstrip(".").lower()
        self.is_video = ext in ["mp4", "mov", "webm", "gif", "avi", "mkv"]
        self.video_path = image_path if self.is_video else None
        
        # Downstream pipeline expects output at input/_center_first_frame.png
        if self.is_video:
            self.output_image_path = os.path.join(os.path.dirname(image_path), "_center_first_frame.png")
        else:
            self.output_image_path = image_path

        self.rotation = 0                      # cumulative degrees (0/90/180/270)
        self.aspect_ratio_mode = default_mode  # "1:1" | "16:9"
        self.color_grade = "none"              # filter key string
        self.bg_mode = "blur"                  # "none" | "blur" | "solid"
        self.bg_color = (30, 30, 30)           # default solid bg color (dark grey)
        self.selected_time = 0.0               # timestamp for video frame selection

        crop_info = None
        crop_json_path = self.crop_json if self.crop_json else (self.output_image_path + ".crop.json")
        if os.path.exists(crop_json_path):
            try:
                import json
                with open(crop_json_path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "crop_info" in data:
                    crop_info = data["crop_info"]
                else:
                    crop_info = data
            except Exception as e:
                print(f"  ⚠️ Warning: Failed to load previous crop config JSON: {e}")

        # Extract previous adjustments if they exist
        self.prev_adj = {}
        if crop_info and "color_adjustments" in crop_info:
            self.prev_adj = crop_info["color_adjustments"]
            self.color_grade = self.prev_adj.get("color_grade", "none")
            self.bg_mode = self.prev_adj.get("bg_mode", "blur")
            self.selected_time = self.prev_adj.get("selected_frame_time", 0.0)
            bg_col_val = self.prev_adj.get("bg_color")
            if bg_col_val:
                self.bg_color = tuple(bg_col_val)

        if crop_info and "rotation" in crop_info:
            self.rotation = crop_info["rotation"]

        if self.is_video:
            self._get_video_info()
            print(f"  ↳ Video loaded: {os.path.basename(self.video_path)} ({self.video_duration:.2f}s, {self.video_w}x{self.video_h})")
            print(f"  ↳ Restored selected frame time: {self.selected_time:.3f}s")
            self.original = self._extract_frame_at_time(self.selected_time)
            if not self.original:
                self.original = Image.new("RGB", (640, 640), (40, 40, 40))
        else:
            self.original = Image.open(image_path)

        self._update_working_image()           # sets self.working, img_w, img_h

        # Restore crop box coordinates if valid
        if crop_info and "x1" in crop_info and "y1" in crop_info and "x2" in crop_info and "y2" in crop_info:
            self.crop_x = float(crop_info["x1"])
            self.crop_y = float(crop_info["y1"])
            self.crop_w = float(crop_info["x2"] - crop_info["x1"])
            self.crop_h = float(crop_info["y2"] - crop_info["y1"])
            self._clamp()
        else:
            self._init_crop()                      # largest centred box

        self._fit_view()                       # zoom / offset to fill canvas

        # interaction state
        self._mode = None          # "drag" | "resize" | "pan" | "text_drag"
        self._corner = None
        self._drag_start = None
        self._crop_start = None

        # text overlay state
        self.text_overlays = []    # list of dicts: {text, img_x, img_y, font, size, color}
        self.text_tool_active = False
        self._selected_text_idx = None
        self._text_font = "Classic"
        self._text_size = 36
        self._text_color = (255, 255, 255)

        self._build()

    def _get_video_info(self):
        """Extract video duration, width, and height using ffprobe."""
        import subprocess
        try:
            cmd_dur = [
                "ffprobe", "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                self.video_path
            ]
            res_dur = subprocess.run(cmd_dur, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.video_duration = float(res_dur.stdout.strip())
            
            cmd_res = [
                "ffprobe", "-v", "error", 
                "-select_streams", "v:0", 
                "-show_entries", "stream=width,height", 
                "-of", "csv=s=x:p=0", 
                self.video_path
            ]
            res_res = subprocess.run(cmd_res, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            parts = res_res.stdout.strip().split("x")
            self.video_w, self.video_h = int(parts[0]), int(parts[1])
        except Exception as e:
            print(f"  ⚠️ Warning: Failed to extract video info using ffprobe: {e}")
            self.video_duration = 10.0
            self.video_w, self.video_h = 640, 640

    def _extract_frame_at_time(self, time_s):
        """Extract a single frame at the given timestamp using fast seeking ffmpeg."""
        import subprocess
        
        # Keep temp directory within input/ to comply with permissions
        temp_dir = os.path.join(os.path.dirname(self.image_path), ".cropper_temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_frame_path = os.path.join(temp_dir, "scrub_frame.png")
        
        cmd = [
            "ffmpeg",
            "-ss", f"{time_s:.3f}",
            "-i", self.video_path,
            "-vframes", "1",
            "-f", "image2",
            "-y", temp_frame_path
        ]
        # Run subprocess silently
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(temp_frame_path):
            try:
                # Copy to avoid locking file handles
                with Image.open(temp_frame_path) as img:
                    copied_img = img.copy()
                try:
                    os.unlink(temp_frame_path)
                except Exception:
                    pass
                return copied_img
            except Exception as e:
                print(f"  ⚠️ Failed to load extracted frame: {e}")
        return None

    # ── working image (after rotation) ─────────────────────────────────

    def _update_working_image(self):
        angle = self.rotation % 360
        if angle == 0:
            self.working = self.original.copy()
        else:
            self.working = self.original.rotate(-angle, expand=True)
        self.img_w, self.img_h = self.working.size

    # ── crop box helpers ───────────────────────────────────────────────

    def _init_crop(self):
        if self.aspect_ratio_mode == "1:1":
            side = min(self.img_w, self.img_h)
            self.crop_w = float(side)
            self.crop_h = float(side)
        elif self.aspect_ratio_mode == "16:9":
            r = 16.0 / 9.0
            if self.img_w / self.img_h > r:
                self.crop_h = float(self.img_h)
                self.crop_w = self.crop_h * r
            else:
                self.crop_w = float(self.img_w)
                self.crop_h = self.crop_w / r
        else:
            side = min(self.img_w, self.img_h)
            self.crop_w = float(side)
            self.crop_h = float(side)

        self.crop_x = (self.img_w - self.crop_w) / 2.0
        self.crop_y = (self.img_h - self.crop_h) / 2.0

    def _clamp(self):
        r = 1.0 if self.aspect_ratio_mode == "1:1" else 16.0 / 9.0
        max_h = min(self.img_h, self.img_w / r)
        self.crop_h = max(MIN_CROP, min(self.crop_h, max_h))
        self.crop_w = self.crop_h * r
        self.crop_x = max(0.0, min(self.crop_x, self.img_w - self.crop_w))
        self.crop_y = max(0.0, min(self.crop_y, self.img_h - self.crop_h))

    # ── view (zoom + offset) ──────────────────────────────────────────

    def _fit_view(self):
        pad = 40
        self.zoom = min((CANVAS_W - pad) / self.img_w,
                        (CANVAS_H - pad) / self.img_h)
        dw = self.img_w * self.zoom
        dh = self.img_h * self.zoom
        self.offset_x = (CANVAS_W - dw) / 2.0
        self.offset_y = (CANVAS_H - dh) / 2.0

    def _i2c(self, ix, iy):
        """Image coords → canvas coords."""
        return ix * self.zoom + self.offset_x, iy * self.zoom + self.offset_y

    def _c2i(self, cx, cy):
        """Canvas coords → image coords."""
        return (cx - self.offset_x) / self.zoom, (cy - self.offset_y) / self.zoom

    # ── build UI ───────────────────────────────────────────────────────

    def _build(self):
        self.root = tk.Tk()
        self.root.title("Xenia — Crop Image")
        self.root.configure(bg="#1a1a2e")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda event: self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen")))

        # ── toolbar ──
        self.bar = tk.Frame(self.root, bg="#16213e", pady=8, padx=8)
        self.bar.pack(fill=tk.X)

        s = {"font": ("Helvetica", 12), "padx": 6, "pady": 4,
             "bg": "#0f3460", "fg": "white", "activebackground": "#533483",
             "activeforeground": "white", "relief": "flat", "cursor": "hand2",
             "borderwidth": 0, "highlightthickness": 0}

        # right-aligned action buttons (packed first so they are anchored to the right and always visible)
        tk.Button(self.bar, text="✅  Complete", command=self._do_crop,
                  font=("Helvetica", 12, "bold"), padx=10, pady=4,
                  bg="#1a936f", fg="white", activebackground="#114b5f",
                  activeforeground="white", relief="flat", cursor="hand2",
                  borderwidth=0, highlightthickness=0).pack(side=tk.RIGHT, padx=4)
        tk.Button(self.bar, text="Skip", command=self._skip, **s).pack(side=tk.RIGHT, padx=4)

        tk.Button(self.bar, text="↺  Left",  command=self._rotate_left,  **s).pack(side=tk.LEFT, padx=4)
        tk.Button(self.bar, text="↻  Right", command=self._rotate_right, **s).pack(side=tk.LEFT, padx=4)
        tk.Button(self.bar, text="🔄  Refresh", command=self._refresh_image, **s).pack(side=tk.LEFT, padx=4)

        tk.Frame(self.bar, width=12, bg="#16213e").pack(side=tk.LEFT)

        tk.Button(self.bar, text="＋",  command=self._zoom_in,  **s).pack(side=tk.LEFT, padx=4)
        tk.Button(self.bar, text="－", command=self._zoom_out, **s).pack(side=tk.LEFT, padx=4)
        tk.Button(self.bar, text="Fit", command=self._zoom_fit, **s).pack(side=tk.LEFT, padx=4)
        tk.Button(self.bar, text="Center", command=self._center_crop, **s).pack(side=tk.LEFT, padx=4)

        tk.Frame(self.bar, width=12, bg="#16213e").pack(side=tk.LEFT)

        # Aspect Ratio Toggles
        self.btn_1_1 = tk.Button(self.bar, text="1:1", command=lambda: self._set_aspect_ratio("1:1"), **s)
        self.btn_1_1.pack(side=tk.LEFT, padx=4)
        self.btn_16_9 = tk.Button(self.bar, text="16:9", command=lambda: self._set_aspect_ratio("16:9"), **s)
        self.btn_16_9.pack(side=tk.LEFT, padx=4)

        # Spacer
        tk.Frame(self.bar, width=12, bg="#16213e").pack(side=tk.LEFT)

        # Checkbutton for 16:9 Blurred BG
        self.blur_bg_var = tk.IntVar(value=self.prev_adj.get("blur_bg_out", 1))
        self.chk_blur_bg = tk.Checkbutton(
            self.bar, text="16:9 Output", variable=self.blur_bg_var,
            command=self._on_toggle_blur_bg,
            font=("Helvetica", 11), bg="#16213e", fg="white",
            selectcolor="#1a1a2e", activebackground="#16213e",
            activeforeground="white", highlightthickness=0, bd=0,
            cursor="hand2"
        )
        self.chk_blur_bg.pack(side=tk.LEFT, padx=4)

        # ── sidebar fill bar ──
        self.bg_bar = tk.Frame(self.root, bg="#16213e", pady=4, padx=8)

        tk.Label(self.bg_bar, text="Sidebar Fill:", font=("Helvetica", 11, "bold"),
                 bg="#16213e", fg="#8c92ac").pack(side=tk.LEFT, padx=(4, 8))

        bgs = {"font": ("Helvetica", 11), "padx": 8, "pady": 3,
               "fg": "white", "activeforeground": "white", "relief": "flat",
               "cursor": "hand2", "borderwidth": 0, "highlightthickness": 0}

        self.bg_mode_btns = {}
        for key, label in [("none", "None"), ("blur", "🔵 Blur"), ("solid", "🎨 Solid Color")]:
            btn = tk.Button(self.bg_bar, text=label,
                            command=lambda k=key: self._set_bg_mode(k),
                            bg="#0f3460", activebackground="#533483", **bgs)
            btn.pack(side=tk.LEFT, padx=3)
            self.bg_mode_btns[key] = btn

        # Scale Slider for Blur Amount (shown only in blur mode)
        self.blur_slider = tk.Scale(
            self.bg_bar, from_=0, to=100, orient=tk.HORIZONTAL,
            bg="#16213e", fg="white", highlightthickness=0,
            activebackground="#533483", troughcolor="#1a1a2e",
            label="Blur", font=("Helvetica", 9),
            showvalue=True, length=100, command=lambda val: self._render()
        )
        self.blur_slider.set(self.prev_adj.get("blur_amount", 25))

        # color swatch preview (small colored label)
        self._color_swatch = tk.Label(self.bg_bar, text="  ", width=3,
                                       bg=self._rgb_to_hex(self.bg_color),
                                       relief="solid", bd=1, cursor="hand2")
        self._color_swatch.bind("<Button-1>", lambda e: self._pick_bg_color())

        self.btn_pick_color = tk.Button(self.bg_bar, text="Pick Color",
                                         command=self._pick_bg_color,
                                         bg="#0f3460", activebackground="#533483", **bgs)

        # ── color grading toolbar ──
        self._grade_frame = tk.Frame(self.root, bg="#16213e", pady=4, padx=8)
        self._grade_frame.pack(fill=tk.X)

        # Row 1: label + basic filters
        grade_row1 = tk.Frame(self._grade_frame, bg="#16213e")
        grade_row1.pack(fill=tk.X, pady=(0, 2))

        tk.Label(grade_row1, text="Filter:", font=("Helvetica", 11, "bold"),
                 bg="#16213e", fg="#8c92ac").pack(side=tk.LEFT, padx=(4, 8))

        gs = {"font": ("Helvetica", 10), "padx": 6, "pady": 2,
              "fg": "white", "activeforeground": "white", "relief": "flat",
              "cursor": "hand2", "borderwidth": 0, "highlightthickness": 0}

        self.grade_btns = {}

        row1_filters = [
            ("none",      "None"),
            ("bw",        "B\u2060&\u2060W"),
            ("maroon",    "🔴 Maroon"),
            ("purple",    "🟣 Purple"),
            ("grain",     "🎞 Film Grain"),
            ("faded",     "🌫 Faded Film"),
            ("golden",    "🌅 Golden Hour"),
        ]
        for key, label in row1_filters:
            btn = tk.Button(grade_row1, text=label,
                            command=lambda k=key: self._set_color_grade(k),
                            bg="#0f3460", activebackground="#533483", **gs)
            btn.pack(side=tk.LEFT, padx=2)
            self.grade_btns[key] = btn

        # Row 2: more aesthetic filters
        grade_row2 = tk.Frame(self._grade_frame, bg="#16213e")
        grade_row2.pack(fill=tk.X)

        tk.Label(grade_row2, text="        ", font=("Helvetica", 10),
                 bg="#16213e").pack(side=tk.LEFT)  # spacer to align with row 1

        row2_filters = [
            ("cool",      "❄️ Cool Blue"),
            ("sepia",     "📜 Vintage Sepia"),
            ("matte",     "☁️ Matte Fade"),
            ("softpink",  "🌸 Soft Pink"),
            ("teal",      "🌊 Moody Teal"),
            ("analog",    "📷 Analog Warm"),
            ("cinema",    "🎬 Cinematic"),
        ]
        for key, label in row2_filters:
            btn = tk.Button(grade_row2, text=label,
                            command=lambda k=key: self._set_color_grade(k),
                            bg="#0f3460", activebackground="#533483", **gs)
            btn.pack(side=tk.LEFT, padx=2)
            self.grade_btns[key] = btn

        # Intensity slider (reused across multiple filters)
        slider_style = {
            "orient": tk.HORIZONTAL, "bg": "#16213e", "fg": "white",
            "highlightthickness": 0, "activebackground": "#533483",
            "troughcolor": "#1a1a2e", "font": ("Helvetica", 9),
            "showvalue": True, "length": 120, "from_": 0, "to": 100,
            "command": lambda val: self._render()
        }

        self.maroon_slider = tk.Scale(grade_row1, label="Maroon Intensity", **slider_style)
        self.maroon_slider.set(self.prev_adj.get("maroon_intensity", 35))

        self.purple_slider = tk.Scale(grade_row1, label="Purple Intensity", **slider_style)
        self.purple_slider.set(self.prev_adj.get("purple_intensity", 35))

        self.filter_intensity_slider = tk.Scale(grade_row2, label="Intensity", **slider_style)
        self.filter_intensity_slider.set(self.prev_adj.get("filter_intensity", 50))

        # Grain toggle (used by filters that support grain overlay)
        self._grain_var = tk.IntVar(value=1)
        self._grain_chk = tk.Checkbutton(
            grade_row2, text="Grain", variable=self._grain_var,
            command=lambda: self._render(),
            font=("Helvetica", 10), bg="#16213e", fg="white",
            selectcolor="#1a1a2e", activebackground="#16213e",
            activeforeground="white", highlightthickness=0, bd=0,
            cursor="hand2"
        )

        # Vignette toggle
        self._vignette_var = tk.IntVar(value=0)
        self._vignette_chk = tk.Checkbutton(
            grade_row2, text="Vignette", variable=self._vignette_var,
            command=lambda: self._render(),
            font=("Helvetica", 10), bg="#16213e", fg="white",
            selectcolor="#1a1a2e", activebackground="#16213e",
            activeforeground="white", highlightthickness=0, bd=0,
            cursor="hand2"
        )

        self._update_grade_buttons()

        # ── adjust toolbar ──
        self._adjust_frame = tk.Frame(self.root, bg="#16213e", pady=4, padx=8)
        self._adjust_frame.pack(fill=tk.X)

        tk.Label(self._adjust_frame, text="Adjustments:", font=("Helvetica", 11, "bold"),
                 bg="#16213e", fg="#8c92ac").pack(side=tk.LEFT, padx=(4, 8))

        slider_style = {
            "orient": tk.HORIZONTAL, "bg": "#16213e", "fg": "white",
            "highlightthickness": 0, "activebackground": "#533483",
            "troughcolor": "#1a1a2e", "font": ("Helvetica", 9),
            "showvalue": True, "length": 100,
            "command": lambda val: self._render()
        }

        self.brightness_slider = tk.Scale(self._adjust_frame, label="Brightness", from_=0, to=200, **slider_style)
        self.brightness_slider.set(self.prev_adj.get("brightness", 100))
        self.brightness_slider.pack(side=tk.LEFT, padx=6)

        self.sat_slider = tk.Scale(self._adjust_frame, label="Saturation", from_=0, to=200, **slider_style)
        self.sat_slider.set(self.prev_adj.get("saturation", 100))
        self.sat_slider.pack(side=tk.LEFT, padx=6)

        self.contrast_slider = tk.Scale(self._adjust_frame, label="Contrast", from_=50, to=150, **slider_style)
        self.contrast_slider.set(self.prev_adj.get("contrast", 100))
        self.contrast_slider.pack(side=tk.LEFT, padx=6)

        self.shade_slider = tk.Scale(self._adjust_frame, label="Shade", from_=0, to=100, **slider_style)
        self.shade_slider.set(self.prev_adj.get("shade", 0))
        self.shade_slider.pack(side=tk.LEFT, padx=6)

        self.vignette_slider = tk.Scale(self._adjust_frame, label="Vignette", from_=0, to=100, **slider_style)
        self.vignette_slider.set(self.prev_adj.get("vignette", 0))
        self.vignette_slider.pack(side=tk.LEFT, padx=6)

        self.glow_slider = tk.Scale(self._adjust_frame, label="Glow", from_=0, to=100, **slider_style)
        self.glow_slider.set(self.prev_adj.get("glow", 0))
        self.glow_slider.pack(side=tk.LEFT, padx=6)

        self.sparkle_slider = tk.Scale(self._adjust_frame, label="Sparkles", from_=0, to=100, **slider_style)
        self.sparkle_slider.set(self.prev_adj.get("sparkles", 0))
        self.sparkle_slider.pack(side=tk.LEFT, padx=6)

        # ── HDR toggle checkboxes ──
        hdr_frame = tk.Frame(self._adjust_frame, bg="#16213e")
        hdr_frame.pack(side=tk.LEFT, padx=(12, 4))

        chk_style = {
            "font": ("Helvetica", 10), "bg": "#16213e", "fg": "white",
            "selectcolor": "#1a1a2e", "activebackground": "#16213e",
            "activeforeground": "white", "highlightthickness": 0, "bd": 0,
            "cursor": "hand2"
        }

        self._hdr_thumb_var = tk.IntVar(value=self.prev_adj.get("hdr_thumbnail", 1))
        tk.Checkbutton(
            hdr_frame, text="HDR Thumb", variable=self._hdr_thumb_var, **chk_style
        ).pack(anchor=tk.W)

        self._hdr_video_var = tk.IntVar(value=self.prev_adj.get("hdr_video", 0))
        tk.Checkbutton(
            hdr_frame, text="HDR Video", variable=self._hdr_video_var, **chk_style
        ).pack(anchor=tk.W)

        # ── canvas ──
        self.canvas = tk.Canvas(self.root, width=CANVAS_W, height=CANVAS_H,
                                bg="#1a1a2e", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=10, pady=(0, 6))

        # ── video timeline ──
        if self.is_video:
            self.timeline_frame = tk.Frame(self.root, bg="#1a1a2e")
            self.timeline_frame.pack(fill=tk.X, padx=20, pady=(0, 10))
            
            self.time_label = tk.Label(
                self.timeline_frame, 
                text=f"Timeline Frame Selection: {self.selected_time:.2f}s / {self.video_duration:.2f}s",
                font=("Helvetica", 11, "bold"), bg="#1a1a2e", fg="#8c92ac"
            )
            self.time_label.pack(side=tk.TOP, anchor=tk.W, pady=(0, 4))
            
            self.timeline_slider = tk.Scale(
                self.timeline_frame,
                from_=0.0,
                to=self.video_duration,
                resolution=0.05,
                orient=tk.HORIZONTAL,
                bg="#16213e", fg="white", highlightthickness=0,
                activebackground="#533483", troughcolor="#1a1a2e",
                font=("Helvetica", 10),
                showvalue=False,
                command=self._on_timeline_slide
            )
            self.timeline_slider.set(self.selected_time)
            self.timeline_slider.pack(fill=tk.X, expand=True)
            self.timeline_slider.bind("<ButtonRelease-1>", self._on_timeline_release)
            
            # Video Speed Factor Slider (Slow-mo)
            speed_frame = tk.Frame(self.timeline_frame, bg="#1a1a2e")
            speed_frame.pack(fill=tk.X, pady=(6, 0))
            
            tk.Label(
                speed_frame, 
                text="Video Speed Factor (Slow-mo):",
                font=("Helvetica", 10, "bold"), bg="#1a1a2e", fg="#8c92ac"
            ).pack(side=tk.LEFT, padx=(0, 10))
            
            self.speed_slider = tk.Scale(
                speed_frame,
                from_=0.25,
                to=1.00,
                resolution=0.05,
                orient=tk.HORIZONTAL,
                bg="#16213e", fg="white", highlightthickness=0,
                activebackground="#533483", troughcolor="#1a1a2e",
                font=("Helvetica", 9),
                showvalue=True, length=200
            )
            self.speed_slider.set(self.prev_adj.get("video_speed", 1.0))
            self.speed_slider.pack(side=tk.LEFT)

        # ── info label ──
        self._info = tk.Label(self.root, text="", font=("Menlo", 11),
                              bg="#1a1a2e", fg="#999999")
        self._info.pack(pady=(0, 10))

        # ── text tool toolbar (hidden by default) ──
        self._text_bar = tk.Frame(self.root, bg="#16213e", pady=4, padx=8)
        # (packed/unpacked dynamically)

        ts = {"font": ("Helvetica", 11), "padx": 8, "pady": 3,
              "fg": "white", "activeforeground": "white", "relief": "flat",
              "cursor": "hand2", "borderwidth": 0, "highlightthickness": 0}

        tk.Label(self._text_bar, text="Text Tool:", font=("Helvetica", 11, "bold"),
                 bg="#16213e", fg="#8c92ac").pack(side=tk.LEFT, padx=(4, 8))

        # Font selector (OptionMenu)
        self._font_var = tk.StringVar(value=self._text_font)
        font_menu = tk.OptionMenu(self._text_bar, self._font_var,
                                  *INSTAGRAM_FONTS.keys(),
                                  command=self._on_font_change)
        font_menu.config(bg="#0f3460", fg="white", font=("Helvetica", 11),
                         activebackground="#533483", activeforeground="white",
                         highlightthickness=0, relief="flat", cursor="hand2")
        font_menu["menu"].config(bg="#0f3460", fg="white",
                                  activebackground="#533483",
                                  activeforeground="white",
                                  font=("Helvetica", 11))
        font_menu.pack(side=tk.LEFT, padx=4)

        # Size slider
        self._text_size_slider = tk.Scale(
            self._text_bar, from_=12, to=120, orient=tk.HORIZONTAL,
            bg="#16213e", fg="white", highlightthickness=0,
            activebackground="#533483", troughcolor="#1a1a2e",
            label="Size", font=("Helvetica", 9),
            showvalue=True, length=100,
            command=self._on_text_size_change
        )
        self._text_size_slider.set(self._text_size)
        self._text_size_slider.pack(side=tk.LEFT, padx=6)

        # Text color swatch
        self._text_color_swatch = tk.Label(
            self._text_bar, text="  ", width=3,
            bg=self._rgb_to_hex(self._text_color),
            relief="solid", bd=1, cursor="hand2"
        )
        self._text_color_swatch.pack(side=tk.LEFT, padx=(6, 2))
        self._text_color_swatch.bind("<Button-1>", lambda e: self._pick_text_color())

        tk.Button(self._text_bar, text="Pick Color",
                  command=self._pick_text_color,
                  bg="#0f3460", activebackground="#533483", **ts
                  ).pack(side=tk.LEFT, padx=3)

        tk.Button(self._text_bar, text="🗑 Delete Selected",
                  command=self._delete_selected_text,
                  bg="#8b0000", activebackground="#a52a2a", **ts
                  ).pack(side=tk.RIGHT, padx=4)

        tk.Button(self._text_bar, text="↔️ Center X",
                  command=self._center_text_x,
                  bg="#0f3460", activebackground="#533483", **ts
                  ).pack(side=tk.LEFT, padx=3)

        # ── key / mouse bindings ──
        self.canvas.bind("<ButtonPress-1>",  self._press)
        self.canvas.bind("<B1-Motion>",       self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double_click)
        self.canvas.bind("<MouseWheel>",      self._on_scroll)           # macOS / Windows
        self.canvas.bind("<Button-4>", lambda e: self._apply_zoom(1.1, e))  # Linux up
        self.canvas.bind("<Button-5>", lambda e: self._apply_zoom(0.9, e))  # Linux down

        # Text toggle button in main toolbar (add it after spacer)
        tk.Frame(self.bar, width=12, bg="#16213e").pack(side=tk.LEFT)
        self._text_toggle_btn = tk.Button(
            self.bar, text="✍️ Text", command=self._toggle_text_tool, **s)
        self._text_toggle_btn.pack(side=tk.LEFT, padx=4)

        self._update_aspect_ratio_buttons()
        self._update_blur_bg_visibility()
        self._update_grade_buttons()
        self._render()

    def _set_aspect_ratio(self, mode):
        if self.aspect_ratio_mode != mode:
            self.aspect_ratio_mode = mode
            self._init_crop()
            self._update_aspect_ratio_buttons()
            self._update_blur_bg_visibility()
            self._render()

    def _on_toggle_blur_bg(self):
        if self.blur_bg_var.get() == 0:
            self.bg_mode = "none"
        self._update_blur_bg_visibility()
        self._render()

    def _update_blur_bg_visibility(self):
        if self.aspect_ratio_mode == "1:1":
            self.chk_blur_bg.configure(state=tk.NORMAL)
        else:
            self.chk_blur_bg.configure(state=tk.DISABLED)
            self.blur_bg_var.set(0)
        self._update_bg_mode_visibility()

    # ── sidebar / background mode ──────────────────────────────────────

    @staticmethod
    def _rgb_to_hex(rgb):
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    def _set_bg_mode(self, mode):
        self.bg_mode = mode
        self._update_bg_mode_buttons()
        self._update_bg_mode_visibility()
        self._render()

    def _update_bg_mode_buttons(self):
        for key, btn in self.bg_mode_btns.items():
            if key == self.bg_mode:
                btn.config(bg="#533483", fg="white")
            else:
                btn.config(bg="#0f3460", fg="#8c92ac")

    def _update_bg_mode_visibility(self):
        """Show/hide sidebar bg bar and its sub-widgets based on blur_bg_var and bg_mode."""
        if self.aspect_ratio_mode == "1:1" and self.blur_bg_var.get() == 1:
            self.bg_bar.pack(fill=tk.X, after=self.bar)
            self.blur_slider.pack_forget()
            self._color_swatch.pack_forget()
            self.btn_pick_color.pack_forget()

            if self.bg_mode == "blur":
                self.blur_slider.pack(side=tk.LEFT, padx=6)
            elif self.bg_mode == "solid":
                self._color_swatch.pack(side=tk.LEFT, padx=(6, 2))
                self.btn_pick_color.pack(side=tk.LEFT, padx=3)
        else:
            self.bg_bar.pack_forget()

    def _pick_bg_color(self):
        color = colorchooser.askcolor(
            initialcolor=self._rgb_to_hex(self.bg_color),
            title="Choose Background Color"
        )
        if color and color[0]:
            self.bg_color = tuple(int(c) for c in color[0])
            self._color_swatch.configure(bg=self._rgb_to_hex(self.bg_color))
            self._render()

    def _update_aspect_ratio_buttons(self):
        if self.aspect_ratio_mode == "1:1":
            self.btn_1_1.config(bg="#533483", fg="white")
            self.btn_16_9.config(bg="#0f3460", fg="#8c92ac")
        else:
            self.btn_1_1.config(bg="#0f3460", fg="#8c92ac")
            self.btn_16_9.config(bg="#533483", fg="white")

    # ── color grading ──────────────────────────────────────────────────

    # Filters that show the intensity slider + grain/vignette toggles
    _FILTERS_WITH_CONTROLS = {"grain", "faded", "golden", "cool", "sepia",
                               "matte", "softpink", "teal", "analog", "cinema"}

    def _set_color_grade(self, grade):
        self.color_grade = grade
        self._update_grade_buttons()
        self._render()

    def _update_grade_buttons(self):
        for key, btn in self.grade_btns.items():
            if key == self.color_grade:
                btn.config(bg="#533483", fg="white")
            else:
                btn.config(bg="#0f3460", fg="#8c92ac")

        # show/hide intensity sliders
        self.maroon_slider.pack_forget()
        self.purple_slider.pack_forget()
        self.filter_intensity_slider.pack_forget()
        self._grain_chk.pack_forget()
        self._vignette_chk.pack_forget()

        if self.color_grade == "maroon":
            self.maroon_slider.pack(side=tk.LEFT, padx=10)
        elif self.color_grade == "purple":
            self.purple_slider.pack(side=tk.LEFT, padx=10)
        elif self.color_grade in self._FILTERS_WITH_CONTROLS:
            self.filter_intensity_slider.pack(side=tk.LEFT, padx=6)
            self._grain_chk.pack(side=tk.LEFT, padx=4)
            self._vignette_chk.pack(side=tk.LEFT, padx=4)

    # ── filter helper effects ──────────────────────────────────────────

    @staticmethod
    def _add_film_grain(img, amount=0.25):
        """Overlay monochrome noise to simulate film grain."""
        if amount <= 0:
            return img
        w, h = img.size
        # Generate grain at quarter-res then upscale for a coarser, more filmic look
        qw, qh = max(1, w // 4), max(1, h // 4)
        grain = Image.new("L", (qw, qh))
        pix = grain.load()
        strength = int(amount * 80)
        for y in range(qh):
            for x in range(qw):
                pix[x, y] = 128 + random.randint(-strength, strength)
        grain = grain.resize((w, h), Image.BILINEAR)
        grain_rgb = Image.merge("RGB", (grain, grain, grain))
        return Image.blend(img, grain_rgb, alpha=min(amount * 0.4, 0.35))

    @staticmethod
    def _add_vignette(img, strength=0.6):
        """Apply a feathered optical fall-off vignette (darkest at corners, clear center)."""
        import numpy as np
        w, h = img.size
        
        # Build coordinates
        cx, cy = w / 2.0, h / 2.0
        y_coords = np.arange(h, dtype=np.float64)
        x_coords = np.arange(w, dtype=np.float64)
        yy, xx = np.meshgrid(y_coords, x_coords, indexing='ij')

        # Normalized distance from center (0 at center, 1 at corners)
        dx = (xx - cx) / cx
        dy = (yy - cy) / cy
        dist = np.sqrt(dx ** 2 + dy ** 2)
        max_d = dist.max()
        if max_d > 0:
            dist = dist / max_d

        # Keep center ~55% completely untouched, then feather out
        threshold = 0.55
        falloff = np.clip((dist - threshold) / (1.0 - threshold), 0.0, 1.0)
        falloff = falloff ** 3.0  # gentle falloff at corners

        # Convert to mask: 0 (center/no change) to strength*255 (corners/darken)
        vignette_alpha = np.clip(falloff * strength * 255, 0, 255).astype(np.uint8)
        mask = Image.fromarray(vignette_alpha, mode="L")
        
        # Smooth the mask using a Gaussian blur proportional to image size
        blur_radius = max(8, int(min(w, h) * 0.05))
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        # Composite black color at corners using the mask
        dark = Image.new("RGB", (w, h), (0, 0, 0))
        return Image.composite(dark, img, mask)

    @staticmethod
    def _apply_curves(img, r_curve=None, g_curve=None, b_curve=None):
        """Apply per-channel lookup-table curves. Each curve is a list of 256 ints."""
        r, g, b = img.split()
        if r_curve:
            r = r.point(r_curve)
        if g_curve:
            g = g.point(g_curve)
        if b_curve:
            b = b.point(b_curve)
        return Image.merge("RGB", (r, g, b))

    def _get_filter_intensity(self):
        """Return current filter intensity as 0.0–1.0."""
        try:
            return self.filter_intensity_slider.get() / 100.0
        except Exception:
            return 0.5

    def _should_grain(self):
        try:
            return self._grain_var.get() == 1
        except Exception:
            return False

    def _should_vignette(self):
        try:
            return self._vignette_var.get() == 1
        except Exception:
            return False

    def _finish_filter(self, img):
        """Apply optional grain and vignette post-processing."""
        if self._should_grain():
            img = self._add_film_grain(img, amount=self._get_filter_intensity() * 0.5)
        if self._should_vignette():
            img = self._add_vignette(img, strength=self._get_filter_intensity() * 0.8)
        return img

    @staticmethod
    def _add_glow(img, amount=0.0):
        """Apply Orton-style soft glow by blending a blurred bright version of the image."""
        if amount <= 0:
            return img
        # Blur the image (radius proportional to image size)
        w, h = img.size
        blur_radius = max(1, int(amount * 35 * (w / 1000.0)))
        blurred = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        # Enhance brightness of the blurred image
        bright_blurred = ImageEnhance.Brightness(blurred).enhance(1.25)
        # Blend with original
        return Image.blend(img, bright_blurred, alpha=amount * 0.45)

    @staticmethod
    def _add_sparkles(img, intensity=0.0):
        """
        Detect bright highlights in the image and draw 4-point star glints / sparkles on them.
        Intensity is a value from 0.0 to 1.0 controlling sparkle size and quantity.
        """
        if intensity <= 0:
            return img

        import numpy as np
        import random
        from PIL import ImageDraw

        # 1. Convert to grayscale to find bright pixels
        gray = img.convert("L")
        w, h = img.size

        # Get pixel values as numpy array
        arr = np.array(gray)

        # Detect highlights (threshold gets lower/more permissive with higher intensity)
        threshold = int(248 - intensity * 15)
        y_indices, x_indices = np.where(arr >= threshold)

        if len(x_indices) == 0:
            return img

        # Group coordinates or select a sparse subset to avoid overlap/crowding
        candidates = list(zip(x_indices, y_indices))
        random.shuffle(candidates)

        # Max sparkles scaled by intensity
        max_sparkles = int(10 + intensity * 45)

        # Filter candidates to keep them spaced out (minimum distance of 30 image pixels)
        selected = []
        for pt in candidates:
            if len(selected) >= max_sparkles:
                break
            too_close = False
            for s_pt in selected:
                if abs(pt[0] - s_pt[0]) < 30 and abs(pt[1] - s_pt[1]) < 30:
                    too_close = True
                    break
            if not too_close:
                selected.append(pt)

        if not selected:
            return img

        # Draw sparkles on a copy of the image
        result = img.copy()
        draw = ImageDraw.Draw(result, "RGBA")

        # Base size scaled by intensity and image width
        base_size = int(15 + intensity * 25 * (w / 1000.0))

        for cx, cy in selected:
            # Determine sparkle color based on original pixel color
            original_pixel = img.getpixel((cx, cy))
            r = min(255, int(original_pixel[0] * 0.2 + 255 * 0.8))
            g = min(255, int(original_pixel[1] * 0.2 + 255 * 0.8))
            b = min(255, int(original_pixel[2] * 0.2 + 255 * 0.8))

            # Sparkle size variation
            glint_size = random.randint(int(base_size * 0.7), int(base_size * 1.3))
            if glint_size <= 0:
                continue

            # 1. Draw central bright core
            core_r = max(2, glint_size // 6)
            draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=(255, 255, 255, 255))

            # 2. Draw fading cross flares (horizontal and vertical lines)
            for offset in range(1, glint_size):
                alpha = int(255 * (1.0 - offset / float(glint_size)) ** 1.8)
                if alpha <= 0:
                    continue
                thickness = max(1, (glint_size - offset) // 8)

                # Horizontal lines
                draw.line([(cx - offset, cy), (cx + offset, cy)], fill=(r, g, b, alpha), width=thickness)
                # Vertical lines
                draw.line([(cx, cy - offset), (cx, cy + offset)], fill=(r, g, b, alpha), width=thickness)

                # Draw diagonal glints (slightly shorter and dimmer)
                diag_offset = int(offset * 0.7)
                if diag_offset > 0:
                    diag_alpha = int(alpha * 0.45)
                    # Diagonal 1
                    draw.line([(cx - diag_offset, cy - diag_offset), (cx + diag_offset, cy + diag_offset)], fill=(r, g, b, diag_alpha), width=1)
                    # Diagonal 2
                    draw.line([(cx - diag_offset, cy + diag_offset), (cx + diag_offset, cy - diag_offset)], fill=(r, g, b, diag_alpha), width=1)

        return result

    def _apply_custom_adjustments(self, img):
        """Apply manual slider adjustments (brightness, saturation, contrast, shade, vignette, glow, sparkles) to the image."""
        if not hasattr(self, "sat_slider"):
            return img

        # 1. Saturation
        sat_val = self.sat_slider.get() / 100.0
        if sat_val != 1.0:
            img = ImageEnhance.Color(img).enhance(sat_val)

        # 2. Contrast
        contrast_val = self.contrast_slider.get() / 100.0
        if contrast_val != 1.0:
            img = ImageEnhance.Contrast(img).enhance(contrast_val)

        # 3. Brightness
        if hasattr(self, "brightness_slider"):
            brightness_val = self.brightness_slider.get() / 100.0
            if brightness_val != 1.0:
                img = ImageEnhance.Brightness(img).enhance(brightness_val)

        # 4. Glow
        glow_val = self.glow_slider.get() / 100.0
        if glow_val > 0.0:
            img = self._add_glow(img, amount=glow_val)

        # 5. Sparkles / Glint
        sparkle_val = self.sparkle_slider.get() / 100.0
        if sparkle_val > 0.0:
            img = self._add_sparkles(img, intensity=sparkle_val)

        # 6. Vignette
        vignette_val = self.vignette_slider.get() / 100.0
        if vignette_val > 0.0:
            img = self._add_vignette(img, strength=vignette_val * 0.85)

        # 7. Shade
        if hasattr(self, "shade_slider"):
            shade_val = self.shade_slider.get() / 100.0
            if shade_val > 0.0:
                black_overlay = Image.new(img.mode, img.size, (0, 0, 0) if img.mode == "RGB" else (0, 0, 0, 255))
                img = Image.blend(img, black_overlay, alpha=shade_val)

        return img

    def _apply_color_grade(self, img):
        """Return a new image with the current color grade and manual adjustments applied."""
        alpha = None
        if img.mode == "RGBA":
            r, g, b, alpha = img.split()
            img = Image.merge("RGB", (r, g, b))

        # 1. Apply preset color filter
        filtered_img = self._get_preset_filtered_image(img)

        # 2. Apply custom adjustments
        result = self._apply_custom_adjustments(filtered_img)

        if alpha is not None:
            result = result.convert("RGBA")
            result.putalpha(alpha)
        return result

    def _get_preset_filtered_image(self, img):
        """Return a new image with the selected preset filter applied."""
        if self.color_grade == "none":
            return img

        elif self.color_grade == "bw":
            return img.convert("L").convert("RGB")

        elif self.color_grade == "maroon":
            alpha = self.maroon_slider.get() / 100.0 * 0.6
            overlay = Image.new("RGB", img.size, (100, 10, 20))
            blended = Image.blend(img, overlay, alpha=max(0.0, min(alpha, 1.0)))
            enhancer = ImageEnhance.Contrast(blended)
            return enhancer.enhance(1.15)

        elif self.color_grade == "purple":
            alpha = self.purple_slider.get() / 100.0 * 0.6
            overlay = Image.new("RGB", img.size, (80, 20, 120))
            blended = Image.blend(img, overlay, alpha=max(0.0, min(alpha, 1.0)))
            enhancer = ImageEnhance.Contrast(blended)
            return enhancer.enhance(1.15)

        elif self.color_grade == "grain":
            # Pure film grain — faithful to original colors, just adds texture
            t = self._get_filter_intensity()
            result = img.copy()
            # Slight desaturation for that film stock feel
            result = ImageEnhance.Color(result).enhance(0.85 + t * 0.1)
            result = ImageEnhance.Contrast(result).enhance(1.05 + t * 0.1)
            return self._finish_filter(result)

        elif self.color_grade == "faded":
            # Faded film — lifted blacks, desaturated, washed out
            t = self._get_filter_intensity()
            result = img.copy()
            # Lift the blacks: remap 0→fade_floor, 255→255
            fade_floor = int(20 + t * 40)  # 20–60
            lut = [int(fade_floor + (255 - fade_floor) * (i / 255.0)) for i in range(256)]
            result = self._apply_curves(result, lut, lut, lut)
            result = ImageEnhance.Color(result).enhance(0.55 + (1 - t) * 0.3)
            result = ImageEnhance.Contrast(result).enhance(0.85)
            return self._finish_filter(result)

        elif self.color_grade == "golden":
            # Golden hour — warm tones, boosted highlights, orange cast
            t = self._get_filter_intensity()
            alpha = t * 0.3
            warm_overlay = Image.new("RGB", img.size, (255, 180, 80))
            result = Image.blend(img, warm_overlay, alpha=max(0.0, min(alpha, 1.0)))
            result = ImageEnhance.Color(result).enhance(1.15 + t * 0.2)
            result = ImageEnhance.Brightness(result).enhance(1.05 + t * 0.08)
            result = ImageEnhance.Contrast(result).enhance(1.08)
            return self._finish_filter(result)

        elif self.color_grade == "cool":
            # Cool blue — lifted blues, muted warm tones, slightly desaturated
            t = self._get_filter_intensity()
            alpha = t * 0.25
            cool_overlay = Image.new("RGB", img.size, (100, 140, 200))
            result = Image.blend(img, cool_overlay, alpha=max(0.0, min(alpha, 1.0)))
            result = ImageEnhance.Color(result).enhance(0.75 + (1 - t) * 0.2)
            result = ImageEnhance.Contrast(result).enhance(1.1)
            return self._finish_filter(result)

        elif self.color_grade == "sepia":
            # Vintage sepia — warm monotone with slight desaturation
            t = self._get_filter_intensity()
            grey = img.convert("L")
            sepia_r = grey.point(lambda p: min(255, int(p * (1.0 + 0.30 * t))))
            sepia_g = grey.point(lambda p: min(255, int(p * (1.0 + 0.05 * t))))
            sepia_b = grey.point(lambda p: max(0,   int(p * (1.0 - 0.20 * t))))
            sepia = Image.merge("RGB", (sepia_r, sepia_g, sepia_b))
            # Blend with original to preserve some color
            result = Image.blend(img, sepia, alpha=0.4 + t * 0.4)
            result = ImageEnhance.Contrast(result).enhance(1.05)
            return self._finish_filter(result)

        elif self.color_grade == "matte":
            # Matte fade — crushed blacks raised, flat contrast, creamy tones
            t = self._get_filter_intensity()
            fade_floor = int(30 + t * 35)
            ceiling = int(245 - t * 15)
            lut = [int(fade_floor + (ceiling - fade_floor) * (i / 255.0)) for i in range(256)]
            result = self._apply_curves(img.copy(), lut, lut, lut)
            result = ImageEnhance.Color(result).enhance(0.7 + (1 - t) * 0.2)
            result = ImageEnhance.Contrast(result).enhance(0.9)
            # Slight warm tint
            warm = Image.new("RGB", result.size, (240, 220, 200))
            result = Image.blend(result, warm, alpha=t * 0.08)
            return self._finish_filter(result)

        elif self.color_grade == "softpink":
            # Soft pink — dreamy pastel pink overlay, bright and airy
            t = self._get_filter_intensity()
            alpha = t * 0.2
            pink_overlay = Image.new("RGB", img.size, (255, 180, 200))
            result = Image.blend(img, pink_overlay, alpha=max(0.0, min(alpha, 1.0)))
            result = ImageEnhance.Brightness(result).enhance(1.08 + t * 0.06)
            result = ImageEnhance.Color(result).enhance(0.85 + t * 0.1)
            result = ImageEnhance.Contrast(result).enhance(0.95)
            return self._finish_filter(result)

        elif self.color_grade == "teal":
            # Moody teal — teal shadows, warm highlights (teal & orange split tone)
            t = self._get_filter_intensity()
            # Shift: boost teal in shadows, warm in highlights
            r_lut = [min(255, max(0, int(i * (0.9 + t * 0.15) + t * 10))) for i in range(256)]
            g_lut = [min(255, max(0, int(i * (0.95 + t * 0.05) + t * 5))) for i in range(256)]
            b_lut = [min(255, max(0, int(i * (1.0 + t * 0.08) + t * 15))) for i in range(256)]
            result = self._apply_curves(img.copy(), r_lut, g_lut, b_lut)
            result = ImageEnhance.Contrast(result).enhance(1.15 + t * 0.1)
            result = ImageEnhance.Color(result).enhance(0.8 + t * 0.15)
            return self._finish_filter(result)

        elif self.color_grade == "analog":
            # Analog warm — cross-processed look, slightly shifted colors
            t = self._get_filter_intensity()
            # Warm up reds, pull greens slightly, keep blues neutral
            r_lut = [min(255, max(0, int(i * (1.0 + t * 0.12)))) for i in range(256)]
            g_lut = [min(255, max(0, int(i * (1.0 + t * 0.04) - t * 5))) for i in range(256)]
            b_lut = [min(255, max(0, int(i * (0.95 - t * 0.05)))) for i in range(256)]
            result = self._apply_curves(img.copy(), r_lut, g_lut, b_lut)
            # Lift blacks slightly
            fade_floor = int(t * 18)
            if fade_floor > 0:
                lift_lut = [max(fade_floor, i) for i in range(256)]
                result = self._apply_curves(result, lift_lut, lift_lut, lift_lut)
            result = ImageEnhance.Color(result).enhance(1.1 + t * 0.15)
            result = ImageEnhance.Contrast(result).enhance(1.08)
            return self._finish_filter(result)

        elif self.color_grade == "cinema":
            # Cinematic — deep shadows, desaturated mid-tones, subtle teal/orange
            t = self._get_filter_intensity()
            result = img.copy()
            # Crush shadows
            shadow_lut = [max(0, int(i * (1.0 - t * 0.15))) for i in range(256)]
            result = self._apply_curves(result, shadow_lut, shadow_lut, shadow_lut)
            # Teal in shadows, orange in highlights
            r_lut = [min(255, max(0, int(i + (i / 255.0) * t * 15 - (1 - i / 255.0) * t * 8))) for i in range(256)]
            g_lut = list(range(256))
            b_lut = [min(255, max(0, int(i - (i / 255.0) * t * 10 + (1 - i / 255.0) * t * 12))) for i in range(256)]
            result = self._apply_curves(result, r_lut, g_lut, b_lut)
            result = ImageEnhance.Color(result).enhance(0.7 + (1 - t) * 0.2)
            result = ImageEnhance.Contrast(result).enhance(1.2 + t * 0.15)
            return self._finish_filter(result)

        return img

    # ── render everything ──────────────────────────────────────────────

    def _render(self):
        self.canvas.delete("all")
        self._sidebar_photos = []  # clear previous blurred sidebar refs

        # apply color grade to working image for display
        graded = self._apply_color_grade(self.working)

        # scaled image
        dw = max(1, int(self.img_w * self.zoom))
        dh = max(1, int(self.img_h * self.zoom))
        resized = graded.resize((dw, dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(self.offset_x, self.offset_y,
                                 anchor=tk.NW, image=self._photo)

        # crop box in canvas coords
        cx1, cy1 = self._i2c(self.crop_x, self.crop_y)
        cx2, cy2 = self._i2c(self.crop_x + self.crop_w,
                             self.crop_y + self.crop_h)

        # ── sidebar / overlay rendering ────────────────────────────────
        sidebar_rendered = False

        if self.aspect_ratio_mode == "1:1" and self.bg_mode != "none":
            # Fill the sidebar areas (outside crop box) with solid color or blur
            try:
                sidebar_regions = [
                    (0, 0, CANVAS_W, int(cy1)),                      # top
                    (0, int(cy2), CANVAS_W, CANVAS_H),               # bottom
                    (0, int(cy1), int(cx1), int(cy2)),               # left
                    (int(cx2), int(cy1), CANVAS_W, int(cy2)),        # right
                ]

                if self.bg_mode == "solid":
                    hex_color = self._rgb_to_hex(self.bg_color)
                    for coords in sidebar_regions:
                        if coords[2] > coords[0] and coords[3] > coords[1]:
                            self.canvas.create_rectangle(*coords, fill=hex_color, outline="")
                    sidebar_rendered = True

                elif self.bg_mode == "blur":
                    # Build a blurred version of the full displayed image
                    slider_val = self.blur_slider.get()
                    blur_radius = max(1, int(slider_val * (dw / 1920.0)))
                    blurred = resized.filter(ImageFilter.GaussianBlur(radius=blur_radius))

                    for coords in sidebar_regions:
                        sx1, sy1, sx2, sy2 = coords
                        # map canvas coords back to the resized-image pixel space
                        ix1 = int(sx1 - self.offset_x)
                        iy1 = int(sy1 - self.offset_y)
                        ix2 = int(sx2 - self.offset_x)
                        iy2 = int(sy2 - self.offset_y)
                        # clamp to valid range within the blurred image
                        ix1c = max(0, min(ix1, dw))
                        iy1c = max(0, min(iy1, dh))
                        ix2c = max(0, min(ix2, dw))
                        iy2c = max(0, min(iy2, dh))
                        rw = sx2 - sx1
                        rh = sy2 - sy1
                        if rw > 0 and rh > 0:
                            if ix2c > ix1c and iy2c > iy1c:
                                patch = blurred.crop((ix1c, iy1c, ix2c, iy2c)).resize(
                                    (rw, rh), Image.LANCZOS)
                            else:
                                patch = Image.new("RGB", (rw, rh), (0, 0, 0))
                            # store refs to prevent GC
                            ph = ImageTk.PhotoImage(patch)
                            if not hasattr(self, '_sidebar_photos'):
                                self._sidebar_photos = []
                            self._sidebar_photos.append(ph)
                            self.canvas.create_image(sx1, sy1, anchor=tk.NW, image=ph)
                    sidebar_rendered = True
            except Exception:
                pass

        # Render 16:9 output preview if active (overlays entire canvas)
        if self.aspect_ratio_mode == "1:1" and self.blur_bg_var.get() == 1:
            try:
                x1_img, y1_img = int(self.crop_x), int(self.crop_y)
                x2_img = x1_img + int(self.crop_w)
                y2_img = y1_img + int(self.crop_h)
                square_img = self._apply_color_grade(
                    self.working.crop((x1_img, y1_img, x2_img, y2_img))
                )

                bg_w, bg_h = CANVAS_W, CANVAS_H

                if self.bg_mode == "solid":
                    bg_final = Image.new("RGB", (bg_w, bg_h), self.bg_color)
                elif self.bg_mode == "blur":
                    bg_resized = square_img.resize((bg_w, bg_w), Image.Resampling.LANCZOS)
                    y_off = (bg_w - bg_h) // 2
                    bg_cropped = bg_resized.crop((0, y_off, bg_w, y_off + bg_h))
                    slider_val = self.blur_slider.get()
                    blur_radius = int(slider_val * (bg_w / 1920.0))
                    if blur_radius > 0:
                        bg_final = bg_cropped.filter(ImageFilter.GaussianBlur(radius=blur_radius))
                    else:
                        bg_final = bg_cropped
                else:
                    # none — black bg for 16:9
                    bg_final = Image.new("RGB", (bg_w, bg_h), (0, 0, 0))

                side = int(cx2 - cx1)
                if side > 0:
                    fg_resized = square_img.resize((side, side), Image.Resampling.LANCZOS)
                    bg_final.paste(fg_resized, (int(cx1), int(cy1)))

                self._preview_photo = ImageTk.PhotoImage(bg_final)
                self.canvas.create_image(0, 0, anchor=tk.NW, image=self._preview_photo)
                sidebar_rendered = True  # suppress dim overlays
            except Exception:
                pass

        # default dim overlays (black stipple) if no sidebar was rendered
        if not sidebar_rendered:
            for coords in [(0, 0, CANVAS_W, int(cy1)),
                           (0, int(cy2), CANVAS_W, CANVAS_H),
                           (0, int(cy1), int(cx1), int(cy2)),
                           (int(cx2), int(cy1), CANVAS_W, int(cy2))]:
                self.canvas.create_rectangle(*coords, fill="black",
                                             stipple="gray50", outline="")

        # crop border
        self.canvas.create_rectangle(cx1, cy1, cx2, cy2,
                                     outline="#00ff88", width=2, dash=(6, 3))

        # corner handles
        hs = 7
        for hx, hy in [(cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)]:
            self.canvas.create_rectangle(hx - hs, hy - hs, hx + hs, hy + hs,
                                         fill="#00ff88", outline="white")

        # edge midpoint handles (visual cue)
        ms = 4
        mx, my = (cx1 + cx2) / 2, (cy1 + cy2) / 2
        for hx, hy in [(mx, cy1), (mx, cy2), (cx1, my), (cx2, my)]:
            self.canvas.create_rectangle(hx - ms, hy - ms, hx + ms, hy + ms,
                                         fill="#00ff88", outline="white")

        # rule-of-thirds guides
        for frac in (1/3, 2/3):
            gx = cx1 + (cx2 - cx1) * frac
            gy = cy1 + (cy2 - cy1) * frac
            self.canvas.create_line(gx, cy1, gx, cy2, fill="#00ff88", dash=(2, 4), width=1)
            self.canvas.create_line(cx1, gy, cx2, gy, fill="#00ff88", dash=(2, 4), width=1)

        # info bar
        cw = int(self.crop_w)
        ch = int(self.crop_h)
        z = self.zoom * 100
        r = self.rotation % 360
        txt_count = len(self.text_overlays)
        extra = f"   |   Text: {txt_count}" if txt_count else ""
        self._info.config(
            text=f"Crop: {cw} × {ch} px   |   Zoom: {z:.0f}%   |   Rotation: {r}°{extra}"
        )

        # ── draw text overlays on canvas ──
        for idx, t in enumerate(self.text_overlays):
            tcx, tcy = self._i2c(t["img_x"], t["img_y"])
            font_family = _TK_FONT_FAMILIES.get(t["font"], "Helvetica")
            # size is stored as a visual size; draw it directly (no zoom multiplication)
            display_size = max(6, int(t["size"]))
            fill_hex = self._rgb_to_hex(t["color"])

            anchor = tk.N if t.get("center_x") else tk.NW
            if t.get("center_x"):
                # anchor at horizontal centre of crop box
                crop_mid_x = self.crop_x + self.crop_w / 2.0
                tcx, _ = self._i2c(crop_mid_x, 0)

            tid = self.canvas.create_text(
                tcx, tcy, text=t["text"],
                font=(font_family, display_size),
                fill=fill_hex, anchor=anchor, tags=f"text_{idx}"
            )
            # highlight selected
            if idx == self._selected_text_idx:
                bbox = self.canvas.bbox(tid)
                if bbox:
                    self.canvas.create_rectangle(
                        bbox[0] - 3, bbox[1] - 3, bbox[2] + 3, bbox[3] + 3,
                        outline="#00ff88", width=1, dash=(4, 2)
                    )

    # ── rotation ───────────────────────────────────────────────────────

    def _rotate_left(self):
        self.rotation = (self.rotation - 90) % 360
        self._update_working_image()
        self._init_crop()
        self._fit_view()
        self._render()

    def _rotate_right(self):
        self.rotation = (self.rotation + 90) % 360
        self._update_working_image()
        self._init_crop()
        self._fit_view()
        self._render()

    def _refresh_image(self):
        import glob
        dir_path = os.path.dirname(self.image_path) or "input"
        if not os.path.exists(dir_path):
            dir_path = "input"

        # Find all images in the input directory
        img_files = []
        for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG"]:
            img_files.extend(glob.glob(os.path.join(dir_path, f"*.{ext}")))

        if not img_files and dir_path != "input":
            # Search input/ as fallback
            for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG"]:
                img_files.extend(glob.glob(os.path.join("input", f"*.{ext}")))

        if not img_files:
            print("  ⚠️ No image files found to refresh.")
            return

        # Sort by modification time (newest first)
        img_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        newest_image = img_files[0]

        print(f"  [Cropper] Refreshing image. Newest image found: {os.path.basename(newest_image)}")
        try:
            self.image_path = newest_image
            self.original = Image.open(newest_image)
            self.rotation = 0
            self._update_working_image()
            self._init_crop()
            self._fit_view()
            self._render()
            print("  ✅ Image refreshed successfully!")
        except Exception as e:
            print(f"  ❌ Failed to reload image: {e}")

    # ── zoom ───────────────────────────────────────────────────────────

    def _zoom_in(self):
        self._apply_zoom(1.25)

    def _zoom_out(self):
        self._apply_zoom(0.8)

    def _zoom_fit(self):
        self._fit_view()
        self._render()

    def _center_crop(self):
        """Scroll the view so the current crop region is centred on-screen."""
        # centre of the crop in image coords
        crop_cx = self.crop_x + self.crop_w / 2.0
        crop_cy = self.crop_y + self.crop_h / 2.0
        # compute offset so that crop centre maps to canvas centre
        self.offset_x = CANVAS_W / 2.0 - crop_cx * self.zoom
        self.offset_y = CANVAS_H / 2.0 - crop_cy * self.zoom
        self._render()

    def _on_scroll(self, event):
        factor = 1.08 if event.delta > 0 else (1 / 1.08)
        self._apply_zoom(factor, event)

    def _apply_zoom(self, factor, event=None):
        old = self.zoom
        self.zoom = max(0.05, min(old * factor, 15.0))

        # zoom towards the cursor (or canvas centre)
        cx = event.x if event else CANVAS_W / 2
        cy = event.y if event else CANVAS_H / 2
        ratio = self.zoom / old
        self.offset_x = cx - (cx - self.offset_x) * ratio
        self.offset_y = cy - (cy - self.offset_y) * ratio
        self._render()

    # ── mouse: press / drag / release ──────────────────────────────────

    def _press(self, event):
        # ── if text tool is active, check if clicking an existing text label ──
        if self.text_tool_active:
            hit_idx = self._hit_test_text(event.x, event.y)
            if hit_idx is not None:
                self._selected_text_idx = hit_idx
                self._mode = "text_drag"
                self._drag_start = (event.x, event.y)
                t = self.text_overlays[hit_idx]
                self._text_drag_start = (t["img_x"], t["img_y"])
                self._render()
                return

        ix, iy = self._c2i(event.x, event.y)
        x1, y1 = self.crop_x, self.crop_y
        x2, y2 = x1 + self.crop_w, y1 + self.crop_h

        grab = max(10, 12 / self.zoom)  # handle hit-radius in image px

        # corners first
        corners = {"nw": (x1, y1), "ne": (x2, y1),
                   "sw": (x1, y2), "se": (x2, y2)}
        for name, (hx, hy) in corners.items():
            if abs(ix - hx) < grab and abs(iy - hy) < grab:
                self._mode = "resize"
                self._corner = name
                return

        # inside box → drag crop box
        if x1 <= ix <= x2 and y1 <= iy <= y2:
            self._mode = "drag"
            self._drag_start = (event.x, event.y)
            self._crop_start = (self.crop_x, self.crop_y)
        else:
            # outside box → pan the entire image
            self._mode = "pan"
            self._drag_start = (event.x, event.y)
            self._pan_start = (self.offset_x, self.offset_y)

    def _motion(self, event):
        if self._mode == "text_drag" and self._selected_text_idx is not None:
            dx = (event.x - self._drag_start[0]) / self.zoom
            dy = (event.y - self._drag_start[1]) / self.zoom
            t = self.text_overlays[self._selected_text_idx]
            t["img_x"] = self._text_drag_start[0] + dx
            t["img_y"] = self._text_drag_start[1] + dy
            self._render()

        elif self._mode == "pan":
            dx = event.x - self._drag_start[0]
            dy = event.y - self._drag_start[1]
            self.offset_x = self._pan_start[0] + dx
            self.offset_y = self._pan_start[1] + dy
            self._render()

        elif self._mode == "drag":
            dx = (event.x - self._drag_start[0]) / self.zoom
            dy = (event.y - self._drag_start[1]) / self.zoom
            nx = self._crop_start[0] + dx
            ny = self._crop_start[1] + dy

            # snap to image edges
            st = SNAP_THRESHOLD / self.zoom
            nx = _snap(nx, 0, st)
            ny = _snap(ny, 0, st)
            nx = _snap(nx, self.img_w - self.crop_w, st)
            ny = _snap(ny, self.img_h - self.crop_h, st)

            new_crop_x = max(0.0, min(nx, self.img_w - self.crop_w))
            new_crop_y = max(0.0, min(ny, self.img_h - self.crop_h))

            # Adjust view offsets so the crop box remains visually stationary on screen
            diff_x = new_crop_x - self.crop_x
            diff_y = new_crop_y - self.crop_y

            self.crop_x = new_crop_x
            self.crop_y = new_crop_y

            self.offset_x -= diff_x * self.zoom
            self.offset_y -= diff_y * self.zoom
            self._render()

        elif self._mode == "resize":
            ix, iy = self._c2i(event.x, event.y)
            c = self._corner
            r = 1.0 if self.aspect_ratio_mode == "1:1" else 16.0 / 9.0

            if c == "se":
                x1, y1 = self.crop_x, self.crop_y
                h = min((ix - x1) / r, iy - y1)
                h = min(h, self.img_h - y1, (self.img_w - x1) / r)
                h = max(MIN_CROP, h)
                self.crop_h = h
                self.crop_w = h * r
            elif c == "nw":
                x2 = self.crop_x + self.crop_w
                y2 = self.crop_y + self.crop_h
                h = min((x2 - ix) / r, y2 - iy)
                h = min(h, y2, x2 / r)
                h = max(MIN_CROP, h)
                self.crop_h = h
                self.crop_w = h * r
                self.crop_x = x2 - self.crop_w
                self.crop_y = y2 - self.crop_h
            elif c == "ne":
                x1 = self.crop_x
                y2 = self.crop_y + self.crop_h
                h = min((ix - x1) / r, y2 - iy)
                h = min(h, y2, (self.img_w - x1) / r)
                h = max(MIN_CROP, h)
                self.crop_h = h
                self.crop_w = h * r
                self.crop_y = y2 - self.crop_h
            elif c == "sw":
                x2 = self.crop_x + self.crop_w
                y1 = self.crop_y
                h = min((x2 - ix) / r, iy - y1)
                h = min(h, self.img_h - y1, x2 / r)
                h = max(MIN_CROP, h)
                self.crop_h = h
                self.crop_w = h * r
                self.crop_x = x2 - self.crop_w
            else:
                return

            self._clamp()
            self._render()

    def _release(self, _event):
        self._mode = None
        self._corner = None

    # ── text tool ──────────────────────────────────────────────────

    def _toggle_text_tool(self):
        self.text_tool_active = not self.text_tool_active
        if self.text_tool_active:
            self._text_toggle_btn.config(bg="#533483", fg="white")
            self._text_bar.pack(fill=tk.X, before=self.canvas)
            self.canvas.config(cursor="text")
        else:
            self._text_toggle_btn.config(bg="#0f3460", fg="white")
            self._text_bar.pack_forget()
            self.canvas.config(cursor="crosshair")
            self._selected_text_idx = None
        self._render()

    def _double_click(self, event):
        """Double-click on canvas to place a new text label."""
        if not self.text_tool_active:
            return
        # convert canvas click to image coords
        ix, iy = self._c2i(event.x, event.y)
        self._prompt_text_input(ix, iy)

    def _prompt_text_input(self, img_x, img_y):
        """Open a small dialog to type text."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Add Text")
        dlg.configure(bg="#1a1a2e")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Enter text:", font=("Helvetica", 13),
                 bg="#1a1a2e", fg="white").pack(padx=16, pady=(12, 4))

        entry = tk.Entry(dlg, font=("Helvetica", 14), width=30,
                         bg="#16213e", fg="white", insertbackground="white",
                         relief="flat", bd=4)
        entry.pack(padx=16, pady=4)
        entry.focus_set()

        def _submit(_event=None):
            txt = entry.get().strip()
            if txt:
                self.text_overlays.append({
                    "text":  txt,
                    "img_x": img_x,
                    "img_y": img_y,
                    "font":  self._text_font,
                    "size":  self._text_size,
                    "color": self._text_color,
                    "center_x": False,
                })
                self._selected_text_idx = len(self.text_overlays) - 1
            dlg.destroy()
            self._render()

        entry.bind("<Return>", _submit)
        tk.Button(dlg, text="Add", command=_submit,
                  font=("Helvetica", 12, "bold"), padx=12, pady=4,
                  bg="#1a936f", fg="white", activebackground="#114b5f",
                  activeforeground="white", relief="flat", cursor="hand2",
                  borderwidth=0, highlightthickness=0
                  ).pack(pady=(4, 12))

        # centre the dialog on the main window
        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

    def _hit_test_text(self, cx, cy):
        """Return index of text overlay under (cx, cy) canvas coords, or None."""
        for idx in range(len(self.text_overlays) - 1, -1, -1):  # top-most first
            t = self.text_overlays[idx]
            tcx, tcy = self._i2c(t["img_x"], t["img_y"])
            font_family = _TK_FONT_FAMILIES.get(t["font"], "Helvetica")
            display_size = max(6, int(t["size"] * self.zoom))
            # estimate bounding box with a temporary canvas text item
            tmp = self.canvas.create_text(
                tcx, tcy, text=t["text"],
                font=(font_family, display_size), anchor=tk.NW
            )
            bbox = self.canvas.bbox(tmp)
            self.canvas.delete(tmp)
            if bbox and bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                return idx
        return None

    def _on_font_change(self, val):
        self._text_font = val
        # update selected text if any
        if self._selected_text_idx is not None:
            self.text_overlays[self._selected_text_idx]["font"] = val
            self._render()

    def _on_text_size_change(self, val):
        self._text_size = int(val)
        if self._selected_text_idx is not None:
            self.text_overlays[self._selected_text_idx]["size"] = self._text_size
            self._render()

    def _pick_text_color(self):
        color = colorchooser.askcolor(
            initialcolor=self._rgb_to_hex(self._text_color),
            title="Choose Text Color"
        )
        if color and color[0]:
            self._text_color = tuple(int(c) for c in color[0])
            self._text_color_swatch.configure(bg=self._rgb_to_hex(self._text_color))
            if self._selected_text_idx is not None:
                self.text_overlays[self._selected_text_idx]["color"] = self._text_color
            self._render()

    def _delete_selected_text(self):
        if self._selected_text_idx is not None and self._selected_text_idx < len(self.text_overlays):
            self.text_overlays.pop(self._selected_text_idx)
            self._selected_text_idx = None
            self._render()

    def _center_text_x(self):
        """Toggle horizontal centering for the selected text within the crop box."""
        if self._selected_text_idx is not None and self._selected_text_idx < len(self.text_overlays):
            t = self.text_overlays[self._selected_text_idx]
            t["center_x"] = not t.get("center_x", False)
            self._render()

    def _get_color_config(self):
        return {
            "color_grade": self.color_grade,
            "maroon_intensity": self.maroon_slider.get() if hasattr(self, "maroon_slider") else 35,
            "purple_intensity": self.purple_slider.get() if hasattr(self, "purple_slider") else 35,
            "filter_intensity": self.filter_intensity_slider.get() if hasattr(self, "filter_intensity_slider") else 50,
            "saturation": self.sat_slider.get() if hasattr(self, "sat_slider") else 100,
            "contrast": self.contrast_slider.get() if hasattr(self, "contrast_slider") else 100,
            "brightness": self.brightness_slider.get() if hasattr(self, "brightness_slider") else 100,
            "shade": self.shade_slider.get() if hasattr(self, "shade_slider") else 0,
            "vignette": self.vignette_slider.get() if hasattr(self, "vignette_slider") else 0,
            "glow": self.glow_slider.get() if hasattr(self, "glow_slider") else 0,
            "sparkles": self.sparkle_slider.get() if hasattr(self, "sparkle_slider") else 0,
            "bg_mode": self.bg_mode,
            "bg_color": list(self.bg_color) if hasattr(self, "bg_color") else [30, 30, 30],
            "blur_amount": self.blur_slider.get() if hasattr(self, "blur_slider") else 25,
            "blur_bg_out": self.blur_bg_var.get() if hasattr(self, "blur_bg_var") else 1,
            "hdr_thumbnail": self._hdr_thumb_var.get() if hasattr(self, "_hdr_thumb_var") else 1,
            "hdr_video": self._hdr_video_var.get() if hasattr(self, "_hdr_video_var") else 0,
            "selected_frame_time": self.selected_time,
            "video_speed": self.speed_slider.get() if hasattr(self, "speed_slider") else 1.0,
        }

    # ── actions ────────────────────────────────────────────────────────

    def _do_crop(self):
        x1, y1 = int(self.crop_x), int(self.crop_y)
        x2 = x1 + int(self.crop_w)
        y2 = y1 + int(self.crop_h)
        cropped = self._apply_color_grade(self.working.crop((x1, y1, x2, y2)))

        # Check if 16:9 output is selected (only applicable in 1:1 mode)
        is_16_9_padded = (hasattr(self, "blur_bg_var") and 
                          self.blur_bg_var.get() == 1 and 
                          self.aspect_ratio_mode == "1:1")

        # Always save the 1:1 cropped square image to self.output_image_path.
        # This keeps intermediate files clean for downstream thumbnail & video composition.
        
        # Burn text directly onto the cropped image
        if self.text_overlays:
            draw = ImageDraw.Draw(cropped)
            for t in self.text_overlays:
                img_font_size = max(8, int(t["size"] / self.zoom))
                font_path = INSTAGRAM_FONTS.get(t["font"])
                try:
                    pil_font = ImageFont.truetype(font_path, img_font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()
                except Exception:
                    pil_font = ImageFont.load_default()

                # Convert canvas coordinates to cropped image coordinates
                tcx, tcy = self._i2c(t["img_x"], t["img_y"])
                cx1, cy1 = self._i2c(self.crop_x, self.crop_y)
                
                if t.get("center_x"):
                    crop_mid_x = cx1 + (self.crop_w * self.zoom) / 2.0
                    tcx = crop_mid_x

                tx = (tcx - cx1) / self.zoom
                ty = (tcy - cy1) / self.zoom

                anchor_mode = "mt" if t.get("center_x") else "lt"
                draw.text((int(tx), int(ty)), t["text"], fill=t["color"], font=pil_font, anchor=anchor_mode)

        # Determine if it's a JPEG and save with high quality
        save_kwargs = {}
        ext = os.path.splitext(self.output_image_path)[1].lower()
        if ext in [".jpg", ".jpeg"]:
            save_kwargs["quality"] = 95
            save_kwargs["subsampling"] = 0

        cropped.save(self.output_image_path, **save_kwargs)
        cw, ch = int(self.crop_w), int(self.crop_h)
        grade_label = f" [{self.color_grade}]" if self.color_grade != "none" else ""
        print(f"  ✅ Cropped to {cw}×{ch}{grade_label} → {self.output_image_path}")

        # If 16:9 padded is selected, also save the 16:9 padded version to a separate path
        if is_16_9_padded:
            target_w, target_h = 1920, 1080

            if self.bg_mode == "solid":
                bg_out = Image.new("RGB", (target_w, target_h), self.bg_color)
            elif self.bg_mode == "blur":
                bg_resized = cropped.resize((target_w, target_w), Image.Resampling.LANCZOS)
                y_offset = (target_w - target_h) // 2
                bg_cropped = bg_resized.crop((0, y_offset, target_w, y_offset + target_h))
                slider_val = self.blur_slider.get()
                if slider_val > 0:
                    bg_out = bg_cropped.filter(ImageFilter.GaussianBlur(radius=slider_val))
                else:
                    bg_out = bg_cropped
            else:
                bg_out = Image.new("RGB", (target_w, target_h), (0, 0, 0))

            # Scale foreground square to match its relative size in the GUI viewport
            cx1, cy1 = self._i2c(self.crop_x, self.crop_y)
            cx2, cy2 = self._i2c(self.crop_x + self.crop_w, self.crop_y + self.crop_h)
            side = cx2 - cx1

            fg_h = int(target_h * (side / CANVAS_H))
            fg_h = max(MIN_CROP, min(fg_h, target_h))
            fg_resized = cropped.resize((fg_h, fg_h), Image.Resampling.LANCZOS)

            x_pos = int(target_w * (cx1 / CANVAS_W))
            y_pos = int(target_h * (cy1 / CANVAS_H))
            bg_out.paste(fg_resized, (x_pos, y_pos))

            # Burn text directly onto the final 16:9 output image
            if self.text_overlays:
                draw = ImageDraw.Draw(bg_out)
                scale_x = 1920.0 / CANVAS_W
                scale_y = 1080.0 / CANVAS_H
                for t in self.text_overlays:
                    img_font_size = max(8, int(t["size"] * scale_x))
                    font_path = INSTAGRAM_FONTS.get(t["font"])
                    try:
                        pil_font = ImageFont.truetype(font_path, img_font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()
                    except Exception:
                        pil_font = ImageFont.load_default()

                    tcx, tcy = self._i2c(t["img_x"], t["img_y"])
                    if t.get("center_x"):
                        crop_mid_x = cx1 + side / 2.0
                        tcx = crop_mid_x
                    
                    tx = tcx * scale_x
                    ty = tcy * scale_y
                    anchor_mode = "mt" if t.get("center_x") else "lt"
                    draw.text((int(tx), int(ty)), t["text"], fill=t["color"], font=pil_font, anchor=anchor_mode)

            padded_path = os.path.splitext(self.output_image_path)[0] + ".16_9.png"
            bg_out.save(padded_path, "PNG")
            print(f"  ✅ Saved standalone 16:9 preview with {self.bg_mode} background → {padded_path}")

        # Save crop information to a metadata file for video frame cropping
        import json
        crop_info_path = self.crop_json if self.crop_json else (self.output_image_path + ".crop.json")
        crop_info_data = {
            "x1": int(self.crop_x),
            "y1": int(self.crop_y),
            "x2": int(self.crop_x + self.crop_w),
            "y2": int(self.crop_y + self.crop_h),
            "rotation": self.rotation,
            "color_adjustments": self._get_color_config()
        }
        try:
            if crop_info_path.endswith(".config.json") and os.path.exists(crop_info_path):
                with open(crop_info_path, "r") as f:
                    cfg_data = json.load(f)
                cfg_data["crop_info"] = crop_info_data
                with open(crop_info_path, "w") as f:
                    json.dump(cfg_data, f, indent=2)
            else:
                with open(crop_info_path, "w") as f:
                    json.dump(crop_info_data, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Warning: Failed to save crop config: {e}")
            
        self.root.destroy()

    def _skip(self):
        if self.is_video or self.rotation % 360 != 0:
            # Determine if it's a JPEG and save with high quality
            save_kwargs = {}
            ext = os.path.splitext(self.output_image_path)[1].lower()
            if ext in [".jpg", ".jpeg"]:
                save_kwargs["quality"] = 95
                save_kwargs["subsampling"] = 0

            # Apply color adjustments if present before exporting skip frame
            adjusted_working = self._apply_color_grade(self.working)
            adjusted_working.save(self.output_image_path, **save_kwargs)
            print(f"  ↳ Saved frame to {self.output_image_path} (no manual crop applied).")
        else:
            print("  ↳ Skipped cropping.")

        # Save skip information to a metadata file for video frame cropping
        import json
        crop_info_path = self.output_image_path + ".crop.json"
        try:
            with open(crop_info_path, "w") as f:
                json.dump({
                    "x1": 0,
                    "y1": 0,
                    "x2": self.original.width,
                    "y2": self.original.height,
                    "rotation": self.rotation,
                    "color_adjustments": self._get_color_config()
                }, f, indent=2)
        except Exception as e:
            print(f"  ⚠️ Warning: Failed to save skip crop config JSON: {e}")

        self.root.destroy()

    def _on_timeline_slide(self, val):
        time_s = float(val)
        self.selected_time = time_s
        self.time_label.configure(text=f"Timeline Frame Selection: {time_s:.2f}s / {self.video_duration:.2f}s")

    def _on_timeline_release(self, event):
        print(f"  [Timeline] Extracting video frame at {self.selected_time:.3f}s...")
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        
        new_frame = self._extract_frame_at_time(self.selected_time)
        if new_frame:
            self.original = new_frame
            self._update_working_image()
            self._render()
            
        self.root.configure(cursor="")

    # ── run ────────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()
        return self.output_image_path


# ── helpers ────────────────────────────────────────────────────────────

def _snap(val, target, threshold):
    return target if abs(val - target) < threshold else val


def crop_image(image_path, mode="1:1", crop_json=None):
    """Open the interactive cropper with the specified aspect ratio mode and return the image path."""
    app = CropperApp(image_path, default_mode=mode, crop_json=crop_json)
    return app.run()


def crop_to_square(image_path, crop_json=None):
    """Open the interactive cropper defaulting to 1:1 and return the image path."""
    # Spawn the cropper in a subprocess to prevent Tkinter multiple-root freezing issues
    import sys
    import subprocess
    print(f"  [Subprocess] Spawning cropper GUI for: {os.path.basename(image_path)}")
    cmd = [sys.executable, "cropper.py", image_path, "1:1"]
    if crop_json:
        cmd.append(crop_json)
    subprocess.run(cmd)
    
    ext = os.path.splitext(image_path)[1].lstrip(".").lower()
    is_video = ext in ["mp4", "mov", "webm", "gif", "avi", "mkv"]
    if is_video:
        return os.path.join(os.path.dirname(image_path), "_center_first_frame.png")
    return image_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 cropper.py <image_path> [mode] [crop_json]")
        sys.exit(1)
    img_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "1:1"
    crop_json = sys.argv[3] if len(sys.argv) > 3 else None
    crop_image(img_path, mode=mode, crop_json=crop_json)
