#!/usr/bin/env python3
"""
InstaBatch Downloader — Flask Backend
Supports: password protection, rate limiting, security headers,
          auto-cleanup, ffmpeg auto-detection, quality selection.

Local:      python app.py
Production: gunicorn wsgi:app  (or waitress-serve --port=5050 wsgi:app)
"""

from __future__ import annotations

import os
import sys
import uuid
import time
import shutil
import urllib.parse
import threading
import subprocess
import secrets
import hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

# ── Load .env file ───────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional — fall back to real env vars

# ── Dependency guard ─────────────────────────────────────────────────────────
missing = []
try:
    import yt_dlp
except ImportError:
    missing.append("yt-dlp")

try:
    from flask import (Flask, request, jsonify, render_template,
                       send_from_directory, session, redirect, url_for, abort)
except ImportError:
    missing.append("flask")

if missing:
    print("\n[ERROR] Missing packages: " + ", ".join(missing))
    print("Run:  pip install " + " ".join(missing))
    sys.exit(1)

# ── Configuration ────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR  = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

SECRET_KEY     = os.environ.get("SECRET_KEY", secrets.token_hex(32))
APP_PASSWORD   = os.environ.get("APP_PASSWORD", "").strip()      # blank = no login required
COOKIES_FILE   = os.environ.get("COOKIES_FILE", "").strip() or None
PORT           = int(os.environ.get("PORT", 5050))
RATE_LIMIT     = int(os.environ.get("RATE_LIMIT", 30))           # downloads per hour per IP
MAX_WORKERS    = int(os.environ.get("MAX_WORKERS", 10))
CLEANUP_HOURS  = int(os.environ.get("CLEANUP_HOURS", 24))
IS_PRODUCTION  = os.environ.get("FLASK_ENV", "development") == "production"

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key           = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"]  = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = IS_PRODUCTION  # HTTPS only in prod
app.config["JSON_SORT_KEYS"]          = False

