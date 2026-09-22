"""Local CPU face detection. Identity matching is deliberately a separate layer."""
import hashlib
from pathlib import Path

import cv2
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Batch, BatchFrame, FaceAnalysis, FaceObservation, Frame, ModelVersion, ProcessingJob, new_id, utcnow

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL_KEY = hashlib.sha256(f"yunet:{MODEL_SHA256}:quality-v1:score0.8:width1280:margin0.2".encode()).hexdigest()


def make_detector(threshold=0.8):
    if not 0.1 <= threshold <= 0.99:
        raise ValueError("Detection confidence must be between 0.10 and 0.99")
    if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Bundled YuNet model checksum mismatch")
    return cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (320, 320), float(threshold), 0.3, 5000)


def face_quality(crop, face_size):
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    exposure = float(((gray > 15) & (gray < 240)).mean())
    quality = min(1.0, face_size / 100) * min(1.0, sharpness / 100) * exposure
    usable = bool(face_size >= 32 and sharpness >= 20 and exposure >= 0.5)
    return quality, usable, {"face_size_px": float(face_size), "sharpness": sharpness, "exposure": exposure}


def detect_observations(image, detector):
    h, w = image.shape[:2]
    scale = min(1.0, 1280 / w)
    small = cv2.resize(image, (round(w * scale), round(h * scale))) if scale < 1 else image
    detector.setInputSize((small.shape[1], small.shape[0]))
    _, detections = detector.detect(small)
    if detections is None:
        return []
    observations = []
    for detection in detections:
        x, y, width, height = [float(v / scale) for v in detection[:4]]
        left, top = max(0, int(x)), max(0, int(y))
        right, bottom = min(w, int(x + width)), min(h, int(y + height))
        if right <= left or bottom <= top:
            continue
        quality, usable, details = face_quality(image[top:bottom, left:right], min(right-left, bottom-top))
        margin = int(max(width, height) * 0.2)
        crop = image[max(0, top-margin):min(h, bottom+margin), max(0, left-margin):min(w, right+margin)]
        observations.append({"bbox": [left, top, right-left, bottom-top],
            "landmarks": (detection[4:14].reshape(5, 2) / scale).tolist(),
            "detector_confidence": float(detection[14]), "quality": quality,
            "usable": usable, "quality_details": details, "crop": crop})
    return observations


def analyse_frame(db, frame, config, detector):
    analysis = db.get(FaceAnalysis, (frame.id, MODEL_KEY))
    existing = db.scalars(select(FaceObservation).where(
        FaceObservation.frame_id == frame.id, FaceObservation.model_key == MODEL_KEY)).all()
    if analysis and len(existing) == analysis.face_count and all(
        (config.appdata_path / face.relative_path).is_file() for face in existing
    ):
        return
    image = cv2.imread(str(config.appdata_path / frame.relative_path))
    if image is None:
        raise RuntimeError(f"Extracted frame unavailable: {frame.relative_path}")
    observations = detect_observations(image, detector)
    db.execute(delete(FaceObservation).where(FaceObservation.frame_id == frame.id, FaceObservation.model_key == MODEL_KEY))
    directory = Path("faces") / frame.id / MODEL_KEY[:12]
    (config.appdata_path / directory).mkdir(parents=True, exist_ok=True)
    for item in observations:
        crop = item.pop("crop")
        identifier = new_id()
        relative = directory / f"{identifier}.jpg"
        if not cv2.imwrite(str(config.appdata_path / relative), crop, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise RuntimeError("Unable to save face crop")
        db.add(FaceObservation(id=identifier, frame_id=frame.id, model_key=MODEL_KEY,
                               relative_path=relative.as_posix(), **item))
    if analysis:
        analysis.face_count = len(observations)
        analysis.completed_at = utcnow()
    else:
        db.add(FaceAnalysis(frame_id=frame.id, model_key=MODEL_KEY, face_count=len(observations)))
    db.commit()


def process_faces(batch_id: str, job_id: str):
    config = get_settings()
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        batch = db.get(Batch, batch_id)
        if job is None or batch is None:
            return
        try:
            job.state = "processing"
            job.attempts += 1
            job.error = None
            job.progress_current = 0
            batch.state = "processing"
            db.commit()
            from app.preferences import read_preferences
            threshold = job.payload.get("detection_threshold", read_preferences(config).detection_threshold)
            detector = make_detector(threshold)
            version = db.scalar(select(ModelVersion).where(ModelVersion.component == "face_detector", ModelVersion.version == MODEL_KEY))
            if version is None:
                db.add(ModelVersion(component="face_detector", name="YuNet", version=MODEL_KEY, active=True,
                    configuration={"sha256": MODEL_SHA256, "quality": "heuristic-v1", "opencv": cv2.__version__}))
                db.commit()
            frames = db.scalars(select(Frame).join(BatchFrame).where(BatchFrame.batch_id == batch_id)
                                .order_by(Frame.scene_id, Frame.timestamp_ms)).all()
            job.progress_total = len(frames)
            for frame in frames:
                analyse_frame(db, frame, config, detector)
                job.progress_current += 1
                db.commit()
            job.state = "complete"
        except Exception as exc:
            db.rollback()
            job = db.get(ProcessingJob, job_id)
            job.state = "failed"
            job.error = str(exc)
        finally:
            # Frame processing remains complete even if the independent detector failed.
            batch = db.get(Batch, batch_id)
            batch.state = job.payload.get("previous_state", "video_processing_complete")
            db.commit()
