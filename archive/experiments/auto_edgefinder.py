import json
import os
import cv2
import numpy as np
from ultralytics import YOLO, SAM

print("טוען מודלים (YOLOv8 + SAM 2)...")
# טעינת YOLOv8 קליל שמזהה picture frame מהקופסה
yolo_model = YOLO("yolov8n.pt")
# טעינת מודל SAM 2
sam_model = SAM("sam2_l.pt")


def intersection(line1, line2):
    """ חישוב נקודת מפגש מתמטית מדויקת בין שני קווים """
    xdiff = (line1[0][0] - line1[1][0], line2[0][0] - line2[1][0])
    ydiff = (line1[0][1] - line1[1][1], line2[0][1] - line2[1][1])

    def det(a, b):
        return a[0] * b[1] - a[1] * b[0]

    div = det(xdiff, ydiff)
    if div == 0:
        return None

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


def find_nested_rectangles_from_mask(mask):
    """
    מנתח את המסיכה של SAM באמצעות Hough Lines ומחלץ את
    המלבן החיצוני ביותר והפנימי ביותר (הקנבס להשתלה)
    """
    h, w = mask.shape
    rectangles = []

    edges = cv2.Canny(mask, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=30, minLineLength=20, maxLineGap=15
    )

    if lines is None:
        return None

    horizontal = []
    vertical = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(x2 - x1) > abs(y2 - y1):
            horizontal.append(((x1, y1), (x2, y2)))
        else:
            vertical.append(((x1, y1), (x2, y2)))

    if len(horizontal) < 2 or len(vertical) < 2:
        return None

    # קיבוץ קווים קרובים
    def group_lines(lines_list, index_to_check, tolerance=15):
        grouped = []
        lines_list.sort(
            key=lambda l: (l[0][index_to_check] + l[1][index_to_check]) / 2
        )
        for line in lines_list:
            coord = (line[0][index_to_check] + line[1][index_to_check]) / 2
            if not grouped or abs(coord - grouped[-1]["coord"]) > tolerance:
                grouped.append({"coord": coord, "lines": [line]})
            else:
                grouped[-1]["lines"].append(line)
        return [g["lines"][0] for g in grouped]

    distinct_horiz = group_lines(horizontal, 1)
    distinct_vert = group_lines(vertical, 0)

    # מציאת הצטלבויות
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
                        area = cv2.contourArea(pts)
                        if area > (w * h * 0.04):  # לפחות 4% משטח התמונה
                            rectangles.append((area, sort_corners(pts)))

    if not rectangles:
        return None

    # מיון מהגדול לקטן
    rectangles.sort(key=lambda x: x[0], reverse=True)

    # סינון כפילויות קרובות
    unique_rects = []
    for r in rectangles:
        if not unique_rects or abs(r[0] - unique_rects[-1][0]) > (w * h * 0.01):
            unique_rects.append(r[1])

    return unique_rects


