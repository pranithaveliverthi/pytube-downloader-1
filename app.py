from flask import Flask, render_template, request, send_from_directory
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import yt_dlp
import logging
import os

app = Flask(__name__)

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_FOLDER = BASE_DIR / "downloads"
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["DOWNLOAD_FOLDER"] = DOWNLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # Optional request size limit

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def sanitize_youtube_url(url: str) -> str:
    """
    Normalize supported YouTube URLs and strip unnecessary tracking parameters.
    Keeps only the video ID when possible.
    """
    if not url:
        raise ValueError("URL is required.")

    url = url.strip()
    parsed = urlparse(url)

    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urlparse(url)

    hostname = (parsed.hostname or "").lower()

    if hostname in {"youtu.be"}:
        video_id = parsed.path.lstrip("/")
        if not video_id:
            raise ValueError("Invalid YouTube short URL.")
        return f"https://www.youtube.com/watch?v={video_id}"

    if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            if not video_id:
                raise ValueError("Invalid YouTube watch URL.")
            return f"https://www.youtube.com/watch?v={video_id}"

        if parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/shorts/")[-1].split("/")[0]
            if not video_id:
                raise ValueError("Invalid YouTube shorts URL.")
            return f"https://www.youtube.com/watch?v={video_id}"

    raise ValueError("Please provide a valid YouTube URL.")


def human_size(size_bytes):
    """Convert bytes to a readable string."""
    if not isinstance(size_bytes, (int, float)) or size_bytes <= 0:
        return "Unknown"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024


def get_video_info(url: str):
    """Fetch video metadata without downloading."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def build_format_options(info_dict: dict):
    """Extract user-friendly format options."""
    formats = info_dict.get("formats", [])
    options = []

    seen = set()
    for fmt in formats:
        format_id = fmt.get("format_id")
        ext = fmt.get("ext")
        resolution = fmt.get("resolution") or fmt.get("format_note") or "Unknown"
        filesize = fmt.get("filesize") or fmt.get("filesize_approx")
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")

        # Skip unusable entries
        if not format_id:
            continue

        # Optional: skip audio-only formats for this UI
        if vcodec == "none":
            continue

        label = f"{resolution} | {ext} | {human_size(filesize)}"

        # Avoid duplicates in the dropdown
        key = (format_id, label)
        if key in seen:
            continue
        seen.add(key)

        options.append({
            "format_id": format_id,
            "ext": ext,
            "resolution": resolution,
            "size": human_size(filesize),
            "label": label,
        })

    return options


def download_selected_format(url: str, format_id: str):
    """
    Download the selected format and return the actual downloaded filename.
    """
    ydl_opts = {
        "format": format_id,
        "outtmpl": str(app.config["DOWNLOAD_FOLDER"] / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    return Path(filename).name, info


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/get_formats", methods=["POST"])
def get_formats():
    url = request.form.get("url", "").strip()

    if not url:
        return render_template("index.html", error="Please enter a YouTube URL.")

    try:
        clean_url = sanitize_youtube_url(url)
        logger.info("Fetching formats for URL: %s", clean_url)

        info_dict = get_video_info(clean_url)
        format_options = build_format_options(info_dict)

        if not format_options:
            return render_template(
                "index.html",
                error="No downloadable video formats were found.",
                url=clean_url
            )

        return render_template(
            "index.html",
            formats=format_options,
            video_title=info_dict.get("title", "Unknown Title"),
            url=clean_url
        )

    except Exception as exc:
        logger.exception("Error fetching formats")
        return render_template("index.html", error=f"Error fetching formats: {exc}")


@app.route("/download", methods=["POST"])
def download_video():
    url = request.form.get("url", "").strip()
    format_id = request.form.get("format_id", "").strip()

    if not url:
        return render_template("index.html", error="Missing YouTube URL.")

    if not format_id:
        return render_template("index.html", error="Please select a format before downloading.")

    try:
        clean_url = sanitize_youtube_url(url)
        logger.info("Downloading video from URL: %s with format: %s", clean_url, format_id)

        filename, info_dict = download_selected_format(clean_url, format_id)
        video_title = info_dict.get("title", "Unknown Title")

        logger.info("Downloaded successfully: %s", filename)

        return render_template(
            "index.html",
            success=True,
            video_title=video_title,
            filename=filename,
            url=clean_url
        )

    except Exception as exc:
        logger.exception("Error downloading video")
        return render_template("index.html", error=f"Error downloading video: {exc}")


@app.route("/download_file/<path:filename>")
def download_file(filename):
    try:
        logger.info("Sending file for download: %s", filename)
        return send_from_directory(
            directory=app.config["DOWNLOAD_FOLDER"],
            path=filename,
            as_attachment=True
        )
    except Exception:
        logger.exception("Error sending file")
        return render_template(
            "index.html",
            error="File not found or could not be downloaded."
        )


if __name__ == "__main__":
    app.run(
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 2000)),
    )
