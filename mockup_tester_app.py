import json
import os
import cv2
import numpy as np
from ultralytics import SAM

print("טוען את מודל SAM 2 ברקע... אנא המתן...")
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

def run_sam_on_points(image_path, orig_w, orig_h, pts_list):
    """ הרצת SAM 2 על נקודה/נקודות ומציאת 4 פינות """
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
    except Exception as e:
        print(f"[-] שגיאה בניתוח SAM: {e}")
        return None

def run_geometry_step(img, img_area):
    """ שלב 1: זיהוי גיאומטרי אוטומטי """
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

# ניהול קליקים לעכבר
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

def list_images_in_dir():
    """ סריקת קבצי תמונה בתיקייה נוכחית """
    extensions = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    return [f for f in os.listdir('.') if f.lower().endswith(extensions)]

# =====================================================================
# מנוע השתלה פרספקטיבי חכם בתוך המסגרת
# =====================================================================
def overlay_artwork(mockup_img, artwork_path, corners):
    artwork = cv2.imread(artwork_path)
    if artwork is None:
        print(f"[-] לא ניתן לטעון את תמונת השתילה {artwork_path}")
        return mockup_img

    # נקודות מקור של תמונת האמנות (הפינות של המלבן השלם שלה)
    art_h, art_w, _ = artwork.shape
    src_pts = np.array([[0, 0], [art_w - 1, 0], [art_w - 1, art_h - 1], [0, art_h - 1]], dtype="float32")
    
    # נקודות יעד (הפינות שחולצו ותוקנו במוקאפ)
    dst_pts = np.array([[c['x'], c['y']] for c in corners], dtype="float32")
    
    # חישוב מטריצת עיוות הפרספקטיבה (Homography)
    M, _ = cv2.findHomography(src_pts, dst_pts)
    
    # עיוות תמונת האמנות לגודל והטיה של המסגרת
    mockup_h, mockup_w, _ = mockup_img.shape
    warped_artwork = cv2.warpPerspective(artwork, M, (mockup_w, mockup_h))
    
    # יצירת מסיכה שחורה באזור ההשתלה על המוקאפ כדי למחוק את מה שהיה שם קודם
    mask = np.zeros((mockup_h, mockup_w), dtype="uint8")
    cv2.fillConvexPoly(mask, dst_pts.astype(int), 255)
    
    mask_inverse = cv2.bitwise_not(mask)
    mockup_background = cv2.bitwise_and(mockup_img, mockup_img, mask=mask_inverse)
    
    # חיבור סופי בין הרקע המנוקה לציור המעוות
    final_output = cv2.add(mockup_background, warped_artwork)
    return final_output

# =====================================================================
# זרימת הבדיקה המלאה
# =====================================================================
def run_main_workflow(mockup_path, artwork_path):
    global sam_clicked_pts
    sam_clicked_pts = None
    
    img = cv2.imread(mockup_path)
    if img is None:
        return

    h, w, _ = img.shape
    img_area = w * h
    scale = MAX_SCREEN_HEIGHT / h if h > MAX_SCREEN_HEIGHT else 1.0
    display_w, display_h = int(w * scale), int(h * scale)
    chosen_points = None

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

    # ---- שלב 3: קליק משתמש במרכז ----
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

    # שמירת ה-JSON כנדרש במערכת שלך
    with open("corners.json", "w", encoding="utf-8") as f:
        json.dump(final_corners, f, indent=2)

    # ---- שלב 5: השתלת הציור והצגת התוצאה הסופית ----
    print("[+] מחשב פרספקטיבה ושותל תמונה...")
    final_mockup = overlay_artwork(img, artwork_path, final_corners)
    
    cv2.setMouseCallback("Detection Process", lambda *args: None) # ביטול אירועי עכבר
    img_disp_final = cv2.resize(final_mockup, (display_w, display_h), interpolation=cv2.INTER_AREA)
    cv2.putText(img_disp_final, "SUCCESS! Press ANY KEY to return to main menu.", 
                (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    
    cv2.imshow("Detection Process", img_disp_final)
    cv2.waitKey(0)
    cv2.destroyWindow("Detection Process")


def main_menu():
    while True:
        images = list_images_in_dir()
        if not images:
            print("[-] לא נמצאו קבצי תמונה בתיקייה הנוכחית!")
            break
            
        print("\n=========================================")
        print(" תפריט אפליקציית בדיקת מוקאפים אינטראקטיבית")
        print("=========================================")
        for idx, img_name in enumerate(images):
            print(f"[{idx}] {img_name}")
        print("=========================================")
        
        try:
            mock_idx = int(input("בחר את מספר תמונת ה-MOCKUP (או -1 ליציאה): "))
            if mock_idx == -1: break
            art_idx = int(input("בחר את מספר תמונת הציור לשתילה (Artwork): "))
            
            if 0 <= mock_idx < len(images) and 0 <= art_idx < len(images):
                run_main_workflow(images[mock_idx], images[art_idx])
            else:
                print("[-] בחירה לא תקינה, נסה שוב.")
        except ValueError:
            print("[-] אנא הזן מספרים בלבד.")

if __name__ == "__main__":
    main_menu()