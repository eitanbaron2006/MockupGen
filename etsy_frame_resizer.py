"""
Etsy Frame Resizer — Python/Tkinter
Conversion of etsy_frame_resizer_v5.html (one-to-one UI & logic)

Requirements: pip install Pillow
Run: python etsy_frame_resizer.py
"""

import math
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageFilter, ImageTk

# ── Real-ESRGAN (optional) ────────────────────────────────────────────────────
try:
    import torch
    from realesrgan import RealESRGANer
    from basicsr.archs.rrdbnet_arch import RRDBNet
    _ESRGAN_AVAILABLE = True
except ImportError:
    _ESRGAN_AVAILABLE = False

_esrgan_upsampler = None   # lazy-loaded on first use

def _get_esrgan():
    """Lazy-load RealESRGAN model (CPU mode for AMD/Intel)."""
    global _esrgan_upsampler
    if _esrgan_upsampler is not None:
        return _esrgan_upsampler
    if not _ESRGAN_AVAILABLE:
        raise RuntimeError(
            "Real-ESRGAN לא מותקן.\n\n"
            "הרץ:\n  pip install realesrgan basicsr torch torchvision"
        )
    import urllib.request, pathlib
    model_path = pathlib.Path(__file__).parent / "RealESRGAN_x4plus.pth"
    if not model_path.exists():
        url = ("https://github.com/xinntao/Real-ESRGAN/"
               "releases/download/v0.1.0/RealESRGAN_x4plus.pth")
        print(f"Downloading Real-ESRGAN model → {model_path} …")
        urllib.request.urlretrieve(url, model_path)
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                    num_block=23, num_grow_ch=32, scale=4)
    _esrgan_upsampler = RealESRGANer(
        scale=4, model_path=str(model_path), model=model,
        tile=256, tile_pad=10, pre_pad=0,
        device=torch.device("cpu"),
    )
    return _esrgan_upsampler

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = "#0f0e0c"
SURFACE = "#1a1916"
BORDER  = "#2e2c28"
ACCENT  = "#d4a853"
ACCENT2 = "#7c6a3e"
TEXT    = "#e8e4dc"
MUTED   = "#6b6660"
SUCCESS = "#5a8a5a"

# ── Print sizes ───────────────────────────────────────────────────────────────
SIZE_GROUPS = [
    ("Small Prints", [
        ("4×4",  1200, 1200), ("4×6",  1200, 1800),
        ("5×5",  1500, 1500), ("5×7",  1500, 2100),
    ]),
    ("Medium Prints", [
        ("8×8",   2400, 2400), ("8×10",  2400, 3000),
        ("8×12",  2400, 3600), ("9×12",  2700, 3600),
        ("10×10", 3000, 3000), ("10×14", 3000, 4200),
    ]),
    ("Large Prints", [
        ("11×14", 3300, 4200), ("12×12", 3600, 3600),
        ("12×16", 3600, 4800), ("12×18", 3600, 5400),
        ("16×20", 4800, 6000), ("18×24", 5400, 7200),
        ("20×24", 6000, 7200), ("24×36", 7200, 10800),
    ]),
]

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
    """
    High-quality bicubic upscale (Catmull-Rom via PIL).
    Strategy:
      1. Step-scale (1.5x per pass, LANCZOS) up to ~2/3 of the target size.
      2. One final BICUBIC pass to hit the exact target — clean, sharp result.
    This is intentionally the slowest mode.
    """
    sw, sh = img.size
    # Step up to 2/3 of target so the final bicubic jump is at most ~1.5x
    step_target_w = max(sw, round(tw * 2 / 3))
    step_target_h = max(sh, round(th * 2 / 3))
    if step_target_w > sw or step_target_h > sh:
        source = scale_step(img, step_target_w, step_target_h)
    else:
        source = img
    return source.resize((tw, th), Image.BICUBIC)

def scale_ai(img, tw, th):
    """
    Real-ESRGAN 4× upscale, then downscale to exact target with LANCZOS.
    Works on CPU (slow but correct for AMD/Intel).
    Input is converted to RGB for the model and back to RGBA after.
    """
    upsampler = _get_esrgan()
    import numpy as np
    # ESRGAN expects RGB uint8 numpy array
    rgb = img.convert("RGB")
    arr = np.array(rgb)
    out_arr, _ = upsampler.enhance(arr, outscale=4)
    out_img = Image.fromarray(out_arr, "RGB")
    # Downscale to exact target with LANCZOS for maximum sharpness
    if out_img.size != (tw, th):
        out_img = out_img.resize((tw, th), Image.LANCZOS)
    return out_img.convert("RGBA")

