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

# ── Ratio-safe Etsy print sizes ──────────────────────────────────────────────
# Professional Etsy printable output should NOT force one artwork into unrelated
# aspect ratios. This table exports only sizes that match the uploaded artwork's
# detected aspect ratio. No crop, no fake extension, no added margins.
RATIO_SIZE_GROUPS = {
    "1:1": {
        "title": "Square 1:1 Prints",
        "sizes": [
            ("8×8", 2400, 2400),
            ("10×10", 3000, 3000),
            ("12×12", 3600, 3600),
            ("16×16", 4800, 4800),
            ("20×20", 6000, 6000),
            ("24×24", 7200, 7200),
        ],
        "note": "Fits square frames: 8x8, 10x10, 12x12, 16x16, 20x20, 24x24",
    },
    "2:3": {
        "title": "2:3 Ratio Prints",
        "sizes": [
            ("4×6", 1200, 1800),
            ("8×12", 2400, 3600),
            ("12×18", 3600, 5400),
            ("16×24", 4800, 7200),
            ("20×30", 6000, 9000),
            ("24×36", 7200, 10800),
        ],
        "note": "Fits frames: 4x6, 8x12, 12x18, 16x24, 20x30, 24x36",
    },
    "3:4": {
        "title": "3:4 Ratio Prints",
        "sizes": [
            ("6×8", 1800, 2400),
            ("9×12", 2700, 3600),
            ("12×16", 3600, 4800),
            ("15×20", 4500, 6000),
            ("18×24", 5400, 7200),
        ],
        "note": "Fits frames: 6x8, 9x12, 12x16, 15x20, 18x24",
    },
    "4:5": {
        "title": "4:5 Ratio Prints",
        "sizes": [
            ("4×5", 1200, 1500),
            ("8×10", 2400, 3000),
            ("12×15", 3600, 4500),
            ("16×20", 4800, 6000),
            ("20×25", 6000, 7500),
            ("24×30", 7200, 9000),
        ],
        "note": "Fits frames: 4x5, 8x10, 12x15, 16x20, 20x25, 24x30",
    },
    "11:14": {
        "title": "11:14 Ratio Prints",
        "sizes": [
            ("11×14", 3300, 4200),
            ("22×28", 6600, 8400),
        ],
        "note": "Fits frames: 11x14 and 22x28",
    },
    "A-Series": {
        "title": "ISO A-Series Prints",
        "sizes": [
            ("A5", 1748, 2480),
            ("A4", 2480, 3508),
            ("A3", 3508, 4961),
            ("A2", 4961, 7016),
            ("A1", 7016, 9933),
        ],
        "note": "Fits ISO A-Series frames: A5, A4, A3, A2, A1",
    },
}

KNOWN_RATIOS = {
    "1:1": 1.0,
    "2:3": 2 / 3,
    "3:4": 3 / 4,
    "4:5": 4 / 5,
    "11:14": 11 / 14,
    "A-Series": 1 / math.sqrt(2),
}

RATIO_TOLERANCE = 0.025  # about 2.5%; enough for minor pixel rounding, not enough to fake a different ratio

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


def detect_ratio_group(w, h):
    """Return the closest supported Etsy ratio group for the artwork.

    Detection uses the artwork as-is. Landscape artwork is matched by its
    normalized portrait ratio, then exported as landscape sizes later.
    """
    if not w or not h:
        return None, None, None

    raw_ratio = w / h
    normalized_ratio = min(raw_ratio, 1 / raw_ratio)  # portrait-style comparison

    best_name = None
    best_diff = 999
    for name, target_ratio in KNOWN_RATIOS.items():
        diff = abs(normalized_ratio - target_ratio)
        if diff < best_diff:
            best_name = name
            best_diff = diff

    if best_diff > RATIO_TOLERANCE:
        return None, raw_ratio, best_diff
    return best_name, raw_ratio, best_diff


def ratio_items_for_artwork(ratio_group, orientation):
    """Build the export item list for the detected artwork ratio only."""
    if ratio_group not in RATIO_SIZE_GROUPS:
        return []

    cfg = RATIO_SIZE_GROUPS[ratio_group]
    items = []
    for idx, (name, w, h) in enumerate(cfg["sizes"], start=1):
        export_name = name
        export_w, export_h = w, h

        # If the source artwork is landscape, create landscape output sizes only.
        # Example: 2:3 portrait sizes become 6x4, 12x8, 36x24, etc.
        if orientation == "landscape" and w != h:
            if "×" in name:
                a, b = name.split("×", 1)
                export_name = f"{b}×{a}"
            else:
                export_name = f"{name} Landscape"
            export_w, export_h = h, w

        safe_ratio = ratio_group.replace(":", "x").replace("-", "_").lower()
        safe_size = export_name.replace("×", "x").replace(" ", "_")
        filename = f"{idx:02d}_{safe_ratio}_{safe_size}_{export_w}x{export_h}px.jpg"

        items.append({
            "name": export_name,
            "ratio": ratio_group,
            "filename": filename,
            "w": export_w,
            "h": export_h,
            "sizes": cfg["note"],
        })
    return items


