import json
import os
import cv2
import numpy as np
from ultralytics import SAM

print("טוען את מודל SAM 2 (דרך Ultralytics)...")
model = SAM("sam2_l.pt")

def find_artwork_bounding_box(image_bgr, w, h):
    """
    מוצא מלבן חוסם כללי (Bounding Box) סביב המסגרת העיקרית בתמונה.
    משתמש בטכניקת השחרת רקע אדפטיבית כדי להתגבר על צללים וצבעים משתנים.
    """
    try:
        # 1. המרה לגווני אפור וטשטוש חזק כדי להעלים פרטים קטנים בתוך הציור
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)
        
        # 2. סף דינמי (Otsu) למציאת האובייקט המרכזי הבולט
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        
        # אם קיבלנו תמונת סף הפוכה (יותר מדי לבן בקצוות), נהפוך אותה
        if np.mean(thresh[0:10, :]) > 127:
            thresh = cv2.bitwise_not(thresh)
            
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        valid_boxes = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < (w * h * 0.08): # לפחות 8% משטח התמונה
                continue
                
            x, y, box_w, box_h = cv2.boundingRect(c)
            
            # ודא שהמלבן לא תופס את כל התמונה (כמו רקע של קיר)
            if box_w < w * 0.95 and box_h < h * 0.95:
                valid_boxes.append((area, [x, y, x + box_w, y + box_h]))
                
        if valid_boxes:
            # לוקחים את הקונטור הדומיננטי ביותר
            valid_boxes.sort(key=lambda x: x[0], reverse=True)
            best_box = valid_boxes[0][1]
            
            # הרחבה קלה של המלבן ב-10 פיקסלים לכל כיוון כדי לוודא שהמסגרת כולה בפנים
            pad = 10
            x1 = max(0, best_box[0] - pad)
            y1 = max(0, best_box[1] - pad)
            x2 = min(w, best_box[2] + pad)
            y2 = min(h, best_box[3] + pad)
            
            print(f"✓ מלבן חוסם הוגדר בהצלחה עבור SAM: [{x1}, {y1}, {x2}, {y2}]")
            return [x1, y1, x2, y2]
            
    except Exception as e:
        print(f"אזהרה בניתוח המלבן החוסם: {e}")
        
    # גיבוי: מלבן חוסם שמכסה את מרכז התמונה (70% מהשטח)
    return [int(w * 0.15), int(h * 0.15), int(w * 0.85), int(h * 0.85)]

def get_mockup_corners(image_path, output_json_path=None):
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        raise FileNotFoundError(f"לא ניתן למצוא את הקובץ: {image_path}")
        
    h, w, _ = image_bgr.shape

    # 1. חישוב מלבן חוסם אוטומטי (Box Prompt)
    bbox = find_artwork_bounding_box(image_bgr, w, h)

    # 2. הרצת SAM 2 באמצעות bboxes במקום נקודות (points)
    # הפרמטר bboxes מקבל רשימה של רשימות: [[x_min, y_min, x_max, y_max]]
    results = model.predict(source=image_path, bboxes=[bbox], device="cpu", verbose=False)
    
    if len(results) == 0 or results[0].masks is None:
        raise ValueError("SAM לא הצליח לזהות אובייקט בתוך המלבן המוגדר.")
        
    mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8) * 255
    if mask.shape[0] != h or mask.shape[1] != w:
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

    # 3. מציאת קווי מתאר וחילוץ 4 הפינות
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("לא זוהו קווי מתאר למסיכה הבוקעת מהמודל.")
        
    largest_contour = max(contours, key=cv2.contourArea)

    # חילוץ 4 נקודות הקיצון הגיאומטריות (TL, TR, BR, BL)
    pts = np.zeros((4, 2), dtype="int32")
    s = largest_contour.sum(axis=2)
    pts[0] = largest_contour[np.argmin(s)]  # top-left
    pts[2] = largest_contour[np.argmax(s)]  # bottom-right
    
    diff = np.diff(largest_contour, axis=2)
    pts[1] = largest_contour[np.argmin(diff)] # top-right
    pts[3] = largest_contour[np.argmax(diff)] # bottom-left

    # מיון הנקודות בסדר קבוע
    def sort_points(points):
        sorted_pts = np.zeros((4, 2), dtype="int32")
        s = points.sum(axis=1)
        sorted_pts[0] = points[np.argmin(s)]
        sorted_pts[2] = points[np.argmax(s)]
        diff = np.diff(points, axis=1)
        sorted_pts[1] = points[np.argmin(diff)]
        sorted_pts[3] = points[np.argmax(diff)]
        return sorted_pts

    final_points = sort_points(pts)

    # 4. תיקון פנימי (Inset) קטן של 3 פיקסלים כדי להבטיח הלבשה מדויקת בתוך מסגרת העץ
    corrected_points = []
    for i, pt in enumerate(final_points):
        x, y = pt[0], pt[1]
        if i == 0:    # TL
            x += 3; y += 3
        elif i == 1:  # TR
            x -= 3; y += 3
        elif i == 2:  # BR
            x -= 3; y -= 3
        elif i == 3:  # BL
            x += 3; y -= 3
        corrected_points.append({"x": int(x), "y": int(y)})

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(corrected_points, f, indent=2)

    return corrected_points
    
if __name__ == "__main__":
    # ודא ששם הקובץ תואם לתמונת המוקאפ שיש לך בתיקייה
    input_mockup = "workspace/1.png"  # נתיב לתמונת המוקאפ שלך
    output_json = "workspace/corners.json"  # נתיב לשמירת ה-JSON
    try:
        corners = get_mockup_corners(input_mockup, output_json)
        print("\n✓ הפינות חולצו בהצלחה!")
        print(json.dumps(corners, indent=2))
    except Exception as e:
        print(f"שגיאה בתהליך: {e}")