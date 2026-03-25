import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file
from yt_dlp import YoutubeDL

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_FILE = BASE_DIR / "cookies.txt"

jobs = {}
jobs_lock = threading.Lock()


def _get_base_ydl_opts(skip_download=False):
    """Build base yt-dlp options with bot detection bypasses and cookie support."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": skip_download,
        # Bot detection bypasses
        "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        "sleep_interval": 2,
        "max_sleep_interval": 5,
    }
    
    # Add cookies file if it exists
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    
    return opts


def _format_bytes(value):
    if value is None:
        return None
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < step:
            return f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} PB"


def _format_seconds(value):
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    minutes, seconds = divmod(max(value, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _video_summary(info):
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url"),
    }


def _extract_info(url):
    ydl_opts = _get_base_ydl_opts(skip_download=True)
    ydl_opts.update({
        "noplaylist": False,
        "extract_flat": False,
    })
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _extract_qualities(info):
    heights = set()
    formats = info.get("formats") or []
    for fmt in formats:
        height = fmt.get("height")
        if isinstance(height, int):
            heights.add(height)
    return sorted(heights, reverse=True)


def _safe_update_job(job_id, **updates):
    with jobs_lock:
        if job_id not in jobs:
            return
        jobs[job_id].update(updates)


def _download_worker(job_id, url, quality):
    def progress_hook(progress):
        status = progress.get("status")
        payload = {
            "state": status,
            "downloaded_bytes": progress.get("downloaded_bytes"),
            "total_bytes": progress.get("total_bytes") or progress.get("total_bytes_estimate"),
            "speed": progress.get("speed"),
            "eta": progress.get("eta"),
            "filename": progress.get("filename"),
        }

        downloaded = payload["downloaded_bytes"] or 0
        total = payload["total_bytes"] or 0
        pct = 0.0
        if total > 0:
            pct = (downloaded / total) * 100.0

        _safe_update_job(
            job_id,
            status="downloading" if status == "downloading" else status,
            percent=round(pct, 2),
            downloaded_readable=_format_bytes(downloaded),
            total_readable=_format_bytes(total),
            speed_readable=_format_bytes(payload["speed"]),
            eta_readable=_format_seconds(payload["eta"]),
            last_event={
                "status": status,
                "percent": round(pct, 2),
                "downloaded": _format_bytes(downloaded),
                "total": _format_bytes(total),
                "speed": _format_bytes(payload["speed"]),
                "eta": _format_seconds(payload["eta"]),
            },
        )

        if status == "finished":
            _safe_update_job(job_id, status="processing", temp_path=payload.get("filename"))

    if quality == "best":
        format_selector = "bestvideo+bestaudio/best"
    else:
        try:
            height = int(quality)
        except (TypeError, ValueError):
            height = 720
        format_selector = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"

    outtmpl = str(DOWNLOAD_DIR / "%(title).180B-%(id)s.%(ext)s")

    ydl_opts = _get_base_ydl_opts(skip_download=False)
    ydl_opts.update({
        "format": format_selector,
        "outtmpl": outtmpl,
        "noplaylist": False,
        "progress_hooks": [progress_hook],
        "merge_output_format": "mp4",
    })

    try:
        _safe_update_job(job_id, status="downloading", started_at=datetime.utcnow().isoformat() + "Z")
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        final_path = None
        with jobs_lock:
            candidate = jobs.get(job_id, {}).get("temp_path")
        if candidate:
            candidate_path = Path(candidate)
            if candidate_path.exists():
                final_path = candidate_path

        if final_path is None:
            recent = sorted(
                [p for p in DOWNLOAD_DIR.iterdir() if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if recent:
                final_path = recent[0]

        if final_path is None:
            raise RuntimeError("Download finished but file path could not be resolved")

        _safe_update_job(
            job_id,
            status="done",
            percent=100.0,
            file_path=str(final_path),
            file_name=final_path.name,
            completed_at=datetime.utcnow().isoformat() + "Z",
        )
    except Exception as exc:
        _safe_update_job(job_id, status="error", error=str(exc), completed_at=datetime.utcnow().isoformat() + "Z")


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/info")
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400

    try:
        info = _extract_info(url)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        first = entries[0] if entries else {}
        qualities = _extract_qualities(first)
        return jsonify(
            {
                "type": "playlist",
                "title": info.get("title"),
                "uploader": info.get("uploader"),
                "thumbnail": first.get("thumbnail"),
                "count": len(entries),
                "qualities": qualities,
                "entries": [_video_summary(e) for e in entries],
            }
        )

    qualities = _extract_qualities(info)
    return jsonify(
        {
            "type": "video",
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url"),
            "qualities": qualities,
        }
    )


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip().lower()

    if not url:
        return jsonify({"error": "Missing url"}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "percent": 0.0,
            "quality": quality,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "error": None,
            "file_path": None,
            "file_name": None,
            "last_event": {},
        }

    thread = threading.Thread(target=_download_worker, args=(job_id, url, quality), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "queued"})


@app.post("/api/download-direct")
def api_download_direct():
    """Download video and immediately serve file as browser download (no progress tracking)."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip().lower()

    if not url:
        return jsonify({"error": "Missing url"}), 400

    if quality == "best":
        format_selector = "bestvideo+bestaudio/best"
    else:
        try:
            height = int(quality)
        except (TypeError, ValueError):
            height = 720
        format_selector = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"

    outtmpl = str(DOWNLOAD_DIR / "%(title).180B-%(id)s.%(ext)s")

    ydl_opts = _get_base_ydl_opts(skip_download=False)
    ydl_opts.update({
        "format": format_selector,
        "outtmpl": outtmpl,
        "noplaylist": False,
        "merge_output_format": "mp4",
    })

    try:
        final_path = None
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        recent = sorted(
            [p for p in DOWNLOAD_DIR.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if recent:
            final_path = recent[0]

        if final_path is None or not final_path.exists():
            return jsonify({"error": "Download completed but file could not be found"}), 500

        # Serve the file directly to browser for download
        return send_file(final_path, as_attachment=True)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/progress/<job_id>")
def api_progress(job_id):
    def stream():
        while True:
            with jobs_lock:
                job = dict(jobs.get(job_id, {}))

            if not job:
                payload = {"status": "error", "error": "Job not found"}
                yield f"data: {json.dumps(payload)}\n\n"
                break

            payload = {
                "job_id": job_id,
                "status": job.get("status"),
                "percent": job.get("percent", 0.0),
                "speed": job.get("speed_readable"),
                "eta": job.get("eta_readable"),
                "downloaded": job.get("downloaded_readable"),
                "total": job.get("total_readable"),
                "file_name": job.get("file_name"),
                "error": job.get("error"),
            }
            yield f"data: {json.dumps(payload)}\n\n"

            if job.get("status") in {"done", "error"}:
                break

            time.sleep(0.7)

    return Response(stream(), mimetype="text/event-stream")


@app.get("/api/file/<job_id>")
def api_file(job_id):
    with jobs_lock:
        job = dict(jobs.get(job_id, {}))

    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") != "done":
        return jsonify({"error": "File not ready"}), 409

    file_path = job.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "Downloaded file not found"}), 404

    return send_file(file_path, as_attachment=True)


@app.get("/api/downloads")
def api_downloads():
    files = []
    for p in sorted(DOWNLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file():
            continue
        stat = p.stat()
        files.append(
            {
                "name": p.name,
                "size": stat.st_size,
                "size_readable": _format_bytes(stat.st_size),
                "modified": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
            }
        )

    return jsonify({"files": files})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
