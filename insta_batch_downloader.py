#!/usr/bin/env python3
"""
Batch Instagram video downloader (10+ links at a time).

Setup:
    pip install yt-dlp
    (install ffmpeg too, so video + audio get merged properly)

Usage:
    1. Put one Instagram link per line in links.txt
    2. Run:  python insta_batch_downloader.py
       or:   python insta_batch_downloader.py -f links.txt -w 10 -o downloads
       or:   python insta_batch_downloader.py "https://www.instagram.com/reel/XXXX/" "https://..."

If Instagram asks for login (common now), pass your browser cookies:
    python insta_batch_downloader.py --cookies-from-browser chrome
    or use a cookies.txt file:  --cookies cookies.txt

Only download content you own or have permission to save.
"""

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yt_dlp
except ImportError:
    sys.exit("yt-dlp is not installed. Run:  pip install yt-dlp")

print_lock = threading.Lock()


def log(msg):
    with print_lock:
        print(msg, flush=True)


def read_links(args):
    links = list(args.urls)
    if os.path.exists(args.file):
        with open(args.file, "r", encoding="utf-8") as f:
            links += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    # remove duplicates, keep order
    seen, unique = set(), []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)
    return unique


def download_one(url, args):
    opts = {
        "outtmpl": os.path.join(args.output, "%(uploader)s_%(id)s.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "ignoreerrors": False,
    }
    if args.cookies:
        opts["cookiefile"] = args.cookies
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = (args.cookies_from_browser,)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("id", url) if info else url
        log(f"[OK]     {url}  ->  {title}")
        return url, True, None
    except Exception as e:
        log(f"[FAILED] {url}  ({str(e).splitlines()[-1][:120]})")
        return url, False, str(e)


def main():
    p = argparse.ArgumentParser(description="Batch Instagram video downloader")
    p.add_argument("urls", nargs="*", help="Instagram links (optional if using links.txt)")
    p.add_argument("-f", "--file", default="links.txt", help="Text file with one link per line")
    p.add_argument("-o", "--output", default="downloads", help="Output folder")
    p.add_argument("-w", "--workers", type=int, default=10, help="Parallel downloads (default 10)")
    p.add_argument("--cookies", help="Path to cookies.txt (Netscape format)")
    p.add_argument("--cookies-from-browser", help="chrome, firefox, edge, brave, safari...")
    args = p.parse_args()

    links = read_links(args)
    if not links:
        sys.exit("No links found. Add them to links.txt (one per line) or pass them as arguments.")

    os.makedirs(args.output, exist_ok=True)
    log(f"Downloading {len(links)} link(s) with {args.workers} parallel workers...\n")

    ok, failed = 0, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_one, url, args) for url in links]
        for fut in as_completed(futures):
            url, success, _ = fut.result()
            if success:
                ok += 1
            else:
                failed.append(url)

    log(f"\nDone. {ok} succeeded, {len(failed)} failed. Files saved in: {os.path.abspath(args.output)}")
    if failed:
        with open("failed_links.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(failed))
        log("Failed links saved to failed_links.txt (retry them, usually needs --cookies-from-browser).")


if __name__ == "__main__":
    main()
