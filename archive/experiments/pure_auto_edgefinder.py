import json
import os
import cv2
import numpy as np


def sort_corners(points):
    """מיון 4 פינות בסדר קבוע: TL, TR, BR, BL"""
    points = points.reshape(4, 2)
    sorted_pts = np.zeros((4, 2), dtype="int32")

    s = points.sum(axis=1)
    sorted_pts[0] = points[np.argmin(s)]  # Top-Left
    sorted_pts[2] = points[np.argmax(s)]  # Bottom-Right

    diff = np.diff(points, axis=1)
    sorted_pts[1] = points[np.argmin(diff)]  # Top-Right
    sorted_pts[3] = points[np.argmax(diff)]  # Bottom-Left

    return sorted_pts


def find_all_mockup_layers(image_path, output_json_path="corners.json"):
    # 1. טעינת התמונה המקורית
    img = cv2.imread(image_path)
    if img is None:
        print(f"שגיאה: לא ניתן לפתוח את הקובץ {image_path}")
        return

    h, w, _ = img.shape
    img_area = w * h

    # 2. עיבוד תמונה בסיסי לחילוץ קווים גיאומטריים חדים
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(edged, kernel, iterations=1)

    # 3. מציאת קווי מתאר ומציאת מרובעים בלבד
    contours, _ = cv2.findContours(
        dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )

    valid_rectangles = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)

        if len(approx) == 4:
            area = cv2.contourArea(approx)
            # סינון לפי גודל יחסי במוקאפ
            if (img_area * 0.04) < area < (img_area * 0.90):
                x, y, box_w, box_h = cv2.boundingRect(approx)
                aspect_ratio = float(box_w) / box_h
                if 0.2 < aspect_ratio < 5.0:
                    valid_rectangles.append((area, approx))

    if not valid_rectangles:
        print("[-] לא זוהו מרובעים גיאומטריים במוקאפ זה.")
        return

    # מיון המרובעים מהגדול ביותר (השכבה החיצונית) לקטן ביותר (השכבה הפנימית)
    valid_rectangles.sort(key=lambda x: x[0], reverse=True)

    # סינון מרובעים כפולים או כמעט זהים (הפרש של פחות מ-1.5% מהשטח)
    unique_layers = []
    for r in valid_rectangles:
        if not unique_layers or abs(r[0] - unique_layers[-1][0]) > (
            img_area * 0.015
        ):
            unique_layers.append(r)

    total_layers = len(unique_layers)
    print(f"[+] זוהו {total_layers} שכבות מרובעות פוטנציאליות במוקאפ.")

    # פלטת צבעים קבועה לציור כל השכבות בו-זמנית (בסדר: ירוק, כחול, צהוב, סגול, אקווה)
    colors = [
        (0, 255, 0),
        (255, 0, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
    ]
    current_idx = 0

    MAX_SCREEN_HEIGHT = 800

    # לולאת הממשק להחלפת שכבות
    while True:
        # יוצרים עותק נקי של התמונה המקורית בכל סיבוב
        img_res = img.copy()

        # א. ציור *כל* השכבות שנמצאו ברקע בצבעים שונים כדי שהמשתמש יראה אותן
        for i, layer in enumerate(unique_layers):
            color = colors[i % len(colors)]
            # עובי דק לשכבות הרקע
            cv2.drawContours(img_res, [layer[1]], -1, color, 2)

        # ב. הדגשת השכבה שנבחרה כרגע (קו אדום עבה במיוחד)
        selected_contour = unique_layers[current_idx][1]
        cv2.drawContours(
            img_res, [selected_contour], -1, (0, 0, 255), 5
        )  # קו אדום עבה

        # ג. חילוץ ומיון הפינות של השכבה הנבחרת לצורך הצגה ושמירה
        final_points = sort_corners(selected_contour)
        json_data = [
            {"x": int(final_points[0][0]), "y": int(final_points[0][1])},  # TL
            {"x": int(final_points[1][0]), "y": int(final_points[1][1])},  # TR
            {"x": int(final_points[2][0]), "y": int(final_points[2][1])},  # BR
            {"x": int(final_points[3][0]), "y": int(final_points[3][1])},  # BL
        ]

        # ציור נקודות ומספרים על הפינות הנבחרות
        for idx, p in enumerate(json_data):
            cv2.circle(img_res, (p["x"], p["y"]), 8, (0, 0, 255), -1)
            cv2.putText(
                img_res,
                f"P{idx+1}",
                (p["x"] + 15, p["y"] + 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 0),
                2,
            )

        # ד. כתיבת הנחיות טקסט על גבי התמונה
        layer_text = (
            "External Frame"
            if current_idx == 0
            else f"Inner Layer {current_idx}"
        )
        if current_idx == total_layers - 1 and total_layers > 1:
            layer_text = "Innermost Canvas (Artwork)"

        cv2.putText(
            img_res,
            f"Layer: {layer_text} ({current_idx + 1}/{total_layers})",
            (30, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            4,
        )
        cv2.putText(
            img_res,
            f"Layer: {layer_text} ({current_idx + 1}/{total_layers})",
            (30, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
        )
        cv2.putText(
            img_res,
            "Press 'N' to switch layers | ESC to save & exit",
            (30, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            3,
        )
        cv2.putText(
            img_res,
            "Press 'N' to switch layers | ESC to save & exit",
            (30, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 0),
            1,
        )

        # ה. התאמה דינמית לגודל המסך (תצוגה בלבד)
        if h > MAX_SCREEN_HEIGHT:
            scale = MAX_SCREEN_HEIGHT / h
            img_display = cv2.resize(
                img_res,
                (int(w * scale), MAX_SCREEN_HEIGHT),
                interpolation=cv2.INTER_AREA,
            )
        else:
            img_display = img_res

        # הצגה בחלון
        cv2.imshow("Multi-Layer Frame Selector", img_display)

        # ו. ניהול מקשים במקלדת
        key = cv2.waitKey(0) & 0xFF
        if key == 27:  # מקש ESC - שומר ויוצא
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2)
            print(f"\n✓ השכבה נשמרה בהצלחה ב-{output_json_path}!")
            print(json.dumps(json_data, indent=2))
            break
        elif key == ord("n") or key == ord("N"):  # מעבר לשכבה הבאה
            current_idx = (current_idx + 1) % total_layers

        # בדיקה אם החלון נסגר עצמאית
        if (
            cv2.getWindowProperty("Multi-Layer Frame Selector", cv2.WND_PROP_VISIBLE)
            < 1
        ):
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    # הגדר את המוקאפ המורכב שברצונך לבדוק כרגע
    target_image = "workspace/3.png"

    if os.path.exists(target_image):
        find_all_mockup_layers(target_image)
    else:
        print(f"הקובץ '{target_image}' לא נמצא בתיקיית הריצה שלך.")