import sqlite3
import pprint
conn = sqlite3.connect(r'C:\Users\cerqu\Documents\SynaBunGlobal\data\memory.db')
cursor = conn.cursor()
cursor.execute("SELECT id, content, project, tags, importance, created_at FROM memories ORDER BY created_at DESC LIMIT 5")
rows = cursor.fetchall()
for row in rows:
    print(f"ID: {row[0]}")
    print(f"Project: {row[2]}")
    print(f"Tags: {row[3]}")
    print(f"Importance: {row[4]}")
    print(f"Created: {row[5]}")
    print(f"Content: {row[1]}")
    print("-" * 50)
conn.close()
