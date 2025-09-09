import os, subprocess, datetime
from django.core.files import File
from yt_dlp import YoutubeDL

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
def ytdlp_download_audio_mp3(url: str, out_path: str):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_path,  # e.g., "/tmp/<youtube_id>.%(ext)s"
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
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
def prepare_job_files(job, media_dir: str):
    """
    - Extract metadata, store into DB (A)
    - Download audio (mp3), convert wav16k, store FileFields (B)
    """
    # A) Metadata
    meta = ytdlp_extract_metadata(job.youtube_url)
    job.youtube_id = meta["youtube_id"] or job.youtube_id
    job.title = meta["title"] or ""
    job.channel_title = meta["channel_title"] or ""
    if meta["published_at"]:
        # 'YYYYMMDD' → datetime
        y = int(meta["published_at"][0:4]); m = int(meta["published_at"][4:6]); d = int(meta["published_at"][6:8])
        job.published_at = datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)
    job.duration_sec = meta["duration_sec"] or None
    job.status = "downloading"
    job.save()

    # B) Artifact filenames (under the job’s own directory in MEDIA_ROOT)
    # We'll generate local temp outputs, then attach them to FileFields so Django puts them under MEDIA_ROOT using upload_to=job_dir
    tmp_mp3 = os.path.join(media_dir, f"{job.job_uuid}_source.%(ext)s")
    mp3_path = ytdlp_download_audio_mp3(job.youtube_url, tmp_mp3)

    # Attach source audio
    with open(mp3_path, "rb") as f:
        job.source_audio.save("source.mp3", File(f), save=False)
    job.status = "converting"
    job.save()

    # Convert to WAV 16k mono
    wav_tmp = os.path.join(media_dir, f"{job.job_uuid}_audio_16k.wav")
    ffmpeg_to_wav_16k_mono(mp3_path, wav_tmp)

    with open(wav_tmp, "rb") as f:
        job.wav_audio.save("audio_16k.wav", File(f), save=False)

    # We stop here (no transcription yet)
    job.status = "ready"
    job.save()