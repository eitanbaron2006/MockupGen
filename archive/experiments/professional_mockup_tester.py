import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import numpy as np
from ultralytics import SAM

print("טוען את מודל SAM 2 ברקע... אנא המתן...")
sam_model = SAM("sam2.1_l.pt")

MAX_SCREEN_HEIGHT = 750

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

def draw_top_bar(img, text, bg_color=(40, 40, 40), text_color=(255, 255, 255)):
    """ יצירת פס הדרכה עליון קריא ומעוצב במקום טקסט צף נוראי """
    bar_h = 50
    bar = np.full((bar_h, img.shape[1], 3), bg_color, dtype=np.uint8)
    combined = np.vstack((bar, img))
    # כתיבת הטקסט במרכז הפס
    cv2.putText(combined, text, (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, text_color, 1, cv2.LINE_AA)
    return combined

# =====================================================================
# ממשק עיבוד התמונה האינטראקטיבי (חלון OpenCV משופר)
# =====================================================================
class InteractiveProcessor:
    def __init__(self, mockup_path, artwork_path):
        self.mockup_path = mockup_path
        self.artwork_path = artwork_path
        self.img = cv2.imread(mockup_path)
        self.h, self.w, _ = self.img.shape
        self.img_area = self.w * self.h
        self.scale = MAX_SCREEN_HEIGHT / self.h if self.h > MAX_SCREEN_HEIGHT else 1.0
        self.display_w = int(self.w * self.scale)
        self.display_h = int(self.h * self.scale)
        
        self.window_name = "Mockup Processing Studio"
        self.current_action = "none"
        self.chosen_points = None
        self.dragged_idx = -1

    def create_button_panel(self):
        """ יצירת פאנל כפתורים תחתון מובנה בתוך החלון הגרפי """
        panel_h = 60
        panel = np.full((panel_h, self.display_w, 3), (50, 50, 50), dtype=np.uint8)
        return panel

    def draw_btn(self, panel, x1, y1, x2, y2, text, bg_color=(80, 80, 80), txt_color=(255, 255, 255)):
        cv2.rectangle(panel, (x1, y1), (x2, y2), bg_color, -1)
        cv2.rectangle(panel, (x1, y1), (x2, y2), (120, 120, 120), 1)
        cv2.putText(panel, text, (x1 + 10, y1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, txt_color, 1, cv2.LINE_AA)

    def process_mouse_click_factory(self, step_type, data=None):
        def mouse_callback(event, x, y, flags, param):
            # התאמה לנפילת הפס העליון (50 פיקסלים)
            img_y = y - 50 
            
            # בדיקת לחיצה באזור כפתורי הפאנל התחתון
            if event == cv2.EVENT_LBUTTONDOWN and y > (self.display_h + 50):
                panel_y = y - (self.display_h + 50)
                if step_type == "geometry":
                    if 20 <= x <= 120: self.current_action = "accept"
                    elif 140 <= x <= 240: self.current_action = "next"
                    elif 260 <= x <= 380: self.current_action = "fallback"
                elif step_type == "center_guess":
                    if 20 <= x <= 120: self.current_action = "accept"
                    elif 140 <= x <= 240: self.current_action = "reject"
                elif step_type == "manual_click":
                    if 20 <= x <= 120 and self.chosen_points is not None: self.current_action = "accept"
                elif step_type == "fine_tune":
                    if 20 <= x <= 140: self.current_action = "confirm_save"
                return

            # אירועי עכבר בתוך אזור התמונה עצמה
            if 0 <= img_y < self.display_h and 0 <= x < self.display_w:
                orig_x = int(x / self.scale)
                orig_y = int(img_y / self.scale)

                if step_type == "manual_click" and event == cv2.EVENT_LBUTTONDOWN:
                    print(f"[+] קליק משתמש בנקודה: X={orig_x}, Y={orig_y}")
                    res = run_sam_on_points(self.mockup_path, self.w, self.h, [[orig_x, orig_y]])
                    if res is not None: self.chosen_points = res

                elif step_type == "fine_tune":
                    if event == cv2.EVENT_LBUTTONDOWN:
                        for i, pt in enumerate(self.chosen_points):
                            screen_pt = (int(pt[0] * self.scale), int(pt[1] * self.scale))
                            if np.hypot(x - screen_pt[0], img_y - screen_pt[1]) < 10:
                                self.dragged_idx = i
                                break
                    elif event == cv2.EVENT_MOUSEMOVE and self.dragged_idx != -1:
                        self.chosen_points[self.dragged_idx] = [orig_x, orig_y]
                    elif event == cv2.EVENT_LBUTTONUP:
                        self.dragged_idx = -1
        return mouse_callback

    def run_flow(self):
        cv2.namedWindow(self.window_name)
        
        # ---- שלב 1: גיאומטרי אוטומטי ----
        layers = run_geometry_step(self.img, self.img_area)
        if layers is not None:
            current_idx = 0
            cv2.setMouseCallback(self.window_name, self.process_mouse_click_factory("geometry"))
            while self.current_action not in ["accept", "fallback"]:
                self.current_action = "none"
                img_show = self.img.copy()
                # קו דק ומדויק של 2 פיקסלים בלבד!
                cv2.drawContours(img_show, [layers[current_idx][1]], -1, (0, 0, 255), 2)
                
                disp = cv2.resize(img_show, (self.display_w, self.display_h), interpolation=cv2.INTER_AREA)
                combined = draw_top_bar(disp, f"STEP 1: Geometric Detector (Layer {current_idx+1}/{len(layers)}). Check if boundary is correct.")
                
                panel = self.create_button_panel()
                self.draw_btn(panel, 20, 10, 120, 50, "Approve", (34, 139, 34))
                self.draw_btn(panel, 140, 10, 240, 50, "Next Border")
                self.draw_btn(panel, 260, 10, 380, 50, "Skip to SAM", (139, 0, 0))
                
                final_frame = np.vstack((combined, panel))
                cv2.imshow(self.window_name, final_frame)
                
                key = cv2.waitKey(100) & 0xFF
                if self.current_action == "next" or key == ord('n'):
                    current_idx = (current_idx + 1) % len(layers)
                if self.current_action == "accept" or key == 27:
                    self.chosen_points = sort_corners(layers[current_idx][1])
                    break

        # ---- שלב 2: ניחוש אוטומטי - מרכז התמונה ----
        if self.chosen_points is None:
            cv2.setMouseCallback(self.window_name, self.process_mouse_click_factory("center_guess"))
            center_guess = run_sam_on_points(self.mockup_path, self.w, self.h, [[int(self.w/2), int(self.h/2)]])
            if center_guess is not None:
                while self.current_action not in ["accept", "reject"]:
                    self.current_action = "none"
                    img_show = self.img.copy()
                    poly = np.array([[int(p[0]), int(p[1])] for p in center_guess], dtype=np.int32)
                    cv2.polylines(img_show, [poly], isClosed=True, color=(0, 255, 255), thickness=2) # קו דק
                    
                    disp = cv2.resize(img_show, (self.display_w, self.display_h), interpolation=cv2.INTER_AREA)
                    combined = draw_top_bar(disp, "STEP 2: Auto-Center Detection Guess. Is this accurate?", (0, 60, 80))
                    
                    panel = self.create_button_panel()
                    self.draw_btn(panel, 20, 10, 120, 50, "Approve", (34, 139, 34))
                    self.draw_btn(panel, 140, 10, 240, 50, "Reject", (139, 0, 0))
                    
                    final_frame = np.vstack((combined, panel))
                    cv2.imshow(self.window_name, final_frame)
                    cv2.waitKey(100)
                    if self.current_action == "accept":
                        self.chosen_points = center_guess.copy()

        # ---- שלב 3: קליק משתמש במרכז ----
        if self.chosen_points is None:
            self.current_action = "none"
            cv2.setMouseCallback(self.window_name, self.process_mouse_click_factory("manual_click"))
            while self.current_action != "accept":
                img_show = self.img.copy()
                disp = cv2.resize(img_show, (self.display_w, self.display_h), interpolation=cv2.INTER_AREA)
                combined = draw_top_bar(disp, "STEP 3: Automatic failed. CLICK once inside the center of the frame artwork.", (100, 0, 0))
                
                panel = self.create_button_panel()
                if self.chosen_points is not None:
                    poly = np.array([[int(p[0]*self.scale), int(p[1]*self.scale)] for p in self.chosen_points], dtype=np.int32)
                    cv2.polylines(disp, [poly], isClosed=True, color=(0, 255, 0), thickness=2)
                    combined = draw_top_bar(disp, "Border generated! Click 'Lock & Continue' to proceed.", (0, 80, 0))
                    self.draw_btn(panel, 20, 10, 160, 50, "Lock & Continue", (34, 139, 34))
                else:
                    self.draw_btn(panel, 20, 10, 200, 50, "Please click on image...", (100, 100, 100))
                    
                final_frame = np.vstack((combined, panel))
                cv2.imshow(self.window_name, final_frame)
                cv2.waitKey(50)

        # ---- שלב 4: כיוונון ידני דק (Fine Tuning) ----
        self.current_action = "none"
        cv2.setMouseCallback(self.window_name, self.process_mouse_click_factory("fine_tune"))
        while self.current_action != "confirm_save":
            img_show = self.img.copy()
            poly = np.array([[int(p[0]), int(p[1])] for p in self.chosen_points], dtype=np.int32)
            # קו דק מאוד של פיקסל אחד לכיוונון סופר מדויק!
            cv2.polylines(img_show, [poly], isClosed=True, color=(255, 0, 0), thickness=1)
            
            # נקודות קטנות ועדינות (רדיוס 5 במקום 10) שלא יסתירו כלום
            for i, pt in enumerate(self.chosen_points):
                cv2.circle(img_show, (int(pt[0]), int(pt[1])), 5, (0, 0, 255), -1)
            
            disp = cv2.resize(img_show, (self.display_w, self.display_h), interpolation=cv2.INTER_AREA)
            combined = draw_top_bar(disp, "STEP 4: DRAG red dots for pixel-perfect adjustments.", (120, 60, 0))
            
            panel = self.create_button_panel()
            self.draw_btn(panel, 20, 10, 160, 50, "Confirm & Blend", (34, 139, 34))
            
            final_frame = np.vstack((combined, panel))
            cv2.imshow(self.window_name, final_frame)
            cv2.waitKey(30)

        cv2.destroyAllWindows()

        # חישוב Inset עדין של 3 פיקסלים פנימה
        centroid = np.mean(self.chosen_points, axis=0)
        final_corners = []
        for pt in self.chosen_points:
            vec = centroid - pt
            inset_pt = pt + (vec / np.linalg.norm(vec)) * 3
            final_corners.append({"x": int(round(inset_pt[0])), "y": int(round(inset_pt[1]))})

        return final_corners

# =====================================================================
# חלון ה-UI הגרפי המרכזי (Tkinter) המשופר
# =====================================================================
class MockupAppUI:
    def __init__(self, root):
        self.root = root
        self.root.title("סטודיו לעיבוד והשתלת מוקאפים")
        self.root.geometry("520x380")
        self.root.resizable(False, False)
        
        # זיכרון מטמון לשמירת המצב האחרון
        self.last_mockup_path = ""
        self.last_cached_corners = None
        
        self.mockup_path = ""
        self.artwork_path = ""

        # עיצוב הכותרת
        lbl_title = tk.Label(root, text="מערכת השתלה היברידית מקצועית", font=("Arial", 14, "bold"), fg="#2C3E50")
        lbl_title.pack(pady=15)

        # כפתור מוקאפ
        self.btn_mockup = tk.Button(root, text="1. בחר תמונת מוקאפ (Mockup)", font=("Arial", 11), width=32, bg="#34495E", fg="white", command=self.select_mockup)
        self.btn_mockup.pack(pady=8)
        self.lbl_mockup_status = tk.Label(root, text="לא נבחר מוקאפ", fg="#E74C3C", font=("Arial", 9, "italic"))
        self.lbl_mockup_status.pack()

        # כפתור תמונה לשתילה
        self.btn_artwork = tk.Button(root, text="2. בחר תמונה לשתילה (Artwork)", font=("Arial", 11), width=32, bg="#34495E", fg="white", command=self.select_artwork)
        self.btn_artwork.pack(pady=8)
        self.lbl_artwork_status = tk.Label(root, text="לא נבחרה תמונה לשתילה", fg="#E74C3C", font=("Arial", 9, "italic"))
        self.lbl_artwork_status.pack()

        # כפתור הפעלה דינמי
        self.btn_run = tk.Button(root, text="הפעל תהליך זיהוי ושתילה", font=("Arial", 12, "bold"), bg="#2ECC71", fg="white", width=25, state=tk.DISABLED, command=self.run_process)
        self.btn_run.pack(pady=25)

    def select_mockup(self):
        file_path = filedialog.askopenfilename(title="בחר תמונת מוקאפ", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if file_path:
            self.mockup_path = file_path
            self.lbl_mockup_status.config(text=os.path.basename(file_path), fg="#27AE60")
            self.check_ready()

    def select_artwork(self):
        file_path = filedialog.askopenfilename(title="בחר תמונה לשתילה", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if file_path:
            self.artwork_path = file_path
            self.lbl_artwork_status.config(text=os.path.basename(file_path), fg="#27AE60")
            self.check_ready()

    def check_ready(self):
        if self.mockup_path and self.artwork_path:
            self.btn_run.config(state=tk.NORMAL)
            # אם המוקאפ זהה למוקאפ האחרון, נשנה את הטקסט כדי ליידע את המשתמש שיבוצע לדלג ישירות לשתילה
            if self.mockup_path == self.last_mockup_path and self.last_cached_corners is not None:
                self.btn_run.config(text="שתילה מהירה (על בסיס זיהוי קיים)", bg="#3498DB")
            else:
                self.btn_run.config(text="הפעל תהליך זיהוי ושתילה", bg="#2ECC71")

    def run_process(self):
        self.root.withdraw() # החבאת חלון ה-UI
        
        mockup_img = cv2.imread(self.mockup_path)
        
        # --- בדיקת קיום מוקאפ בזיכרון המטמון (Caching) ---
        if self.mockup_path == self.last_mockup_path and self.last_cached_corners is not None:
            print("[+] מוקאפ זהה זוהה! מדלג על תהליך הזיהוי ומבצע שתילה מהירה...")
            corners = self.last_cached_corners
        else:
            # הרצת תהליך הזיהוי המלא והאינטראקטיבי
            processor = InteractiveProcessor(self.mockup_path, self.artwork_path)
            corners = processor.run_flow()
            
            if corners:
                # שמירה במטמון לשימוש הבא
                self.last_mockup_path = self.mockup_path
                self.last_cached_corners = corners
                
                # שמירת קובץ ה-JSON למערכת שלך
                with open("corners.json", "w", encoding="utf-8") as f:
                    json.dump(corners, f, indent=2)

        if corners:
            # שלב השתילה הסופי והצגת חלון התוצאה המעוצב
            final_blend = overlay_artwork(mockup_img, self.artwork_path, corners)
            
            scale = MAX_SCREEN_HEIGHT / mockup_img.shape[0] if mockup_img.shape[0] > MAX_SCREEN_HEIGHT else 1.0
            disp_final = cv2.resize(final_blend, (int(mockup_img.shape[1]*scale), int(mockup_img.shape[0]*scale)), interpolation=cv2.INTER_AREA)
            
            combined_output = draw_top_bar(disp_final, "PREVIEW: Blending complete perfectly. Press ANY KEY to exit to main menu.", (39, 174, 96))
            
            cv2.imshow("Final Result Preview", combined_output)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
            
        self.check_ready()
        self.root.deiconify() # החזרת חלון ה-UI למסך

if __name__ == "__main__":
    root = tk.Tk()
    app = MockupAppUI(root)
    root.mainloop()