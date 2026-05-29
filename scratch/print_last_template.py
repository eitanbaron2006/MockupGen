import sqlite3
import json

db_path = "data/mockup_catalog.sqlite3"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== LATEST TEMPLATES ===")
cursor.execute("SELECT * FROM templates ORDER BY created_at DESC LIMIT 5")
for row in cursor.fetchall():
    d = dict(row)
    print(f"ID: {d['template_id']}")
    print(f"Name: {d['name']}")
    print(f"Status: {d['status']}")
    print(f"Artwork Area: {d['artwork_area']}")
    print(f"Raw Artwork Area: {d.get('raw_artwork_area')}")
    print("-" * 50)
conn.close()
