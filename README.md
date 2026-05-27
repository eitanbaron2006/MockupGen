# Mockup Studio Backend

שרת Flask ליצירת מוקאפ מתמונת משתמש, כולל ממשק פנימי לניהול תבניות, קטגוריות
וזיהוי אזור ההדבקה באמצעות Vertex AI, מודל מקומי, או זיהוי קצוות קלאסי ללא AI.
הרינדור הסופי נשאר אמין ופשוט: שכבות
PNG באמצעות Pillow. מנגנוני `psd` ו-`ai` לרינדור סופי נשארים הרחבות עתידיות.

## הפעלה ועצירה ב-Git Bash

מהתיקייה `~/Desktop/MockupGen`:

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env
```

פתח את `.env` והחלף לפחות את:

```env
SECRET_KEY=replace-with-a-long-random-secret
ADMIN_PASSWORD=replace-with-a-private-admin-password
```

הפעל:

```bash
./.venv/Scripts/python.exe app.py
```

עצירה: בחלון הטרמינל שבו השרת רץ, לחץ `Ctrl+C`.

אחרי שינוי קוד או `.env`, עצור והפעל מחדש. כאשר מופיע:

```text
Running on http://127.0.0.1:5000
```

הממשק האמיתי נמצא בכתובת:

```text
http://localhost:5000/admin
```

## חיבור Vertex AI

קובץ `.env.example` כבר כולל את Project ID שסיפקת:

```env
DETECTION_PROVIDER=vertex
VERTEX_PROJECT_ID=vertextai-project-497513
VERTEX_LOCATION=global
VERTEX_MODEL=gemini-2.5-flash
VERTEX_MEDIA_RESOLUTION=high
DETECTION_REFINEMENT=ai_only
```

השרת משתמש ב-Google Application Default Credentials, לא במפתח שנשמר בדפדפן.
בטרמינל שבו מותקן `gcloud`, בצע פעם אחת:

```bash
gcloud auth application-default login
gcloud config set project vertextai-project-497513
gcloud auth application-default set-quota-project vertextai-project-497513
```

אם Vertex AI API אינו מופעל בפרויקט, יש להפעיל אותו ב-Google Cloud Console
עבור `vertextai-project-497513`.

בתוך `/admin`:

1. צור קטגוריה, למשל `Wall Art`.
2. בחר כמה תמונות רקע יחד תחת `Choose images`.
3. בחר מוקאפ מה-Import Queue.
4. תחת `Detection engine` בחר אחת מהאפשרויות:
   `Vertex AI` לזיהוי ענן, `Local AI` לשירות מקומי, או `Classic` לזיהוי קצוות ללא מודל.
5. ב-`Vertex AI` הרשימה נטענת מ-Vertex Model Garden ומציגה מודלי Gemini
   המתאימים לניתוח תמונה ולהחזרת מלבן; מודלי יצירת תמונה, אודיו ו-embedding מסוננים.
6. מומלץ להשאיר `AI bounding box only`: בבדיקות על המוקאפים שלך Vertex נתן
   מלבן טוב, בעוד הצמדת קצוות עלולה להתבלבל מטקסט פנימי. האפשרות
   `AI + guarded edge refinement` נשארת לניסוי ומבוטלת אוטומטית אם היא מעוותת
   את המלבן של Vertex.
7. לחץ `Test connection` כדי לבדוק את הספק הנבחר על המוקאפ המסומן, ואז
   `Preview detection` להצגת המלבן על הקנבס.
8. שגיאת אימות או הרשאה תוצג במסך ולא תיעלם בשקט.
9. בדוק את המלבן, גרור או שנה את גודלו במידת הצורך, ולחץ `Save draft`.
10. לחץ `Approve` כדי לפרסם אותו לשימוש ה-API.

### אילו מוקאפים נותנים זיהוי טוב יותר

מומלץ להעדיף תמונת מוקאפ שבה אזור ההדבקה מסומן במלבן פנימי ברור, ובפרט
מלבן מקווקו אפור סביב הכיתוב `YOUR ARTWORK HERE`. מצב `Classic / No AI`
משתמש בגבול הזה ישירות. במצב Vertex, ברירת המחדל היא לשמור את המלבן שמחזיר
המודל; ניתן לנסות הצמדת קצוות שמורה, אך עדיין יש לעבור על ההצעה לפני אישור.

נתוני הניהול נשמרים מקומית ב-`data/mockup_catalog.sqlite3`. תבניות בטיוטה
נשמרות ב-`draft_templates/`; רק תבניות מאושרות נכתבות ל-`templates_data/`.

## API ציבורי

בדיקת תקינות:

```bash
curl http://localhost:5000/api/health
```

קטגוריות פעילות:

```bash
curl http://localhost:5000/api/mockups/categories
```

תבניות פעילות בקטגוריה:

```bash
curl "http://localhost:5000/api/mockups/templates?product_type=wall-art"
```

רינדור עם תבנית מסוימת:

```bash
curl.exe -X POST http://localhost:5000/api/mockups/render \
  -F "mode=simple" \
  -F "template_id=template_001" \
  -F "artwork=@my-artwork.png" \
  -F "output_format=png"
```

רינדור עם בחירה אוטומטית: השרת בוחר תבנית באותה קטגוריה שהיחס של אזור
ההדבקה שלה הוא הקרוב ביותר לתמונה שהמשתמש העלה.

```bash
curl.exe -X POST http://localhost:5000/api/mockups/render \
  -F "mode=simple" \
  -F "product_type=wall-art" \
  -F "artwork=@my-artwork.png" \
  -F "output_format=png"
```

ב-Git Bash המשך שורה הוא `\`, לא backtick של PowerShell.

## מבנה תבנית מאושרת

כל תבנית חיה תחת `templates_data/<template_id>/`:

```text
manifest.json
background.png
preview.png
foreground.png   # אופציונלי
mask.png         # אופציונלי
```

- `background.png` היא תמונת הסצנה המקורית.
- `foreground.png` היא שכבת PNG שקופה אמיתית מעל האמנות. אם אינה קיימת,
  הרינדור ממשיך ללא שכבה זו.
- `mask.png` מגביל את שטח ההדבקה אם נדרש.
- `manifest.json` מכיל את גודל הקנבס, הקטגוריה והמלבן `artwork_area`.

## הכנת מוקאפ לזיהוי מדויק

כדי לשפר את הזיהוי, העדף תמונת רקע חזיתית וברזולוציה גבוהה, עם מסגרת פנימית
ברורה וניגוד טוב בין אזור ההדפסה למסגרת. עדיף להשאיר את אזור האמנות ריק
ונקי מטקסט כמו `YOUR ARTWORK HERE`, להימנע מפרספקטיבה חדה והצללות כבדות
בתוך המסגרת, ולשמור פריטי עיצוב שחייבים להיות מעל האמנות בשכבת
`foreground.png` נפרדת.
