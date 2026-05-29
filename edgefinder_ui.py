import json
import os
import cv2
import numpy as np
from ultralytics import SAM

print("טוען את מודל SAM 2...")
model = SAM("sam2_l.pt")

# משתנים גלובליים
current_image_path = ""
original_image = None
detected_rectangles = []  # רשימה שתכיל את כל המרובעים שנמצאו (פנימיים וחיצוניים)
current_rect_idx = 0      # אינדקס המרובע הנוכחי


def intersection(line1, line2):
    """ חישוב נקודת מפגש מדויקת בין שני קווים """
    xdiff = (line1[0][0] - line1[1][0], line2[0][0] - line2[1][0])
    ydiff = (line1[0][1] - line1[1][1], line2[0][1] - line2[1][1])

    def det(a, b):
        return a[0] * b[1] - a[1] * b[0]

    div = det(xdiff, ydiff)
    if div == 0: return None

    d = (det(*line1), det(*line2))
    x = det(d, xdiff) / div
    y = det(d, ydiff) / div
    return int(round(x)), int(round(y))


def sort_corners(points):
    """ מיון 4 פינות בסדר קבוע: TL, TR, BR, BL """
    sorted_pts = np.zeros((4, 2), dtype="int32")
    s = points.sum(axis=1)
    sorted_pts[0] = points[np.argmin(s)]
    sorted_pts[2] = points[np.argmax(s)]
    diff = np.diff(points, axis=1)
    sorted_pts[1] = points[np.argmin(diff)]
    sorted_pts[3] = points[np.argmax(diff)]
    return sorted_pts


def find_all_nested_rectangles(mask):
    """
    מוצא את כל המלבנים המקוננים (פנימיים וחיצוניים) בתוך המסיכה
    בעזרת זיהוי קווים וחלוקה להיררכיות.
    """
    h, w = mask.shape
    rectangles = []

    # 1. מציאת קצוות וקווים ישרים
    edges = cv2.Canny(mask, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=30, minLineLength=20, maxLineGap=15)

    if lines is None:
        return rectangles

    # 2. הפרדה לקווים אופקיים ואנכיים
    horizontal = []
    vertical = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(x2 - x1) > abs(y2 - y1):
            horizontal.append(((x1, y1), (x2, y2)))
        else:
            vertical.append(((x1, y1), (x2, y2)))

    if len(horizontal) < 2 or len(vertical) < 2:
        return rectangles

    # 3. קיבוץ קווים קרובים (למניעת כפילויות של פיקסלים בודדים)
    def group_lines(lines_list, index_to_check, tolerance=15):
        grouped = []
        lines_list.sort(key=lambda l: (l[0][index_to_check] + l[1][index_to_check]) / 2)
        for line in lines_list:
            coord = (line[0][index_to_check] + line[1][index_to_check]) / 2
            if not grouped or abs(coord - grouped[-1]['coord']) > tolerance:
                grouped.append({'coord': coord, 'lines': [line]})
            else:
                grouped[-1]['lines'].append(line)
        return [g['lines'][0] for g in grouped] # לוקחים קו מייצג מכל קבוצה

    distinct_horiz = group_lines(horizontal, 1) # לפי Y
    distinct_vert = group_lines(vertical, 0)    # לפי X

    # 4. מציאת הצטלבויות בין כל זוג אופקי לכל זוג אנכי (מייצר את שכבות המסגרת)
    for i in range(len(distinct_horiz) - 1):
        for j in range(i + 1, len(distinct_horiz)):
            top_l = distinct_horiz[i]
            bot_l = distinct_horiz[j]
            
            for m in range(len(distinct_vert) - 1):
                for n in range(m + 1, len(distinct_vert)):
                    left_l = distinct_vert[m]
                    right_l = distinct_vert[n]

                    tl = intersection(top_l, left_l)
                    tr = intersection(top_l, right_l)
                    br = intersection(bot_l, right_l)
                    bl = intersection(bot_l, left_l)

                    if all([tl, tr, br, bl]):
                        pts = np.array([tl, tr, br, bl])
                        # סינון לפי מינימום שטח כדי להוריד רעשים
                        area = cv2.contourArea(pts)
                        if area > (w * h * 0.05):
                            rectangles.append((area, sort_corners(pts)))

    # מיון המרובעים מהגדול ביותר (חיצוני) לקטן ביותר (פנימי)
    rectangles.sort(key=lambda x: x[0], reverse=True)
    
    # הסרת מרובעים כמעט זהים
    unique_rects = []
    for r in rectangles:
        if not unique_rects or abs(r[0] - unique_rects[-1][0]) > (w * h * 0.02):
            unique_rects.append(r)

    return [r[1] for r in unique_rects]