def process_image(img, w, h, quality):
    if   quality == "basic":        return scale_basic(img, w, h)
    elif quality == "step":         return scale_step(img, w, h)
    elif quality == "step-unsharp": return apply_unsharp(scale_step(img, w, h), 0.5, 1.0)
    elif quality == "bicubic":      return apply_unsharp(scale_bicubic(img, w, h), 0.4, 0.8)
    elif quality == "ai":            return scale_ai(img, w, h)
    return img

def gcd(a, b): return gcd(b, a%b) if b else a

def get_orientation(w, h):
    if w > h: return "landscape"
    if w < h: return "portrait"
    return "square"

def adapt_size(name, w, h, orientation):
    if w == h or orientation != "landscape": return name, w, h
    a, b = name.split("×")
    return f"{b}×{a}", h, w


# ── App ───────────────────────────────────────────────────────────────────────

CARD_W      = 180   # card pixel width
THUMB_MAX_W = 160   # max thumbnail width inside card

# Limit parallel workers → cards finish progressively, not all at once
_WORKER_SEM = threading.Semaphore(3)

# Global render-generation counter — threads from old renders self-cancel
_render_gen = 0

class FrameResizerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Frame Resizer — Etsy · 300 DPI")
        self.configure(bg=BG)
        self.geometry("1100x820")
        self.minsize(700, 500)

        self.current_img       = None
        self.current_orientation = "portrait"
        self.current_quality   = "step-unsharp"
        self._thumb_refs       = []
        self._render_gen       = 0   # bumped on every _render_all; threads check this
        self._ai_error_shown   = False  # show AI install error only once

        self._build_ui()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Fixed top area (header + upload + stats + quality) ────────────────
        top = tk.Frame(self, bg=BG)
        top.pack(side="top", fill="x")

        # Header
        header = tk.Frame(top, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 0))
        tk.Label(header, text="Frame Resizer", bg=BG, fg=ACCENT,
                 font=("Georgia", 20, "italic")).pack(side="left")
        for tag_text in ("Etsy", "300 DPI"):
            tk.Label(header, text=tag_text, bg=SURFACE, fg=MUTED,
                     font=("Courier", 8), padx=6, pady=2,
                     relief="solid", bd=1).pack(side="left", padx=(8, 0))
        self.orient_tag = tk.Label(header, text="", bg=SURFACE, fg=ACCENT,
                                   font=("Courier", 8), padx=6, pady=2,
                                   relief="solid", bd=1)

        tk.Frame(top, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(8, 0))

        # Upload zone
        self._build_upload_zone(top)

        # Stats bar
        self.stats_frame = tk.Frame(top, bg=SURFACE)
        # NOT packed yet — shown on first load

        self._stat_vars = {}
        for key in ("Width", "Height", "Ratio", "Size"):
            col = tk.Frame(self.stats_frame, bg=SURFACE)
            col.pack(side="left", padx=14, pady=8)
            var = tk.StringVar(value="—")
            self._stat_vars[key] = var
            tk.Label(col, textvariable=var, bg=SURFACE, fg=ACCENT,
                     font=("Georgia", 13)).pack(anchor="w")
            tk.Label(col, text=key.upper(), bg=SURFACE, fg=MUTED,
                     font=("Courier", 8)).pack(anchor="w")

        # Quality bar
        self.quality_frame = tk.Frame(top, bg=BG)
        # NOT packed yet — shown on first load

        tk.Label(self.quality_frame, text="QUALITY:", bg=BG, fg=MUTED,
                 font=("Courier", 9)).pack(side="left", padx=(24, 10))
        q_options = [
            ("basic",        "Basic",          "Canvas default"),
            ("step",         "Step Scale",     "1.5× per step"),
            ("step-unsharp", "Step + Unsharp", "Recommended ✓"),
            ("bicubic",      "Bicubic",        "Slow / best"),
            ("ai",           "AI Upscale",     "Real-ESRGAN ✦"),
        ]
        self._q_buttons = []
        btn_row = tk.Frame(self.quality_frame, bg=BORDER, bd=1, relief="solid")
        btn_row.pack(side="left")
        for i, (q, label, sub) in enumerate(q_options):
            btn = tk.Button(btn_row, text=f"{label}\n{sub}",
                            bg=SURFACE, fg=MUTED,
                            activebackground=BORDER, activeforeground=TEXT,
                            font=("Courier", 9), relief="flat", bd=0,
                            padx=12, pady=7, cursor="hand2",
                            command=lambda _q=q: self._set_quality(_q))
            btn.grid(row=0, column=i,
                     padx=(0, 1 if i < len(q_options)-1 else 0))
            self._q_buttons.append(btn)
        self._set_quality_ui("step-unsharp")

        # Separator before grid
        self.grid_sep = tk.Frame(top, bg=BORDER, height=1)
        # packed after quality bar appears

        # ── Scrollable grid (fills rest of window) ────────────────────────────
        scroll_container = tk.Frame(self, bg=BG)
        scroll_container.pack(side="top", fill="both", expand=True,
                              padx=0, pady=(0, 0))

        self._scroll_canvas = tk.Canvas(scroll_container, bg=BG,
                                        highlightthickness=0)
        sb = ttk.Scrollbar(scroll_container, orient="vertical",
                           command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._scroll_canvas.pack(side="left", fill="both", expand=True)

        self.grid_inner = tk.Frame(self._scroll_canvas, bg=BG)
        self._cw_id = self._scroll_canvas.create_window(
            (0, 0), window=self.grid_inner, anchor="nw")

        self.grid_inner.bind("<Configure>",
            lambda e: self._scroll_canvas.configure(
                scrollregion=self._scroll_canvas.bbox("all")))
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

    def _build_upload_zone(self, parent):
        outer = tk.Frame(parent, bg=ACCENT2, padx=1, pady=1)
        outer.pack(fill="x", padx=24, pady=12)
        self.upload_bg = tk.Frame(outer, bg=SURFACE, cursor="hand2")
        self.upload_bg.pack(fill="both", expand=True)
        inner = tk.Frame(self.upload_bg, bg=SURFACE)
        inner.pack(padx=40, pady=24)
        tk.Label(inner, text="⬡", bg=SURFACE, fg=ACCENT,
                 font=("Courier", 26)).pack()
        tk.Label(inner, text="Drop your image here", bg=SURFACE, fg=ACCENT,
                 font=("Georgia", 14, "italic")).pack(pady=(4, 2))
        tk.Label(inner, text="PNG, JPG, WEBP  —  or click to browse",
                 bg=SURFACE, fg=MUTED, font=("Courier", 9)).pack()
        for w in (outer, self.upload_bg, inner) + tuple(inner.winfo_children()):
            w.bind("<Button-1>", self._on_click_upload)
        self.upload_bg.bind("<Enter>",
            lambda e: self.upload_bg.configure(bg="#201e1a"))
        self.upload_bg.bind("<Leave>",
            lambda e: self.upload_bg.configure(bg=SURFACE))

    # ── Empty state ───────────────────────────────────────────────────────────

    def _build_empty_grid(self):
        for w in self.grid_inner.winfo_children():
            w.destroy()
        for group_label, sizes in SIZE_GROUPS:
            self._section_title(self.grid_inner, group_label)
            row = tk.Frame(self.grid_inner, bg=BG)
            row.pack(fill="x", padx=24, pady=(0, 8))
            for name, w, h in sizes:
                self._empty_card(row, name, w, h)

    def _empty_card(self, parent, name, w, h):
        ph = max(40, round(90 * h / w))
        card = tk.Frame(parent, bg=SURFACE, bd=1, relief="solid",
                        width=CARD_W, height=ph + 40)
        card.pack(side="left", padx=4, pady=2)
        card.pack_propagate(False)
        preview = tk.Frame(card, bg=BG, height=ph)
        preview.pack(fill="x")
        preview.pack_propagate(False)
        tk.Label(preview, text=f'{name}"', bg=BG, fg=MUTED,
                 font=("Courier", 9)).place(relx=0.5, rely=0.5, anchor="center")
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")
        info = tk.Frame(card, bg=SURFACE)
        info.pack(fill="x", padx=6, pady=4)
        tk.Label(info, text=f"{w}×{h}", bg=SURFACE, fg=MUTED,
                 font=("Courier", 8)).pack(side="left")
        tk.Label(info, text="300 DPI", bg=SURFACE, fg=MUTED,
                 font=("Courier", 8)).pack(side="right")

    def _section_title(self, parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=24, pady=(14, 4))
        tk.Label(f, text=text.upper(), bg=BG, fg=MUTED,
                 font=("Courier", 8), anchor="w").pack(side="left")
        tk.Frame(f, bg=BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    # ── Load ──────────────────────────────────────────────────────────────────

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
        self.current_orientation = get_orientation(*img.size)

        # Orientation tag
        labels = {"portrait": "▯ Portrait",
                  "landscape": "▭ Landscape",
                  "square": "▢ Square"}
        self.orient_tag.configure(text=labels[self.current_orientation])
        if not self.orient_tag.winfo_ismapped():
            self.orient_tag.pack(side="left", padx=(8, 0))

        # Stats
        w, h = img.size
        g = gcd(w, h)
        self._stat_vars["Width"].set(str(w))
        self._stat_vars["Height"].set(str(h))
        self._stat_vars["Ratio"].set(f"{w//g}:{h//g}")
        try:
            self._stat_vars["Size"].set(
                f"{os.path.getsize(path)/1024/1024:.1f} MB")
        except OSError:
            self._stat_vars["Size"].set("—")

        # Show stats + quality bars (only pack once)
        if not self.stats_frame.winfo_ismapped():
            self.stats_frame.pack(fill="x", padx=24, pady=(0, 0),
                                  in_=self.stats_frame.master)
        if not self.quality_frame.winfo_ismapped():
            self.quality_frame.pack(fill="x", pady=(6, 8))
            self.grid_sep.pack(fill="x", padx=24, pady=(0, 4))

        self._render_all()

    # ── Render ────────────────────────────────────────────────────────────────

    def _render_all(self):
        if not self.current_img:
            return

        # Bump generation so in-flight threads from a previous render abort.
        self._render_gen += 1
        my_gen = self._render_gen

        for w in self.grid_inner.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

        img = self.current_img
        src_w, src_h = img.size
        groups = list(SIZE_GROUPS)

        def render_group(idx):
            if my_gen != self._render_gen or idx >= len(groups):
                return
            group_label, sizes = groups[idx]
            self._section_title(self.grid_inner, group_label)
            row = tk.Frame(self.grid_inner, bg=BG)
            row.pack(fill="x", padx=24, pady=(0, 8))
            for raw_name, raw_w, raw_h in sizes:
                name, tw, th = adapt_size(raw_name, raw_w, raw_h,
                                          self.current_orientation)
                scale    = min(tw / src_w, th / src_h)
                actual_w = round(src_w * scale)
                actual_h = round(src_h * scale)
                card_dict = self._make_card(row, name, tw, th, actual_w, actual_h)
                threading.Thread(
                    target=self._process_and_update,
                    args=(img, actual_w, actual_h,
                          self.current_quality, card_dict, name,
                          actual_w, actual_h, my_gen),
                    daemon=True,
                ).start()
            # Yield to the event loop before building the next group
            self.after(0, render_group, idx + 1)

        render_group(0)

    def _make_card(self, parent, name, tw, th, actual_w, actual_h):
        # Compute thumbnail size to know card height upfront
        thumb_w = min(actual_w, THUMB_MAX_W)
        thumb_h = round(actual_h * thumb_w / actual_w)

        card = tk.Frame(parent, bg=SURFACE, bd=1, relief="solid")
        card.pack(side="left", padx=4, pady=2)
        # do NOT fix width/height — let content drive size

        # Preview area — fixed height = thumb height + padding
        preview_frame = tk.Frame(card, bg="#1e1c18",
                                 width=CARD_W, height=thumb_h + 12)
        preview_frame.pack(fill="x")
        preview_frame.pack_propagate(False)

        # Spinner (centered in preview area)
        spin_var = tk.StringVar(value="◌")
        spin_lbl = tk.Label(preview_frame, textvariable=spin_var,
                            bg="#1e1c18", fg=ACCENT, font=("Courier", 18))
        spin_lbl.place(relx=0.5, rely=0.4, anchor="center")
        proc_lbl = tk.Label(preview_frame, text="PROCESSING",
                            bg="#1e1c18", fg=ACCENT, font=("Courier", 8))
        proc_lbl.place(relx=0.5, rely=0.7, anchor="center")

        spin_chars = ["◌", "◎", "◉", "●", "◉", "◎"]
        spin_idx   = [0]
        spin_job   = [None]

        def spin():
            if spin_lbl.winfo_exists():
                spin_var.set(spin_chars[spin_idx[0] % len(spin_chars)])
                spin_idx[0] += 1
                spin_job[0] = self.after(150, spin)
        spin()

        # Image label — placed inside preview_frame, hidden until ready
        img_lbl = tk.Label(preview_frame, bg="#1e1c18")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")

        # Info row
        info = tk.Frame(card, bg=SURFACE)
        info.pack(fill="x", padx=8, pady=5)
        tk.Label(info, text=f'{name}"', bg=SURFACE, fg=ACCENT,
                 font=("Georgia", 11, "italic")).pack(side="left")
        right = tk.Frame(info, bg=SURFACE)
        right.pack(side="right")
        tk.Label(right, text=f"{tw}×{th}", bg=SURFACE, fg=MUTED,
                 font=("Courier", 8)).pack(anchor="e")
        tk.Label(right, text=f"{actual_w}×{actual_h}", bg=SURFACE, fg=SUCCESS,
                 font=("Courier", 8)).pack(anchor="e")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x")

        dl_btn = tk.Button(card, text="↓  Download PNG",
                           bg=SURFACE, fg=MUTED,
                           activebackground=ACCENT, activeforeground=BG,
                           font=("Courier", 8), relief="flat", bd=0,
                           pady=6, cursor="hand2", state="disabled",
                           width=CARD_W // 7)
        dl_btn.pack(fill="x")
        dl_btn.bind("<Enter>",
            lambda e: dl_btn.configure(bg=ACCENT, fg=BG)
                      if str(dl_btn["state"]) == "normal" else None)
        dl_btn.bind("<Leave>",
            lambda e: dl_btn.configure(bg=SURFACE, fg=MUTED)
                      if str(dl_btn["state"]) == "normal" else None)

        return dict(card=card, preview_frame=preview_frame,
                    spin_lbl=spin_lbl, proc_lbl=proc_lbl,
                    spin_job=spin_job, img_lbl=img_lbl,
                    dl_btn=dl_btn, thumb_w=thumb_w, thumb_h=thumb_h)

    def _process_and_update(self, img, actual_w, actual_h, quality,
                             cd, name, aw, ah, my_gen):
        # ── All heavy work stays in the background thread ──────────────────
        with _WORKER_SEM:   # max 3 concurrent; others queue up → progressive UI
            # Abort early if a newer render started while we were queued
            if my_gen != self._render_gen:
                return
            try:
                result = process_image(img, actual_w, actual_h, quality)
            except RuntimeError as exc:
                # Show friendly popup only once across all concurrent threads
                err_msg = str(exc)
                def _show_err(msg=err_msg):
                    if self._ai_error_shown:
                        return
                    self._ai_error_shown = True
                    messagebox.showerror("AI Upscale — שגיאה", msg)
                    self.current_quality = "step-unsharp"
                    self._set_quality_ui("step-unsharp")
                    self._ai_error_shown = False
                    self._render_all()
                self.after(0, _show_err)
                return
            if my_gen != self._render_gen:
                return

            # Also build the thumbnail PIL image here (CPU work, off main thread)
            tw, th = cd["thumb_w"], cd["thumb_h"]
            thumb  = result.resize((tw, th), Image.LANCZOS)

        # ── Only the tiny Tk-object creation goes to the main thread ───────
        def update():
            if my_gen != self._render_gen:
                return   # render was superseded; don't touch stale widgets
            if not cd["card"].winfo_exists():
                return

            # Stop spinner
            if cd["spin_job"][0]:
                self.after_cancel(cd["spin_job"][0])
            for w in (cd["spin_lbl"], cd["proc_lbl"]):
                if w.winfo_exists():
                    w.place_forget()
                    w.destroy()

            # PhotoImage must be created on main thread — but it's instant
            photo = ImageTk.PhotoImage(thumb)
            self._thumb_refs.append(photo)

            lbl = cd["img_lbl"]
            lbl.configure(image=photo, bg="#1e1c18")
            lbl.image = photo
            lbl.place(relx=0.5, rely=0.5, anchor="center")

            # Enable download
            btn  = cd["dl_btn"]
            safe = name.replace("×", "x")
            _res = result

            def download():
                p = filedialog.asksaveasfilename(
                    defaultextension=".png",
                    initialfile=f"print_{safe}_{aw}x{ah}.png",
                    filetypes=[("PNG files", "*.png")])
                if p:
                    _res.convert("RGB").save(p, "PNG")

            btn.configure(state="normal", command=download)

        self.after(0, update)

    # ── Quality ───────────────────────────────────────────────────────────────

    def _set_quality(self, q):
        self.current_quality = q
        self._set_quality_ui(q)
        if q == "ai":
            self._ai_error_shown = False
        if self.current_img:
            self._render_all()

    def _set_quality_ui(self, q):
        keys = ["basic", "step", "step-unsharp", "bicubic", "ai"]
        for btn, key in zip(self._q_buttons, keys):
            btn.configure(bg=ACCENT if key == q else SURFACE,
                          fg=BG     if key == q else MUTED)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = FrameResizerApp()
    app.mainloop()