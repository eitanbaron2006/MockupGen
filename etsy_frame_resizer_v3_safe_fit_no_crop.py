"""
Etsy Frame Resizer — Python/Tkinter

Requirements: pip install Pillow
Run: python etsy_frame_resizer.py
"""

import math
import os
import threading
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageFilter, ImageTk
import sqlite3
import subprocess, tempfile, pathlib

# Allow very large images from AI 4× upscale (ncnn/Gigapixel output)
Image.MAX_IMAGE_PIXELS = None

# ── SQLite Database Setup ─────────────────────────────────────────────────────
DB_FILE = "etsy_resizer_settings.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        value TEXT
    )
    """)
    
    # Sizes table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sizes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        label TEXT,
        filename TEXT,
        w INTEGER,
        h INTEGER,
        sizes TEXT,
        is_custom INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
    )
    """)
    
    # Seed default settings
    default_settings = [
        ("ncnn_exe_path", r"C:\realesrgan\realesrgan-ncnn-vulkan.exe"),
        ("tpai_exe_path", r"C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe"),
        ("default_output_folder", ""),
        ("theme", "light")
    ]
    for key, val in default_settings:
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        
    # Seed default sizes
    default_sizes = [
        ("2:3 Ratio", "2:3", "01_2x3_ratio_24x36_inch.jpg", 7200, 10800, "4x6, 8x12, 12x18, 16x24, 20x30, 24x36", 0, 1),
        ("3:4 Ratio", "3:4", "02_3x4_ratio_18x24_inch.jpg", 5400, 7200, "6x8, 9x12, 12x16, 15x20, 18x24", 0, 1),
        ("4:5 Ratio", "4:5", "03_4x5_ratio_24x30_inch.jpg", 7200, 9000, "4x5, 8x10, 12x15, 16x20, 20x25, 24x30", 0, 1),
        ("11:14 Ratio", "11:14", "04_11x14_ratio_22x28_inch.jpg", 6600, 8400, "11x14, 22x28", 0, 1),
        ("A-Series Ratio", "A", "05_A_series_ratio_A1.jpg", 7016, 9933, "A5, A4, A3, A2, A1", 0, 1),
        ("1:1 Square", "1:1", "06_1x1_square_ratio_24x24_inch.jpg", 7200, 7200, "5x5, 8x8, 10x10, 12x12, 16x16, 20x20, 24x24", 0, 1),
        # Pre-populated but inactive sizes
        ("5:7 Ratio", "5:7", "07_5x7_ratio_50x70_cm.jpg", 5000, 7000, "5x7, 50x70cm", 0, 0),
        ("US Letter", "US Letter", "08_letter_8.5x11_inch.jpg", 5100, 6600, "8.5x11", 0, 0),
        ("3:1 Panoramic", "3:1", "09_3x1_panoramic_36x12_inch.jpg", 10800, 3600, "36x12, 30x10", 0, 0),
        ("2:1 Panoramic", "2:1", "10_2x1_panoramic_36x18_inch.jpg", 10800, 5400, "36x18, 24x12", 0, 0)
    ]
    
    for row in default_sizes:
        cursor.execute("""
        INSERT OR IGNORE INTO sizes (name, label, filename, w, h, sizes, is_custom, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        
    conn.commit()
    conn.close()

def get_setting(key, default=""):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default

def set_setting(key, value):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving setting {key}: {e}")
        return False

def get_active_sizes():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT name, label, filename, w, h, sizes FROM sizes WHERE is_active=1")
        rows = cursor.fetchall()
        conn.close()
        
        outputs = []
        for r in rows:
            outputs.append({
                "name": r[0],
                "label": r[1],
                "filename": r[2],
                "w": r[3],
                "h": r[4],
                "sizes": r[5]
            })
        return outputs
    except Exception as e:
        print(f"Error fetching active sizes: {e}")
        return []

def reload_size_groups():
    global SIZE_GROUPS
    active_outputs = get_active_sizes()
    SIZE_GROUPS = [("Etsy Printable Ratio Files", active_outputs)]

# Initialize DB on import
init_db()
active_outputs_init = get_active_sizes()
SIZE_GROUPS = [("Etsy Printable Ratio Files", active_outputs_init)]

# ── Real-ESRGAN ncnn-vulkan ───────────────────────────────────────────────────
def scale_ai_ncnn(img, tw, th):
    exe_path_str = get_setting("ncnn_exe_path", r"C:\realesrgan\realesrgan-ncnn-vulkan.exe")
    ncnn_exe = pathlib.Path(exe_path_str)
    if not ncnn_exe.exists():
        raise RuntimeError(
            f"לא נמצא:\n{ncnn_exe}\n\n"
            "ודא שחילצת את realesrgan-ncnn-vulkan או הגדרת את המיקום הנכון בהגדרות."
        )
    with tempfile.TemporaryDirectory() as tmp:
        src_path = pathlib.Path(tmp) / "input.png"
        dst_path = pathlib.Path(tmp) / "output.png"
        img.convert("RGB").save(str(src_path), "PNG")
        result = subprocess.run(
            [str(ncnn_exe),
             "-i", str(src_path),
             "-o", str(dst_path),
             "-n", "realesrgan-x4plus",
             "-j", "1:1:1"],
            capture_output=True, text=True
        )
        if result.returncode != 0 or not dst_path.exists():
            raise RuntimeError(
                f"realesrgan-ncnn-vulkan נכשל:\n{result.stderr or result.stdout}"
            )
        out_img = Image.open(str(dst_path)).copy()
    import time; time.sleep(3)
    if out_img.size != (tw, th):
        out_img = out_img.resize((tw, th), Image.LANCZOS)
    return out_img.convert("RGBA")

# ── Topaz Photo AI ───────────────────────────────────────────────────────────
def scale_ai_gigapixel(img, tw, th):
    exe_path_str = get_setting("tpai_exe_path", r"C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe")
    tpai_exe = pathlib.Path(exe_path_str)
    if not tpai_exe.exists():
        raise RuntimeError(
            f"לא נמצא:\n{tpai_exe}\n\n"
            "ודא ש-Topaz Photo AI מותקן או הגדרת את המיקום הנכון בהגדרות."
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        out_dir  = tmp_path / "out"
        out_dir.mkdir()
        src_path = tmp_path / "input.png"
        img.convert("RGB").save(str(src_path), "PNG")

        print(f"[Topaz] Processing single image → {tw}×{th} ...")
        result = subprocess.run(
            [str(tpai_exe),
             str(src_path),
             "-o", str(out_dir),
             "--format", "png",
             "--upscale", "scale=4",
             "--override"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )
        print(f"[Topaz] Done. Return code: {result.returncode}")
        if result.stdout: print(result.stdout[-500:])

        candidates = list(out_dir.glob("*.png"))
        if not candidates:
            raise RuntimeError(
                f"Topaz לא יצר פלט.\nstderr: {result.stderr or result.stdout}"
            )
        out_img = Image.open(str(candidates[0])).copy()

    if out_img.size != (tw, th):
        out_img = out_img.resize((tw, th), Image.LANCZOS)
    return out_img.convert("RGBA")

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = "#efe7d2"  # Warm page parchment bg
SURFACE = "#f7f1de"  # Lighter cream beige panel bg
BORDER  = "#d5cdb8"  # Solid warm beige border
ACCENT  = "#ed6f5c"  # Coral red accent
ACCENT2 = "#faeae6"  # Soft accent tint (light coral pink)
TEXT    = "#15140f"  # Ink / Dark Charcoal text
MUTED   = "#5a5448"  # Muted brown-grey secondary text
SUCCESS = "#6e7448"  # Olive green success state
FOLDER_ACTIVE = "#e2e6c7"   # Soft warm olive-green tint when folder is active

# ── Algorithms ────────────────────────────────────────────────────────────────
def scale_basic(img, w, h):
    return img.resize((w, h), Image.LANCZOS)

def scale_step(img, w, h):
    cur = img.copy()
    cw, ch = cur.size
    while cw < w or ch < h:
        nw = min(math.ceil(cw * 1.5), w)
        nh = min(math.ceil(ch * 1.5), h)
        cur = cur.resize((nw, nh), Image.LANCZOS)
        cw, ch = nw, nh
    return cur

def apply_unsharp(img, amount=0.5, radius=1.0):
    return img.filter(ImageFilter.UnsharpMask(
        radius=radius, percent=int(amount * 100), threshold=0))

def scale_bicubic(img, tw, th):
    sw, sh = img.size
    step_target_w = max(sw, round(tw * 2 / 3))
    step_target_h = max(sh, round(th * 2 / 3))
    if step_target_w > sw or step_target_h > sh:
        source = scale_step(img, step_target_w, step_target_h)
    else:
        source = img
    return source.resize((tw, th), Image.BICUBIC)

def process_image(img, w, h, quality):
    if   quality == "basic":        return scale_basic(img, w, h)
    elif quality == "step":         return scale_step(img, w, h)
    elif quality == "step-unsharp": return apply_unsharp(scale_step(img, w, h), 0.5, 1.0)
    elif quality == "bicubic":      return apply_unsharp(scale_bicubic(img, w, h), 0.4, 0.8)
    elif quality == "ai":           return scale_ai_ncnn(img, w, h)
    elif quality == "gigapixel":    return scale_ai_gigapixel(img, w, h)
    return img


def center_crop_to_ratio(img, target_w, target_h):
    """Crop the source image to the target aspect ratio without resizing yet.

    This is kept only as an optional helper for the blurred-background mode.
    The main artwork itself is NOT cropped in the safe Etsy export modes.
    """
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_w = round(src_h * target_ratio)
        left = (src_w - new_w) // 2
        return img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = round(src_w / target_ratio)
        top = (src_h - new_h) // 2
        return img.crop((0, top, src_w, top + new_h))


def paste_center(canvas, layer):
    """Paste an RGBA layer in the center of an RGBA canvas."""
    x = (canvas.width - layer.width) // 2
    y = (canvas.height - layer.height) // 2
    canvas.paste(layer, (x, y), layer if layer.mode == "RGBA" else None)
    return canvas


def fit_whole_artwork(img, target_w, target_h, quality):
    """Resize the full source image so it fits inside the target canvas. No crop."""
    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
    fitted_w = max(1, round(src_w * scale))
    fitted_h = max(1, round(src_h * scale))
    return process_image(img, fitted_w, fitted_h, quality).convert("RGBA")


def make_blurred_background(img, target_w, target_h, quality):
    """Create a soft full-bleed background from the artwork itself.

    The background may be cropped/blurred, but the real artwork pasted on top
    remains complete and uncropped. This avoids empty white margins while still
    preserving frames, borders, text, and edge details in the original artwork.
    """
    bg_crop = center_crop_to_ratio(img, target_w, target_h)
    bg = process_image(bg_crop, target_w, target_h, quality).convert("RGBA")
    blur_radius = max(18, round(max(target_w, target_h) / 90))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # Add a subtle white wash so the background stays elegant for wall art,
    # not too noisy or distracting behind the complete artwork.
    wash = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 70))
    bg.alpha_composite(wash)
    return bg


def render_etsy_output(img, target_w, target_h, quality, fit_mode="fit_white"):
    """Create the final Etsy print file in the exact requested pixel size.

    fit_mode="fit_white" -> exact canvas, complete artwork, white margins if needed.
    fit_mode="fit_blur"  -> exact canvas, complete artwork, blurred extension behind it.
    fit_mode="fill"      -> old crop mode; kept as an expert fallback only.
    """
    if fit_mode == "fit_blur":
        canvas = make_blurred_background(img, target_w, target_h, quality)
        fitted = fit_whole_artwork(img, target_w, target_h, quality)
        return paste_center(canvas, fitted)

    if fit_mode == "fill":
        # Not recommended for artwork with internal frames/borders/text.
        cropped = center_crop_to_ratio(img, target_w, target_h)
        return process_image(cropped, target_w, target_h, quality).convert("RGBA")

    # Default and safest mode: never crop the artwork.
    fitted = fit_whole_artwork(img, target_w, target_h, quality)
    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    return paste_center(canvas, fitted)

def adapt_etsy_output(item, orientation):
    """For landscape source art, export landscape ratio files by swapping w/h."""
    name = item["name"]
    label = item["label"]
    filename = item["filename"]
    w, h = item["w"], item["h"]

    if orientation == "landscape" and w != h:
        name = f"{name} Landscape"
        filename = filename.replace(".jpg", "_landscape.jpg")
        w, h = h, w

    return {
        **item,
        "name": name,
        "label": label,
        "filename": filename,
        "w": w,
        "h": h,
    }


def printing_guide_text():
    return """Thank you for your purchase!

This digital printable wall art package includes high-resolution JPG files in several aspect ratios.
Choose the file that matches your frame size before printing.
The artwork is prepared in safe-fit mode, so the full artwork is preserved without cutting important borders or details.

2:3 Ratio:
4x6, 8x12, 12x18, 16x24, 20x30, 24x36

3:4 Ratio:
6x8, 9x12, 12x16, 15x20, 18x24

4:5 Ratio:
4x5, 8x10, 12x15, 16x20, 20x25, 24x30

11:14 Ratio:
11x14, 22x28

A-Series Ratio:
A5, A4, A3, A2, A1

1:1 Square Ratio:
5x5, 8x8, 10x10, 12x12, 16x16, 20x20, 24x24

Printing tips:
- Print on high-quality matte paper, fine art paper, or canvas.
- For best results, use a professional print shop.
- Colors may vary slightly depending on monitor and printer settings.
- This is a digital download. No physical item will be shipped.
"""

def gcd(a, b): return gcd(b, a%b) if b else a

def get_orientation(w, h):
    if w > h: return "landscape"
    if w < h: return "portrait"
    return "square"

def adapt_size(name, w, h, orientation):
    if w == h or orientation != "landscape": return name, w, h
    a, b = name.split("×")
    return f"{b}×{a}", h, w

# ── Constants ─────────────────────────────────────────────────────────────────
CARD_W      = 236
CARD_H      = 280
THUMB_W     = 224
THUMB_H     = 136

_WORKER_SEM = threading.Semaphore(3)
_AI_SEM     = threading.Semaphore(1)

class FrameResizerApp(tk.Tk):
    def _apply_theme(self):
        theme = get_setting("theme", "light")
        global BG, SURFACE, BORDER, ACCENT, ACCENT2, TEXT, MUTED, SUCCESS, FOLDER_ACTIVE
        if theme == "dark":
            BG      = "#0f0e0c"  # Dark gray
            SURFACE = "#1a1916"  # Darker gray
            BORDER  = "#2e2c28"  # Dark border
            ACCENT  = "#d4a853"  # Gold accent
            ACCENT2 = "#7c6a3e"  # Soft gold
            TEXT    = "#e8e4dc"  # Off white
            MUTED   = "#6b6660"  # Muted text
            SUCCESS = "#5a8a5a"  # Soft green
            FOLDER_ACTIVE = "#323528"
        else: # light
            BG      = "#efe7d2"  # Warm page parchment bg
            SURFACE = "#f7f1de"  # Lighter cream beige panel bg
            BORDER  = "#d5cdb8"  # Solid warm beige border
            ACCENT  = "#ed6f5c"  # Coral red accent
            ACCENT2 = "#faeae6"  # Soft accent tint (light coral pink)
            TEXT    = "#15140f"  # Ink / Dark Charcoal text
            MUTED   = "#5a5448"  # Muted brown-grey secondary text
            SUCCESS = "#6e7448"  # Olive green success state
            FOLDER_ACTIVE = "#e2e6c7"   # Soft warm olive-green tint when folder is active

    def __init__(self):
        super().__init__()
        self._apply_theme()
        self.title("Wall Art Resizer — Etsy · 300 DPI")
        self.configure(bg=BG)
        self._center_window(1110, 760)
        self.resizable(False, False)

        self.current_img         = None
        self.session_uid         = ""
        self.current_orientation = "portrait"
        self.current_quality     = "step-unsharp"
        self.current_fit_mode    = "fit_white"  # safest Etsy mode: exact canvas, whole artwork, no crop
        self._thumb_refs         = []
        self._render_gen         = 0
        self._ai_error_shown     = False
        self._card_registry      = {}
        self._ready_cards        = {}

        # Modern TTK style config to make scrollbars match warm parchment style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TScrollbar",
                        gripcount=0,
                        background=SURFACE,
                        troughcolor=BG,
                        bordercolor=BG,
                        lightcolor=BG,
                        darkcolor=BG)
        style.map("TScrollbar",
                  background=[('pressed', ACCENT2), ('active', BORDER)])

        self._build_ui()

    def _center_window(self, width=1110, height=740):
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # We divide the app into a Left Sidebar and a Right Shell.
        
        # Left Sidebar (width 310px)
        self.sidebar = tk.Frame(self, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        self.sidebar.pack(side="left", fill="y", padx=0, pady=0)
        self.sidebar.pack_propagate(False)
        self.sidebar.configure(width=310)

        # Right Shell
        self.shell = tk.Frame(self, bg=BG)
        self.shell.pack(side="right", fill="both", expand=True)

        # ── Left Sidebar Content ───────────────────────────────────────────────
        # Logo branding at top
        logo_container = tk.Frame(self.sidebar, bg=SURFACE)
        logo_container.pack(fill="x", padx=20, pady=(12, 6))
        
        tk.Label(logo_container, text="Wall Art", bg=SURFACE, fg=TEXT,
                 font=("Georgia", 20, "italic")).pack(side="left")
        tk.Label(logo_container, text="Resizer", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        tk.Label(logo_container, text=".", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")

        # Load custom settings gear icon (settings-gears.png)
        self._settings_icon = None
        try:
            icon_path = "settings-gears.png"
            if os.path.exists(icon_path):
                img = Image.open(icon_path)
                # Resize to a perfect elegant size (20x20 px)
                img = img.resize((20, 20), Image.LANCZOS)
                self._settings_icon = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Error loading settings icon: {e}")

        # Badges tags row
        self.badges_frame = tk.Frame(self.sidebar, bg=SURFACE)
        self.badges_frame.pack(fill="x", padx=20, pady=(0, 8))
        for tag_text in ("Etsy", "300 DPI"):
            tk.Label(self.badges_frame, text=tag_text, bg=ACCENT2, fg=ACCENT,
                     font=("Segoe UI", 8, "bold"), padx=6, pady=2,
                     relief="flat").pack(side="left", padx=(0, 6))
        self.orient_tag = tk.Label(self.badges_frame, text="", bg=ACCENT2, fg=ACCENT,
                                   font=("Segoe UI", 8, "bold"), padx=6, pady=2,
                                   relief="flat")

        self._sidebar_divider()

        # SECTION: Wall Art File
        self._sidebar_section_label("Wall Art File")
        self._build_upload_zone(self.sidebar)

        # We will pack the stats frame inside the sidebar, always shown
        self.stats_frame = tk.Frame(self.sidebar, bg=SURFACE)
        self.stats_frame.pack(fill="x", pady=(0, 0))
        self._stat_vars = {}
        
        stats_card = tk.Frame(self.stats_frame, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        stats_card.pack(fill="x", padx=20, pady=(2, 4))
        
        stats_items = [("Width", 0, 0), ("Height", 0, 1), ("Ratio", 1, 0), ("Size", 1, 1)]
        for label, r, c in stats_items:
            cell = tk.Frame(stats_card, bg=SURFACE, padx=6, pady=2)
            cell.grid(row=r, column=c, sticky="nsew")
            var = tk.StringVar(value="—")
            self._stat_vars[label] = var
            tk.Label(cell, textvariable=var, bg=SURFACE, fg=ACCENT,
                     font=("Georgia", 9, "bold")).pack(anchor="w")
            tk.Label(cell, text=label.upper(), bg=SURFACE, fg=MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w")
        stats_card.grid_columnconfigure(0, weight=1)
        stats_card.grid_columnconfigure(1, weight=1)

        # SECTION: Quality Profile (stacked vertical options)
        self.quality_section = tk.Frame(self.sidebar, bg=SURFACE)
        self.quality_section.pack(fill="x")
        self._sidebar_section_label("Quality Profile", parent=self.quality_section)
        
        q_options = [
            ("basic",        "Basic",          "Canvas default"),
            ("step",         "Step Scale",     "1.5× per step"),
            ("step-unsharp", "Step + Unsharp", "Recommended ✓"),
            ("bicubic",      "Bicubic",        "Slow / best"),
            ("ai",           "AI Upscale",     "Real-ESRGAN ✦"),
            ("gigapixel",    "Gigapixel AI",   "Topaz ✦"),
        ]
        self._q_buttons = []
        self._q_container = tk.Frame(self.quality_section, bg=SURFACE)
        self._q_container.pack(fill="x", padx=20, pady=(2, 4))
        
        for q, label, sub in q_options:
            btn = tk.Button(self._q_container, text=f"{label}  —  {sub}",
                            bg=SURFACE, fg=MUTED, anchor="w",
                            activebackground=BORDER, activeforeground=TEXT,
                            font=("Segoe UI", 8, "bold"), relief="flat", bd=0,
                            padx=8, pady=3, cursor="hand2",
                            command=lambda _q=q: self._set_quality(_q))
            btn.pack(fill="x", pady=1)
            self._q_buttons.append(btn)
            
        self._set_quality_ui("step-unsharp")

        # SECTION: Etsy output mode
        self.fit_mode_section = tk.Frame(self.sidebar, bg=SURFACE)
        self.fit_mode_section.pack(fill="x")
        self._sidebar_section_label("Etsy Output Mode", parent=self.fit_mode_section)

        mode_card = tk.Frame(self.fit_mode_section, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        mode_card.pack(fill="x", padx=20, pady=(2, 4))
        mode_inner = tk.Frame(mode_card, bg=SURFACE, padx=10, pady=8)
        mode_inner.pack(fill="x")

        self._fit_mode_var = tk.StringVar(value="fit_white")
        self._fit_mode_radios = []

        r1 = tk.Radiobutton(
            mode_inner, text="Safe Fit — no crop, white margins if needed",
            variable=self._fit_mode_var, value="fit_white",
            bg=SURFACE, fg=TEXT, selectcolor=SURFACE,
            activebackground=SURFACE, activeforeground=TEXT,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_fit_mode("fit_white")
        )
        r1.pack(anchor="w")
        self._fit_mode_radios.append(r1)

        r2 = tk.Radiobutton(
            mode_inner, text="Safe Fill — no crop, blurred extension",
            variable=self._fit_mode_var, value="fit_blur",
            bg=SURFACE, fg=TEXT, selectcolor=SURFACE,
            activebackground=SURFACE, activeforeground=TEXT,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_fit_mode("fit_blur")
        )
        r2.pack(anchor="w", pady=(2, 0))
        self._fit_mode_radios.append(r2)

        r3 = tk.Radiobutton(
            mode_inner, text="Expert: Fill / Crop — may cut artwork",
            variable=self._fit_mode_var, value="fill",
            bg=SURFACE, fg=ACCENT, selectcolor=SURFACE,
            activebackground=SURFACE, activeforeground=ACCENT,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_fit_mode("fill")
        )
        r3.pack(anchor="w", pady=(2, 0))
        self._fit_mode_radios.append(r3)

        tk.Label(
            mode_inner,
            text="Recommended: Safe Fit. It creates exact Etsy ratio files without cutting the artwork. Use blurred extension only when you do not want plain margins.",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7),
            wraplength=240, justify="left"
        ).pack(anchor="w", pady=(2, 0))


        # ── Right Shell Content ───────────────────────────────────────────────
        
        # Header Topbar matching Mockup Studio topbar style
        topbar = tk.Frame(self.shell, bg=BG, height=66, highlightbackground=BORDER, highlightthickness=1, bd=0)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)
        topbar.configure(height=66)

        # Breadcrumb / Title on left
        crumb_frame = tk.Frame(topbar, bg=BG)
        crumb_frame.pack(side="left", padx=24, pady=18)
        tk.Label(crumb_frame, text="ETSY PACKAGE /", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(crumb_frame, text="RATIO FILES", bg=BG, fg=TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(6, 0))

        # Settings gear icon on the far right of topbar
        if self._settings_icon:
            self._settings_btn = tk.Button(topbar, image=self._settings_icon, bg=BG,
                                           activebackground=BG, relief="flat", bd=0, cursor="hand2",
                                           command=self._show_settings)
            self._settings_btn.image = self._settings_icon  # Keep reference alive
        else:
            self._settings_btn = tk.Button(topbar, text="⚙", bg=BG, fg=MUTED,
                                           activebackground=BG, activeforeground=TEXT,
                                           font=("Segoe UI Symbol", 10), relief="flat", bd=0, cursor="hand2",
                                           command=self._show_settings)
        self._settings_btn.pack(side="right", padx=24, pady=18)

        # Download Bar (always shown)
        self._dl_bar = tk.Frame(self.shell, bg=BG, highlightbackground=BORDER, highlightthickness=1, bd=0)
        self._dl_bar.pack(fill="x")
        
        dl_inner = tk.Frame(self._dl_bar, bg=BG, padx=24, pady=8)
        dl_inner.pack(fill="x")
        tk.Label(dl_inner, text="DOWNLOADS:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 10))
        
        self._download_all_btn = tk.Button(dl_inner, text="↓  Download All",
                   bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
                   relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                   command=self._download_all)
        self._download_all_btn.pack(side="left", padx=(0, 4))
        
        self._download_selected_btn = tk.Button(dl_inner, text="↓  Download Selected",
                   bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
                   relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                   command=self._download_selected)
        self._download_selected_btn.pack(side="left")
        
        self._dl_count_lbl = tk.Label(dl_inner, text="", bg=BG, fg=MUTED,
                                      font=("Segoe UI", 8, "bold"))
        self._dl_count_lbl.pack(side="left", padx=(10, 0))

        # Action Buttons on right (Create Etsy Files and Select All)
        self._process_btn = tk.Button(
            dl_inner, text="▶  Create Etsy Files",
            bg=ACCENT, fg="#ffffff", font=("Segoe UI", 8, "bold"),
            relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
            command=self._process_selected)
        self._process_btn.pack(side="right")

        self._select_all_btn = tk.Button(
            dl_inner, text="Select All",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
            relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
            command=self._toggle_all)
        self._select_all_btn.pack(side="right", padx=(0, 6))

        self._sel_count_lbl = tk.Label(dl_inner, text="", bg=BG, fg=MUTED,
                                       font=("Segoe UI", 8, "bold"))
        self._sel_count_lbl.pack(side="right", padx=(0, 12))

        self.grid_sep = tk.Frame(self.shell, bg=BORDER, height=1)
        self.grid_sep.pack(fill="x", padx=24, pady=0)

        # Scrollable grid container
        scroll_container = tk.Frame(self.shell, bg=BG)
        scroll_container.pack(fill="both", expand=True)

        self._scroll_canvas = tk.Canvas(scroll_container, bg=BG, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scroll_canvas.pack(side="left", fill="both", expand=True)

        self.grid_inner = tk.Frame(self._scroll_canvas, bg=BG)
        self._cw_id = self._scroll_canvas.create_window(
            (0, 0), window=self.grid_inner, anchor="nw")
        
        def _on_grid_configure(e):
            canvas_h = self._scroll_canvas.winfo_height()
            content_h = e.height
            if content_h > canvas_h:
                if not self._scrollbar.winfo_ismapped():
                    self._scrollbar.pack(side="right", fill="y")
            else:
                if self._scrollbar.winfo_ismapped():
                    self._scrollbar.pack_forget()
            self._scroll_canvas.configure(
                scrollregion=(0, 0, e.width, max(content_h, canvas_h)))

        self.grid_inner.bind("<Configure>", _on_grid_configure)
        self._scroll_canvas.bind("<Configure>",
            lambda e: self._scroll_canvas.itemconfig(self._cw_id, width=e.width))
        
        self._bind_mouse_wheel(scroll_container)
        self._bind_mouse_wheel(self._scroll_canvas)

        self._build_empty_grid()
        self._update_download_bar_state()
        self._set_sidebar_state(enabled=False)

    # ── Sidebar Helpers ───────────────────────────────────────────────────────
    def _sidebar_section_label(self, text, parent=None):
        if parent is None:
            parent = self.sidebar
        lbl = tk.Label(parent, text=text.upper(), bg=SURFACE, fg=MUTED,
                       font=("Segoe UI", 7, "bold"), anchor="w")
        lbl.pack(fill="x", padx=20, pady=(16, 4))
        return lbl

    def _sidebar_divider(self):
        div = tk.Frame(self.sidebar, bg=BORDER, height=1)
        div.pack(fill="x", padx=20, pady=6)
        return div

    def _auto_save(self, result_img, fname):
        """Save one image to the output folder in a background thread."""
        folder = get_setting("default_output_folder", "")
        if not folder:
            return  # no auto-save, user downloads manually
        def _worker():
            try:
                self._write_printing_guide(folder)
                dest = os.path.join(folder, fname)
                ext = os.path.splitext(dest)[1].lower()
                if ext in (".jpg", ".jpeg"):
                    result_img.convert("RGB").save(dest, "JPEG", quality=95, optimize=True)
                else:
                    result_img.convert("RGB").save(dest, "PNG")
                print(f"[AutoSave] Saved → {dest}")
            except Exception as e:
                print(f"[AutoSave] Error: {e}")
                err_str = str(e)
                self.after(0, lambda: messagebox.showerror(
                    "שמירה אוטומטית", f"לא הצלחתי לשמור:\n{fname}\n\n{err_str}"))
        threading.Thread(target=_worker, daemon=True).start()

    # ── Empty grid ────────────────────────────────────────────────────────────
    def _build_empty_grid(self):
        for w in self.grid_inner.winfo_children():
            w.destroy()
        for group_label, items in SIZE_GROUPS:
            self._section_title(self.grid_inner, group_label)
            grid_frame = tk.Frame(self.grid_inner, bg=BG)
            grid_frame.pack(fill="x", padx=24, pady=(0, 8))
            for col in range(3):
                grid_frame.columnconfigure(col, weight=1)
            for idx, raw_item in enumerate(items):
                item = adapt_etsy_output(raw_item, "portrait")
                r = idx // 3
                c = idx % 3
                self._empty_card(grid_frame, item["name"], item["w"], item["h"], item["sizes"], r, c)
        self._bind_mouse_wheel(self.grid_inner)

    def _empty_card(self, parent, name, w, h, sizes, r, c):
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER,
                        highlightcolor=BORDER, highlightthickness=1, bd=0,
                        width=CARD_W, height=CARD_H)
        card.grid(row=r, column=c, padx=4, pady=4, sticky="nw")
        card.pack_propagate(False)
        
        preview = tk.Frame(card, bg=BG, width=THUMB_W, height=THUMB_H)
        preview.pack(side="top", padx=6, pady=6)
        preview.pack_propagate(False)
        
        tk.Label(preview, text="▢", bg=BG, fg=MUTED,
                 font=("Segoe UI", 24)).place(relx=0.5, rely=0.5, anchor="center")
                 
        info = tk.Frame(card, bg=SURFACE)
        info.pack(side="top", fill="both", expand=True, padx=10, pady=(2, 4))
        
        tk.Label(info, text=name, bg=SURFACE, fg=TEXT,
                 font=("Georgia", 10, "bold"), anchor="w").pack(anchor="w")
                 
        tk.Label(info, text=f"{w}×{h}px", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(anchor="w")
        tk.Label(info, text=sizes, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 7), anchor="w", wraplength=216, justify="left").pack(anchor="w")

        controls = tk.Frame(card, bg=SURFACE)
        controls.pack(side="bottom", fill="x", padx=10, pady=(0, 8))

        chk = tk.Checkbutton(controls, text="Select", bg=SURFACE, fg=MUTED,
                             activebackground=SURFACE, activeforeground=MUTED,
                             selectcolor=SURFACE, font=("Segoe UI", 9, "bold"),
                             state="disabled")
        chk.pack(side="left", anchor="w")

        dl_btn = tk.Button(controls, text="↓ Save", bg=SURFACE, fg=MUTED,
                           font=("Segoe UI", 8, "bold"), relief="flat", bd=0,
                           padx=12, pady=4, state="disabled")
        dl_btn.pack(side="right", anchor="e")

    def _section_title(self, parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=24, pady=(14, 4))
        tk.Label(f, text=text.upper(), bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left")
        tk.Frame(f, bg=BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _on_click_upload(self, event=None):
        path = filedialog.askopenfilename(
            title="Select Original Artwork",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.tiff"),
                       ("All files", "*.*")])
        if path:
            self._load_file(path)

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")
        if os.path.isfile(path):
            self._load_file(path)

    def _load_file(self, path):
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("Error", f"Cannot open image:\n{exc}")
            return

        self.current_img = img
        self.session_uid = uuid.uuid4().hex[:6].upper()
        self.current_orientation = get_orientation(*img.size)

        labels = {"portrait": "▯ Portrait", "landscape": "▭ Landscape", "square": "▢ Square"}
        self.orient_tag.configure(text=labels[self.current_orientation])
        if not self.orient_tag.winfo_ismapped():
            self.orient_tag.pack(side="left", padx=(6, 0))

        # ── Source image preview in upload zone ───────────────────────────────
        self._update_source_preview(img, path)

        w, h = img.size
        g = gcd(w, h)
        self._stat_vars["Width"].set(str(w))
        self._stat_vars["Height"].set(str(h))
        self._stat_vars["Ratio"].set(f"{w//g}:{h//g}")
        try:
            self._stat_vars["Size"].set(f"{os.path.getsize(path)/1024/1024:.1f} MB")
        except OSError:
            self._stat_vars["Size"].set("—")

        self._set_sidebar_state(enabled=True)

        self._build_selectable_grid()

    def _build_upload_zone(self, parent):
        outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1, height=122)
        outer.pack(fill="x", padx=20, pady=(4, 8))
        outer.pack_propagate(False)
        self.upload_bg = tk.Frame(outer, bg=SURFACE, cursor="hand2")
        self.upload_bg.pack(fill="both", expand=True)
        self.upload_bg.pack_propagate(False)

        # ── Left: source image preview (hidden until an image is loaded) ──────
        self._src_preview_frame = tk.Frame(
            self.upload_bg, bg=BG, height=120, relief="flat")
        # not packed yet — appears after first upload

        self._src_thumb_lbl = tk.Label(
            self._src_preview_frame, bg=BG)
        # placed via place() in _update_source_preview

        self._src_thumb_ref = None   # keep ImageTk reference alive

        # ── Right: drop / click zone ──────────────────────────────────────────
        self._upload_inner = tk.Frame(self.upload_bg, bg=SURFACE)
        self._upload_inner.pack(fill="both", expand=True, padx=20, pady=5)
        
        tk.Label(self._upload_inner, text="⬡", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 24)).pack()
        tk.Label(self._upload_inner, text="Drop artwork image", bg=SURFACE, fg=TEXT,
                 font=("Georgia", 12, "italic")).pack(pady=(2, 1))
        self._upload_sub_lbl = tk.Label(
            self._upload_inner, text="or click to browse",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 8))
        self._upload_sub_lbl.pack()

        # Dynamic upload overlay label underneath upload card
        self._upload_replace_lbl = tk.Label(
            parent, text="Click preview to change artwork",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7, "bold"))
        # Dynamic upload overlay label underneath upload card
        self._upload_replace_lbl = tk.Label(
            parent, text="Click preview to change artwork",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7, "bold"))

        # Recursive hover effects for light parchment aesthetic
        def _on_enter(e):
            self.upload_bg["background"] = ACCENT2
            self._upload_inner["background"] = ACCENT2
            for child in self._upload_inner.winfo_children():
                child["background"] = ACCENT2
            self._src_preview_frame["background"] = ACCENT2
            self._src_thumb_lbl["background"] = ACCENT2

        def _on_leave(e):
            self.upload_bg["background"] = SURFACE
            self._upload_inner["background"] = SURFACE
            for child in self._upload_inner.winfo_children():
                child["background"] = SURFACE
            self._src_preview_frame["background"] = BG
            self._src_thumb_lbl["background"] = BG

        for w in (outer, self.upload_bg, self._upload_inner) + tuple(self._upload_inner.winfo_children()) + (self._src_preview_frame, self._src_thumb_lbl):
            w.bind("<Button-1>", self._on_click_upload)
            
        self.upload_bg.bind("<Enter>", _on_enter)
        self.upload_bg.bind("<Leave>", _on_leave)

    def _update_source_preview(self, img, path):
        """Show a thumbnail of the source image filling the upload zone."""
        # The upload zone card is packed in the sidebar (~270px inner width)
        PREV_W = 220
        PREV_H = 110

        iw, ih = img.size
        scale  = min(PREV_W / iw, PREV_H / ih)
        tw     = max(1, round(iw * scale))
        th     = max(1, round(ih * scale))

        thumb  = img.resize((tw, th), Image.LANCZOS)
        photo  = ImageTk.PhotoImage(thumb)
        self._src_thumb_ref = photo   # prevent GC

        self._src_thumb_lbl.configure(image=photo)
        self._src_thumb_lbl.image = photo
        
        # Hide the text drop zone when image is loaded to give full space to the thumbnail
        self._upload_inner.pack_forget()
        
        self._src_thumb_lbl.place(relx=0.5, rely=0.5, anchor="center")

        if not self._src_preview_frame.winfo_ismapped():
            self._src_preview_frame.pack(fill="both", expand=True)
            
        # Display the replace label helper below the card
        self._upload_replace_lbl.pack(pady=(0, 6))

    def _build_selectable_grid(self):
        self._render_gen += 1

        for w in self.grid_inner.winfo_children():
            w.destroy()
        self._thumb_refs.clear()
        self._card_registry.clear()
        self._ready_cards.clear()
        self._update_download_bar_state()
        self._dl_count_lbl.config(text="")
        self._sel_count_lbl.config(text="")
        self._select_all_btn.config(text="Select All")

        img = self.current_img
        groups = list(SIZE_GROUPS)

        def build_group(idx):
            if idx >= len(groups):
                self._update_sel_count()
                self._bind_mouse_wheel(self.grid_inner)
                return
            group_label, items = groups[idx]
            self._section_title(self.grid_inner, group_label)
            grid_frame = tk.Frame(self.grid_inner, bg=BG)
            grid_frame.pack(fill="x", padx=24, pady=(0, 8))
            for col in range(3):
                grid_frame.columnconfigure(col, weight=1)
                
            for col_idx, raw_item in enumerate(items):
                item = adapt_etsy_output(raw_item, self.current_orientation)
                r = col_idx // 3
                c = col_idx % 3
                name = item["name"]
                tw = item["w"]
                th = item["h"]
                filename = item["filename"]
                sizes = item["sizes"]
                card_key = f"{item['label']}_{tw}x{th}_{filename}"
                cd = self._make_selectable_card(
                    grid_frame, name, tw, th, tw, th, card_key, r, c, sizes=sizes)
                self._card_registry[card_key] = {
                    "cd": cd, "img": img,
                    "w": tw, "h": th, "name": name,
                    "filename": filename,
                    "sizes": sizes,
                    "var": cd["sel_var"]
                }
            self.after(0, build_group, idx + 1)

        build_group(0)

    def _make_selectable_card(self, parent, name, tw, th, actual_w, actual_h, card_key, r, c, sizes=""):
        scale = min(THUMB_W / actual_w, THUMB_H / actual_h)
        thumb_w = max(1, round(actual_w * scale))
        thumb_h = max(1, round(actual_h * scale))

        # Custom elegant border matching Mockup Studio light style
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER,
                        highlightcolor=BORDER, highlightthickness=1, bd=0,
                        width=CARD_W, height=CARD_H)
        card.grid(row=r, column=c, padx=4, pady=4, sticky="nw")
        card.pack_propagate(False)

        preview_frame = tk.Frame(card, bg=BG,
                                 width=THUMB_W, height=THUMB_H)
        preview_frame.pack(side="top", padx=6, pady=6)
        preview_frame.pack_propagate(False)

        ph_lbl = tk.Label(preview_frame, text=name.replace(" Ratio", ""),
                          bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold"))
        ph_lbl.place(relx=0.5, rely=0.5, anchor="center")

        spin_var = tk.StringVar(value="◌")
        spin_lbl = tk.Label(preview_frame, textvariable=spin_var,
                            bg=BG, fg=ACCENT, font=("Segoe UI", 16))
        proc_lbl = tk.Label(preview_frame, text="PROC",
                            bg=BG, fg=ACCENT, font=("Segoe UI", 7, "bold"))
        spin_chars = ["◌", "◎", "◉", "●", "◉", "◎"]
        spin_idx   = [0]
        spin_job   = [None]

        def spin():
            if not spin_lbl.winfo_exists():
                return
            spin_var.set(spin_chars[spin_idx[0] % len(spin_chars)])
            spin_idx[0] += 1
            spin_job[0] = self.after(150, spin)

        img_lbl = tk.Label(preview_frame, bg=BG)

        info = tk.Frame(card, bg=SURFACE)
        info.pack(side="top", fill="both", expand=True, padx=10, pady=(2, 4))
        
        tk.Label(info, text=name, bg=SURFACE, fg=TEXT,
                 font=("Georgia", 10, "bold"), anchor="w").pack(anchor="w")
                 
        tk.Label(info, text=f"{tw}×{th}px", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(anchor="w")
        tk.Label(info, text=sizes, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 7), anchor="w", wraplength=216, justify="left").pack(anchor="w")
                 
        status_lbl = tk.Label(info, text="Ready to create", bg=SURFACE, fg=SUCCESS,
                              font=("Segoe UI", 7, "bold"), anchor="w")
        status_lbl.pack(anchor="w")

        controls = tk.Frame(card, bg=SURFACE)
        controls.pack(side="bottom", fill="x", padx=10, pady=(0, 8))

        sel_var = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(controls, text="Select", variable=sel_var,
                             bg=SURFACE, fg=TEXT,
                             selectcolor=BG,
                             activebackground=SURFACE, activeforeground=TEXT,
                             font=("Segoe UI", 9, "bold"), cursor="hand2",
                             command=self._update_sel_count)
        chk.pack(side="left", anchor="w")

        dl_btn = tk.Button(controls, text="↓ Save",
                           bg=SURFACE, fg=MUTED,
                           activebackground=ACCENT, activeforeground="#ffffff",
                           font=("Segoe UI", 8, "bold"), relief="flat", bd=0,
                           padx=12, pady=4, cursor="hand2", state="disabled")
        dl_btn.pack(side="right", anchor="e")
        dl_btn.bind("<Enter>",
            lambda e: dl_btn.configure(bg=ACCENT, fg="#ffffff")
                      if str(dl_btn["state"]) == "normal" else None)
        dl_btn.bind("<Leave>",
            lambda e: dl_btn.configure(bg=SURFACE, fg=ACCENT if str(dl_btn["state"]) == "normal" else MUTED)
                      if str(dl_btn["state"]) == "normal" else None)

        return dict(
            card=card, preview_frame=preview_frame,
            ph_lbl=ph_lbl, spin_lbl=spin_lbl, proc_lbl=proc_lbl,
            spin_job=spin_job, spin=spin, img_lbl=img_lbl,
            dl_btn=dl_btn, thumb_w=thumb_w, thumb_h=thumb_h,
            sel_var=sel_var, status_lbl=status_lbl
        )

    def _update_sel_count(self):
        count = sum(1 for r in self._card_registry.values() if r["var"].get())
        total = len(self._card_registry)
        self._sel_count_lbl.config(
            text=f"{count} of {total} selected" if total else "")
        all_sel = count == total and total > 0
        self._select_all_btn.config(
            text="Deselect All" if all_sel else "Select All")
        self._update_download_bar_state()

    def _update_download_bar_state(self):
        if not hasattr(self, "_download_all_btn") or not hasattr(self, "_download_selected_btn"):
            return
        has_ready = len(self._ready_cards) > 0
        if has_ready:
            self._download_all_btn.configure(state="normal", fg=ACCENT)
        else:
            self._download_all_btn.configure(state="disabled", fg=MUTED)
        any_selected = any(r["var"].get() for k, r in self._card_registry.items() if k in self._ready_cards)
        if has_ready and any_selected:
            self._download_selected_btn.configure(state="normal", fg=ACCENT)
        else:
            self._download_selected_btn.configure(state="disabled", fg=MUTED)

    def _set_sidebar_state(self, enabled=True):
        state = "normal" if enabled else "disabled"
        if hasattr(self, "_q_buttons"):
            for btn in self._q_buttons:
                btn.configure(state=state)
        if hasattr(self, "_fit_mode_radios"):
            for rad in self._fit_mode_radios:
                rad.configure(state=state)

    def _on_mouse_wheel(self, event):
        if event.num == 4:
            self._scroll_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._scroll_canvas.yview_scroll(1, "units")
        elif event.delta:
            self._scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_mouse_wheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mouse_wheel)
        widget.bind("<Button-4>", self._on_mouse_wheel)
        widget.bind("<Button-5>", self._on_mouse_wheel)
        for child in widget.winfo_children():
            self._bind_mouse_wheel(child)

    def _toggle_all(self):
        vals = [r["var"].get() for r in self._card_registry.values()]
        new_val = not all(vals)
        for r in self._card_registry.values():
            r["var"].set(new_val)
        self._select_all_btn.config(
            text="Deselect All" if new_val else "Select All")
        self._update_sel_count()

    def _process_selected(self):
        selected = {k: v for k, v in self._card_registry.items()
                    if v["var"].get()}
        if not selected:
            messagebox.showinfo("Process", "לא נבחרו קבצי יחס לעיבוד.")
            return

        current_q = self.current_quality
        current_mode = self.current_fit_mode
        selected = {k: v for k, v in selected.items()
                    if self._ready_cards.get(k, (None, None, None, None))[2] != current_q
                    or self._ready_cards.get(k, (None, None, None, None))[3] != current_mode}
        if not selected:
            messagebox.showinfo("Process",
                "כל קבצי היחס הנבחרים כבר עובדו באיכות ובמצב ההתאמה הנוכחיים.\n"
                "החלף איכות או מצב התאמה ולחץ שוב כדי לעבד מחדש.")
            return

        folder = get_setting("default_output_folder", "")
        if folder:
            self._write_printing_guide(folder)

        my_gen  = self._render_gen
        quality = self.current_quality
        fit_mode = self.current_fit_mode

        for card_key, info in selected.items():
            cd = info["cd"]
            if cd["ph_lbl"].winfo_exists():
                cd["ph_lbl"].place_forget()
            cd["spin_lbl"].place(relx=0.5, rely=0.35, anchor="center")
            cd["proc_lbl"].place(relx=0.5, rely=0.75, anchor="center")
            cd["spin"]()
            cd["dl_btn"].config(state="disabled", bg=SURFACE, fg=MUTED)
            if "status_lbl" in cd and cd["status_lbl"].winfo_exists():
                cd["status_lbl"].configure(text="◌ Creating Etsy file...", fg=ACCENT)

        self.update_idletasks()

        for card_key, info in selected.items():
            threading.Thread(
                target=self._process_and_update,
                args=(info["img"], info["w"], info["h"],
                      quality, fit_mode, info["cd"], info["name"],
                      info["filename"], my_gen, card_key),
                daemon=True,
            ).start()

    def _process_and_update(self, img, target_w, target_h, quality,
                             fit_mode, cd, name, filename, my_gen, card_key):
        ai_mode = (quality in ("ai", "gigapixel"))
        with (_AI_SEM if ai_mode else _WORKER_SEM):
            if my_gen != self._render_gen:
                return
            try:
                result = render_etsy_output(img, target_w, target_h, quality, fit_mode)
            except RuntimeError as exc:
                err_msg = str(exc)
                def _show_err(msg=err_msg):
                    if self._ai_error_shown:
                        return
                    self._ai_error_shown = True
                    messagebox.showerror("AI Upscale — שגיאה", msg)
                    self._ai_error_shown = False
                self.after(0, _show_err)
                return
            if my_gen != self._render_gen:
                return
            tw, th = cd["thumb_w"], cd["thumb_h"]
            thumb  = result.resize((tw, th), Image.LANCZOS)

        _fname = filename

        self._auto_save(result, _fname)

        def update():
            if my_gen != self._render_gen or not cd["card"].winfo_exists():
                return
            if cd["spin_job"][0]:
                self.after_cancel(cd["spin_job"][0])
            for w in (cd["spin_lbl"], cd["proc_lbl"]):
                if w.winfo_exists():
                    w.place_forget()
            photo = ImageTk.PhotoImage(thumb)
            self._thumb_refs.append(photo)
            lbl = cd["img_lbl"]
            lbl.configure(image=photo, bg=BG)
            lbl.image = photo
            lbl.place(relx=0.5, rely=0.5, anchor="center")

            self._ready_cards[card_key] = (result, _fname, quality, fit_mode)

            if "status_lbl" in cd and cd["status_lbl"].winfo_exists():
                mode_label = {"fit_white": "Safe Fit", "fit_blur": "Safe Blur", "fill": "Crop"}.get(fit_mode, fit_mode)
                folder = get_setting("default_output_folder", "")
                cd["status_lbl"].configure(
                    text=f"✓ {'Saved' if folder else 'Ready'} · {mode_label}",
                    fg=SUCCESS)

            self._dl_count_lbl.config(text=f"{len(self._ready_cards)} ready")
            self._update_download_bar_state()

            def download(_r=result, _f=_fname):
                p = filedialog.asksaveasfilename(
                    defaultextension=".jpg", initialfile=_f,
                    filetypes=[("JPEG files", "*.jpg"), ("PNG files", "*.png")])
                if p:
                    ext = os.path.splitext(p)[1].lower()
                    if ext in (".jpg", ".jpeg"):
                        _r.convert("RGB").save(p, "JPEG", quality=95, optimize=True)
                    else:
                        _r.convert("RGB").save(p, "PNG")

            cd["dl_btn"].configure(state="normal", bg=SURFACE, fg=ACCENT, command=download)

        self.after(0, update)

    def _set_quality(self, q):
        self.current_quality = q
        self._set_quality_ui(q)
        if q in ("ai", "gigapixel"):
            self._ai_error_shown = False

    def _set_quality_ui(self, q):
        keys = ["basic", "step", "step-unsharp", "bicubic", "ai", "gigapixel"]
        for btn, key in zip(self._q_buttons, keys):
            btn.configure(bg=ACCENT if key == q else SURFACE,
                          fg="#ffffff" if key == q else MUTED)

    def _set_fit_mode(self, mode):
        self.current_fit_mode = mode
        self._ready_cards.clear()
        self._update_download_bar_state()
        for info in self._card_registry.values():
            cd = info.get("cd")
            if cd and "status_lbl" in cd and cd["status_lbl"].winfo_exists():
                cd["status_lbl"].configure(text="Ready to create", fg=SUCCESS)
            if cd and "dl_btn" in cd:
                cd["dl_btn"].configure(state="disabled", bg=SURFACE, fg=MUTED)

    def _write_printing_guide(self, folder):
        try:
            with open(os.path.join(folder, "README_Printing_Guide.txt"), "w", encoding="utf-8") as f:
                f.write(printing_guide_text())
        except Exception as e:
            print(f"[Guide] Error writing guide: {e}")

    def _save_in_thread(self, items, label):
        """Save a list of (img, fname) to folder — runs in background thread."""
        def _worker(folder, snapshot, count):
            saved = 0
            self._write_printing_guide(folder)
            for item in snapshot:
                res, fname = item[0], item[1]
                try:
                    dest = os.path.join(folder, fname)
                    ext = os.path.splitext(dest)[1].lower()
                    if ext in (".jpg", ".jpeg"):
                        res.convert("RGB").save(dest, "JPEG", quality=95, optimize=True)
                    else:
                        res.convert("RGB").save(dest, "PNG")
                    saved += 1
                except Exception as e:
                    print(f"[Save] Error saving {fname}: {e}")
            self.after(0, lambda s=saved, c=count: messagebox.showinfo(
                "Download", f"נשמרו {s} מתוך {c} תמונות."))

        folder = filedialog.askdirectory(title=label)
        if not folder:
            return
        snapshot = list(items.values())
        threading.Thread(
            target=_worker,
            args=(folder, snapshot, len(snapshot)),
            daemon=True,
        ).start()

    def _download_all(self):
        if not self._ready_cards:
            messagebox.showinfo("Download", "אין תמונות מוכנות עדיין.")
            return
        self._save_in_thread(self._ready_cards, "בחר תיקייה לשמירת כל התמונות")

    def _download_selected(self):
        selected = {k: v for k, v in self._ready_cards.items()
                    if self._card_registry.get(k, {}).get("var",
                       tk.BooleanVar()).get()}
        if not selected:
            messagebox.showinfo("Download",
                "לא נבחרו גדלים מוכנים.\nסמן checkboxes ונסה שוב.")
            return
        self._save_in_thread(selected, "בחר תיקייה לשמירת הנבחרים")

    def _show_settings(self):
        settings_win = tk.Toplevel(self)
        settings_win.title("Settings & Size Manager")
        settings_win.configure(bg=BG)
        
        # Center the window
        self.update_idletasks()
        win_w = 700
        win_h = 520
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        settings_win.geometry(f"{win_w}x{win_h}+{x}+{y}")
        settings_win.resizable(False, False)
        settings_win.grab_set() # Make it modal

        # Styled Notebook
        style = ttk.Style()
        style.configure("Settings.TNotebook", background=BG, tabmargins=[2, 5, 2, 0])
        style.configure("Settings.TNotebook.Tab", background=SURFACE, foreground=TEXT, font=("Segoe UI", 9, "bold"), padding=[12, 4])
        style.map("Settings.TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])

        notebook = ttk.Notebook(settings_win, style="Settings.TNotebook")
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        # Tab 1: General Settings (AI Paths)
        tab_general = tk.Frame(notebook, bg=BG)
        notebook.add(tab_general, text=" AI Configurations ")

        # Section label
        lbl_sec = tk.Label(tab_general, text="AI EXECUTABLE PATHS", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold"))
        lbl_sec.pack(anchor="w", padx=16, pady=(16, 12))

        # Real-ESRGAN path
        frame_ncnn = tk.Frame(tab_general, bg=BG)
        frame_ncnn.pack(fill="x", padx=16, pady=8)
        tk.Label(frame_ncnn, text="Real-ESRGAN Exe Path:", bg=BG, fg=TEXT, font=("Segoe UI", 8, "bold"), width=22, anchor="w").pack(side="left")
        
        ncnn_var = tk.StringVar(value=get_setting("ncnn_exe_path", r"C:\realesrgan\realesrgan-ncnn-vulkan.exe"))
        ncnn_entry = tk.Entry(frame_ncnn, textvariable=ncnn_var, bg=SURFACE, fg=TEXT, relief="flat", highlightbackground=BORDER, highlightthickness=1, font=("Segoe UI", 8), width=45)
        ncnn_entry.pack(side="left", padx=8)

        def browse_ncnn():
            path = filedialog.askopenfilename(title="Select realesrgan-ncnn-vulkan.exe", filetypes=[("Executable files", "*.exe")])
            if path:
                ncnn_var.set(path.replace("/", "\\"))

        tk.Button(frame_ncnn, text="Browse...", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=8, pady=2, cursor="hand2", command=browse_ncnn).pack(side="left")

        # Topaz Photo AI path
        frame_tpai = tk.Frame(tab_general, bg=BG)
        frame_tpai.pack(fill="x", padx=16, pady=8)
        tk.Label(frame_tpai, text="Topaz Photo AI Path:", bg=BG, fg=TEXT, font=("Segoe UI", 8, "bold"), width=22, anchor="w").pack(side="left")
        
        tpai_var = tk.StringVar(value=get_setting("tpai_exe_path", r"C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe"))
        tpai_entry = tk.Entry(frame_tpai, textvariable=tpai_var, bg=SURFACE, fg=TEXT, relief="flat", highlightbackground=BORDER, highlightthickness=1, font=("Segoe UI", 8), width=45)
        tpai_entry.pack(side="left", padx=8)

        def browse_tpai():
            path = filedialog.askopenfilename(title="Select tpai.exe", filetypes=[("Executable files", "*.exe")])
            if path:
                tpai_var.set(path.replace("/", "\\"))

        tk.Button(frame_tpai, text="Browse...", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=8, pady=2, cursor="hand2", command=browse_tpai).pack(side="left")

        # Output Folder section
        lbl_sec2 = tk.Label(tab_general, text="DEFAULT OUTPUT DIRECTORY", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold"))
        lbl_sec2.pack(anchor="w", padx=16, pady=(16, 12))

        frame_out = tk.Frame(tab_general, bg=BG)
        frame_out.pack(fill="x", padx=16, pady=8)
        tk.Label(frame_out, text="Default Output Folder:", bg=BG, fg=TEXT, font=("Segoe UI", 8, "bold"), width=22, anchor="w").pack(side="left")
        
        out_var = tk.StringVar(value=get_setting("default_output_folder", ""))
        out_entry = tk.Entry(frame_out, textvariable=out_var, bg=SURFACE, fg=TEXT, relief="flat", highlightbackground=BORDER, highlightthickness=1, font=("Segoe UI", 8), width=45)
        out_entry.pack(side="left", padx=8)

        def browse_out():
            path = filedialog.askdirectory(title="Select Default Output Folder")
            if path:
                out_var.set(path.replace("/", "\\"))

        tk.Button(frame_out, text="Browse...", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=8, pady=2, cursor="hand2", command=browse_out).pack(side="left")

        # Display Mode section
        lbl_sec3 = tk.Label(tab_general, text="DISPLAY THEME", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold"))
        lbl_sec3.pack(anchor="w", padx=16, pady=(16, 12))

        frame_theme = tk.Frame(tab_general, bg=BG)
        frame_theme.pack(fill="x", padx=16, pady=8)
        tk.Label(frame_theme, text="App Display Mode:", bg=BG, fg=TEXT, font=("Segoe UI", 8, "bold"), width=22, anchor="w").pack(side="left")

        theme_var = tk.StringVar(value=get_setting("theme", "light"))
        
        # Radio buttons for Light / Dark Mode
        r_light = tk.Radiobutton(frame_theme, text="Parchment (Light Mode)", variable=theme_var, value="light", bg=BG, fg=TEXT, selectcolor=SURFACE, activebackground=BG, activeforeground=TEXT, font=("Segoe UI", 8, "bold"))
        r_light.pack(side="left", padx=8)
        
        r_dark = tk.Radiobutton(frame_theme, text="Charcoal & Gold (Dark Mode)", variable=theme_var, value="dark", bg=BG, fg=TEXT, selectcolor=SURFACE, activebackground=BG, activeforeground=TEXT, font=("Segoe UI", 8, "bold"))
        r_dark.pack(side="left", padx=8)

        # Info label
        info_lbl = tk.Label(tab_general, text="Note: Changing these paths will take effect immediately for new upscale operations without requiring an app restart.", bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left", wraplength=600)
        info_lbl.pack(anchor="w", padx=16, pady=16)

        def save_general_settings():
            success = set_setting("ncnn_exe_path", ncnn_var.get()) and \
                      set_setting("tpai_exe_path", tpai_var.get()) and \
                      set_setting("default_output_folder", out_var.get()) and \
                      set_setting("theme", theme_var.get())
            if success:
                self._apply_theme() # Reload globals
                settings_win.destroy()
                messagebox.showinfo("Display Mode", "אנא הפעל מחדש את האפליקציה כדי להחיל את שינוי מצב התצוגה באופן מלא.", parent=self)
            else:
                messagebox.showerror("Error", "Failed to save settings.", parent=settings_win)

        # Save Button
        tk.Button(tab_general, text="✓ Save", bg=ACCENT, fg="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=16, pady=6, cursor="hand2", command=save_general_settings).pack(side="bottom", anchor="e", padx=16, pady=16)


        # Tab 2: Sizes & Ratios Manager
        tab_sizes = tk.Frame(notebook, bg=BG)
        notebook.add(tab_sizes, text=" Output Sizes Manager ")

        # Treeview to list all sizes
        tree_frame = tk.Frame(tab_sizes, bg=BG)
        tree_frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, font=("Segoe UI", 8))
        style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, font=("Segoe UI", 8, "bold"))
        style.map("Treeview", background=[("selected", ACCENT2)], foreground=[("selected", TEXT)])

        tree_scroll = ttk.Scrollbar(tree_frame)
        tree_scroll.pack(side="right", fill="y")
        
        self._sizes_tree = ttk.Treeview(tree_frame, columns=("active", "name", "resolution", "sizes", "custom"), show="headings", yscrollcommand=tree_scroll.set, height=10)
        self._sizes_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.config(command=self._sizes_tree.yview)

        # Set headings and column widths
        headers = [("active", "Status", 60), ("name", "Ratio Name", 120), ("resolution", "Resolution", 100), ("sizes", "Print Sizes", 200), ("custom", "Custom?", 60)]
        for col, text, width in headers:
            self._sizes_tree.heading(col, text=text, anchor="center")
            self._sizes_tree.column(col, width=width, anchor="center" if col in ("active", "resolution", "custom") else "w")

        def refresh_treeview():
            for item in self._sizes_tree.get_children():
                self._sizes_tree.delete(item)
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, label, w, h, sizes, is_custom, is_active FROM sizes")
                rows = cursor.fetchall()
                conn.close()
                for row in rows:
                    size_id, name, label, w, h, sz_str, is_custom, is_active = row
                    active_text = "✓ Active" if is_active else "Inactive"
                    custom_text = "Yes" if is_custom else "No"
                    res = f"{w}×{h}"
                    self._sizes_tree.insert("", "end", iid=str(size_id), values=(active_text, name, res, sz_str, custom_text))
            except Exception as e:
                print(f"Error refreshing treeview: {e}")

        # Controls frame under treeview
        ctl_frame = tk.Frame(tab_sizes, bg=BG)
        ctl_frame.pack(fill="x", padx=16, pady=(0, 16))

        def toggle_active():
            sel = self._sizes_tree.selection()
            if not sel:
                messagebox.showwarning("Selection", "Please select a size to toggle.", parent=settings_win)
                return
            size_id = int(sel[0])
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("SELECT is_active FROM sizes WHERE id=?", (size_id,))
                active = cursor.fetchone()[0]
                new_active = 0 if active else 1
                cursor.execute("UPDATE sizes SET is_active=? WHERE id=?", (new_active, size_id))
                conn.commit()
                conn.close()
                
                refresh_treeview()
                reload_size_groups()
                
                # Refresh main window grid immediately
                if self.current_img is None:
                    self._build_empty_grid()
                else:
                    self._build_selectable_grid()
            except Exception as e:
                print(f"Error toggling active size: {e}")

        def delete_custom_size():
            sel = self._sizes_tree.selection()
            if not sel:
                messagebox.showwarning("Selection", "Please select a size to delete.", parent=settings_win)
                return
            size_id = int(sel[0])
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("SELECT is_custom, name FROM sizes WHERE id=?", (size_id,))
                row = cursor.fetchone()
                if row:
                    is_custom, name = row
                    if is_custom == 0:
                        messagebox.showwarning("Delete Restricted", f"Built-in size '{name}' cannot be permanently deleted.\n\nYou can only toggle its active status to hide/show it in the main view.", parent=settings_win)
                        conn.close()
                        return
                
                confirm = messagebox.askyesno("Confirm Delete", f"Are you sure you want to permanently delete custom size '{name}'?", parent=settings_win)
                if confirm:
                    cursor.execute("DELETE FROM sizes WHERE id=?", (size_id,))
                    conn.commit()
                    conn.close()
                    refresh_treeview()
                    reload_size_groups()
                    if self.current_img is None:
                        self._build_empty_grid()
                    else:
                        self._build_selectable_grid()
                else:
                    conn.close()
            except Exception as e:
                print(f"Error deleting size: {e}")

        def open_add_size_dialog():
            add_win = tk.Toplevel(settings_win)
            add_win.title("Add Custom Size")
            add_win.configure(bg=BG)
            add_win_w = 400
            add_win_h = 320
            ax = x + (win_w - add_win_w) // 2
            ay = y + (win_h - add_win_h) // 2
            add_win.geometry(f"{add_win_w}x{add_win_h}+{ax}+{ay}")
            add_win.resizable(False, False)
            add_win.grab_set()

            # Inputs
            form_frame = tk.Frame(add_win, bg=BG)
            form_frame.pack(fill="both", expand=True, padx=16, pady=16)

            fields = [("Ratio Name:", "name_var", "e.g., 4:7 Ratio"), 
                      ("Label:", "label_var", "e.g., 4:7"), 
                      ("Filename:", "file_var", "e.g., 07_4x7_ratio_20x35_inch.jpg"), 
                      ("Width (px):", "w_var", "7200"), 
                      ("Height (px):", "h_var", "12600"), 
                      ("Print Sizes:", "sz_var", "4x7, 8x14, 12x21, 16x28")]

            form_vars = {}
            for idx, (label_txt, var_name, default_txt) in enumerate(fields):
                tk.Label(form_frame, text=label_txt, bg=BG, fg=TEXT, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=idx, column=0, sticky="ew", pady=4)
                var = tk.StringVar(value=default_txt)
                form_vars[var_name] = var
                tk.Entry(form_frame, textvariable=var, bg=SURFACE, fg=TEXT, relief="flat", highlightbackground=BORDER, highlightthickness=1, font=("Segoe UI", 8), width=30).grid(row=idx, column=1, sticky="ew", pady=4, padx=(8, 0))

            def save_custom_size():
                name_val = form_vars["name_var"].get().strip()
                label_val = form_vars["label_var"].get().strip()
                file_val = form_vars["file_var"].get().strip()
                sz_val = form_vars["sz_var"].get().strip()
                
                try:
                    w_val = int(form_vars["w_var"].get().strip())
                    h_val = int(form_vars["h_var"].get().strip())
                except ValueError:
                    messagebox.showerror("Error", "Width and Height must be valid numbers.", parent=add_win)
                    return

                if not name_val or not label_val or not file_val:
                    messagebox.showerror("Error", "Please fill in all mandatory fields.", parent=add_win)
                    return

                try:
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("""
                    INSERT INTO sizes (name, label, filename, w, h, sizes, is_custom, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 1)
                    """, (name_val, label_val, file_val, w_val, h_val, sz_val))
                    conn.commit()
                    conn.close()
                    
                    messagebox.showinfo("Success", f"Custom size '{name_val}' added successfully!", parent=add_win)
                    add_win.destroy()
                    refresh_treeview()
                    reload_size_groups()
                    if self.current_img is None:
                        self._build_empty_grid()
                    else:
                        self._build_selectable_grid()
                except sqlite3.IntegrityError:
                    messagebox.showerror("Error", f"A size with name '{name_val}' already exists.", parent=add_win)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save custom size: {e}", parent=add_win)

            # Save / Cancel Buttons in dialog
            btn_frame = tk.Frame(form_frame, bg=BG)
            btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=(12, 0), sticky="e")
            
            tk.Button(btn_frame, text="Cancel", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=12, pady=4, cursor="hand2", command=add_win.destroy).pack(side="left", padx=4)
            tk.Button(btn_frame, text="✓ Add", bg=ACCENT, fg="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=16, pady=4, cursor="hand2", command=save_custom_size).pack(side="left", padx=4)

        # Action Buttons in Size Manager
        tk.Button(ctl_frame, text="Toggle Active Status", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=12, pady=6, cursor="hand2", command=toggle_active).pack(side="left", padx=4)
        tk.Button(ctl_frame, text="+ Add Custom Size", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=12, pady=6, cursor="hand2", command=open_add_size_dialog).pack(side="left", padx=4)
        tk.Button(ctl_frame, text="✕ Delete Custom Size", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"), relief="flat", bd=0, padx=12, pady=6, cursor="hand2", command=delete_custom_size).pack(side="left", padx=4)

        refresh_treeview()


if __name__ == "__main__":
    app = FrameResizerApp()
    app.mainloop()
