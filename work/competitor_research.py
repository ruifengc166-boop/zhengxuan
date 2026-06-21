# Research script to study competitor AI video platforms
# This will fetch public information about these platforms

import urllib.request
import json
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

targets = [
    "https://libtv.com",
    "https://anishort.com", 
    "https://www.tapnow.com",
    "https://flova.com",
]

for url in targets:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            # Extract title and meta description
            title = ""
            desc = ""
            for line in html.split("\n"):
                if "<title" in line.lower():
                    import re
                    m = re.search(r"<title[^>]*>(.*?)</title>", line, re.IGNORECASE)
                    if m: title = m.group(1)
                if 'name="description"' in line.lower() or 'name="Description"' in line:
                    m = re.search(r'content="([^"]*)"', line)
                    if m: desc = m.group(1)[:200]
            print(f"\n=== {url} ===")
            print(f"Title: {title}")
            print(f"Desc: {desc[:300] if desc else "N/A"}")
            print(f"Size: {len(html)} bytes")
    except Exception as e:
        print(f"\n=== {url} === ERROR: {e}")
