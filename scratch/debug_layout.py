import tkinter as tk
from etsy_frame_resizer_v4 import FrameResizerApp

app = FrameResizerApp()
app.update()

print("=== self.shell Children Pack Info ===")
for child in app.shell.winfo_children():
    print(f"Widget: {child}")
    try:
        print(f"  Pack info: {child.pack_info()}")
    except Exception as e:
        print(f"  Pack info: Not packed ({e})")
    print(f"  Geometry: {child.winfo_width()}x{child.winfo_height()}+{child.winfo_x()}+{child.winfo_y()}")
    print(f"  Is mapped: {child.winfo_ismapped()}")

print("\n=== self.grid_inner Children ===")
for child in app.grid_inner.winfo_children():
    print(f"Widget: {child}")
    print(f"  Geometry: {child.winfo_width()}x{child.winfo_height()}+{child.winfo_x()}+{child.winfo_y()}")

app.destroy()