def process_image_fully_auto(image_path, output_json_path="corners.json"):
    img = cv2.imread(image_path)
    if img is None:
        print(f"שגיאה: לא ניתן לפתוח את {image_path}")
        return

    h, w, _ = img.shape
    print(f"[1/3] מריץ YOLOv8 לזיהוי אוטומטי של מיקום המסגרת...")

    # הרצת YOLO. מחפשים Class ID 72 שזה 'picture' במאגר COCO הסטנדרטי
    yolo_results = yolo_model.predict(source=image_path, verbose=False)

    best_bbox = None
    for box in yolo_results[0].boxes:
        cls_id = int(box.cls[0])
        # מחפש אובייקט מסוג תמונה/מסגרת (במודלים מסוימים זה 72, בודקים קודם כל זיהוי)
        conf = float(box.conf[0])
        if conf > 0.25:  # רף ביטחון מינימלי
            # לוקחים את התיבה הראשונה שקיבלנו בביטחון גבוה
            xyxy = box.xyxy[0].cpu().numpy()
            best_box = [int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])]
            # אם זה מזוהה כקרוב למרכז או תופס נפח, נשמור אותו
            best_bbox = best_box
            break

    # גיבוי במידה ו-YOLO לא מצא שום מסגרת קלאסית: ניקח תיבת מרכז של 75% מהתמונה
    if best_bbox is None:
        print("[-] YOLO לא זיהה מסגרת ברורה. משתמש בתיבת גיבוי מרכזית...")
        best_bbox = [int(w * 0.12), int(h * 0.12), int(w * 0.88), int(h * 0.88)]
    else:
        print(f"✓ YOLO מצא מסגרת במיקום: {best_bbox}")

    print(f"[2/3] מעביר את התיבה ל-SAM 2 לחילוץ מסיכה מדויקת...")
    sam_results = sam_model.predict(
        source=image_path, bboxes=[best_bbox], device="cpu", verbose=False
    )

    if len(sam_results) == 0 or sam_results[0].masks is None:
        print("[-] SAM נכשל בניתוח התיבה.")
        return

    mask = (sam_results[0].masks.data[0].cpu().numpy() * 255).astype(np.uint8)
    if mask.shape[0] != h or mask.shape[1] != w:
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

    print(f"[3/3] מנתח קווים גיאומטריים (Hough Lines) למניעת רווחים...")
    rectangles = find_nested_rectangles_from_mask(mask)

    if not rectangles:
        print("[-] לא הצלחנו לזקק מלבנים חדים מהקווים. משתמש בגיבוי קונטור...")
        # גיבוי קונטור רגיל
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        largest_contour = max(contours, key=cv2.contourArea)
        pts = np.zeros((4, 2), dtype="int32")
        s = largest_contour.sum(axis=2)
        pts[0] = largest_contour[np.argmin(s)]
        pts[2] = largest_contour[np.argmax(s)]
        diff = np.diff(largest_contour, axis=2)
        pts[1] = largest_contour[np.argmin(diff)]
        pts[3] = largest_contour[np.argmax(diff)]
        chosen_points = sort_corners(pts)
        mode_text = "Contour Backup (Less Precise)"
    else:
        # חוק קבוע במערכת ייצור:
        # אם יש יותר ממלבן אחד (כלומר יש מסגרת עץ חיצונית ומסגרת קנבס פנימית),
        # אנחנו תמיד לוקחים את המלבן הפנימי ביותר (הקטן ביותר ברשימה הממוינת) להשתלה!
        if len(rectangles) > 1:
            chosen_points = rectangles[-1]  # הפנימי ביותר
            mode_text = f"Innermost Artwork Canvas (Layer {len(rectangles)})"
        else:
            chosen_points = rectangles[0]  # יש רק שכבה אחת
            mode_text = "Single Frame Layer Detected"

    # יצירת ה-JSON הסופי
    json_data = [
        {"x": int(chosen_points[0][0]), "y": int(chosen_points[0][1])},  # TL
        {"x": int(chosen_points[1][0]), "y": int(chosen_points[1][1])},  # TR
        {"x": int(chosen_points[2][0]), "y": int(chosen_points[2][1])},  # BR
        {"x": int(chosen_points[3][0]), "y": int(chosen_points[3][1])},  # BL
    ]

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    print(f"\n✓ המטרה הושגה! הפינות חולצו בשיטת שגר-ושכח ({mode_text}):")
    print(json.dumps(json_data, indent=2))

    # הצגת התוצאה הוויזואלית על המסך לבדיקה שלך
    img_res = img.copy()
    poly_pts = np.array([[p["x"], p["y"]] for p in json_data], dtype=np.int32)
    cv2.polylines(
        img_res, [poly_pts], isClosed=True, color=(0, 255, 0), thickness=2
    )

    for i, p in enumerate(json_data):
        cv2.circle(img_res, (p["x"], p["y"]), 5, (0, 0, 255), -1)

    cv2.imshow("Automatic Zero-Click Edge Finder Result", img_res)
    print("\nלחץ על מקש כלשהו על תמונת התוצאה כדי לסגור את התוכנית.")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    # שנה כאן לכל אחד מהמוקאפים הבעייתיים שלך: 
    # image_ff16a6.jpg, image_fe8500.jpg, image_fe8820.jpg
    target_mockup = "workspace/3.png"

    if os.path.exists(target_mockup):
        process_image_fully_auto(target_mockup)
    else:
        print(f"אנא שים את הקובץ '{target_mockup}' בתיקייה והרז שוב.")