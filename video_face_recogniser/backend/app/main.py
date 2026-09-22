import logging
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis import Redis
from rq import Queue, Retry
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.preferences import Preferences, read_preferences, save_preferences
from app.review import router as review_router
from app.scene_suggestions import router as scene_suggestions_router
from app.redetection import router as redetection_router
from app.stash_export import router as stash_export_router
from app.database import get_db
from app.models import Batch, BatchFrame, BatchScene, FaceObservation, Frame, Library, ProcessingJob, StashScene, utcnow
from app.faces import MODEL_KEY
from app.schemas import (
    BatchCreate, BatchResponse, FrameResponse, HealthResponse, ProcessingJobResponse,
    SceneFailure, SceneIdList, SceneList, SceneResponse, SettingsResponse, StashStatus, SyncResult,
)
from app.stash import StashClient, StashError, normalise_scene

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"],
)
settings.appdata_path.mkdir(parents=True, exist_ok=True)
app.mount("/generated", StaticFiles(directory=settings.appdata_path), name="generated")


@contextmanager
def stash_client(config: Settings):
    client = StashClient(config.stash_url, config.stash_api_key)
    try:
        yield client
    finally:
        client.close()


def batch_response(batch: Batch) -> BatchResponse:
    latest_job = max(batch.jobs, key=lambda item: item.created_at, default=None)
    failures = [item for item in batch.scenes if item.state == "failed"]
    errors = [
        SceneFailure(
            scene_id=item.scene_id, title=item.scene.title, source_path=item.scene.source_path,
            error=item.error or "Unknown processing error",
        )
        for item in failures[:20]
    ]
    return BatchResponse(
        id=batch.id, name=batch.name, description=batch.description, state=batch.state, archived=batch.archived,
        selection=batch.selection, scene_count=len(batch.scenes),
        created_at=batch.created_at, updated_at=batch.updated_at,
        latest_job_type=latest_job.job_type if latest_job else None,
        latest_job_state=latest_job.state if latest_job else None,
        progress_current=latest_job.progress_current if latest_job else 0,
        progress_total=latest_job.progress_total if latest_job else len(batch.scenes),
        frame_count=len(batch.frames),
        error=(f"{len(failures)} scene(s) failed" if failures else latest_job.error if latest_job else None),
        failed_scene_count=len(failures), errors=errors,
    )


def filter_scenes(statement, query: str | None, processed: str, date_from: date | None, date_to: date | None):
    if query:
        pattern = f"%{query}%"
        statement = statement.where(or_(
            StashScene.title.ilike(pattern), StashScene.source_path.ilike(pattern),
            StashScene.stash_scene_id.ilike(pattern),
        ))
    has_frames = select(Frame.id).where(Frame.scene_id == StashScene.id).exists()
    if processed == "processed":
        statement = statement.where(has_frames)
    elif processed == "unprocessed":
        statement = statement.where(~has_frames)
    if date_from:
        statement = statement.where(StashScene.scene_date >= date_from)
    if date_to:
        statement = statement.where(StashScene.scene_date <= date_to)
    return statement


def scene_responses(db: Session, scenes: list[StashScene]) -> list[SceneResponse]:
    ids = [scene.id for scene in scenes]
    stats = {}
    if ids:
        rows = db.execute(
            select(Frame.scene_id, func.count(Frame.id), func.max(Frame.created_at))
            .where(Frame.scene_id.in_(ids)).group_by(Frame.scene_id)
        ).all()
        stats = {scene_id: (count, latest) for scene_id, count, latest in rows}
    return [
        SceneResponse.model_validate(scene).model_copy(update={
            "processed": scene.id in stats, "frame_count": stats.get(scene.id, (0, None))[0],
            "last_processed_at": stats.get(scene.id, (0, None))[1],
            "thumbnail_url": f"/api/v1/scenes/{scene.id}/thumbnail" if scene.thumbnail_url else None,
        })
        for scene in scenes
    ]


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return HealthResponse(status="ok", version=settings.app_version, database="ok")


@app.get("/api/v1/settings", response_model=SettingsResponse, tags=["settings"])
def read_settings(config: Settings = Depends(get_settings)) -> SettingsResponse:
    return SettingsResponse(
        stash_url=config.stash_url, stash_api_key_configured=bool(config.stash_api_key),
        stash_path_prefix=config.stash_path_prefix, media_path_prefix=config.media_path_prefix,
        appdata_path=str(config.appdata_path),
    )