# ── Security headers ─────────────────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    h = response.headers
    h["X-Content-Type-Options"]    = "nosniff"
    h["X-Frame-Options"]           = "DENY"
    h["X-XSS-Protection"]          = "1; mode=block"
    h["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    h["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
    if IS_PRODUCTION:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Content-Security-Policy: allow inline styles/scripts (needed for single-page app)
    h["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    # CORS: only allow same-origin in production
    if not IS_PRODUCTION:
        h["Access-Control-Allow-Origin"]  = "*"
        h["Access-Control-Allow-Headers"] = "Content-Type"
        h["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return "", 204

# ── Rate limiter (in-memory per-IP) ─────────────────────────────────────────
_rate_store: dict[str, list] = defaultdict(list)
_rate_lock = threading.Lock()

def is_rate_limited(ip: str, limit: int = RATE_LIMIT, window: int = 3600) -> bool:
    now = time.time()
    with _rate_lock:
        timestamps = _rate_store[ip]
        # Remove timestamps older than the window
        _rate_store[ip] = [t for t in timestamps if now - t < window]
        if len(_rate_store[ip]) >= limit:
            return True
        _rate_store[ip].append(now)
    return False

# ── Login / auth helpers ─────────────────────────────────────────────────────
def auth_required(f):
    """Decorator: redirect to /login if APP_PASSWORD is set and user not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if APP_PASSWORD and not session.get("authenticated"):
            if request.is_json:
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

def check_password(candidate: str) -> bool:
    # Constant-time comparison to prevent timing attacks
    return secrets.compare_digest(
        hashlib.sha256(candidate.encode()).hexdigest(),
        hashlib.sha256(APP_PASSWORD.encode()).hexdigest()
    )

# ── In-memory job store ──────────────────────────────────────────────────────
jobs: dict   = {}
jobs_lock    = threading.Lock()

def new_job(urls: list, workers: int, cfb: Optional[str]) -> str:
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status":               "running",
            "logs":                 [],
            "ok":                   0,
            "failed":               [],
            "total":                len(urls),
            "done":                 0,
            "workers":              workers,
            "cookies_from_browser": cfb,
            "started_at":           datetime.utcnow().isoformat() + "Z",
            "finished_at":          None,
        }
    return job_id

def job_log(job_id: str, msg: str):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["logs"].append(msg)

# ── ffmpeg helper ────────────────────────────────────────────────────────────
def get_ffmpeg_path() -> Optional[str]:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")

# ── URL cleaning ─────────────────────────────────────────────────────────────
def clean_url(url: str) -> str:
    url = url.strip()
    try:
        parsed = urllib.parse.urlparse(url)
        if "instagram.com" in parsed.netloc:
            path = parsed.path.rstrip("/") + "/"
            return f"https://www.instagram.com{path}"
    except Exception:
        pass
    return url

# ── Quality format strings ────────────────────────────────────────────────────
QUALITY_FORMATS = {
    "best":  "bestvideo+bestaudio/bestvideo*+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/bestvideo[height<=1080]/best[height<=1080]/best",
    "720p":  "bestvideo[height<=720]+bestaudio/bestvideo[height<=720]/best[height<=720]/best",
    "480p":  "bestvideo[height<=480]+bestaudio/bestvideo[height<=480]/best[height<=480]/best",
    "360p":  "bestvideo[height<=360]+bestaudio/bestvideo[height<=360]/best[height<=360]/best",
}

# ── Download logic ───────────────────────────────────────────────────────────
def download_one(url: str, job_id: str, output_dir: str,
                 cfb: Optional[str] = None,
                 cookies_file: Optional[str] = None,
                 quality: str = "best"):

    ffmpeg_exe = get_ffmpeg_path()

    opts = {
        "outtmpl":             os.path.join(output_dir, "%(uploader)s_%(id)s.%(ext)s"),
        "quiet":               True,
        "no_warnings":         True,
        "noprogress":          True,
        "retries":             5,
        "fragment_retries":    5,
        "ignoreerrors":        False,
        "prefer_free_formats": False,
        "writethumbnail":      False,
        "writeinfojson":       False,
    }

    if ffmpeg_exe:
        fmt = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])
        opts["format"]              = fmt
        opts["ffmpeg_location"]     = ffmpeg_exe
        opts["merge_output_format"] = "mp4"
    else:
        h = quality.replace("p", "") if quality.endswith("p") else None
        opts["format"] = f"b[height<={h}]/best[height<={h}]/b/best" if h else "b/best"

    # Cookies: prefer explicit cookies.txt file, fall back to browser cookies
    effective_cookies_file = cookies_file or COOKIES_FILE
    if effective_cookies_file and os.path.isfile(effective_cookies_file):
        opts["cookiefile"] = effective_cookies_file
    elif cfb:
        opts["cookiesfrombrowser"] = (cfb,)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info:
                title   = info.get("title") or info.get("id") or url
                height  = info.get("height") or "?"
                vcodec  = (info.get("vcodec") or "").split(".")[0] or "?"
                acodec  = (info.get("acodec") or "").split(".")[0] or "?"
                size    = info.get("filesize") or info.get("filesize_approx") or 0
                size_mb = f"{size / 1024 / 1024:.1f} MB" if size else "?"
                job_log(job_id, f"[OK]  {title}")
                job_log(job_id, f"      {height}p  |  {vcodec}+{acodec}  |  {size_mb}")
            else:
                job_log(job_id, f"[OK]  {url}")
        return url, True, None
    except yt_dlp.utils.DownloadError as exc:
        short = str(exc).splitlines()[-1][:180]
        job_log(job_id, f"[FAIL] {url}\n       {short}")
        return url, False, str(exc)
    except Exception as exc:
        short = str(exc).splitlines()[-1][:180]
        job_log(job_id, f"[FAIL] {url}\n       {short}")
        return url, False, str(exc)


def run_batch(job_id: str, urls: list, workers: int,
              cfb: Optional[str], quality: str = "best"):
    job_log(job_id, f"[INFO] {len(urls)} URL(s)  •  {workers} workers  •  Quality: {quality}")
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(download_one, url, job_id, DOWNLOADS_DIR, cfb, None, quality): url
                for url in urls
            }
            for fut in as_completed(futures):
                url, success, _ = fut.result()
                with jobs_lock:
                    if success:
                        jobs[job_id]["ok"] += 1
                    else:
                        jobs[job_id]["failed"].append(url)
                    jobs[job_id]["done"] += 1

        with jobs_lock:
            ok, fail = jobs[job_id]["ok"], len(jobs[job_id]["failed"])
        job_log(job_id, f"[INFO] Done — {ok} succeeded, {fail} failed → {DOWNLOADS_DIR}")
        with jobs_lock:
            jobs[job_id]["status"]      = "done"
            jobs[job_id]["finished_at"] = datetime.utcnow().isoformat() + "Z"
    except Exception as exc:
        job_log(job_id, f"[ERROR] Batch crashed: {exc}")
        with jobs_lock:
            jobs[job_id]["status"]      = "error"
            jobs[job_id]["finished_at"] = datetime.utcnow().isoformat() + "Z"

# ── Auto-cleanup thread ──────────────────────────────────────────────────────
def _cleanup_loop():
    while True:
        time.sleep(3600)  # check every hour
        if CLEANUP_HOURS > 0:
            cutoff = datetime.utcnow() - timedelta(hours=CLEANUP_HOURS)
            try:
                for fname in os.listdir(DOWNLOADS_DIR):
                    fpath = os.path.join(DOWNLOADS_DIR, fname)
                    if os.path.isfile(fpath):
                        mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
                        if mtime < cutoff:
                            os.remove(fpath)
            except Exception:
                pass

if CLEANUP_HOURS > 0:
    threading.Thread(target=_cleanup_loop, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════

# ── Login ────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login_page():
    if not APP_PASSWORD:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if check_password(pw):
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Incorrect password. Try again."

    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ── Main page ────────────────────────────────────────────────────────────────
@app.route("/")
@auth_required
def index():
    return render_template("index.html")

# ── Download API ─────────────────────────────────────────────────────────────
@app.route("/api/download", methods=["POST"])
@auth_required
def api_download():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if is_rate_limited(ip):
        return jsonify({"error": f"Rate limit exceeded — max {RATE_LIMIT} downloads/hour"}), 429

    data     = request.get_json(silent=True) or {}
    raw_urls = data.get("urls", [])
    if not raw_urls or not isinstance(raw_urls, list):
        return jsonify({"error": "Provide a non-empty 'urls' list"}), 400

    seen, urls = set(), []
    for u in raw_urls:
        cleaned = clean_url(str(u))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)

    if not urls:
        return jsonify({"error": "No valid URLs"}), 400

    workers = max(1, min(int(data.get("workers", 5)), MAX_WORKERS))
    cfb     = data.get("cookies_from_browser") or None
    if cfb and cfb.lower() in ("none", ""):
        cfb = None

    quality = data.get("quality", "best")
    if quality not in QUALITY_FORMATS:
        quality = "best"

    job_id = new_job(urls, workers, cfb)
    threading.Thread(target=run_batch, args=(job_id, urls, workers, cfb, quality), daemon=True).start()
    return jsonify({"job_id": job_id}), 202

# ── Job status ───────────────────────────────────────────────────────────────
@app.route("/api/status/<job_id>")
@auth_required
def api_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    snap = dict(job)
    snap["logs"]   = list(snap["logs"])
    snap["failed"] = list(snap["failed"])
    # Never expose internal file paths
    snap.pop("output_dir", None)
    return jsonify(snap)

@app.route("/api/jobs")
@auth_required
def api_jobs():
    with jobs_lock:
        result = [
            {"job_id": jid, "status": j["status"], "total": j["total"],
             "done": j["done"], "ok": j["ok"], "failed": len(j["failed"]),
             "started_at": j.get("started_at")}
            for jid, j in jobs.items()
        ]
    return jsonify(result)

@app.route("/api/jobs/<job_id>", methods=["DELETE"])
@auth_required
def api_delete_job(job_id):
    with jobs_lock:
        removed = jobs.pop(job_id, None)
    if removed is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"deleted": job_id})

# ── Files API ────────────────────────────────────────────────────────────────
@app.route("/api/files")
@auth_required
def api_files():
    entries = []
    try:
        for fname in sorted(os.listdir(DOWNLOADS_DIR), reverse=True):
            fpath = os.path.join(DOWNLOADS_DIR, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                entries.append({
                    "filename": fname,
                    "url":      f"/downloads/{fname}",
                    "size_mb":  round(stat.st_size / 1024 / 1024, 2),
                    "modified": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(entries)

@app.route("/downloads/<path:filename>")
@auth_required
def serve_download(filename):
    # Prevent path traversal attacks
    safe = os.path.basename(filename)
    return send_from_directory(DOWNLOADS_DIR, safe, as_attachment=False)

# ── Utility routes ────────────────────────────────────────────────────────────
@app.route("/api/open-folder", methods=["POST"])
@auth_required
def api_open_folder():
    try:
        subprocess.Popen(["explorer", DOWNLOADS_DIR])
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/check")
@auth_required
def api_check():
    ffmpeg = get_ffmpeg_path()
    return jsonify({
        "status":    "ok",
        "yt_dlp":   yt_dlp.version.__version__,
        "ffmpeg":   ffmpeg is not None,
        # Never expose full server file paths to the client
    })

# ── 404 / 403 handlers ───────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(_):
    if request.is_json:
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404

@app.errorhandler(403)
def forbidden(_):
    if request.is_json:
        return jsonify({"error": "Forbidden"}), 403
    return render_template("404.html"), 403

@app.errorhandler(429)
def too_many(_):
    return jsonify({"error": "Too many requests"}), 429

# ── Blocked paths (extra safety) ──────────────────────────────────────────────
BLOCKED = {".env", "cookies.txt", ".git", "venv", "__pycache__", "wsgi.py"}

@app.before_request
def block_sensitive_paths():
    path = request.path.lower().lstrip("/")
    for blocked in BLOCKED:
        if path == blocked or path.startswith(blocked + "/"):
            abort(404)

# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  InstaBatch Downloader")
    print(f"  Password protection : {'ON' if APP_PASSWORD else 'OFF (set APP_PASSWORD to enable)'}")
    print(f"  Rate limit          : {RATE_LIMIT} downloads/hour/IP")
    print(f"  ffmpeg              : {get_ffmpeg_path() or 'not found'}")
    print(f"  Downloads folder    : {DOWNLOADS_DIR}")
    print(f"  Open browser at     : http://localhost:{PORT}")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=PORT, debug=not IS_PRODUCTION, threaded=True)
