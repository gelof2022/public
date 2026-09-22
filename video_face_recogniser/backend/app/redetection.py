"""Scene-only re-detection previews; existing observations are never replaced."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal, get_db
from app.faces import detect_observations, make_detector
from app.grouping import MODEL_KEY as EMBEDDING_KEY, embedding, make_recognizer
from app.models import (Batch, BatchFrame, FaceObservation, Frame, ProcessingJob, ReviewCluster,
                        ReviewEvidence, SceneRedetection, new_id)
from app.review import audit, fail, lock_batch, review_scenes

router = APIRouter(prefix="/api/v1/review", tags=["scene re-detection"])


class DetectionInput(BaseModel):
    threshold: float | None = Field(default=None, ge=0.1, le=0.99)


class ApplyInput(BaseModel):
    face_ids: list[str] = Field(min_length=1)


def valid_box(box):
    return isinstance(box, list) and len(box) == 4 and np.isfinite(box).all() and box[2] > 0 and box[3] > 0


def overlap(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0]+a[2], b[0]+b[2]), min(a[1]+a[3], b[1]+b[3])
    intersection = max(0, right-left) * max(0, bottom-top)
    return intersection / max(1e-9, a[2]*a[3] + b[2]*b[3] - intersection)


def detection_context(db, batch_id, scene_id):
    scene = next((s for s in review_scenes(batch_id, db) if s['scene_id'] == scene_id), None)
    if scene is None:
        fail("Scene not found in this batch", 404)
    frames = db.scalars(select(Frame).join(BatchFrame, BatchFrame.frame_id == Frame.id)
        .where(BatchFrame.batch_id == batch_id, Frame.scene_id == scene_id)
        .order_by(Frame.timestamp_ms, Frame.id)).all()
    if not frames:
        fail("Extract frames for this scene before re-detecting")
    frame_ids = {frame.id for frame in frames}
    originals = db.scalars(select(FaceObservation).where(FaceObservation.frame_id.in_(frame_ids))).all()
    known = {face.id: {'id':face.id, 'frame_id':face.frame_id, 'bbox':face.bbox} for face in originals if valid_box(face.bbox)}
    clusters = db.scalars(select(ReviewCluster).where(ReviewCluster.batch_id == batch_id,
        ReviewCluster.scene_id == scene_id, ReviewCluster.active.is_(True))).all()
    reviewed_ids = {fid for cluster in clusters for fid in cluster.member_ids}
    for face in db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(reviewed_ids))):
        if face.frame_id not in frame_ids:
            continue
        box = face.bbox or known.get(face.id, {}).get('bbox')
        if not valid_box(box):
            fail("Some older reviewed faces lack their original locations. Re-detection is blocked to avoid duplicating reviewed or ignored faces.")
        known[face.id] = {'id':face.id, 'frame_id':face.frame_id, 'bbox':box}
    frame_data = [(f.id, f.relative_path, f.source_fingerprint, f.sampling_key, f.timestamp_ms) for f in frames]
    signature = hashlib.sha256(json.dumps({'scene':scene['fingerprint'], 'frames':frame_data,
        'known':sorted(known.values(), key=lambda item:item['id'])}, sort_keys=True).encode()).hexdigest()
    return frames, list(known.values()), signature


def run_data(db, run):
    job = db.get(ProcessingJob, run.job_id)
    return {'id':run.id, 'scene_id':run.scene_id, 'state':run.state, 'threshold':run.threshold,
        'progress_current':job.progress_current, 'progress_total':job.progress_total,
        'summary':run.summary, 'error':run.error,
        'proposals':[{'id':p['id'], 'frame_id':p['frame_id'], 'timestamp_seconds':p['timestamp_ms']/1000,
            'url':f"/generated/{p['relative_path']}", 'video_url':f"/api/v1/scenes/{run.scene_id}/video",
            'quality':p['quality'], 'detector_confidence':p['detector_confidence'],
            'frame_context_url':f"/api/v1/review/redetections/{run.id}/candidates/{p['id']}/frame",
            'has_embedding':p.get('vector') is not None} for p in run.proposals]}


@router.get('/redetections/{run_id}/candidates/{face_id}/frame')
def candidate_frame(run_id: str, face_id: str, db: Session = Depends(get_db),
                    config: Settings = Depends(get_settings)):
    run = db.get(SceneRedetection, run_id)
    candidate = next((p for p in run.proposals if p['id'] == face_id), None) if run else None
    if candidate is None:
        fail('Candidate not found', 404)
    frame = db.get(Frame, candidate['frame_id'])
    if frame is None or frame.scene_id != run.scene_id or not (config.appdata_path / frame.relative_path).is_file():
        fail('The original extracted frame is no longer available', 404)
    if not valid_box(candidate.get('bbox')):
        fail('The location of this face is unavailable', 409)
    return {'url':f'/generated/{frame.relative_path}', 'bbox':candidate['bbox']}


@router.get('/batches/{batch_id}/scenes/{scene_id}/redetection')
def latest_redetection(batch_id: str, scene_id: str, db: Session = Depends(get_db)):
    if db.get(Batch, batch_id) is None:
        fail("Batch not found", 404)
    run = db.scalar(select(SceneRedetection).where(SceneRedetection.batch_id == batch_id,
        SceneRedetection.scene_id == scene_id).order_by(SceneRedetection.created_at.desc(), SceneRedetection.id.desc()).limit(1))
    return run_data(db, run) if run else None


@router.post('/batches/{batch_id}/scenes/{scene_id}/redetection', status_code=202)
def start_redetection(batch_id: str, scene_id: str, payload: DetectionInput,
                      db: Session = Depends(get_db), config: Settings = Depends(get_settings)):
    from app.preferences import read_preferences
    if payload.threshold is None:
        payload.threshold = read_preferences(config).detection_threshold
    batch = lock_batch(db, batch_id)
    if batch.state in {'queued','processing','clustering','committing'} or db.scalar(select(ProcessingJob.id).where(
        ProcessingJob.batch_id == batch_id, ProcessingJob.state.in_(['queued','processing'])).limit(1)):
        fail("Wait for the active batch job to finish")
    frames, _, signature = detection_context(db, batch_id, scene_id)
    job = ProcessingJob(batch_id=batch_id, job_type='scene_redetection', state='queued',
        progress_total=len(frames), payload={'previous_state':batch.state,'scene_id':scene_id,'threshold':payload.threshold})
    db.add(job)
    db.flush()
    run = SceneRedetection(batch_id=batch_id, scene_id=scene_id, job_id=job.id,
        threshold=payload.threshold, baseline=signature, frame_ids=[f.id for f in frames])
    db.add(run)
    batch.state = 'queued'
    db.commit()
    try:
        Queue(connection=Redis.from_url(config.redis_url)).enqueue('app.redetection.process_redetection',
            run.id, job_id=f'scene-redetection-{run.id}', job_timeout=config.processing_job_timeout_seconds)
    except Exception as exc:
        batch.state = job.payload['previous_state']
        job.state = run.state = 'failed'
        job.error = run.error = f'Unable to queue scene re-detection: {exc}'
        db.commit()
        fail(run.error, 503)
    return run_data(db, run)


def process_redetection(run_id):
    config = get_settings()
    with SessionLocal() as db:
        run = db.get(SceneRedetection, run_id)
        if run is None or run.state not in {'queued','processing'}:
            return
        job, batch = db.get(ProcessingJob, run.job_id), db.get(Batch, run.batch_id)
        try:
            run.state = job.state = batch.state = 'processing'
            job.attempts += 1
            job.progress_current = 0
            db.commit()
            frames, known, signature = detection_context(db, run.batch_id, run.scene_id)
            if signature != run.baseline or [f.id for f in frames] != run.frame_ids:
                raise RuntimeError('Scene or frames changed; start a fresh re-detection preview')
            detector, recognizer = make_detector(run.threshold), make_recognizer()
            directory = Path('redetection') / run.id
            (config.appdata_path / directory).mkdir(parents=True, exist_ok=True)
            proposals, matched = [], set()
            detected_count = 0
            for frame in frames:
                image = cv2.imread(str(config.appdata_path / frame.relative_path))
                if image is None:
                    raise RuntimeError(f'Existing extracted frame unavailable: {frame.relative_path}')
                existing = [item for item in known if item['frame_id'] == frame.id]
                for item in detect_observations(image, detector):
                    detected_count += 1
                    matches = [old['id'] for old in existing if overlap(old['bbox'], item['bbox']) >= 0.30]
                    if matches:
                        matched.update(matches)
                        continue
                    identifier = new_id()
                    path = directory / f'{identifier}.jpg'
                    if not cv2.imwrite(str(config.appdata_path/path), item.pop('crop'), [cv2.IMWRITE_JPEG_QUALITY,95]):
                        raise RuntimeError('Unable to save preview crop')
                    vector = embedding(image, SimpleNamespace(id=identifier, **item), recognizer) if item['usable'] else None
                    proposals.append({'id':identifier,'frame_id':frame.id,'timestamp_ms':frame.timestamp_ms,
                        'relative_path':path.as_posix(), 'vector':vector, **item})
                job.progress_current += 1
                db.commit()
            run.proposals = proposals
            run.summary = {'frames':len(frames),'detected':detected_count,'new':len(proposals),
                'matched_existing':len(matched),'existing_not_detected':len(known)-len(matched)}
            run.state, job.state = 'ready', 'complete'
        except Exception as exc:
            db.rollback()
            run, job = db.get(SceneRedetection, run_id), db.get(ProcessingJob, job.id)
            run.state = job.state = 'failed'
            run.error = job.error = str(getattr(exc, 'detail', exc))
        finally:
            batch = db.get(Batch, run.batch_id)
            batch.state = job.payload['previous_state']
            db.commit()


def editable_run(db, run_id):
    run = db.get(SceneRedetection, run_id)
    if run is None:
        fail('Re-detection preview not found', 404)
    batch = lock_batch(db, run.batch_id)
    db.refresh(run)
    if batch.state in {'queued','processing','clustering','committing'}:
        fail('Wait for processing to finish')
    return run


@router.post('/redetections/{run_id}/apply')
def apply_redetection(run_id: str, payload: ApplyInput, db: Session = Depends(get_db),
                      config: Settings = Depends(get_settings)):
    run = editable_run(db, run_id)
    if run.state != 'ready':
        fail('Only a ready preview can be applied')
    _, _, signature = detection_context(db, run.batch_id, run.scene_id)
    if signature != run.baseline:
        fail('Scene decisions or frames changed since this preview. Re-detect again before applying it.')
    selected = set(payload.face_ids)
    proposals = {p['id']:p for p in run.proposals}
    if not selected <= proposals.keys():
        fail('Select only faces from this preview', 422)
    for identifier in sorted(selected):
        item = proposals[identifier]
        if not (config.appdata_path / item['relative_path']).is_file():
            fail('Preview crop missing; re-detect again')
        db.add(ReviewEvidence(id=identifier, scene_id=run.scene_id, frame_id=item['frame_id'],
            timestamp_ms=item['timestamp_ms'], relative_path=item['relative_path'], quality=item['quality'],
            bbox=item['bbox'], vector=item['vector'], model_key=EMBEDDING_KEY if item['vector'] is not None else None))
        db.add(ReviewCluster(batch_id=run.batch_id, scene_id=run.scene_id, member_ids=[identifier]))
    run.state = 'accepted'
    run.summary = {**run.summary, 'accepted':len(selected)}
    audit(db, None, 'redetection_accepted', {'batch_id':run.batch_id,'scene_id':run.scene_id,
        'run_id':run.id,'threshold':run.threshold,'face_ids':sorted(selected)})
    db.commit()
    return {'accepted':len(selected)}


@router.post('/redetections/{run_id}/discard')
def discard_redetection(run_id: str, db: Session = Depends(get_db)):
    run = editable_run(db, run_id)
    if run.state not in {'ready','failed'}:
        fail('Only a ready or failed preview can be discarded')
    run.state = 'discarded'
    audit(db, None, 'redetection_discarded', {'batch_id':run.batch_id,'scene_id':run.scene_id,'run_id':run.id})
    db.commit()
    return {'state':'discarded'}
