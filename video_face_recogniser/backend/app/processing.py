import hashlib
import json
import re
import subprocess
import tempfile

import cv2
import numpy as np
from pathlib import Path

from sqlalchemy import delete, select

from app.config import Settings, get_settings
from app.sampling import choose_candidates
from app.database import SessionLocal
from app.models import Batch, BatchFrame, BatchScene, Frame, ProcessingJob, StashScene, utcnow

SHOWINFO_TIMESTAMP = re.compile(r"pts_time:([-+0-9.eE]+)")


def run_media(command, **kwargs):
    try:
        return subprocess.run(command, check=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ''
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8',errors='replace')
        details = '\n'.join(line[:300] for line in stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"{command[0]} failed (exit {exc.returncode}): {details or 'No diagnostic output'}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{command[0]} exceeded the {exc.timeout}-second processing timeout") from exc


def target_selection(targets):
    """Keep FFmpeg expression depth logarithmic for long sampling plans."""
    if not targets:
        raise ValueError('At least one frame timestamp is required')
    terms = [f"gt({t:.6f},prev_selected_t)*gte(t,{t:.6f})" for t in targets]
    def balanced(start,end):
        if end-start == 1:
            return terms[start]
        middle = (start+end)//2
        return f"({balanced(start,middle)}+{balanced(middle,end)})"
    return f"if(isnan(prev_selected_t),gte(t,{targets[0]:.6f}),{balanced(0,len(terms))})"


def source_fingerprint(path: Path) -> str:
    stat = path.stat()
    value = f"{stat.st_size}:{stat.st_mtime_ns}".encode()
    return hashlib.sha256(value).hexdigest()


def sampling_key(config: Settings) -> str:
    value = (
        f"coverage-v2:{config.frame_scan_fps}:{config.frame_sample_interval_seconds}:"
        f"{config.frame_scene_threshold}:{config.max_frames_per_video}:{config.frame_max_width}"
    )
    return hashlib.sha256(value.encode()).hexdigest()


def probe_video(path: Path, timeout: int) -> dict:
    result = run_media(
        [
            "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
        ],
        text=True, timeout=timeout,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
    media_format = payload.get("format") or {}
    numerator, _, denominator = str(stream.get("avg_frame_rate", "0/1")).partition("/")
    try:
        frame_rate = float(numerator) / float(denominator or 1) if float(denominator or 1) else None
    except ValueError:
        frame_rate = None
    details = {
        "format": {
            key: media_format.get(key) for key in
            ("format_name", "format_long_name", "start_time", "duration", "bit_rate", "tags")
            if media_format.get(key) is not None
        },
        "video": {
            key: stream.get(key) for key in
            ("codec_name", "codec_long_name", "profile", "pix_fmt", "color_space", "color_transfer",
             "color_primaries", "field_order", "width", "height", "avg_frame_rate", "tags")
            if stream.get(key) is not None
        },
        "audio": {
            key: audio_stream.get(key) for key in
            ("codec_name", "codec_long_name", "profile", "sample_rate", "channels", "channel_layout", "tags")
            if audio_stream.get(key) is not None
        },
    }
    return {
        "duration_seconds": float(media_format["duration"]) if media_format.get("duration") else None,
        "file_size": int(media_format["size"]) if media_format.get("size") else path.stat().st_size,
        "width": stream.get("width"), "height": stream.get("height"),
        "frame_rate": frame_rate, "video_codec": stream.get("codec_name"),
        "media_format": media_format.get("format_name"), "audio_codec": audio_stream.get("codec_name"),
        "bit_rate": int(media_format["bit_rate"]) if media_format.get("bit_rate") else None,
        "media_metadata": details,
    }


def extract_frames(path: Path, output_dir: Path, config: Settings) -> list[tuple[Path, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # A disposable scan occupies ~16 MiB/hour at 2 fps; only selected JPEGs persist.
    with tempfile.TemporaryDirectory(prefix="scan-", dir=output_dir) as temporary:
        raw = Path(temporary) / "scan.gray"
        run_media([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
            "-an", "-vf", f"setpts=PTS-STARTPTS,fps={config.frame_scan_fps},scale=64:36",
            "-pix_fmt", "gray", "-f", "rawvideo", str(raw),
        ], timeout=config.processing_job_timeout_seconds)
        count = raw.stat().st_size // (64 * 36)
        if not count:
            # Very short videos may not yield a resampled candidate; retain first frame.
            targets = [0.0]
        else:
            images = np.memmap(raw, dtype=np.uint8, mode="r", shape=(count, 36, 64))
            indices = choose_candidates(images, config.frame_scan_fps,
                                        config.frame_sample_interval_seconds,
                                        config.max_frames_per_video, config.frame_scene_threshold)
            targets = [i / config.frame_scan_fps for i in indices]
            del images
        selection = target_selection(targets)
        frame_filter = (
            "setpts=PTS-STARTPTS,select='" + selection + "',"
            f"scale=min({config.frame_max_width}\\,iw):-2,showinfo"
        )
        # Use a filter file so Linux's per-argument size limit cannot reject
        # large plans even after their expression depth has been bounded.
        filter_path = Path(temporary) / 'selection.ffscript'
        filter_path.write_text(frame_filter)
        # Remove partial files from an interrupted attempt, not other sampling versions.
        for stale in output_dir.glob("frame-*.jpg"):
            stale.unlink()
        result = run_media([
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-i", str(path),
            "-an", "-/filter:v", str(filter_path), "-fps_mode", "vfr", "-q:v", "2",
            str(output_dir / "frame-%06d.jpg"),
        ], text=True, timeout=config.processing_job_timeout_seconds)
    timestamps = [max(0, round(float(value) * 1000)) for value in SHOWINFO_TIMESTAMP.findall(result.stderr)]
    files = sorted(output_dir.glob("frame-*.jpg"))
    if not files or len(files) != len(timestamps):
        raise RuntimeError(f"FFmpeg produced {len(files)} frames but reported {len(timestamps)} timestamps")
    return list(zip(files, timestamps, strict=True))


def _process_scene(db, batch: Batch, batch_scene: BatchScene, config: Settings) -> int:
    scene = db.get(StashScene, batch_scene.scene_id)
    path = Path(scene.source_path)
    if not path.is_file():
        raise FileNotFoundError(f"Source video is not visible to the worker: {path}")

    fingerprint = source_fingerprint(path)
    sample_key = sampling_key(config)
    metadata = probe_video(path, config.processing_job_timeout_seconds)
    for key, value in metadata.items():
        setattr(scene, key, value)
    scene.source_fingerprint = fingerprint
    scene.probed_at = utcnow()

    frames = db.scalars(
        select(Frame).where(
            Frame.scene_id == scene.id, Frame.source_fingerprint == fingerprint, Frame.sampling_key == sample_key,
        ).order_by(Frame.timestamp_ms)
    ).all()
    complete_cache = bool(frames) and all((config.appdata_path / f.relative_path).is_file() for f in frames)
    if not complete_cache:
        relative_dir = Path("frames") / scene.id / f"{fingerprint[:12]}-{sample_key[:8]}"
        extracted = extract_frames(path, config.appdata_path / relative_dir, config)
        existing_by_time = {frame.timestamp_ms: frame for frame in frames}
        frames = [
            existing_by_time.get(timestamp) or Frame(
                scene_id=scene.id, source_fingerprint=fingerprint, sampling_key=sample_key,
                timestamp_ms=timestamp, relative_path=(relative_dir / file.name).as_posix(),
                selection_reason="coverage-v2",
                width=min(config.frame_max_width, metadata["width"]) if metadata["width"] else None,
                height=None,
            )
            for file, timestamp in extracted
        ]
        for frame in frames:
            image = cv2.imread(str(config.appdata_path / frame.relative_path))
            if image is not None:
                frame.height, frame.width = image.shape[:2]
        db.add_all(frames)
        db.flush()

    scene_frame_ids = select(Frame.id).where(Frame.scene_id == scene.id)
    db.execute(delete(BatchFrame).where(
        BatchFrame.batch_id == batch.id, BatchFrame.frame_id.in_(scene_frame_ids),
    ))
    db.add_all([BatchFrame(batch_id=batch.id, frame_id=frame.id) for frame in frames])
    batch_scene.state = "complete"
    batch_scene.error = None
    db.commit()
    return len(frames)


def process_batch(batch_id: str, job_id: str) -> None:
    config = get_settings()
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        job = db.get(ProcessingJob, job_id)
        if batch is None or job is None:
            raise RuntimeError("Batch or processing job no longer exists")
        job.state = "processing"
        job.attempts += 1
        job.progress_current = 0
        job.error = None
        batch.state = "processing"
        batch.started_at = batch.started_at or utcnow()
        db.commit()

        failures = []
        batch_scenes = db.scalars(select(BatchScene).where(BatchScene.batch_id == batch.id)).all()
        for batch_scene in batch_scenes:
            batch_scene.state = "processing"
            db.commit()
            try:
                _process_scene(db, batch, batch_scene, config)
            except Exception as exc:
                db.rollback()
                batch_scene = db.get(BatchScene, (batch.id, batch_scene.scene_id))
                batch_scene.state = "failed"
                batch_scene.error = str(exc)
                scene = db.get(StashScene, batch_scene.scene_id)
                failures.append(f"{scene.source_path}: {exc}")
                db.commit()
            job = db.get(ProcessingJob, job.id)
            job.progress_current += 1
            db.commit()

        job = db.get(ProcessingJob, job.id)
        batch = db.get(Batch, batch.id)
        if failures:
            job.state = "failed"
            job.error = "\n".join(failures[:20])
            batch.state = "failed"
        else:
            job.state = "complete"
            batch.state = "video_processing_complete"
            batch.completed_at = utcnow()
        db.commit()
