import json
import os
import cv2
import numpy as np
from ultralytics import SAM

print("טוען את מודל SAM 2 ברקע...")
sam_model = SAM("sam2_l.pt")

MAX_SCREEN_HEIGHT = 800

def sort_corners(points):
    """ מיון 4 פינות בסדר קבוע: TL, TR, BR, BL """
    points = points.reshape(4, 2)
    sorted_pts = np.zeros((4, 2), dtype="int32")
    s = points.sum(axis=1)
    sorted_pts[0] = points[np.argmin(s)]  # Top-Left
    sorted_pts[2] = points[np.argmax(s)]  # Bottom-Right
    diff = np.diff(points, axis=1)
    sorted_pts[1] = points[np.argmin(diff)]  # Top-Right
    sorted_pts[3] = points[np.argmax(diff)]  # Bottom-Left
    return sorted_pts

# =====================================================================
# שלב 1: אלגוריתם גיאומטרי אוטומטי (רב-שכבתי)
# =====================================================================
def run_geometry_step(image_path, img, h, w, img_area):
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

# =====================================================================
# שלב 2: ממשק קליק במרכז והפעלת SAM 2
# =====================================================================
sam_clicked_pts = None

def sam_click_callback(event, x, y, flags, param):
    global sam_clicked_pts
    img_trigger, scale, image_path = param
    if event == cv2.EVENT_LBUTTONDOWN:
        # החזרת הקואורדינטות לגודל המקורי של התמונה
        orig_x = int(x / scale)
        orig_y = int(y / scale)
        print(f"[+] נקודה נבחרה בגודל מקורי: X={orig_x}, Y={orig_y}. מריץ SAM 2...")
        
        results = sam_model.predict(source=image_path, points=[[orig_x, orig_y]], labels=[1], device="cpu", verbose=False)
        if len(results) == 0 or results[0].masks is None:
            print("[-] SAM 2 לא הצלח להפיק מסיכה בנקודה זו.")
            return

        orig_h, orig_w, _ = img_trigger.shape
        mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8) * 255
        if mask.shape[0] != orig_h or mask.shape[1] != orig_w:
            mask = cv2.resize(mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            print("[-] לא נמצאו קווי מתאר למסיכה.")
            return

        largest_contour = max(contours, key=cv2.contourArea)
        pts = np.zeros((4, 2), dtype="int32")
        s = largest_contour.sum(axis=2)
        pts[0] = largest_contour[np.argmin(s)]
        pts[2] = largest_contour[np.argmax(s)]
        diff = np.diff(largest_contour, axis=2)
        pts[1] = largest_contour[np.argmin(diff)]
        pts[3] = largest_contour[np.argmax(diff)]
        
        sam_clicked_pts = sort_corners(pts)

# =====================================================================
# שלב 3: ממשק כיוונון ידני (Drag Corners)
# =====================================================================
dragged_idx = -1

def manual_drag_callback(event, x, y, flags, param):
    global dragged_idx
    points, scale = param
    if event == cv2.EVENT_LBUTTONDOWN:
        for i, pt in enumerate(points):
            screen_pt = (int(pt[0] * scale), int(pt[1] * scale))
            if np.hypot(screen_x - screen_pt[0], screen_y - screen_pt[1]) < 12:
                dragged_idx = i
                break
    elif event == cv2.EVENT_MOUSEMOVE and dragged_idx != -1:
        points[dragged_idx] = [int(x / scale), int(y / scale)]
    elif event == cv2.EVENT_LBUTTONUP:
        dragged_idx = -1

# פונקציית עזר קטנה לטיפול הגלובלי בעכבר בשלב הגרירה
def manual_drag_callback_global(event, x, y, flags, param):
    global screen_x, screen_y
    screen_x, screen_y = x, y
    manual_drag_callback(event, x, y, flags, param)


# =====================================================================
# פונקציית הניהול הראשית של הזרימה ההיברידית
# =====================================================================
def run_hybrid_edge_finder(image_path):
    global sam_clicked_pts
    img = cv2.imread(image_path)
    if img is None:
        print(f"שגיאה בטעינת התמונה {image_path}")
        return

    h, w, _ = img.shape
    img_area = w * h
    scale = MAX_SCREEN_HEIGHT / h if h > MAX_SCREEN_HEIGHT else 1.0
    display_w, display_h = int(w * scale), int(h * scale)

    chosen_points = None

    # ---- שלב 1: ניסיון גיאומטרי אוטומטי ----
    print("[1/3] מריץ בדיקה גיאומטרית אוטומטית...")
    layers = run_geometry_step(image_path, img, h, w, img_area)

    if layers is not None:
        print("[✓] נמצאו מרובעים אוטומטיים! פותח ממשק בחירת שכבה...")
        current_idx = 0
        while True:
            img_show = img.copy()
            selected_contour = layers[current_idx][1]
            cv2.drawContours(img_show, [selected_contour], -1, (0, 0, 255), 4)
            
            # תצוגה מוקטנת למסך
            img_disp = cv2.resize(img_show, (display_w, display_h), interpolation=cv2.INTER_AREA)
            cv2.putText(img_disp, f"Layer {current_idx+1}/{len(layers)} | Press 'N' for next | ESC to accept | 'F' for SAM fallback", 
                        (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            
            cv2.imshow("Layer Selector", img_disp)
            key = cv2.waitKey(0) & 0xFF
            if key == 27:  # ESC - מאשר את השכבה וממשיך איתה
                chosen_points = sort_corners(layers[current_idx][1])
                break
            elif key == ord('n') or key == ord('N'):
                current_idx = (current_idx + 1) % len(layers)
            elif key == ord('f') or key == ord('F'): # המשתמש לא מרוצה, מבקש לעבור ל-SAM
                chosen_points = None
                break
        cv2.destroyWindow("Layer Selector")

    # ---- שלב 2: Fallback - קליק במרכז עם SAM 2 ----
    if chosen_points is None:
        print("[2/3] שלב אוטומטי דילג/נכשל. נא ללחוץ על מרכז המסגרת להפעלת SAM 2...")
        cv2.namedWindow("SAM Fallback - Click Center")
        cv2.setMouseCallback("SAM Fallback - Click Center", sam_click_callback, param=(img, scale, image_path))
        
        while True:
            img_disp = cv2.resize(img, (display_w, display_h), interpolation=cv2.INTER_AREA)
            cv2.putText(img_disp, "CLICK inside the frame artwork area. Press ESC when polygon appears to lock it.", 
                        (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 2)
            
            if sam_clicked_pts is not None:
                # ציור פוליגון זמני לבדיקה
                for pt in sam_clicked_pts:
                    cv2.circle(img_disp, (int(pt[0] * scale), int(pt[1] * scale)), 6, (0, 255, 0), -1)
                poly = np.array([[int(p[0]*scale), int(p[1]*scale)] for p in sam_clicked_pts], dtype=np.int32)
                cv2.polylines(img_disp, [poly], isClosed=True, color=(0, 255, 0), thickness=2)

            cv2.imshow("SAM Fallback - Click Center", img_disp)
            key = cv2.waitKey(30) & 0xFF
            if key == 27 and sam_clicked_pts is not None: # ננעל על הבחירה של SAM
                chosen_points = sam_clicked_pts.copy()
                break
        cv2.destroyWindow("SAM Fallback - Click Center")

    # ---- שלב 3: כיוונון ידני פיקסל פרפקט בגרירה ----
    print("[3/3] פותח ממשק כיוונון ידני מנוהל עכבר...")
    cv2.namedWindow("Manual Fine Tuning")
    cv2.setMouseCallback("Manual Fine Tuning", manual_drag_callback_global, param=(chosen_points, scale))

    while True:
        img_show = img.copy()
        # ציור קווים בין ארבע הנקודות
        poly = np.array([[int(p[0]), int(p[1])] for p in chosen_points], dtype=np.int32)
        cv2.polylines(img_show, [poly], isClosed=True, color=(255, 0, 0), thickness=3)
        
        # ציור הנקודות עצמן
        for i, pt in enumerate(chosen_points):
            cv2.circle(img_show, (int(pt[0]), int(pt[1])), 10, (0, 0, 255), -1)
            cv2.putText(img_show, f"P{i+1}", (int(pt[0])+15, int(pt[1])+15), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

        img_disp = cv2.resize(img_show, (display_w, display_h), interpolation=cv2.INTER_AREA)
        cv2.putText(img_disp, "DRAG red dots with left mouse button to perfect corners. Press ENTER/Space to save.", 
                    (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 2)
        
        cv2.imshow("Manual Fine Tuning", img_disp)
        key = cv2.waitKey(10) & 0xFF
        if key == 13 or key == 32:  # מקש Enter או רווח - אישור סופי!
            break

    cv2.destroyAllWindows()

    # ביצוע ה-Inset (פיצוי של 3 פיקסלים פנימה כדי למנוע רווחים לבנים/עלייה על העץ)
    # מחשבים את המרכז הגיאומטרי כדי לדעת לאן למשוך פנימה
    centroid = np.mean(chosen_points, axis=0)
    final_json_data = []
    for pt in chosen_points:
        # וקטור כיוון מהנקודה למרכז בגודל 3 פיקסלים
        vec = centroid - pt
        vec_normalized = vec / np.linalg.norm(vec)
        inset_pt = pt + vec_normalized * 3
        final_json_data.append({"x": int(round(inset_pt[0])), "y": int(round(inset_pt[1]))})

    # שמירה ל-JSON
    output_path = "corners.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_json_data, f, indent=2)

    print(f"\n[✓] תהליך הסתיים בהצלחה! הפינות המדויקות נשמרו ב- {output_path}:")
    print(json.dumps(final_json_data, indent=2))


if __name__ == "__main__":
    # נסה להריץ על התמונה החדשה והמורכבת ששלחת עכשיו!
    target_image = "workspace/2.png"
    
    if os.path.exists(target_image):
        run_hybrid_edge_finder(target_image)
    else:
        print(f"אנא שים את הקובץ '{target_image}' בתיקייה והרץ שוב.")