import os, subprocess, datetime, tempfile
from typing import Callable, Optional
from django.core.files import File
from yt_dlp import YoutubeDL

ProgressCB = Optional[Callable[[str, int, str], None]]  # (step, percent, message)

# Step 1: extract metadata (no download)
def ytdlp_extract_metadata(url: str):
    ydl_opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    # Some fields may be missing depending on the video
    return {
        "youtube_id": info.get("id"),
        "title": info.get("title"),
        "channel_title": info.get("channel") or info.get("uploader"),
        "published_at": info.get("upload_date"),  # 'YYYYMMDD' or None
        "duration_sec": float(info.get("duration") or 0.0),
        "ext": info.get("ext"),
    }

# Step 2: download best audio as mp3 into a temp path we control
def ytdlp_download_audio_mp3(url: str, out_path: str, progress_hook: Optional[Callable]=None) -> str:
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_path,  # e.g., "/tmp/<youtube_id>.%(ext)s"
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook] if progress_hook else [],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    # After postprocess, the file will be out_path with .mp3 extension replaced
    return os.path.splitext(out_path)[0] + ".mp3"

# Step 3: convert to 16 kHz mono WAV for Whisper
def ffmpeg_to_wav_16k_mono(src_path: str, dst_path: str):
    # ffmpeg -y -i input.mp3 -ac 1 -ar 16000 -f wav output.wav
    cmd = ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000", "-f", "wav", dst_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# High-level orchestration for A + B
def prepare_job_files(job, on_progress: ProgressCB = None, tmp_dir: Optional[str] = None):
    """
    - Extract metadata, store DB
    - Download MP3 (yt-dlp) with optional progress callback
    - Convert to 16k mono WAV (ffmpeg)
    - Save FileFields to storage (GCS via django-storages)
    """
    # A) Metadata
    meta = ytdlp_extract_metadata(job.youtube_url)
    job.youtube_id = meta["youtube_id"] or job.youtube_id
    job.title = meta["title"] or ""
    job.channel_title = meta["channel_title"] or ""
    if meta["published_at"]:
        # 'YYYYMMDD' → datetime
        yy = int(meta["published_at"][0:4])
        mm = int(meta["published_at"][4:6])
        dd = int(meta["published_at"][6:8])
        job.published_at = datetime.datetime(yy, mm, dd, tzinfo=datetime.timezone.utc)
    job.duration_sec = meta["duration_sec"] or None
    #job.status = "downloading"
    job.save()


    # B) Work in a temp dir unless provided

    # def notify(step, percent, message=""):
    #     if on_progress:
    #         on_progress(step, int(max(0, min(100, percent))), message)

    # yt-dlp → on_progress adapter

    cleanup = False
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp(prefix="prep_")
        cleanup = True

    try:
        # progress helper
        def emit(step, percent, message):
            if on_progress:
                on_progress(step, int(max(0, min(100, percent))), message or "")

        last = {"overall": -1}
        def yt_hook(d):
            st = d.get("status")
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done  = d.get("downloaded_bytes") or 0
                pct = int(done * 100 / total) if total else 1         # 0..100 (download)
                overall = max(1, min(40, int(pct * 0.40)))            # map to 0..40 for global progress
                if overall != last["overall"]:
                    last["overall"] = overall                          # debounce DB writes
                    emit("downloading", overall, f"Downloading… {pct}%")
            elif st == "finished":
                emit("downloading", 40, "Download finished.")
        
        emit("downloading", 1, "Starting download…")
        tmp_mp3_tpl = os.path.join(tmp_dir, f"{job.job_uuid}_source.%(ext)s")
        mp3_path = ytdlp_download_audio_mp3(job.youtube_url, tmp_mp3_tpl, progress_hook=yt_hook)
        #emit("downloading", 40, "Download finished.")

        # Convert
        emit("converting", 50, "Converting to 16k WAV…")
        wav_tmp = os.path.join(tmp_dir, f"{job.job_uuid}_audio_16k.wav")
        ffmpeg_to_wav_16k_mono(mp3_path, wav_tmp)
        emit("converting", 58, "Uploading artifacts…")

        # Save to storage
        with open(mp3_path, "rb") as f:
            job.source_audio.save("source.mp3", File(f), save=False)
        with open(wav_tmp, "rb") as f:
            job.wav_audio.save("audio_16k.wav", File(f), save=False)
        job.save()

        emit("awaiting_transcription", 65, "Waiting for GPU worker…")

    finally:
        if cleanup:
            # optional: shutil.rmtree(tmp_dir, ignore_errors=True)
            pass

    # # B) Artifact filenames (under the job’s own directory in MEDIA_ROOT)
    # # We'll generate local temp outputs, then attach them to FileFields so Django puts them under MEDIA_ROOT using upload_to=job_dir
    # tmp_mp3 = os.path.join(media_dir, f"{job.job_uuid}_source.%(ext)s")
    # mp3_path = ytdlp_download_audio_mp3(job.youtube_url, tmp_mp3)

    # # Attach source audio
    # with open(mp3_path, "rb") as f:
    #     job.source_audio.save("source.mp3", File(f), save=False)
    # job.status = "converting"
    # job.save()

    # # Convert to WAV 16k mono
    # wav_tmp = os.path.join(media_dir, f"{job.job_uuid}_audio_16k.wav")
    # ffmpeg_to_wav_16k_mono(mp3_path, wav_tmp)

    # with open(wav_tmp, "rb") as f:
    #     job.wav_audio.save("audio_16k.wav", File(f), save=False)

    # # wait for transcription
    # job.status = "awaiting_transcription"
    # job.save()