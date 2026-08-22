import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import numpy as np
from ultralytics import SAM

print("טוען את מודל SAM 2 ברקע... אנא המתן...")
sam_model = SAM("sam2_l.pt")

MAX_SCREEN_HEIGHT = 800

def sort_corners(points):
    points = points.reshape(4, 2)
    sorted_pts = np.zeros((4, 2), dtype="int32")
    s = points.sum(axis=1)
    sorted_pts[0] = points[np.argmin(s)]  # TL
    sorted_pts[2] = points[np.argmax(s)]  # BR
    diff = np.diff(points, axis=1)
    sorted_pts[1] = points[np.argmin(diff)]  # TR
    sorted_pts[3] = points[np.argmax(diff)]  # BL
    return sorted_pts

def run_sam_on_points(image_path, orig_w, orig_h, pts_list):
    try:
        results = sam_model.predict(source=image_path, points=pts_list, labels=[1]*len(pts_list), device="cpu", verbose=False)
        if len(results) == 0 or results[0].masks is None:
            return None
        mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8) * 255
        if mask.shape[0] != orig_h or mask.shape[1] != orig_w:
            mask = cv2.resize(mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        largest_contour = max(contours, key=cv2.contourArea)
        pts = np.zeros((4, 2), dtype="int32")
        s = largest_contour.sum(axis=2)
        pts[0] = largest_contour[np.argmin(s)]
        pts[2] = largest_contour[np.argmax(s)]
        diff = np.diff(largest_contour, axis=2)
        pts[1] = largest_contour[np.argmin(diff)]
        pts[3] = largest_contour[np.argmax(diff)]
        return sort_corners(pts)
    except:
        return None

def run_geometry_step(img, img_area):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(edged, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    valid_rectangles = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            area = cv2.contourArea(approx)
            if (img_area * 0.04) < area < (img_area * 0.90):
                x, y, box_w, box_h = cv2.boundingRect(approx)
                aspect_ratio = float(box_w) / box_h
                if 0.2 < aspect_ratio < 5.0:
                    valid_rectangles.append((area, approx))
    if not valid_rectangles:
        return None
    valid_rectangles.sort(key=lambda x: x[0], reverse=True)
    unique_layers = []
    for r in valid_rectangles:
        if not unique_layers or abs(r[0] - unique_layers[-1][0]) > (img_area * 0.015):
            unique_layers.append(r)
    return unique_layers

sam_clicked_pts = None
def sam_click_callback(event, x, y, flags, param):
    global sam_clicked_pts
    scale, image_path, orig_w, orig_h = param
    if event == cv2.EVENT_LBUTTONDOWN:
        orig_x = int(x / scale)
        orig_y = int(y / scale)
        sam_clicked_pts = run_sam_on_points(image_path, orig_w, orig_h, [[orig_x, orig_y]])

dragged_idx = -1
def manual_drag_callback(event, x, y, flags, param):
    global dragged_idx
    points, scale = param
    if event == cv2.EVENT_LBUTTONDOWN:
        for i, pt in enumerate(points):
            screen_pt = (int(pt[0] * scale), int(pt[1] * scale))
            if np.hypot(x - screen_pt[0], y - screen_pt[1]) < 12:
                dragged_idx = i
                break
    elif event == cv2.EVENT_MOUSEMOVE and dragged_idx != -1:
        points[dragged_idx] = [int(x / scale), int(y / scale)]
    elif event == cv2.EVENT_LBUTTONUP:
        dragged_idx = -1

def overlay_artwork(mockup_img, artwork_path, corners):
    artwork = cv2.imread(artwork_path)
    if artwork is None:
        return mockup_img
    art_h, art_w, _ = artwork.shape
    src_pts = np.array([[0, 0], [art_w - 1, 0], [art_w - 1, art_h - 1], [0, art_h - 1]], dtype="float32")
    dst_pts = np.array([[c['x'], c['y']] for c in corners], dtype="float32")
    M, _ = cv2.findHomography(src_pts, dst_pts)
    mockup_h, mockup_w, _ = mockup_img.shape
    warped_artwork = cv2.warpPerspective(artwork, M, (mockup_w, mockup_h))
    mask = np.zeros((mockup_h, mockup_w), dtype="uint8")
    cv2.fillConvexPoly(mask, dst_pts.astype(int), 255)
    mask_inverse = cv2.bitwise_not(mask)
    mockup_background = cv2.bitwise_and(mockup_img, mockup_img, mask=mask_inverse)
    return cv2.add(mockup_background, warped_artwork)

# =====================================================================
# זרימת העבודה הראשית מחלון ה-UI הגרפי
# =====================================================================
def run_main_workflow(mockup_path, artwork_path):
    global sam_clicked_pts
    sam_clicked_pts = None
    
    img = cv2.imread(mockup_path)
    if img is None:
        messagebox.showerror("שגיאה", "לא ניתן לטעון את תמונת המוקאפ")
        return

    h, w, _ = img.shape
    img_area = w * h
    scale = MAX_SCREEN_HEIGHT / h if h > MAX_SCREEN_HEIGHT else 1.0
    display_w, display_h = int(w * scale), int(h * scale)
    chosen_points = None

    cv2.namedWindow("Detection Process")

    # ---- שלב 1: בדיקה גיאומטרית ----
    layers = run_geometry_step(img, img_area)
    if layers is not None:
        current_idx = 0
        while True:
            img_show = img.copy()
            cv2.drawContours(img_show, [layers[current_idx][1]], -1, (0, 0, 255), 4)
            img_disp = cv2.resize(img_show, (display_w, display_h), interpolation=cv2.INTER_AREA)
            cv2.putText(img_disp, f"Layer {current_idx+1}/{len(layers)} | ESC: Accept | 'N': Next | 'F': Fallback to SAM", 
                        (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 2)
            cv2.imshow("Detection Process", img_disp)
            key = cv2.waitKey(0) & 0xFF
            if key == 27:
                chosen_points = sort_corners(layers[current_idx][1])
                break
            elif key == ord('n') or key == ord('N'):
                current_idx = (current_idx + 1) % len(layers)
            elif key == ord('f') or key == ord('F'):
                break

    # ---- שלב 2: ניחוש אוטומטי - מרכז התמונה ----
    if chosen_points is None:
        center_guess = run_sam_on_points(mockup_path, w, h, [[int(w/2), int(h/2)]])
        if center_guess is not None:
            while True:
                img_show = img.copy()
                poly = np.array([[int(p[0]), int(p[1])] for p in center_guess], dtype=np.int32)
                cv2.polylines(img_show, [poly], isClosed=True, color=(0, 255, 255), thickness=4)
                img_disp = cv2.resize(img_show, (display_w, display_h), interpolation=cv2.INTER_AREA)
                cv2.putText(img_disp, "Auto-Center Guess | ESC: Accept | 'N': Reject (Manual Click)", 
                            (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2)
                cv2.imshow("Detection Process", img_disp)
                key = cv2.waitKey(0) & 0xFF
                if key == 27:
                    chosen_points = center_guess.copy()
                    break
                elif key == ord('n') or key == ord('N'):
                    break

    # ---- שלב 3: קליק משתמש במרכז המסגרת ----
    if chosen_points is None:
        cv2.setMouseCallback("Detection Process", sam_click_callback, param=(scale, mockup_path, w, h))
        while True:
            img_disp = cv2.resize(img, (display_w, display_h), interpolation=cv2.INTER_AREA)
            cv2.putText(img_disp, "CLICK inside the mockup frame artwork area. Press ESC when polygon appears.", 
                        (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 255), 2)
            if sam_clicked_pts is not None:
                poly = np.array([[int(p[0]*scale), int(p[1]*scale)] for p in sam_clicked_pts], dtype=np.int32)
                cv2.polylines(img_disp, [poly], isClosed=True, color=(0, 255, 0), thickness=2)
            cv2.imshow("Detection Process", img_disp)
            key = cv2.waitKey(30) & 0xFF
            if key == 27 and sam_clicked_pts is not None:
                chosen_points = sam_clicked_pts.copy()
                break

    # ---- שלב 4: כיוונון ידני בגרירה ----
    cv2.setMouseCallback("Detection Process", manual_drag_callback, param=(chosen_points, scale))
    while True:
        img_show = img.copy()
        poly = np.array([[int(p[0]), int(p[1])] for p in chosen_points], dtype=np.int32)
        cv2.polylines(img_show, [poly], isClosed=True, color=(255, 0, 0), thickness=3)
        for i, pt in enumerate(chosen_points):
            cv2.circle(img_show, (int(pt[0]), int(pt[1])), 10, (0, 0, 255), -1)
        img_disp = cv2.resize(img_show, (display_w, display_h), interpolation=cv2.INTER_AREA)
        cv2.putText(img_disp, "DRAG red dots to perfect corners. Press ENTER or SPACE to confirm & blend.", 
                    (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 2)
        cv2.imshow("Detection Process", img_disp)
        key = cv2.waitKey(10) & 0xFF
        if key == 13 or key == 32:
            break

    # ביצוע Inset עדין של 3 פיקסלים פנימה
    centroid = np.mean(chosen_points, axis=0)
    final_corners = []
    for pt in chosen_points:
        vec = centroid - pt
        inset_pt = pt + (vec / np.linalg.norm(vec)) * 3
        final_corners.append({"x": int(round(inset_pt[0])), "y": int(round(inset_pt[1]))})

    # שמירת ה-JSON למערכת שלך
    with open("corners.json", "w", encoding="utf-8") as f:
        json.dump(final_corners, f, indent=2)

    # ---- שלב 5: השתלה והצגה סופית ----
    final_mockup = overlay_artwork(img, artwork_path, final_corners)
    cv2.setMouseCallback("Detection Process", lambda *args: None)
    img_disp_final = cv2.resize(final_mockup, (display_w, display_h), interpolation=cv2.INTER_AREA)
    cv2.putText(img_disp_final, "SUCCESS! Press ANY KEY to return to UI Window.", 
                (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    
    cv2.imshow("Detection Process", img_disp_final)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

# =====================================================================
# חלון ה-UI הגרפי המרכזי (Tkinter)
# =====================================================================
class MockupAppUI:
    def __init__(self, root):
        self.root = root
        self.root.title("מערכת בדיקת מוקאפים אינטראקטיבית")
        self.root.geometry("500x350")
        self.root.resizable(False, False)
        
        self.mockup_path = ""
        self.artwork_path = ""

        # כותרת ראשית
        lbl_title = tk.Label(root, text="בודק מוקאפים היברידי - ממשק גרפי מלא", font=("Arial", 14, "bold"))
        lbl_title.pack(pady=15)

        # אזור בחירת מוקאפ
        self.btn_mockup = tk.Button(root, text="1. בחר תמונת מוקאפ (Mockup)", font=("Arial", 11), width=30, command=self.select_mockup)
        self.btn_mockup.pack(pady=10)
        self.lbl_mockup_status = tk.Label(root, text="לא נבחר קובץ", fg="red", font=("Arial", 9, "italic"))
        self.lbl_mockup_status.pack()

        # אזור בחירת תמונה לשתילה
        self.btn_artwork = tk.Button(root, text="2. בחר תמונה לשתילה (Artwork)", font=("Arial", 11), width=30, command=self.select_artwork)
        self.btn_artwork.pack(pady=10)
        self.lbl_artwork_status = tk.Label(root, text="לא נבחר קובץ", fg="red", font=("Arial", 9, "italic"))
        self.lbl_artwork_status.pack()

        # כפתור הפעלה סופי
        self.btn_run = tk.Button(root, text="הפעל תהליך זיהוי ושתילה", font=("Arial", 12, "bold"), bg="#4CAF50", fg="white", width=25, state=tk.DISABLED, command=self.run_process)
        self.btn_run.pack(pady=25)

    def select_mockup(self):
        file_path = filedialog.askopenfilename(title="בחר תמונת מוקאפ", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if file_path:
            self.mockup_path = file_path
            self.lbl_mockup_status.config(text=os.path.basename(file_path), fg="green")
            self.check_ready()

    def select_artwork(self):
        file_path = filedialog.askopenfilename(title="בחר תמונה לשתילה", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if file_path:
            self.artwork_path = file_path
            self.lbl_artwork_status.config(text=os.path.basename(file_path), fg="green")
            self.check_ready()

    def check_ready(self):
        if self.mockup_path and self.artwork_path:
            self.btn_run.config(state=tk.NORMAL)

    def run_process(self):
        # מחביא זמנית את חלון ה-UI כדי שלא יפריע בזמן התצוגה של התמונות
        self.root.withdraw()
        run_main_workflow(self.mockup_path, self.artwork_path)
        # מחזיר את חלון ה-UI לפעילות ברגע שחלון התמונות נסגר
        self.root.deiconify()

if __name__ == "__main__":
    root = tk.Tk()
    app = MockupAppUI(root)
    root.mainloop()