"""Versioned SFace features and conservative grouping within one video only."""
import heapq
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select

from app.config import get_settings
from app.appearance import enrich_items, model_key as appearance_model_key
from app.database import SessionLocal
from app.faces import MODEL_KEY as DETECTOR_KEY
from app.models import Batch, BatchFrame, FaceEmbedding, FaceGrouping, FaceObservation, Frame, ModelVersion, ProcessingJob, utcnow

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "face_recognition_sface_2021dec.onnx"
MODEL_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
MODEL_KEY = hashlib.sha256(f"sface:{MODEL_SHA256}:opencv4.11:align5:l2-v1".encode()).hexdigest()
def algorithm_key(config):
    rules = {name:value for name,value in config.model_dump().items() if name.startswith('grouping_')}
    return hashlib.sha256(json.dumps(['assisted-average-v3:normal0.50:floor0.25:duplicate-mass:pose-diversity',
        rules,config.appearance_enabled,appearance_model_key(config)],sort_keys=True).encode()).hexdigest()


ALGORITHM_KEY = algorithm_key(get_settings())
THRESHOLD = 0.50
PAIR_FLOOR = 0.25


def make_recognizer():
    if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Bundled SFace model checksum mismatch")
    return cv2.FaceRecognizerSF.create(str(MODEL_PATH), "")


def embedding(image, face, recognizer):
    landmarks = np.asarray(face.landmarks, dtype=np.float32)
    if landmarks.shape != (5, 2) or not np.isfinite(landmarks).all():
        raise ValueError(f"Face {face.id} has invalid landmarks; rerun detection")
    detection = np.asarray([*face.bbox, *landmarks.reshape(-1), face.detector_confidence], dtype=np.float32)
    aligned = recognizer.alignCrop(image, detection)
    feature = np.asarray(recognizer.feature(aligned), dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(feature))
    if feature.shape != (128,) or not np.isfinite(feature).all() or norm < 1e-8:
        raise RuntimeError("SFace returned an invalid embedding")
    return (feature / norm).tolist()


def input_key(faces):
    return hashlib.sha256(json.dumps(sorted((face.id, face.frame_id, face.usable, face.quality, face.model_key)
                                            for face in faces)).encode()).hexdigest()


