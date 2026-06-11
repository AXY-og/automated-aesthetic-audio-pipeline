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
from PIL import Image, ImageTk

CANVAS_W, CANVAS_H = 1000, 750
MIN_CROP = 50                    # smallest allowed crop (image px)
SNAP_THRESHOLD = 12              # canvas px — snap distance to edges


class CropperApp:
    def __init__(self, image_path):
        self.image_path = image_path
        self.original = Image.open(image_path)
        self.rotation = 0                      # cumulative degrees (0/90/180/270)

        self._update_working_image()           # sets self.working, img_w, img_h
        self._init_crop()                      # largest centred square
        self._fit_view()                       # zoom / offset to fill canvas

        # interaction state
        self._mode = None          # "drag" | "resize"
        self._corner = None
        self._drag_start = None
        self._crop_start = None

        self._build()

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
        side = min(self.img_w, self.img_h)
        self.crop_x = (self.img_w - side) / 2.0
        self.crop_y = (self.img_h - side) / 2.0
        self.crop_size = float(side)

    def _clamp(self):
        self.crop_size = max(MIN_CROP, min(self.crop_size, self.img_w, self.img_h))
        self.crop_x = max(0.0, min(self.crop_x, self.img_w - self.crop_size))
        self.crop_y = max(0.0, min(self.crop_y, self.img_h - self.crop_size))

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
        self.root.title("Xenia — Crop to 1:1")
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(False, False)

        # ── toolbar ──
        bar = tk.Frame(self.root, bg="#16213e", pady=8, padx=8)
        bar.pack(fill=tk.X)

        s = {"font": ("Helvetica", 13), "padx": 12, "pady": 4,
             "bg": "#0f3460", "fg": "white", "activebackground": "#533483",
             "activeforeground": "white", "relief": "flat", "cursor": "hand2",
             "borderwidth": 0, "highlightthickness": 0}

        tk.Button(bar, text="↺  Rotate Left",  command=self._rotate_left,  **s).pack(side=tk.LEFT, padx=4)
        tk.Button(bar, text="↻  Rotate Right", command=self._rotate_right, **s).pack(side=tk.LEFT, padx=4)

        tk.Frame(bar, width=24, bg="#16213e").pack(side=tk.LEFT)

        tk.Button(bar, text="＋ Zoom In",  command=self._zoom_in,  **s).pack(side=tk.LEFT, padx=4)
        tk.Button(bar, text="－ Zoom Out", command=self._zoom_out, **s).pack(side=tk.LEFT, padx=4)
        tk.Button(bar, text="Fit",         command=self._zoom_fit, **s).pack(side=tk.LEFT, padx=4)

        # right-aligned action buttons
        tk.Button(bar, text="Skip", command=self._skip, **s).pack(side=tk.RIGHT, padx=4)
        tk.Button(bar, text="✅  Crop & Continue", command=self._do_crop,
                  font=("Helvetica", 13, "bold"), padx=16, pady=4,
                  bg="#1a936f", fg="white", activebackground="#114b5f",
                  activeforeground="white", relief="flat", cursor="hand2",
                  borderwidth=0, highlightthickness=0).pack(side=tk.RIGHT, padx=4)

        # ── canvas ──
        self.canvas = tk.Canvas(self.root, width=CANVAS_W, height=CANVAS_H,
                                bg="#1a1a2e", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=10, pady=(0, 6))

        # ── info label ──
        self._info = tk.Label(self.root, text="", font=("Menlo", 11),
                              bg="#1a1a2e", fg="#999999")
        self._info.pack(pady=(0, 10))

        # ── key / mouse bindings ──
        self.canvas.bind("<ButtonPress-1>",  self._press)
        self.canvas.bind("<B1-Motion>",       self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>",      self._on_scroll)           # macOS / Windows
        self.canvas.bind("<Button-4>", lambda e: self._apply_zoom(1.1, e))  # Linux up
        self.canvas.bind("<Button-5>", lambda e: self._apply_zoom(0.9, e))  # Linux down

        self._render()

    # ── render everything ──────────────────────────────────────────────

    def _render(self):
        self.canvas.delete("all")

        # background checkerboard is implied by canvas bg

        # scaled image
        dw = max(1, int(self.img_w * self.zoom))
        dh = max(1, int(self.img_h * self.zoom))
        resized = self.working.resize((dw, dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(self.offset_x, self.offset_y,
                                 anchor=tk.NW, image=self._photo)

        # crop box in canvas coords
        cx1, cy1 = self._i2c(self.crop_x, self.crop_y)
        cx2, cy2 = self._i2c(self.crop_x + self.crop_size,
                              self.crop_y + self.crop_size)

        # image edges in canvas coords (for dim overlays)
        ix1, iy1 = self._i2c(0, 0)
        ix2, iy2 = self._i2c(self.img_w, self.img_h)

        # dim outside crop (4 rectangles with stipple)
        for coords in [(ix1, iy1, ix2, cy1),        # top
                       (ix1, cy2, ix2, iy2),        # bottom
                       (ix1, cy1, cx1, cy2),        # left
                       (cx2, cy1, ix2, cy2)]:       # right
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
        sz = int(self.crop_size)
        z = self.zoom * 100
        r = self.rotation % 360
        self._info.config(
            text=f"Crop: {sz} × {sz} px   |   Zoom: {z:.0f}%   |   Rotation: {r}°"
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

    # ── zoom ───────────────────────────────────────────────────────────

    def _zoom_in(self):
        self._apply_zoom(1.25)

    def _zoom_out(self):
        self._apply_zoom(0.8)

    def _zoom_fit(self):
        self._fit_view()
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
        ix, iy = self._c2i(event.x, event.y)
        x1, y1 = self.crop_x, self.crop_y
        x2, y2 = x1 + self.crop_size, y1 + self.crop_size

        grab = max(10, 12 / self.zoom)  # handle hit-radius in image px

        # corners first
        corners = {"nw": (x1, y1), "ne": (x2, y1),
                   "sw": (x1, y2), "se": (x2, y2)}
        for name, (hx, hy) in corners.items():
            if abs(ix - hx) < grab and abs(iy - hy) < grab:
                self._mode = "resize"
                self._corner = name
                return

        # inside box → drag
        if x1 <= ix <= x2 and y1 <= iy <= y2:
            self._mode = "drag"
            self._drag_start = (event.x, event.y)
            self._crop_start = (self.crop_x, self.crop_y)

    def _motion(self, event):
        if self._mode == "drag":
            dx = (event.x - self._drag_start[0]) / self.zoom
            dy = (event.y - self._drag_start[1]) / self.zoom
            nx = self._crop_start[0] + dx
            ny = self._crop_start[1] + dy

            # snap to image edges
            st = SNAP_THRESHOLD / self.zoom
            nx = _snap(nx, 0, st)
            ny = _snap(ny, 0, st)
            nx = _snap(nx, self.img_w - self.crop_size, st)
            ny = _snap(ny, self.img_h - self.crop_size, st)

            self.crop_x = max(0, min(nx, self.img_w - self.crop_size))
            self.crop_y = max(0, min(ny, self.img_h - self.crop_size))
            self._render()

        elif self._mode == "resize":
            ix, iy = self._c2i(event.x, event.y)
            c = self._corner

            if c == "se":
                ns = min(ix - self.crop_x, iy - self.crop_y)
                ns = min(ns, self.img_w - self.crop_x, self.img_h - self.crop_y)
            elif c == "nw":
                ax = self.crop_x + self.crop_size
                ay = self.crop_y + self.crop_size
                ns = min(ax - ix, ay - iy, ax, ay)
                ns = max(MIN_CROP, ns)
                self.crop_x, self.crop_y = ax - ns, ay - ns
            elif c == "ne":
                ay = self.crop_y + self.crop_size
                ns = min(ix - self.crop_x, ay - iy)
                ns = min(ns, self.img_w - self.crop_x, ay)
                ns = max(MIN_CROP, ns)
                self.crop_y = ay - ns
            elif c == "sw":
                ax = self.crop_x + self.crop_size
                ns = min(ax - ix, iy - self.crop_y)
                ns = min(ns, ax, self.img_h - self.crop_y)
                ns = max(MIN_CROP, ns)
                self.crop_x = ax - ns
            else:
                return

            self.crop_size = max(MIN_CROP, ns)
            self._clamp()
            self._render()

    def _release(self, _event):
        self._mode = None
        self._corner = None

    # ── actions ────────────────────────────────────────────────────────

    def _do_crop(self):
        x1, y1 = int(self.crop_x), int(self.crop_y)
        x2 = x1 + int(self.crop_size)
        y2 = y1 + int(self.crop_size)
        cropped = self.working.crop((x1, y1, x2, y2))
        cropped.save(self.image_path)
        sz = int(self.crop_size)
        print(f"  ✅ Cropped to {sz}×{sz} → {self.image_path}")
        self.root.destroy()

    def _skip(self):
        if self.rotation % 360 != 0:
            self.working.save(self.image_path)
            print(f"  ↳ Saved rotated image ({self.rotation % 360}°), no crop applied.")
        else:
            print("  ↳ Skipped cropping.")
        self.root.destroy()

    # ── run ────────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()
        return self.image_path


# ── helpers ────────────────────────────────────────────────────────────

def _snap(val, target, threshold):
    return target if abs(val - target) < threshold else val


def crop_to_square(image_path):
    """Open the interactive cropper and return the (possibly cropped) image path."""
    app = CropperApp(image_path)
    return app.run()
