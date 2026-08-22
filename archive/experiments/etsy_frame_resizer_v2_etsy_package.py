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

# Allow very large images from AI 4× upscale (ncnn/Gigapixel output)
Image.MAX_IMAGE_PIXELS = None

# ── Real-ESRGAN ncnn-vulkan ───────────────────────────────────────────────────
import subprocess, tempfile, pathlib

NCNN_EXE = pathlib.Path(r"C:\realesrgan\realesrgan-ncnn-vulkan.exe")

def scale_ai_ncnn(img, tw, th):
    if not NCNN_EXE.exists():
        raise RuntimeError(
            f"לא נמצא:\n{NCNN_EXE}\n\n"
            "ודא שחילצת את realesrgan-ncnn-vulkan לתיקייה C:\\realesrgan\\"
        )
    with tempfile.TemporaryDirectory() as tmp:
        src_path = pathlib.Path(tmp) / "input.png"
        dst_path = pathlib.Path(tmp) / "output.png"
        img.convert("RGB").save(str(src_path), "PNG")
        result = subprocess.run(
            [str(NCNN_EXE),
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
TPAI_EXE = pathlib.Path(
    r"C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe")

def scale_ai_gigapixel(img, tw, th):
    if not TPAI_EXE.exists():
        raise RuntimeError(
            f"לא נמצא:\n{TPAI_EXE}\n\n"
            "ודא ש-Topaz Photo AI מותקן בנתיב הנכון."
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        out_dir  = tmp_path / "out"
        out_dir.mkdir()
        src_path = tmp_path / "input.png"
        img.convert("RGB").save(str(src_path), "PNG")

        print(f"[Topaz] Processing single image → {tw}×{th} ...")
        result = subprocess.run(
            [str(TPAI_EXE),
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

# ── Etsy print ratio package ─────────────────────────────────────────────────
# Instead of exporting dozens of individual physical sizes, this list creates
# the standard aspect-ratio files that most Etsy printable-wall-art buyers need.
# Pixel sizes are based on 300 DPI and cover the largest common print in each ratio.
ETSY_RATIO_OUTPUTS = [
    {
        "name": "2:3 Ratio",
        "label": "2:3",
        "filename": "01_2x3_ratio_24x36_inch.jpg",
        "w": 7200,
        "h": 10800,
        "sizes": "4x6, 8x12, 12x18, 16x24, 20x30, 24x36",
    },
    {
        "name": "3:4 Ratio",
        "label": "3:4",
        "filename": "02_3x4_ratio_18x24_inch.jpg",
        "w": 5400,
        "h": 7200,
        "sizes": "6x8, 9x12, 12x16, 15x20, 18x24",
    },
    {
        "name": "4:5 Ratio",
        "label": "4:5",
        "filename": "03_4x5_ratio_24x30_inch.jpg",
        "w": 7200,
        "h": 9000,
        "sizes": "4x5, 8x10, 12x15, 16x20, 20x25, 24x30",
    },
    {
        "name": "11:14 Ratio",
        "label": "11:14",
        "filename": "04_11x14_ratio_22x28_inch.jpg",
        "w": 6600,
        "h": 8400,
        "sizes": "11x14, 22x28",
    },
    {
        "name": "A-Series Ratio",
        "label": "A",
        "filename": "05_A_series_ratio_A1.jpg",
        "w": 7016,
        "h": 9933,
        "sizes": "A5, A4, A3, A2, A1",
    },
    {
        "name": "1:1 Square",
        "label": "1:1",
        "filename": "06_1x1_square_ratio_24x24_inch.jpg",
        "w": 7200,
        "h": 7200,
        "sizes": "5x5, 8x8, 10x10, 12x12, 16x16, 20x20, 24x24",
    },
]

SIZE_GROUPS = [("Etsy Printable Ratio Files", ETSY_RATIO_OUTPUTS)]

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
    """Crop the source image to the target aspect ratio without resizing yet."""
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


def render_etsy_output(img, target_w, target_h, quality, fit_mode="fill"):
    """
    Create the final Etsy print file in the exact requested pixel size.

    fit_mode="fill"  -> fills the entire canvas, center-cropping if needed.
    fit_mode="fit"   -> keeps the whole source image, adding white margins if needed.
    """
    if fit_mode == "fit":
        src_w, src_h = img.size
        scale = min(target_w / src_w, target_h / src_h)
        fitted_w = max(1, round(src_w * scale))
        fitted_h = max(1, round(src_h * scale))
        fitted = process_image(img, fitted_w, fitted_h, quality).convert("RGBA")

        canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
        x = (target_w - fitted_w) // 2
        y = (target_h - fitted_h) // 2
        canvas.paste(fitted, (x, y), fitted if fitted.mode == "RGBA" else None)
        return canvas

    cropped = center_crop_to_ratio(img, target_w, target_h)
    return process_image(cropped, target_w, target_h, quality).convert("RGBA")


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
CARD_W      = 240
CARD_H      = 80
THUMB_SIZE  = 68

_WORKER_SEM = threading.Semaphore(3)
_AI_SEM     = threading.Semaphore(1)

class FrameResizerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mockup Resizer — Etsy · 300 DPI")
        self.configure(bg=BG)
        self._center_window(1110, 880)
        self.resizable(False, False)

        self.current_img         = None
        self.session_uid         = ""
        self.current_orientation = "portrait"
        self.current_quality     = "step-unsharp"
        self.current_fit_mode    = "fill"  # "fill" = crop to fill, "fit" = no crop with margins
        self._thumb_refs         = []
        self._render_gen         = 0
        self._ai_error_shown     = False
        self._card_registry      = {}
        self._ready_cards        = {}
        self._output_folder      = ""   # ← NEW: auto-save destination

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
                  background=[('pressed', '#faeae6'), ('active', '#ece4cf')])

        self._build_ui()

    def _center_window(self, width=1110, height=880):
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
        logo_container.pack(fill="x", padx=20, pady=(24, 12))
        
        tk.Label(logo_container, text="Mockup", bg=SURFACE, fg=TEXT,
                 font=("Georgia", 20, "italic")).pack(side="left")
        tk.Label(logo_container, text="Resizer", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        tk.Label(logo_container, text=".", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")

        # Badges tags row
        self.badges_frame = tk.Frame(self.sidebar, bg=SURFACE)
        self.badges_frame.pack(fill="x", padx=20, pady=(0, 16))
        for tag_text in ("Etsy", "300 DPI"):
            tk.Label(self.badges_frame, text=tag_text, bg="#faeae6", fg=ACCENT,
                     font=("Segoe UI", 8, "bold"), padx=6, pady=2,
                     relief="flat").pack(side="left", padx=(0, 6))
        self.orient_tag = tk.Label(self.badges_frame, text="", bg="#faeae6", fg=ACCENT,
                                   font=("Segoe UI", 8, "bold"), padx=6, pady=2,
                                   relief="flat")

        self._sidebar_divider()

        # SECTION: Mockup File
        self._sidebar_section_label("Mockup File")
        self._build_upload_zone(self.sidebar)

        # We will pack the stats frame inside the sidebar, hidden until loaded
        self.stats_frame = tk.Frame(self.sidebar, bg=SURFACE)
        self._stat_vars = {}
        
        stats_card = tk.Frame(self.stats_frame, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        stats_card.pack(fill="x", padx=20, pady=(4, 12))
        
        stats_items = [("Width", 0, 0), ("Height", 0, 1), ("Ratio", 1, 0), ("Size", 1, 1)]
        for label, r, c in stats_items:
            cell = tk.Frame(stats_card, bg=SURFACE, padx=10, pady=6)
            cell.grid(row=r, column=c, sticky="nsew")
            var = tk.StringVar(value="—")
            self._stat_vars[label] = var
            tk.Label(cell, textvariable=var, bg=SURFACE, fg=ACCENT,
                     font=("Georgia", 11, "bold")).pack(anchor="w")
            tk.Label(cell, text=label.upper(), bg=SURFACE, fg=MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w")
        stats_card.grid_columnconfigure(0, weight=1)
        stats_card.grid_columnconfigure(1, weight=1)

        # SECTION: Quality Profile (stacked vertical options)
        self.quality_section = tk.Frame(self.sidebar, bg=SURFACE)
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
        self._q_container.pack(fill="x", padx=20, pady=(2, 10))
        
        for q, label, sub in q_options:
            btn = tk.Button(self._q_container, text=f"{label}  —  {sub}",
                            bg=SURFACE, fg=MUTED, anchor="w",
                            activebackground=BORDER, activeforeground=TEXT,
                            font=("Segoe UI", 8, "bold"), relief="flat", bd=0,
                            padx=12, pady=6, cursor="hand2",
                            command=lambda _q=q: self._set_quality(_q))
            btn.pack(fill="x", pady=2)
            self._q_buttons.append(btn)
            
        self._set_quality_ui("step-unsharp")

        # SECTION: Etsy output mode
        self.fit_mode_section = tk.Frame(self.sidebar, bg=SURFACE)
        self._sidebar_section_label("Etsy Output Mode", parent=self.fit_mode_section)

        mode_card = tk.Frame(self.fit_mode_section, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        mode_card.pack(fill="x", padx=20, pady=(2, 12))
        mode_inner = tk.Frame(mode_card, bg=SURFACE, padx=10, pady=8)
        mode_inner.pack(fill="x")

        self._fit_mode_var = tk.StringVar(value="fill")

        tk.Radiobutton(
            mode_inner, text="Fill / Crop — exact print ratio",
            variable=self._fit_mode_var, value="fill",
            bg=SURFACE, fg=TEXT, selectcolor="#ffffff",
            activebackground=SURFACE, activeforeground=TEXT,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_fit_mode("fill")
        ).pack(anchor="w")

        tk.Radiobutton(
            mode_inner, text="Fit / No Crop — white margins if needed",
            variable=self._fit_mode_var, value="fit",
            bg=SURFACE, fg=TEXT, selectcolor="#ffffff",
            activebackground=SURFACE, activeforeground=TEXT,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_fit_mode("fit")
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(
            mode_inner,
            text="Recommended for Etsy: Fill / Crop. Use Fit only when you must keep the whole artwork.",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7),
            wraplength=240, justify="left"
        ).pack(anchor="w", pady=(6, 0))

        # SECTION: Output Folder
        self.folder_section = tk.Frame(self.sidebar, bg=SURFACE)
        self._sidebar_section_label("Output Location", parent=self.folder_section)
        
        folder_card = tk.Frame(self.folder_section, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        folder_card.pack(fill="x", padx=20, pady=(2, 12))
        
        folder_inner = tk.Frame(folder_card, bg=SURFACE, padx=10, pady=8)
        folder_inner.pack(fill="x")
        
        self._folder_btn = tk.Button(
            folder_inner, text="📁  Set Output Folder",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
            relief="flat", bd=0, padx=10, pady=5, cursor="hand2",
            command=self._pick_output_folder)
        self._folder_btn.pack(anchor="w", pady=(0, 4))
        
        self._folder_lbl = tk.Label(
            folder_inner, text="Not set — manual download required",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7, "bold"), wraplength=240, justify="left")
        self._folder_lbl.pack(anchor="w")

        self._clear_folder_btn = tk.Button(
            folder_inner, text="✕ Clear Output",
            bg=SURFACE, fg=ACCENT, font=("Segoe UI", 7, "bold"),
            relief="flat", bd=0, padx=4, pady=2, cursor="hand2",
            command=self._clear_output_folder)

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

        # Action Buttons on right
        actions_frame = tk.Frame(topbar, bg=BG)
        actions_frame.pack(side="right", padx=24, pady=13)

        self._sel_count_lbl = tk.Label(actions_frame, text="", bg=BG, fg=MUTED,
                                       font=("Segoe UI", 9, "bold"))
        self._sel_count_lbl.pack(side="left", padx=(0, 12))

        self._select_all_btn = tk.Button(
            actions_frame, text="Select All",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
            command=self._toggle_all)
        self._select_all_btn.pack(side="left", padx=(0, 6))

        self._process_btn = tk.Button(
            actions_frame, text="▶  Create Etsy Files",
            bg=ACCENT, fg="#ffffff", font=("Segoe UI", 8, "bold"),
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            command=self._process_selected)
        self._process_btn.pack(side="left")

        # Download Bar (shown only when output folder is NOT set)
        self._dl_bar = tk.Frame(self.shell, bg=BG, highlightbackground=BORDER, highlightthickness=1, bd=0)
        
        dl_inner = tk.Frame(self._dl_bar, bg=BG, padx=24, pady=8)
        dl_inner.pack(fill="x")
        tk.Label(dl_inner, text="DOWNLOADS:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 10))
        
        tk.Button(dl_inner, text="↓  Download All",
                  bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._download_all).pack(side="left", padx=(0, 4))
        
        tk.Button(dl_inner, text="↓  Download Selected",
                  bg=SURFACE, fg=MUTED, font=("Segoe UI", 8, "bold"),
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._download_selected).pack(side="left")
        
        self._dl_count_lbl = tk.Label(dl_inner, text="", bg=BG, fg=MUTED,
                                      font=("Segoe UI", 8, "bold"))
        self._dl_count_lbl.pack(side="left", padx=(10, 0))

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
        
        for seq, delta in (("<MouseWheel>", None), ("<Button-4>", -1), ("<Button-5>", 1)):
            if delta is None:
                self._scroll_canvas.bind(seq,
                    lambda e: self._scroll_canvas.yview_scroll(
                        int(-1 * e.delta / 120), "units"))
            else:
                self._scroll_canvas.bind(seq,
                    lambda e, d=delta: self._scroll_canvas.yview_scroll(d, "units"))

        self._build_empty_grid()

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
        div.pack(fill="x", padx=20, pady=10)
        return div

    # ── Output folder helpers (NEW) ───────────────────────────────────────────
    def _pick_output_folder(self):
        folder = filedialog.askdirectory(title="בחר תיקיית יעד לשמירה אוטומטית")
        if not folder:
            return
        self._output_folder = folder
        short = folder if len(folder) <= 32 else "…" + folder[-29:]
        self._folder_lbl.configure(
            text=f"✓  {short}", fg=SUCCESS)
        self._folder_btn.configure(bg=FOLDER_ACTIVE, fg=SUCCESS)
        self._clear_folder_btn.pack(anchor="w", pady=(2, 0))
        # Hide the manual download bar — not needed any more
        self._dl_bar.pack_forget()

    def _clear_output_folder(self):
        self._output_folder = ""
        self._folder_lbl.configure(
            text="Not set — manual download required", fg=MUTED)
        self._folder_btn.configure(bg=SURFACE, fg=MUTED)
        self._clear_folder_btn.pack_forget()

    def _auto_save(self, result_img, fname):
        """Save one image to the output folder in a background thread."""
        folder = self._output_folder
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

    def _empty_card(self, parent, name, w, h, sizes, r, c):
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER,
                        highlightcolor=BORDER, highlightthickness=1, bd=0,
                        width=CARD_W, height=CARD_H)
        card.grid(row=r, column=c, padx=4, pady=4, sticky="nw")
        card.pack_propagate(False)
        
        preview = tk.Frame(card, bg=BG, width=THUMB_SIZE, height=THUMB_SIZE)
        preview.pack(side="left", padx=6, pady=6)
        preview.pack_propagate(False)
        
        tk.Label(preview, text="▢", bg=BG, fg=MUTED,
                 font=("Segoe UI", 16)).place(relx=0.5, rely=0.5, anchor="center")
                 
        info = tk.Frame(card, bg=SURFACE)
        info.pack(side="left", fill="both", expand=True, padx=4, pady=6)
        
        tk.Label(info, text=name, bg=SURFACE, fg=TEXT,
                 font=("Georgia", 10, "bold"), anchor="w").pack(anchor="w")
                 
        tk.Label(info, text=f"{w}×{h}px", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(anchor="w")
        tk.Label(info, text=sizes, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 7), anchor="w", wraplength=130, justify="left").pack(anchor="w")

    def _section_title(self, parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=24, pady=(14, 4))
        tk.Label(f, text=text.upper(), bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left")
        tk.Frame(f, bg=BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _on_click_upload(self, event=None):
        path = filedialog.askopenfilename(
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

        if not self.stats_frame.winfo_ismapped():
            self.stats_frame.pack(fill="x", pady=(0, 0))
        if not self.quality_section.winfo_ismapped():
            self.quality_section.pack(fill="x")
        if not self.folder_section.winfo_ismapped():
            self.folder_section.pack(fill="x")

        self._build_selectable_grid()

    def _build_upload_zone(self, parent):
        outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        outer.pack(fill="x", padx=20, pady=(4, 8))
        self.upload_bg = tk.Frame(outer, bg=SURFACE, cursor="hand2")
        self.upload_bg.pack(fill="both", expand=True)

        # ── Left: source image preview (hidden until an image is loaded) ──────
        self._src_preview_frame = tk.Frame(
            self.upload_bg, bg=BG, height=140, relief="flat")
        # not packed yet — appears after first upload

        self._src_thumb_lbl = tk.Label(
            self._src_preview_frame, bg=BG)
        # placed via place() in _update_source_preview

        self._src_thumb_ref = None   # keep ImageTk reference alive

        # ── Right: drop / click zone ──────────────────────────────────────────
        self._upload_inner = tk.Frame(self.upload_bg, bg=SURFACE)
        self._upload_inner.pack(fill="both", expand=True, padx=20, pady=20)
        
        tk.Label(self._upload_inner, text="⬡", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 24)).pack()
        tk.Label(self._upload_inner, text="Drop mockup image", bg=SURFACE, fg=TEXT,
                 font=("Georgia", 12, "italic")).pack(pady=(2, 1))
        self._upload_sub_lbl = tk.Label(
            self._upload_inner, text="or click to browse",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 8))
        self._upload_sub_lbl.pack()

        # Dynamic upload overlay label underneath upload card
        self._upload_replace_lbl = tk.Label(
            parent, text="Click preview to change mockup",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7, "bold"))

        # Recursive hover effects for light parchment aesthetic
        def _on_enter(e):
            self.upload_bg.configure(background="#faeae6")
            self._upload_inner.configure(background="#faeae6")
            for child in self._upload_inner.winfo_children():
                child.configure(background="#faeae6")
            self._src_preview_frame.configure(background="#faeae6")
            self._src_thumb_lbl.configure(background="#faeae6")

        def _on_leave(e):
            self.upload_bg.configure(background=SURFACE)
            self._upload_inner.configure(background=SURFACE)
            for child in self._upload_inner.winfo_children():
                child.configure(background=SURFACE)
            self._src_preview_frame.configure(background=BG)
            self._src_thumb_lbl.configure(background=BG)

        for w in (outer, self.upload_bg, self._upload_inner) + tuple(self._upload_inner.winfo_children()) + (self._src_preview_frame, self._src_thumb_lbl):
            w.bind("<Button-1>", self._on_click_upload)
            
        self.upload_bg.bind("<Enter>", _on_enter)
        self.upload_bg.bind("<Leave>", _on_leave)

    def _update_source_preview(self, img, path):
        """Show a thumbnail of the source image filling the upload zone."""
        # The upload zone card is packed in the sidebar (~270px inner width)
        PREV_W = 260
        PREV_H = 140

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
        self._dl_bar.pack_forget()
        self._dl_count_lbl.config(text="")
        self._sel_count_lbl.config(text="")
        self._select_all_btn.config(text="Select All")

        img = self.current_img
        groups = list(SIZE_GROUPS)

        def build_group(idx):
            if idx >= len(groups):
                self._update_sel_count()
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
        scale = min(THUMB_SIZE / actual_w, THUMB_SIZE / actual_h)
        thumb_w = max(1, round(actual_w * scale))
        thumb_h = max(1, round(actual_h * scale))

        # Custom elegant border matching Mockup Studio light style
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER,
                        highlightcolor=BORDER, highlightthickness=1, bd=0,
                        width=CARD_W, height=CARD_H)
        card.grid(row=r, column=c, padx=4, pady=4, sticky="nw")
        card.pack_propagate(False)

        preview_frame = tk.Frame(card, bg=BG,
                                 width=THUMB_SIZE, height=THUMB_SIZE)
        preview_frame.pack(side="left", padx=6, pady=6)
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
        info.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        
        tk.Label(info, text=name, bg=SURFACE, fg=TEXT,
                 font=("Georgia", 10, "bold"), anchor="w").pack(anchor="w")
                 
        tk.Label(info, text=f"{tw}×{th}px", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(anchor="w")
        tk.Label(info, text=sizes, bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 7), anchor="w", wraplength=130, justify="left").pack(anchor="w")
                 
        status_lbl = tk.Label(info, text="Ready to create", bg=SURFACE, fg=SUCCESS,
                              font=("Segoe UI", 7, "bold"), anchor="w")
        status_lbl.pack(anchor="w")

        controls = tk.Frame(card, bg=SURFACE)
        controls.pack(side="right", padx=6, pady=4, fill="y")

        sel_var = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(controls, variable=sel_var,
                             bg=SURFACE, fg=MUTED,
                             selectcolor="#ffffff",
                             activebackground=SURFACE, activeforeground=TEXT,
                             font=("Segoe UI", 8, "bold"), cursor="hand2",
                             command=self._update_sel_count)
        chk.pack(side="top", anchor="e")

        dl_btn = tk.Button(controls, text="↓ Save",
                           bg=SURFACE, fg=MUTED,
                           activebackground=ACCENT, activeforeground="#ffffff",
                           font=("Segoe UI", 8, "bold"), relief="flat", bd=0,
                           padx=4, pady=2, cursor="hand2", state="disabled")
        dl_btn.pack(side="bottom", anchor="e")
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
                "כל קבצי היחס הנבחרים כבר עובדו באיכות ובמצב החיתוך הנוכחיים.\n"
                "החלף איכות או מצב Fill/Fit ולחץ שוב כדי לעבד מחדש.")
            return

        if self._output_folder:
            self._write_printing_guide(self._output_folder)

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
                mode_label = "Fill/Crop" if fit_mode == "fill" else "Fit/No Crop"
                cd["status_lbl"].configure(
                    text=f"✓ {'Saved' if self._output_folder else 'Ready'} · {mode_label}",
                    fg=SUCCESS)

            if not self._output_folder:
                self._dl_count_lbl.config(text=f"{len(self._ready_cards)} ready")
                if not self._dl_bar.winfo_ismapped():
                    self._dl_bar.pack(fill="x", pady=(0, 4), before=self.grid_sep)
            else:
                n = len(self._ready_cards)
                short = self._output_folder if len(self._output_folder) <= 45 else "…" + self._output_folder[-42:]
                self._folder_lbl.configure(text=f"✓  {short}  [{n} files saved]")

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
        self._dl_bar.pack_forget()
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


if __name__ == "__main__":
    app = FrameResizerApp()
    app.mainloop()