@app.post("/api/v1/stash/test", response_model=StashStatus, tags=["stash"])
def test_stash(config: Settings = Depends(get_settings)) -> StashStatus:
    try:
        with stash_client(config) as client:
            return StashStatus(connected=True, version=client.version())
    except StashError as exc:
        return StashStatus(connected=False, error=str(exc))


@app.post("/api/v1/stash/sync", response_model=SyncResult, tags=["stash"])
def sync_stash(db: Session = Depends(get_db), config: Settings = Depends(get_settings)) -> SyncResult:
    library = db.scalar(select(Library).limit(1))
    if library is None:
        library = Library(
            name="Home videos", stash_url=config.stash_url,
            stash_path_prefix=config.stash_path_prefix, media_path_prefix=config.media_path_prefix,
        )
        db.add(library)
        db.flush()
    else:
        library.stash_url = config.stash_url
        library.stash_path_prefix = config.stash_path_prefix
        library.media_path_prefix = config.media_path_prefix

    existing = {
        scene.stash_scene_id: scene
        for scene in db.scalars(select(StashScene).where(StashScene.library_id == library.id))
    }
    imported = updated = skipped = total = 0
    try:
        with stash_client(config) as client:
            for raw, total in client.iter_scenes():
                scene_data = normalise_scene(raw, config.stash_path_prefix, config.media_path_prefix)
                if scene_data is None:
                    skipped += 1
                    continue
                scene = existing.get(scene_data.stash_scene_id)
                values = scene_data.__dict__
                if scene is None:
                    db.add(StashScene(library_id=library.id, **values))
                    imported += 1
                else:
                    for key, value in values.items():
                        setattr(scene, key, value)
                    scene.synced_at = utcnow()
                    updated += 1
        library.last_synced_at = utcnow()
        db.commit()
    except (StashError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SyncResult(imported=imported, updated=updated, skipped_without_files=skipped, total_in_stash=total)


def workflow_review_ids(db, awaiting_review=False, review_batch=None):
    if not awaiting_review and not review_batch:
        return None
    from app.review import review_scenes
    if review_batch:
        scenes = review_scenes(review_batch,db,include_cluster_ids=False)
        return [s['scene_id'] for s in scenes if not awaiting_review or not s['complete']]
    # A newer active batch is authoritative for Workflow; old open batches must
    # not make a subsequently completed scene appear outstanding again.
    latest = {}
    batches = db.scalars(select(Batch).where(Batch.archived.is_(False)).order_by(Batch.created_at.desc(),Batch.id)).all()
    for batch in batches:
        for scene in review_scenes(batch.id,db,include_cluster_ids=False):
            latest.setdefault(scene['scene_id'],scene['complete'])
    return [sid for sid,complete in latest.items() if not complete]


@app.get("/api/v1/scenes", response_model=SceneList, tags=["scenes"])
def list_scenes(
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
    query: str | None = None, processed: Literal["all", "processed", "unprocessed"] = "all",
    date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
    awaiting_review: bool = False, review_batch: str | None = None,
) -> SceneList:
    statement = filter_scenes(select(StashScene), query, processed, date_from, date_to)
    count_statement = filter_scenes(select(func.count()).select_from(StashScene), query, processed, date_from, date_to)
    review_ids = workflow_review_ids(db,awaiting_review,review_batch)
    if review_ids is not None:
        statement = statement.where(StashScene.id.in_(review_ids))
        count_statement = count_statement.where(StashScene.id.in_(review_ids))
    total = db.scalar(count_statement) or 0
    scenes = db.scalars(
        statement.order_by(StashScene.scene_date.desc().nullslast(), StashScene.stash_scene_id)
        .offset((page - 1) * per_page).limit(per_page)
    ).all()
    return SceneList(
        items=scene_responses(db, scenes), total=total, page=page, per_page=per_page,
    )


@app.get("/api/v1/scenes/ids", response_model=SceneIdList, tags=["scenes"])
def list_scene_ids(
    query: str | None = None, processed: Literal["all", "processed", "unprocessed"] = "all",
    date_from: date | None = None, date_to: date | None = None,
    limit: int = Query(10000, ge=1, le=10000), db: Session = Depends(get_db),
    awaiting_review: bool = False, review_batch: str | None = None,
) -> SceneIdList:
    statement = filter_scenes(select(StashScene.id), query, processed, date_from, date_to)
    count_statement = filter_scenes(select(func.count()).select_from(StashScene), query, processed, date_from, date_to)
    review_ids = workflow_review_ids(db,awaiting_review,review_batch)
    if review_ids is not None:
        statement = statement.where(StashScene.id.in_(review_ids))
        count_statement = count_statement.where(StashScene.id.in_(review_ids))
    ids = list(db.scalars(statement.order_by(StashScene.scene_date.desc().nullslast()).limit(limit)))
    return SceneIdList(ids=ids, total=db.scalar(count_statement) or 0)


@app.get("/api/v1/scenes/{scene_id}/thumbnail", tags=["scenes"])
def scene_thumbnail(
    scene_id: str, db: Session = Depends(get_db), config: Settings = Depends(get_settings),
) -> Response:
    scene = db.get(StashScene, scene_id)
    if scene is None or not scene.thumbnail_url:
        raise HTTPException(status_code=404, detail="Scene thumbnail not available")
    try:
        with stash_client(config) as client:
            content, content_type = client.fetch_asset(scene.thumbnail_url)
    except StashError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/v1/batches", response_model=BatchResponse, status_code=status.HTTP_201_CREATED, tags=["batches"])
def create_batch(payload: BatchCreate, db: Session = Depends(get_db)) -> BatchResponse:
    unique_ids = list(dict.fromkeys(payload.scene_ids))
    scenes = db.scalars(select(StashScene).where(StashScene.id.in_(unique_ids))).all()
    if len(scenes) != len(unique_ids):
        found = {scene.id for scene in scenes}
        missing = [scene_id for scene_id in unique_ids if scene_id not in found]
        raise HTTPException(status_code=422, detail={"message": "Unknown scene IDs", "scene_ids": missing})
    batch = Batch(
        name=payload.name, description=payload.description, state="created",
        selection={"type": "scene_ids", "scene_ids": unique_ids},
    )
    batch.scenes = [BatchScene(scene_id=scene.id) for scene in scenes]
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch_response(batch)


@app.get("/api/v1/batches", response_model=list[BatchResponse], tags=["batches"])
def list_batches(include_archived: bool = False, db: Session = Depends(get_db)) -> list[BatchResponse]:
    batches = db.scalars(select(Batch).where(True if include_archived else Batch.archived.is_(False)).order_by(Batch.created_at.desc())).unique().all()
    return [batch_response(batch) for batch in batches]


@app.get("/api/v1/batches/{batch_id}", response_model=BatchResponse, tags=["batches"])
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> BatchResponse:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch_response(batch)


@app.post('/api/v1/batches/{batch_id}/archive', tags=['batches'])
def archive_batch(batch_id: str, archived: bool = True, db: Session = Depends(get_db)):
    batch = db.scalar(select(Batch).where(Batch.id==batch_id).with_for_update())
    if batch is None:
        raise HTTPException(404,'Batch not found')
    if batch.state in {'queued','processing','committing'} or db.scalar(select(ProcessingJob.id).where(
            ProcessingJob.batch_id==batch_id,ProcessingJob.state.in_(['queued','processing'])).limit(1)):
        raise HTTPException(409,'Wait for active processing to finish before archiving or restoring')
    batch.archived = archived
    db.commit()
    return batch_response(batch)


@app.delete("/api/v1/batches/{batch_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["batches"])
def delete_batch(batch_id: str, db: Session = Depends(get_db)) -> Response:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.state in {"queued", "processing", "committing"}:
        raise HTTPException(status_code=409, detail="Active batches cannot be deleted")
    if batch.state == "committed":
        raise HTTPException(status_code=409, detail="Committed batches are retained for audit")
    from app.models import ReviewCluster, SceneReview, SceneRedetection
    if db.scalar(select(SceneRedetection.id).where(SceneRedetection.batch_id == batch_id).limit(1)) or db.scalar(select(ReviewCluster.id).where(ReviewCluster.batch_id == batch_id).limit(1)) or db.scalar(select(SceneReview.batch_id).where(SceneReview.batch_id == batch_id).limit(1)):
        raise HTTPException(status_code=409, detail="Batches with review history are retained to preserve identity decisions")
    db.delete(batch)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/v1/batches/{batch_id}/process", response_model=ProcessingJobResponse,
    status_code=status.HTTP_202_ACCEPTED, tags=["batches"],
)
def start_batch_processing(
    batch_id: str, db: Session = Depends(get_db), config: Settings = Depends(get_settings),
) -> ProcessingJob:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    active = db.scalar(select(ProcessingJob).where(
        ProcessingJob.batch_id == batch.id, ProcessingJob.state.in_(["queued", "processing"]),
    ))
    if active:
        raise HTTPException(status_code=409, detail="This batch is already queued or processing")
    job = ProcessingJob(
        batch_id=batch.id, job_type="video_processing", state="queued",
        progress_total=len(batch.scenes), payload={"batch_id": batch.id},
    )
    batch.state = "queued"
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        queue = Queue(connection=Redis.from_url(config.redis_url))
        queue.enqueue(
            "app.processing.process_batch", batch.id, job.id,
            job_timeout=config.processing_job_timeout_seconds,
            retry=Retry(max=2, interval=[30, 120]), job_id=f"video-processing-{job.id}",
        )
    except Exception as exc:
        job.state = "failed"
        job.error = f"Unable to queue processing: {exc}"
        batch.state = "failed"
        db.commit()
        raise HTTPException(status_code=503, detail=job.error) from exc
    return job


@app.get("/api/v1/jobs/{job_id}", response_model=ProcessingJobResponse, tags=["jobs"])
def get_processing_job(job_id: str, db: Session = Depends(get_db)) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found")
    return job


@app.get("/api/v1/batches/{batch_id}/frames", response_model=list[FrameResponse], tags=["batches"])
def list_batch_frames(
    batch_id: str, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
    scene_id: str | None = Query(default=None), db: Session = Depends(get_db),
) -> list[FrameResponse]:
    if db.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    rows = db.execute(
        select(Frame, StashScene).select_from(Frame)
        .join(BatchFrame, BatchFrame.frame_id == Frame.id)
        .join(StashScene, StashScene.id == Frame.scene_id)
        .where(BatchFrame.batch_id == batch_id, *([Frame.scene_id == scene_id] if scene_id else [])).order_by(Frame.scene_id, Frame.timestamp_ms, Frame.id)
        .offset(offset).limit(limit)
    ).all()
    return [
        FrameResponse(
            id=frame.id, scene_id=frame.scene_id, timestamp_seconds=frame.timestamp_ms / 1000,
            url=f"/generated/{frame.relative_path}", width=frame.width, height=frame.height,
            scene_title=scene.title or Path(scene.source_path).name,
            scene_duration_seconds=scene.duration_seconds,
            scene_thumbnail_url=f"/api/v1/scenes/{scene.id}/thumbnail" if scene.thumbnail_url else None,
            video_url=f"/api/v1/scenes/{scene.id}/video",
        )
        for frame, scene in rows
    ]


@app.get("/api/v1/scenes/{scene_id}/video", tags=["scenes"])
def scene_video(
    scene_id: str, db: Session = Depends(get_db), config: Settings = Depends(get_settings),
) -> FileResponse:
    scene = db.get(StashScene, scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    path = Path(scene.source_path).resolve()
    if not path.is_relative_to(Path(config.media_path_prefix).resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Scene media file is not available")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.post("/api/v1/batches/{batch_id}/detect-faces", response_model=ProcessingJobResponse,
          status_code=status.HTTP_202_ACCEPTED, tags=["faces"])
def start_face_detection(batch_id: str, db: Session = Depends(get_db), config: Settings = Depends(get_settings)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.state not in {"video_processing_complete", "failed"} or not batch.frames:
        raise HTTPException(status_code=409, detail="Finish frame processing before detecting faces")
    active = db.scalar(select(ProcessingJob).where(
        ProcessingJob.batch_id == batch.id, ProcessingJob.state.in_(["queued", "processing"])))
    if active:
        raise HTTPException(status_code=409, detail="Batch already has an active job")
    job = ProcessingJob(batch_id=batch.id, job_type="face_detection", state="queued",
        progress_total=len(batch.frames), payload={"previous_state": batch.state, "detection_threshold": read_preferences(config).detection_threshold})
    batch.state = "queued"
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        Queue(connection=Redis.from_url(config.redis_url)).enqueue(
            "app.faces.process_faces", batch.id, job.id, job_id=f"face-detection-{job.id}",
            job_timeout=config.processing_job_timeout_seconds)
    except Exception as exc:
        batch.state = job.payload["previous_state"]
        job.state = "failed"
        job.error = f"Unable to queue face detection: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail=job.error) from exc
    return job


@app.get("/api/v1/batches/{batch_id}/faces", tags=["faces"])
def list_faces(batch_id: str, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    if db.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    rows = db.execute(select(FaceObservation, Frame, StashScene).select_from(FaceObservation)
        .join(Frame, Frame.id == FaceObservation.frame_id)
        .join(BatchFrame, BatchFrame.frame_id == Frame.id)
        .join(StashScene, StashScene.id == Frame.scene_id)
        .where(BatchFrame.batch_id == batch_id, FaceObservation.model_key == MODEL_KEY)
        .order_by(Frame.scene_id, Frame.timestamp_ms, FaceObservation.id).offset(offset).limit(limit)).all()
    return [{"id": face.id, "scene_id": scene.id, "scene_title": scene.title or Path(scene.source_path).name,
        "timestamp_seconds": frame.timestamp_ms / 1000, "url": f"/generated/{face.relative_path}",
        "video_url": f"/api/v1/scenes/{scene.id}/video", "bbox": face.bbox, "landmarks": face.landmarks,
        "detector_confidence": face.detector_confidence, "quality": face.quality,
        "quality_details": face.quality_details, "usable": face.usable, "model_key": face.model_key}
        for face, frame, scene in rows]


@app.post("/api/v1/batches/{batch_id}/group-faces", response_model=ProcessingJobResponse,
          status_code=status.HTTP_202_ACCEPTED, tags=["faces"])
def start_face_grouping(batch_id: str, db: Session = Depends(get_db), config: Settings = Depends(get_settings)):
    from app.models import FaceAnalysis
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.state not in {"video_processing_complete", "failed"} or not batch.frames:
        raise HTTPException(status_code=409, detail="Finish face detection before grouping")
    active = db.scalar(select(ProcessingJob).where(ProcessingJob.batch_id == batch.id,
                                                  ProcessingJob.state.in_(["queued", "processing"])))
    if active:
        raise HTTPException(status_code=409, detail="Batch already has an active job")
    analysed = db.scalar(select(func.count()).select_from(FaceAnalysis)
        .join(BatchFrame, BatchFrame.frame_id == FaceAnalysis.frame_id)
        .where(BatchFrame.batch_id == batch_id, FaceAnalysis.model_key == MODEL_KEY))
    if analysed != len(batch.frames):
        raise HTTPException(status_code=409, detail="Run Detect faces on all batch frames before grouping")
    job = ProcessingJob(batch_id=batch.id, job_type="face_grouping", state="queued",
                        payload={"previous_state": batch.state})
    batch.state = "queued"
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        Queue(connection=Redis.from_url(config.redis_url)).enqueue(
            "app.grouping.process_groups", batch.id, job.id, job_id=f"face-grouping-{job.id}",
            job_timeout=config.processing_job_timeout_seconds)
    except Exception as exc:
        batch.state = job.payload["previous_state"]
        job.state, job.error = "failed", f"Unable to queue face grouping: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail=job.error) from exc
    return job


@app.get('/api/v1/batches/{batch_id}/stages', tags=['batches'])
def batch_stages(batch_id: str, db: Session = Depends(get_db)):
    from app.models import FaceAnalysis
    from app.review import review_scenes
    if db.get(Batch,batch_id) is None:
        raise HTTPException(404,'Batch not found')
    linked = db.scalars(select(BatchScene).where(BatchScene.batch_id==batch_id)).all()
    frame_ids = set(db.scalars(select(BatchFrame.frame_id).where(BatchFrame.batch_id==batch_id)))
    analysed = set(db.scalars(select(FaceAnalysis.frame_id).where(FaceAnalysis.frame_id.in_(frame_ids),FaceAnalysis.model_key==MODEL_KEY)))
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.batch_id==batch_id).order_by(ProcessingJob.created_at.desc())).all()
    latest = {}
    for job in jobs:
        latest.setdefault(job.job_type,job.state)
    frames_done = bool(linked) and all(s.state=='complete' for s in linked) and latest.get('video_processing') not in {'queued','processing','failed'}
    faces_done = frames_done and bool(frame_ids) and frame_ids <= analysed and latest.get('face_detection') not in {'queued','processing','failed'}
    groups = list_face_groups(batch_id,db)
    groups_done = faces_done and latest.get('face_grouping')=='complete' and all(g['status']=='ready' for g in groups)
    scenes = review_scenes(batch_id,db)
    return {'frames':frames_done,'detection':faces_done,'grouping':groups_done,
            'review':bool(scenes) and all(s['complete'] for s in scenes)}


@app.get("/api/v1/batches/{batch_id}/face-groups", tags=["faces"])
def list_face_groups(batch_id: str, db: Session = Depends(get_db)):
    from app.grouping import ALGORITHM_KEY, MODEL_KEY as EMBEDDING_KEY, input_key, scene_faces
    from app.models import FaceGrouping
    if db.get(Batch, batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    by_scene = {}
    for face, frame in scene_faces(db, batch_id):
        by_scene.setdefault(frame.scene_id, []).append(face)
    results = []
    for scene_id, faces in by_scene.items():
        saved = db.get(FaceGrouping, (batch_id, scene_id))
        ready = bool(saved and saved.input_key == input_key(faces) and saved.model_key == EMBEDDING_KEY
                     and saved.algorithm_key == ALGORITHM_KEY)
        results.append({"scene_id": scene_id, "status": "ready" if ready else "stale" if saved else "not_grouped",
                        "groups": saved.groups if ready else [],
                        "diagnostics": saved.diagnostics if ready else {},
                        "excluded_face_ids": [face.id for face in faces if not face.usable],
                        "model_key": EMBEDDING_KEY, "algorithm_key": ALGORITHM_KEY})
    return results


app.include_router(review_router)
app.include_router(scene_suggestions_router)
app.include_router(redetection_router)
app.include_router(stash_export_router)


@app.get("/api/v1/preferences", tags=["settings"])
def preferences(config: Settings = Depends(get_settings)):
    return read_preferences(config)


@app.post("/api/v1/preferences", tags=["settings"])
def update_preferences(payload: Preferences, config: Settings = Depends(get_settings)):
    return save_preferences(payload, config)


@app.get("/api/v1/scenes/{scene_id}/stash-video", tags=["scenes"])
def stash_video(scene_id: str, request: Request, db: Session = Depends(get_db),
                config: Settings = Depends(get_settings)):
    from urllib.parse import urlsplit
    import httpx
    scene = db.get(StashScene, scene_id)
    if scene is None:
        raise HTTPException(404, "Scene not found")
    if not config.stash_url or not read_preferences(config).stash_playback_fallback:
        raise HTTPException(404, "Stash playback fallback is disabled")
    client = StashClient(config.stash_url, config.stash_api_key, timeout=120)
    try:
        data = client._graphql('query($id:ID!){findScene(id:$id){sceneStreams{url mime_type label}}}',
                               {"id": scene.stash_scene_id})
        streams = (data.get('findScene') or {}).get('sceneStreams') or []
        choices = [item for item in streams if item['mime_type'] == 'video/mp4']
        if not choices:
            raise ValueError('No compatible stream')
        chosen = next((item for item in choices if '720p' in item['label']), choices[0])
        url = urlsplit(chosen['url'])
        # Only the configured Stash host is contacted; signed queries remain server-side.
        if not url.path.startswith('/scene/') or url.path.startswith('//'):
            raise ValueError('Invalid stream path')
        headers = {key: request.headers[key] for key in ('range', 'if-range') if key in request.headers}
        upstream = client.client.send(client.client.build_request('GET',
            url.path + ('?' + url.query if url.query else ''), headers=headers), stream=True)
        upstream.raise_for_status()
    except (StashError, httpx.HTTPError, ValueError, KeyError):
        client.close()
        raise HTTPException(502, "Stash playback is unavailable") from None
    def chunks():
        try:
            yield from upstream.iter_raw()
        finally:
            upstream.close()
            client.close()
    headers = {key: upstream.headers[key] for key in
               ('content-length', 'content-range', 'accept-ranges') if key in upstream.headers}
    return StreamingResponse(chunks(), status_code=upstream.status_code,
                             media_type=upstream.headers.get('content-type', 'video/mp4'), headers=headers)