def render_ratio_safe_output(img, target_w, target_h, quality):
    """Resize only to a matching-ratio target size.

    No crop, no background extension, no extra margins. This assumes target_w / target_h
    matches the source artwork ratio within the accepted ratio group.
    """
    return process_image(img, target_w, target_h, quality).convert("RGBA")


def printing_guide_text(ratio_group, items, orientation):
    if not ratio_group or ratio_group not in RATIO_SIZE_GROUPS:
        return """Thank you for your purchase!\n\nThis package contains ratio-safe printable artwork files.\n"""

    cfg = RATIO_SIZE_GROUPS[ratio_group]
    lines = [
        "Thank you for your purchase!",
        "",
        "This is a digital printable wall art package. No physical item will be shipped.",
        "All files were generated in ratio-safe mode:",
        "- no cropping",
        "- no fake background extension",
        "- no added margins",
        "- only print sizes that match the original artwork ratio",
        "",
        f"Detected artwork ratio: {ratio_group}",
        f"Orientation: {orientation}",
        "",
        cfg["note"],
        "",
        "Included files:",
    ]
    for item in items:
        lines.append(f"- {item['filename']} — {item['name']} — {item['w']}x{item['h']} px")
    lines.extend([
        "",
        "Print tip: choose the file that matches your frame size exactly.",
        "For best results, print on high-quality matte paper, fine-art paper, or canvas.",
    ])
    return "\n".join(lines)


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
        self.current_ratio_group = None
        self.current_ratio_items = []
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

        # SECTION: Ratio-safe policy
        self.fit_mode_section = tk.Frame(self.sidebar, bg=SURFACE)
        self._sidebar_section_label("Ratio-Safe Output", parent=self.fit_mode_section)

        mode_card = tk.Frame(self.fit_mode_section, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        mode_card.pack(fill="x", padx=20, pady=(2, 12))
        mode_inner = tk.Frame(mode_card, bg=SURFACE, padx=10, pady=8)
        mode_inner.pack(fill="x")

        self._ratio_policy_lbl = tk.Label(
            mode_inner,
            text="Upload artwork to detect its ratio. The app will export only matching print sizes. No crop, no fake extension, no added margins.",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 7),
            wraplength=240, justify="left"
        )
        self._ratio_policy_lbl.pack(anchor="w")

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

        self._section_title(self.grid_inner, "Upload artwork")
        box = tk.Frame(self.grid_inner, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        box.pack(fill="x", padx=24, pady=(8, 12))

        tk.Label(
            box,
            text="Upload one artwork file. The app will detect its aspect ratio and show only matching Etsy print sizes.",
            bg=SURFACE, fg=MUTED, font=("Segoe UI", 10, "bold"),
            padx=18, pady=18, wraplength=650, justify="left"
        ).pack(anchor="w")

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
        ratio_group, raw_ratio, diff = detect_ratio_group(w, h)
        self.current_ratio_group = ratio_group
        self.current_ratio_items = ratio_items_for_artwork(ratio_group, self.current_orientation) if ratio_group else []

        self._stat_vars["Width"].set(str(w))
        self._stat_vars["Height"].set(str(h))
        self._stat_vars["Ratio"].set(f"{w//g}:{h//g}")

        if ratio_group:
            self._ratio_policy_lbl.configure(
                text=f"Detected: {ratio_group} · {self.current_orientation}. Only matching print sizes will be created. No crop, no fake extension, no added margins.",
                fg=SUCCESS
            )
        else:
            self._ratio_policy_lbl.configure(
                text="Custom / unsupported ratio detected. This app will not force it into Etsy ratios. Create a matching-ratio artwork version first, or add this ratio to RATIO_SIZE_GROUPS.",
                fg=ACCENT
            )
        try:
            self._stat_vars["Size"].set(f"{os.path.getsize(path)/1024/1024:.1f} MB")
        except OSError:
            self._stat_vars["Size"].set("—")

        if not self.stats_frame.winfo_ismapped():
            self.stats_frame.pack(fill="x", pady=(0, 0))
        if not self.quality_section.winfo_ismapped():
            self.quality_section.pack(fill="x")
        if not self.fit_mode_section.winfo_ismapped():
            self.fit_mode_section.pack(fill="x")
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
        items = list(self.current_ratio_items or [])

        if not items:
            self._section_title(self.grid_inner, "Unsupported ratio")
            box = tk.Frame(self.grid_inner, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
            box.pack(fill="x", padx=24, pady=(8, 12))
            tk.Label(
                box,
                text=("This artwork ratio does not closely match the standard Etsy ratios in this app. "
                      "The app will not crop, stretch, add margins, or fake new areas. "
                      "Create/export a source artwork in 1:1, 2:3, 3:4, 4:5, 11:14, or A-Series, then upload it here."),
                bg=SURFACE, fg=ACCENT, font=("Segoe UI", 10, "bold"),
                padx=18, pady=18, wraplength=650, justify="left"
            ).pack(anchor="w")
            self._update_sel_count()
            return

        title = RATIO_SIZE_GROUPS[self.current_ratio_group]["title"]
        if self.current_orientation == "landscape" and self.current_ratio_group != "1:1":
            title += " — Landscape"
        self._section_title(self.grid_inner, title)

        grid_frame = tk.Frame(self.grid_inner, bg=BG)
        grid_frame.pack(fill="x", padx=24, pady=(0, 8))
        for col in range(3):
            grid_frame.columnconfigure(col, weight=1)

        for col_idx, item in enumerate(items):
            r = col_idx // 3
            c = col_idx % 3
            name = item["name"]
            tw = item["w"]
            th = item["h"]
            filename = item["filename"]
            sizes = item["sizes"]
            card_key = f"{self.current_ratio_group}_{tw}x{th}_{filename}"
            cd = self._make_selectable_card(
                grid_frame, name, tw, th, tw, th, card_key, r, c, sizes=sizes)
            self._card_registry[card_key] = {
                "cd": cd, "img": img,
                "w": tw, "h": th, "name": name,
                "filename": filename,
                "sizes": sizes,
                "var": cd["sel_var"]
            }

        self._update_sel_count()

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
            messagebox.showinfo("Process", "לא נבחרו גדלים לעיבוד.")
            return

        current_q = self.current_quality
        selected = {k: v for k, v in selected.items()
                    if self._ready_cards.get(k, (None, None, None))[2] != current_q}
        if not selected:
            messagebox.showinfo("Process",
                "כל הגדלים הנבחרים כבר עובדו באיכות הנוכחית.\n"
                "החלף איכות ולחץ שוב כדי לעבד מחדש.")
            return

        if self._output_folder:
            self._write_printing_guide(self._output_folder)

        my_gen  = self._render_gen
        quality = self.current_quality

        for card_key, info in selected.items():
            cd = info["cd"]
            if cd["ph_lbl"].winfo_exists():
                cd["ph_lbl"].place_forget()
            cd["spin_lbl"].place(relx=0.5, rely=0.35, anchor="center")
            cd["proc_lbl"].place(relx=0.5, rely=0.75, anchor="center")
            cd["spin"]()
            cd["dl_btn"].config(state="disabled", bg=SURFACE, fg=MUTED)
            if "status_lbl" in cd and cd["status_lbl"].winfo_exists():
                cd["status_lbl"].configure(text="◌ Creating ratio-safe file...", fg=ACCENT)

        self.update_idletasks()

        for card_key, info in selected.items():
            threading.Thread(
                target=self._process_and_update,
                args=(info["img"], info["w"], info["h"],
                      quality, info["cd"], info["name"],
                      info["filename"], my_gen, card_key),
                daemon=True,
            ).start()

    def _process_and_update(self, img, target_w, target_h, quality,
                             cd, name, filename, my_gen, card_key):
        ai_mode = (quality in ("ai", "gigapixel"))
        with (_AI_SEM if ai_mode else _WORKER_SEM):
            if my_gen != self._render_gen:
                return
            try:
                result = render_ratio_safe_output(img, target_w, target_h, quality)
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

            self._ready_cards[card_key] = (result, _fname, quality)

            if "status_lbl" in cd and cd["status_lbl"].winfo_exists():
                cd["status_lbl"].configure(
                    text=f"✓ {'Saved' if self._output_folder else 'Ready'} · Ratio-safe",
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

    def _write_printing_guide(self, folder):
        try:
            with open(os.path.join(folder, "README_Printing_Guide.txt"), "w", encoding="utf-8") as f:
                f.write(printing_guide_text(
                    self.current_ratio_group,
                    self.current_ratio_items,
                    self.current_orientation
                ))
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