def cosine_matrix(vectors, dimensions):
    values = np.asarray(vectors,dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != dimensions or not np.isfinite(values).all():
        raise ValueError('Invalid clustering feature matrix')
    norms = np.linalg.norm(values,axis=1,keepdims=True)
    if not np.isfinite(norms).all() or np.any(norms < 1e-8):
        raise ValueError('Invalid clustering feature norm')
    values = values/norms
    # Apple BLAS may set floating-point status flags despite finite unit inputs.
    # Check the result explicitly; never permit invalid scores into the heap.
    with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
        scores = values @ values.T
    if not np.isfinite(scores).all():
        raise ValueError('Invalid clustering similarity matrix')
    return np.clip(scores,-1,1)


def pose_bucket(item):
    points = np.asarray(item.get('landmarks') or [], dtype=float)
    if points.shape != (5,2) or not np.isfinite(points).all():
        return 'unknown'
    eyes = sorted(points[:2], key=lambda p:p[0])
    distance = eyes[1][0]-eyes[0][0]
    if distance < 1:
        return 'unknown'
    offset = (points[2][0]-(eyes[0][0]+eyes[1][0])/2)/distance
    return 'left' if offset < -.18 else 'right' if offset > .18 else 'front'


def representatives(members, ordered, duplicates, scores):
    chosen, seen = [], set()
    remaining = sorted(members)
    while remaining and len(chosen) < 3:
        candidates = [i for i in remaining if duplicates[i] not in seen]
        if not candidates:
            break
        if not chosen:
            best = candidates[0]
        else:
            poses = {pose_bucket(ordered[i]) for i in chosen}
            best = max(candidates, key=lambda i:(
                pose_bucket(ordered[i]) not in poses and pose_bucket(ordered[i]) != 'unknown',
                .7*(1-max(float(scores[i,j]) for j in chosen)) + .3*ordered[i]['quality'], -i))
        chosen.append(best)
        seen.add(duplicates[best])
        remaining.remove(best)
    return [ordered[i]['id'] for i in chosen]


def cluster_faces(items, threshold=THRESHOLD, diagnostics=None, config=None):
    """Face-primary average linkage; complete cross-pair gates for assisted merges."""
    config = config or get_settings()
    started = time.perf_counter()
    ordered = sorted(items, key=lambda item: (-item['quality'], item['id']))
    if not ordered:
        return []
    vectors = np.asarray([item['vector'] for item in ordered], dtype=np.float32)
    raw = cosine_matrix(vectors,128)
    scores, floors = raw.copy(), raw.copy()
    n = len(ordered)
    timestamps = np.asarray([item.get('timestamp_ms') if item.get('timestamp_ms') is not None else np.nan for item in ordered])
    times = np.abs(timestamps[:,None]-timestamps[None,:]).astype(np.float32)/1000
    times[~np.isfinite(times)] = np.inf
    available = np.asarray([item.get('appearance') is not None for item in ordered])
    appearances = np.full((n,n), -1.0, dtype=np.float32)
    indices = np.flatnonzero(available)
    if len(indices):
        features = np.asarray([ordered[i]['appearance'] for i in indices],dtype=np.float32)
        appearances[np.ix_(indices,indices)] = cosine_matrix(features,1280)
    # Complete-link duplicate families avoid transitive temporal/pose chains.
    families, duplicates = [], {}
    for i in range(n):
        family = next((f for f in families if all(raw[i,j] >= config.grouping_duplicate_min
            and times[i,j] <= config.grouping_duplicate_seconds
            and ordered[i]['frame_id'] != ordered[j]['frame_id']
            and ordered[i].get('scene_id') == ordered[j].get('scene_id') for j in f)), None)
        if family is None:
            family = []
            families.append(family)
        family.append(i)
    mass = np.ones(n)
    for family in families:
        for i in family:
            duplicates[i] = family[0]
            mass[i] = 1/len(family)
    groups = {i:[i] for i in range(n)}
    frames = {i:{ordered[i]['frame_id']} for i in groups}
    scenes = {i:{ordered[i].get('scene_id')} for i in groups}
    versions, heap = [0]*n, []
    report = {'algorithm_key':algorithm_key(config),'threshold':threshold,'duplicate_families':[
        [ordered[i]['id'] for i in f] for f in families if len(f)>1], 'counts':{},'decisions':[]}

    def enqueue(i,j):
        conflict = bool(frames[i] & frames[j])
        average, worst = float(scores[i,j]),float(floors[i,j])
        app, seconds = float(appearances[i,j]),float(times[i,j])
        if scenes[i] != scenes[j]:
            reason = 'different_scene'
        elif conflict:
            reason = 'same_frame_conflict'
        elif worst < min(PAIR_FLOOR,threshold):
            reason = 'face_pair_floor'
        elif average >= threshold:
            reason = 'normal_face_match'
        elif worst >= config.grouping_assisted_min and average >= config.grouping_assisted_min:
            reason = ('appearance_assisted' if config.appearance_enabled and app >= config.grouping_appearance_min
                and seconds <= config.grouping_assisted_seconds else 'insufficient_context')
        else:
            reason = 'weak_face_match'
        merge = reason in {'normal_face_match','appearance_assisted'}
        report['counts'][reason] = report['counts'].get(reason,0)+1
        if len(report['decisions']) < 200:
            report['decisions'].append({'left':ordered[i]['id'],'right':ordered[j]['id'],
                'face_similarity':average,'minimum_face_similarity':worst,
                'appearance_similarity':app if app >= 0 else None,
                'temporal_seconds':seconds if np.isfinite(seconds) else None,
                'same_frame_conflict':conflict,'decision':'eligible' if merge else 'separate','reason':reason})
        if merge:
            heapq.heappush(heap,(-average,i,j,versions[i],versions[j],reason))

    for i in groups:
        for j in range(i+1,n):
            enqueue(i,j)
    merges = []
    while heap:
        _,i,j,vi,vj,reason = heapq.heappop(heap)
        if i not in groups or j not in groups or versions[i] != vi or versions[j] != vj:
            continue
        if len(merges) < 200:
            merges.append({'left':ordered[i]['id'],'right':ordered[j]['id'],'reason':reason,
                'face_similarity':float(scores[i,j]),'minimum_face_similarity':float(floors[i,j]),
                'appearance_similarity':float(appearances[i,j]),
                'temporal_seconds':float(times[i,j]) if np.isfinite(times[i,j]) else None})
        scores[i,:] = (scores[i,:]*mass[i]+scores[j,:]*mass[j])/(mass[i]+mass[j])
        scores[:,i] = scores[i,:]
        mass[i] += mass[j]
        for matrix,operation in [(floors,np.minimum),(appearances,np.minimum),(times,np.maximum)]:
            matrix[i,:] = operation(matrix[i,:],matrix[j,:])
            matrix[:,i] = matrix[i,:]
        groups[i].extend(groups.pop(j))
        frames[i].update(frames.pop(j))
        versions[i] += 1
        for k in groups:
            if k != i:
                enqueue(min(i,k),max(i,k))
    report['merges'],report['seconds'] = merges,round(time.perf_counter()-started,4)
    if diagnostics is not None:
        diagnostics.update(report)
    return [{'face_ids':[ordered[i]['id'] for i in sorted(members)],
        'representative_ids':representatives(members,ordered,duplicates,raw)}
        for _,members in sorted(groups.items())]


def scene_faces(db, batch_id):
    return db.execute(select(FaceObservation, Frame).select_from(FaceObservation)
        .join(Frame, Frame.id == FaceObservation.frame_id).join(BatchFrame, BatchFrame.frame_id == Frame.id)
        .where(BatchFrame.batch_id == batch_id, FaceObservation.model_key == DETECTOR_KEY)
        .order_by(Frame.scene_id, Frame.timestamp_ms, FaceObservation.id)).all()


def process_groups(batch_id, job_id):
    config = get_settings()
    with SessionLocal() as db:
        job, batch = db.get(ProcessingJob, job_id), db.get(Batch, batch_id)
        if job is None or batch is None:
            return
        try:
            job.state, batch.state = "processing", "processing"
            job.progress_current = 0
            job.error = None
            job.attempts += 1
            db.commit()
            recognizer = make_recognizer()
            if db.scalar(select(ModelVersion).where(ModelVersion.component == "face_embedding", ModelVersion.version == MODEL_KEY)) is None:
                db.add(ModelVersion(component="face_embedding", name="SFace", version=MODEL_KEY, active=True,
                                    configuration={"sha256": MODEL_SHA256, "dimensions": 128, "alignment": "5 landmarks", "normalization": "L2"}))
                db.commit()
            rows = scene_faces(db, batch_id)
            job.progress_total = sum(face.usable for face, _ in rows)
            db.commit()
            by_scene = {}
            cached_image, cached_frame = None, None
            for face, frame in rows:
                by_scene.setdefault(frame.scene_id, []).append(face)
                if not face.usable:
                    continue
                saved = db.get(FaceEmbedding, (face.id, MODEL_KEY))
                if saved is None:
                    if cached_frame != frame.id:
                        cached_image = cv2.imread(str(config.appdata_path / frame.relative_path))
                        cached_frame = frame.id
                    if cached_image is None:
                        raise RuntimeError(f"Source frame unavailable: {frame.relative_path}")
                    db.add(FaceEmbedding(face_id=face.id, model_key=MODEL_KEY,
                                         vector=embedding(cached_image, face, recognizer)))
                job.progress_current += 1
                db.commit()
            for scene_id, faces in by_scene.items():
                key = input_key(faces)
                saved = db.get(FaceGrouping, (batch_id, scene_id))
                if saved and saved.input_key == key and saved.model_key == MODEL_KEY and saved.algorithm_key == ALGORITHM_KEY:
                    continue
                items = [{"id": face.id, "frame_id": face.frame_id, "quality": face.quality,
                          "vector": np.asarray(db.get(FaceEmbedding, (face.id, MODEL_KEY)).vector)}
                         for face in faces if face.usable]
                diagnostics = {}
                appearance_stats = enrich_items(db,items,faces,config)
                groups = cluster_faces(items,diagnostics=diagnostics,config=config)
                diagnostics['appearance'] = appearance_stats
                if saved is None:
                    saved = FaceGrouping(batch_id=batch_id, scene_id=scene_id)
                    db.add(saved)
                saved.input_key, saved.model_key, saved.algorithm_key = key, MODEL_KEY, ALGORITHM_KEY
                saved.groups, saved.created_at = groups, utcnow()
                saved.diagnostics = diagnostics
                db.commit()
            job.state = "complete"
        except Exception as exc:
            db.rollback()
            job = db.get(ProcessingJob, job_id)
            job.state, job.error = "failed", str(exc)
        finally:
            batch = db.get(Batch, batch_id)
            batch.state = job.payload.get("previous_state", "video_processing_complete")
            db.commit()
