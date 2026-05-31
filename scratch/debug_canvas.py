import tkinter as tk
from etsy_frame_resizer_v4 import FrameResizerApp

app = FrameResizerApp()
app.update()

print("Canvas yview:", app._scroll_canvas.yview())
print("Canvas bbox('all'):", app._scroll_canvas.bbox("all"))
print("Canvas window item coords:", app._scroll_canvas.coords(app._cw_id))
print("grid_inner height:", app.grid_inner.winfo_height())
print("scroll_canvas height:", app._scroll_canvas.winfo_height())

app.destroy()