def draw_current_selection():
    """ מציג את המרובע שנבחר כרגע ומדפיס JSON """
    global detected_rectangles, current_rect_idx, original_image
    if not detected_rectangles: return

    final_points = detected_rectangles[current_rect_idx]

    # יצירת ה-JSON
    json_data = [
        {"x": int(final_points[0][0]), "y": int(final_points[0][1])},  # TL
        {"x": int(final_points[1][0]), "y": int(final_points[1][1])},  # TR
        {"x": int(final_points[2][0]), "y": int(final_points[2][1])},  # BR
        {"x": int(final_points[3][0]), "y": int(final_points[3][1])}   # BL
    ]

    with open("corners.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    # שכפול תמונה לציור
    img_result = original_image.copy()

    # ציור קו אדום בולט סביב הבחירה הנוכחית
    poly_pts = np.array([[p["x"], p["y"]] for p in json_data], dtype=np.int32)
    cv2.polylines(img_result, [poly_pts], isClosed=True, color=(0, 0, 255), thickness=2)

    # סימון ומספור הפינות
    for i, p in enumerate(json_data):
        cv2.circle(img_result, (p["x"], p["y"]), 6, (255, 0, 0), -1)
        cv2.putText(img_result, f"P{i+1}", (p["x"]+10, p["y"]+10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    # כיתוב ממשק
    total_layers = len(detected_rectangles)
    layer_name = "External Frame" if current_rect_idx == 0 else f"Inner Frame Layer {current_rect_idx}"
    if current_rect_idx == total_layers - 1 and total_layers > 1:
        layer_name = "Artwork / Canvas (Innermost)"

    cv2.putText(img_result, f"Selected: {layer_name} ({current_rect_idx + 1}/{total_layers})", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 4)
    cv2.putText(img_result, f"Selected: {layer_name} ({current_rect_idx + 1}/{total_layers})", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    cv2.putText(img_result, "Press 'N' to switch layers | ESC to exit & save", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    cv2.imshow("Mockup Precision Edge Finder", img_result)
    
    print(f"\n[➔] שכבה {current_rect_idx + 1} מתוך {total_layers} מוצגת. ה-JSON עודכן:")
    print(json.dumps(json_data, indent=2))


def click_event(event, x, y, flags, param):
    global detected_rectangles, current_rect_idx, original_image
    if event == cv2.EVENT_LBUTTONDOWN:
        print(f"\n[+] קליק בנקודה: X={x}, Y={y}. מחלץ קווים ומלבנים מקוננים...")
        
        # הרצת SAM 2
        results = model.predict(source=current_image_path, points=[x, y], labels=[1], device="cpu", verbose=False)
        if len(results) == 0 or results[0].masks is None:
            print("[-] לא זוהה אובייקט.")
            return

        h, w, _ = original_image.shape
        mask = (results[0].masks.data[0].cpu().numpy() * 255).astype(np.uint8)
        if mask.shape[0] != h or mask.shape[1] != w:
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

        # הפעלת מנגנון זיהוי מלבנים רב-שכבתי
        detected_rectangles = find_all_nested_rectangles(mask)
        current_rect_idx = 0

        if detected_rectangles:
            draw_current_selection()
        else:
            print("[-] לא הצלחנו לבנות מלבנים מבוססי קווים מהמסיכה הזו. נסה ללחוץ שוב קרוב יותר למסגרת.")


def run_ui(image_path):
    global current_image_path, original_image, current_rect_idx, detected_rectangles
    current_image_path = image_path
    original_image = cv2.imread(image_path)
    
    if original_image is None:
        print(f"Error: Could not open {image_path}")
        return

    cv2.namedWindow("Mockup Precision Edge Finder", cv2.WINDOW_AUTOSIZE)
    cv2.imshow("Mockup Precision Edge Finder", original_image)
    cv2.setMouseCallback("Mockup Precision Edge Finder", click_event)

    while True:
        key = cv2.waitKey(1) & 0xFF
        if key == 27:  # ESC
            break
        elif key == ord('n') or key == ord('N'):
            if detected_rectangles:
                current_rect_idx = (current_rect_idx + 1) % len(detected_rectangles)
                draw_current_selection()

        if cv2.getWindowProperty("Mockup Precision Edge Finder", cv2.WND_PROP_VISIBLE) < 1:
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    # שנה כאן לתמונת המוקאפ הרב-שכבתית שאתה רוצה לבדוק
    target_image = "workspace/3.png"
    
    if os.path.exists(target_image):
        run_ui(target_image)
    else:
        print(f"קובץ '{target_image}' לא נמצא.")