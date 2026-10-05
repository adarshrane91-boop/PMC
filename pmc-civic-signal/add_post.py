"""Submit one public PMC Instagram/Facebook post to the running PMC Civic Signal server.

Usage (server must be running on port 8000):
    python add_post.py <post_url> <caption_file.txt> [ward hint]

Example:
    python add_post.py https://www.instagram.com/p/XXXXXXXXXXX/ caption.txt "Kasba Peth"

caption_file.txt = the caption copied from the post (Marathi is fine, save as UTF-8).
The server saves it, runs ingestion (Sarvam reads the caption), and tells you
whether it landed on the map and under which ward/category.
"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000"

sys.stdout.reconfigure(encoding="utf-8")  # Devanagari on Windows consoles


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, caption_file = sys.argv[1], sys.argv[2]
    ward = sys.argv[3] if len(sys.argv) > 3 else None
    text = open(caption_file, encoding="utf-8").read().strip()

    status = call("GET", "/api/source-status")
    if not status.get("sarvam_ai", {}).get("configured"):
        print("WARNING: SARVAM_API_KEY is not set - Sarvam will NOT read the caption.")
        print("         Put it in a file named .env next to classify.py, then restart the server.\n")

    post = {"url": url, "text": text}
    if ward:
        post["ward"] = ward
    res = call("POST", "/api/posts", [post])

    print(f"saved: {res['saved']}   placed_on_map: {res['placed_on_map']}")
    if res["rejected_not_facebook_or_instagram_url"]:
        print("REJECTED (not an instagram.com / facebook.com URL):", res["rejected_not_facebook_or_instagram_url"])
    if res["not_placed"]:
        print("NOT PLACED on a ward (shown as PMC-wide news only):", res["not_placed"])
    for r in res["reports"]:
        if r.get("ward_id") is None:
            print("\n*** NOT ON A WARD: this post is PMC-wide only (no marker). Reasons to check:")
            print("    - Sarvam not configured / returned no locality (see warning above)")
            print("    - pass a ward hint:  python add_post.py <url> caption.txt \"Kasba Peth\"")
        print(json.dumps(
            {k: r.get(k) for k in ("ward_id", "ward_name", "location_precision", "location_source",
                                   "category", "language", "location_hint", "summary_en",
                                   "places", "geocoded_place", "matched_terms", "lat", "lon")},
            ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()