"""Manual review snapshots, identity decisions, and explicit match suggestions."""
import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.faces import MODEL_KEY as DETECTOR_KEY
from app.appearance import enrich_items
from app.config import get_settings
from app.grouping import ALGORITHM_KEY, MODEL_KEY, input_key, scene_faces, cluster_faces
from app.models import (Batch, BatchScene, SceneReview, FaceEmbedding, FaceGrouping, Person, RejectedSuggestion,
                        ReviewCluster, ReviewDecision, ReviewEvidence, StashScene, ScenePerson)

router = APIRouter(prefix="/api/v1/review", tags=["review"])


class PersonInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a person's name")
        return value


class DecisionInput(BaseModel):
    revision: int = Field(ge=1)
    person_id: str | None = None
    face_ids: list[str] | None = Field(default=None, min_length=1)
    all_faces: bool = False
    allow_same_frame: bool = False
    state: Literal["pending", "unresolved"] = "pending"


class SplitInput(BaseModel):
    revision: int = Field(ge=1)
    face_ids: list[str] = Field(min_length=1)
    separate_each: bool = False


class MergeInput(BaseModel):
    revisions: dict[str, int]


def fail(message, code=409):
    raise HTTPException(status_code=code, detail=message)


def lock_batch(db, batch_id):
    batch = db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        fail("Batch not found", 404)
    return batch


def snapshot(cluster):
    return {"id": cluster.id, "member_ids": list(cluster.member_ids), "person_id": cluster.person_id,
            "state": cluster.state, "active": cluster.active, "revision": cluster.revision,
            "confirmed_ids": list(cluster.confirmed_ids or [])}


def audit(db, cluster, action, details):
    db.add(ReviewDecision(cluster_id=cluster.id if cluster else None, action=action, details=details))


def editable(db, cluster_id, revision):
    cluster = db.get(ReviewCluster, cluster_id)
    if cluster is None:
        fail("Review cluster not found", 404)
    lock_batch(db, cluster.batch_id)
    db.refresh(cluster)
    if not cluster.active:
        fail("This cluster was replaced by a merge; refresh review")
    if cluster.revision != revision:
        fail("This cluster changed in another view; refresh before editing")
    return cluster


def evidence_data(face):
    return {"id": face.id, "scene_id": face.scene_id, "timestamp_seconds": face.timestamp_ms / 1000,
            "url": f"/generated/{face.relative_path}", "quality": face.quality,
            "video_url": f"/api/v1/scenes/{face.scene_id}/video", "has_embedding": face.vector is not None}


@router.get("/evidence/{face_id}/frame")
def evidence_frame(face_id: str, db: Session = Depends(get_db)):
    from app.models import Frame, FaceObservation
    from app.config import get_settings

    face = db.get(ReviewEvidence, face_id)
    if face is None:
        fail("Face not found", 404)
    frame = db.get(Frame, face.frame_id)
    original = db.get(FaceObservation, face_id)
    bbox = face.bbox or (original.bbox if original else None)
    if frame is None or not (get_settings().appdata_path / frame.relative_path).is_file():
        fail("The original extracted frame is no longer available", 404)
    if not bbox or len(bbox) != 4 or bbox[2] <= 0 or bbox[3] <= 0:
        fail("The location of this face is unavailable", 409)
    return {"url": f"/generated/{frame.relative_path}", "bbox": bbox}


def cluster_data(db, cluster):
    scene = db.get(StashScene, cluster.scene_id)
    faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(cluster.member_ids))).all()
    faces.sort(key=lambda face: (face.timestamp_ms, face.id))
    person = db.get(Person, cluster.person_id) if cluster.person_id else None
    return {**snapshot(cluster), "batch_id": cluster.batch_id, "scene_id": cluster.scene_id,
            "scene_title": scene.title or Path(scene.source_path).name,
            "person_name": person.name if person else None, "faces": [evidence_data(face) for face in faces]}


@router.post("/batches/{batch_id}/prepare")
def prepare_review(batch_id: str, db: Session = Depends(get_db)):
    batch = lock_batch(db, batch_id)
    if batch.state in {"queued", "processing", "clustering", "committing"}:
        fail("Wait for processing to finish before importing groups")
    existing = db.scalars(select(ReviewCluster).where(ReviewCluster.batch_id == batch_id)).all()
    claimed = {face_id for cluster in existing for face_id in cluster.member_ids}
    by_scene = {}
    for face, frame in scene_faces(db, batch_id):
        by_scene.setdefault(frame.scene_id, []).append((face, frame))
    created = 0
    stale = 0
    for scene_id, rows in by_scene.items():
        grouping = db.get(FaceGrouping, (batch_id, scene_id))
        faces = [face for face, _ in rows]
        if not grouping or grouping.input_key != input_key(faces) or grouping.model_key != MODEL_KEY or grouping.algorithm_key != ALGORITHM_KEY:
            stale += 1
            continue
        for face, _ in rows:
            evidence = db.get(ReviewEvidence, face.id)
            if evidence is not None and not evidence.bbox:
                evidence.bbox = face.bbox
            if evidence is not None and evidence.vector is None:
                embedding = db.get(FaceEmbedding, (face.id, MODEL_KEY))
                if embedding is not None:
                    evidence.vector = np.asarray(embedding.vector).tolist()
                    evidence.model_key = MODEL_KEY
        face_map = {face.id: (face, frame) for face, frame in rows}
        members = [group["face_ids"] for group in grouping.groups]
        in_groups = {face_id for group in members for face_id in group}
        members += [[face.id] for face in faces if face.id not in in_groups]
        for ids in members:
            new_ids = [face_id for face_id in ids if face_id not in claimed and face_id in face_map]
            if not new_ids:
                continue
            for face_id in new_ids:
                face, frame = face_map[face_id]
                if db.get(ReviewEvidence, face_id) is None:
                    embedded = db.get(FaceEmbedding, (face_id, MODEL_KEY))
                    db.add(ReviewEvidence(id=face.id, scene_id=scene_id, frame_id=frame.id,
                        timestamp_ms=frame.timestamp_ms, relative_path=face.relative_path, quality=face.quality, bbox=face.bbox,
                        vector=np.asarray(embedded.vector).tolist() if embedded else None,
                        model_key=MODEL_KEY if embedded else None))
            cluster = ReviewCluster(batch_id=batch_id, scene_id=scene_id, member_ids=new_ids)
            db.add(cluster)
            db.flush()
            audit(db, cluster, "import", {"after": snapshot(cluster), "detector": DETECTOR_KEY,
                                        "embedding": MODEL_KEY, "algorithm": ALGORITHM_KEY})
            claimed.update(new_ids)
            created += 1
    db.commit()
    return {"created": created, "scenes_needing_grouping": stale}


@router.get("/batches/{batch_id}/clusters")
def review_clusters(batch_id: str, scene_ids: list[str] | None = Query(default=None), db: Session = Depends(get_db)):
    if db.get(Batch, batch_id) is None:
        fail("Batch not found", 404)
    statement = select(ReviewCluster).where(ReviewCluster.batch_id == batch_id, ReviewCluster.active.is_(True))
    if scene_ids is not None:
        statement = statement.where(ReviewCluster.scene_id.in_(scene_ids))
    clusters = db.scalars(statement.order_by(ReviewCluster.scene_id, ReviewCluster.created_at, ReviewCluster.id)).all()
    scenes = {scene.id:scene for scene in db.scalars(select(StashScene).where(
        StashScene.id.in_({cluster.scene_id for cluster in clusters})))}
    people = {person.id:person.name for person in db.scalars(select(Person).where(
        Person.id.in_({cluster.person_id for cluster in clusters if cluster.person_id})))}
    ids = {fid for cluster in clusters for fid in cluster.member_ids}
    # Read display metadata in one query, not vectors and one lookup per cluster.
    rows = db.execute(select(ReviewEvidence.id,ReviewEvidence.scene_id,ReviewEvidence.timestamp_ms,
        ReviewEvidence.relative_path,ReviewEvidence.quality,
        ReviewEvidence.vector.is_not(None).label('has_embedding')).where(ReviewEvidence.id.in_(ids))).all()
    faces = {row.id:{'id':row.id,'scene_id':row.scene_id,'timestamp_seconds':row.timestamp_ms/1000,
        'url':f'/generated/{row.relative_path}','quality':row.quality,
        'video_url':f'/api/v1/scenes/{row.scene_id}/video','has_embedding':row.has_embedding} for row in rows}
    return [{**snapshot(cluster),'batch_id':cluster.batch_id,'scene_id':cluster.scene_id,
        'scene_title':scenes[cluster.scene_id].title or Path(scenes[cluster.scene_id].source_path).name,
        'person_name':people.get(cluster.person_id),
        'faces':sorted([faces[fid] for fid in cluster.member_ids if fid in faces],
                       key=lambda face:(face['timestamp_seconds'],face['id']))} for cluster in clusters]


@router.post("/people", status_code=201)
def create_person(payload: PersonInput, db: Session = Depends(get_db)):
    person = Person(name=payload.name, name_key=payload.name.casefold())
    db.add(person)
    try:
        db.flush()
        audit(db, None, "create_person", {"person_id": person.id, "name": person.name})
        db.commit()
    except IntegrityError:
        db.rollback()
        fail("A person with this name already exists")
    return {"id": person.id, "name": person.name}


def confirmed_references(db):
    groups = db.scalars(select(ReviewCluster).where(ReviewCluster.active.is_(True), ReviewCluster.state == "assigned", ReviewCluster.person_id.is_not(None))).all()
    by_person = {}
    for cluster in groups:
        by_person.setdefault(cluster.person_id, set()).update(set(cluster.member_ids).intersection(cluster.confirmed_ids or []))
    return by_person


@router.get("/people")
def list_people(db: Session = Depends(get_db)):
    refs = confirmed_references(db)
    results = []
    for person in db.scalars(select(Person).order_by(Person.name_key)):
        ids = refs.get(person.id, set())
        faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(ids)).order_by(ReviewEvidence.quality.desc())).all() if ids else []
        results.append({"id": person.id, "name": person.name, "reference_count": len(faces),
                        "review_required_count": sum(len(set(c.member_ids) - set(c.confirmed_ids or [])) for c in db.scalars(select(ReviewCluster).where(ReviewCluster.active.is_(True), ReviewCluster.person_id == person.id))),
                        "representatives": [evidence_data(face) for face in faces[:6]]})
    return results


class ReferenceRemoval(BaseModel):
    face_ids: list[str] = Field(min_length=1)
    revisions: dict[str, int]


@router.get("/people/{person_id}/references")
def person_references(person_id: str, db: Session = Depends(get_db)):
    if db.get(Person, person_id) is None:
        fail("Person not found", 404)
    groups = db.scalars(select(ReviewCluster).where(ReviewCluster.active.is_(True),
        ReviewCluster.state == "assigned", ReviewCluster.person_id == person_id)).all()
    by_face = {}
    for cluster in groups:
        batch = db.get(Batch, cluster.batch_id)
        for face_id in set(cluster.member_ids).intersection(cluster.confirmed_ids or []):
            by_face.setdefault(face_id, []).append({"cluster_id": cluster.id, "revision": cluster.revision,
                "batch_id": cluster.batch_id, "batch_name": batch.name})
    faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(by_face))
                       .order_by(ReviewEvidence.quality.desc(), ReviewEvidence.id)).all()
    return [{**evidence_data(face), "confirmations": by_face[face.id]} for face in faces]


@router.post("/people/{person_id}/references/remove")
def remove_person_references(person_id: str, payload: ReferenceRemoval, db: Session = Depends(get_db)):
    if db.get(Person, person_id) is None:
        fail("Person not found", 404)
    selected = set(payload.face_ids)
    # Lock the supplied batches first, in the same order used for cluster edits.
    supplied = db.scalars(select(ReviewCluster).where(ReviewCluster.id.in_(payload.revisions))).all()
    for batch_id in sorted({cluster.batch_id for cluster in supplied}):
        lock_batch(db, batch_id)
    db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(selected))
               .order_by(ReviewEvidence.id).with_for_update()).all()
    groups = db.scalars(select(ReviewCluster).where(ReviewCluster.active.is_(True),
        ReviewCluster.state == "assigned", ReviewCluster.person_id == person_id)
        .execution_options(populate_existing=True)).all()
    affected = [c for c in groups if selected.intersection(c.confirmed_ids or [])]
    if {c.id: c.revision for c in affected} != payload.revisions:
        fail("Reference confirmations changed; reload reference faces before removing them")
    available = {fid for c in affected for fid in c.confirmed_ids or []}
    if not selected <= available:
        fail("Some selected faces are no longer references; reload reference faces")
    for cluster in affected:
        before = snapshot(cluster)
        removed = selected.intersection(cluster.confirmed_ids or [])
        # Move removed references back to pending review, preserving remaining confirmations.
        if removed == set(cluster.member_ids):
            cluster.person_id = None
            cluster.state = "pending"
        else:
            pending = ReviewCluster(batch_id=cluster.batch_id, scene_id=cluster.scene_id,
                                    member_ids=[fid for fid in cluster.member_ids if fid in removed])
            db.add(pending)
            db.flush()
            audit(db, pending, "reference_returned", {"person_id": person_id, "face_ids": list(removed)})
            cluster.member_ids = [fid for fid in cluster.member_ids if fid not in removed]
        cluster.confirmed_ids = [fid for fid in cluster.confirmed_ids or [] if fid not in removed]
        cluster.revision += 1
        audit(db, cluster, "remove_reference", {"person_id": person_id, "face_ids": list(removed),
                                               "before": before, "after": snapshot(cluster)})
    db.commit()
    return {"removed_faces": len(selected), "updated_clusters": len(affected)}


@router.patch("/people/{person_id}")
def rename_person(person_id: str, payload: PersonInput, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person is None:
        fail("Person not found", 404)
    before = person.name
    person.name, person.name_key = payload.name, payload.name.casefold()
    audit(db, None, "rename_person", {"person_id": person.id, "before": before, "after": person.name})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail("A person with this name already exists")
    return {"id": person.id, "name": person.name}


@router.post("/clusters/{cluster_id}/assign")
def assign_person(cluster_id: str, payload: DecisionInput, db: Session = Depends(get_db)):
    cluster = editable(db, cluster_id, payload.revision)
    before = snapshot(cluster)
    if payload.face_ids is not None and payload.all_faces:
        fail("Choose selected faces or all faces, not both", 422)
    if payload.person_id and payload.face_ids is None and not payload.all_faces:
        fail("Select faces to confirm, or explicitly choose all faces", 422)
    selected = set(payload.face_ids) if payload.face_ids is not None else set(cluster.member_ids)
    if not selected <= set(cluster.member_ids):
        fail("Select faces belonging to this cluster", 422)
    if payload.person_id:
        if cluster.state == "ignored":
            fail("Restore ignored faces before confirming", 422)
        if db.scalar(select(Person).where(Person.id==payload.person_id).with_for_update()) is None:
            fail("Person not found", 404)
        selected_faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(selected))).all()
        frame_ids = [face.frame_id for face in selected_faces]
        existing_ids = set(confirmed_references(db).get(payload.person_id, [])) - selected
        existing = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(existing_ids),
                ReviewEvidence.frame_id.in_(frame_ids))).all()
        by_frame = {}
        for face in [*selected_faces, *existing]:
            by_frame.setdefault(face.frame_id, []).append(face)
        conflicts = [faces for faces in by_frame.values() if len(faces) > 1]
        if conflicts and not payload.allow_same_frame:
            fail({'code':'same_frame_conflict',
                  'message':'These faces share a frame. Deselect incorrect faces, or enable the mirror/reflection exception and confirm again.',
                  'conflicts':[{'frame_id':faces[0].frame_id,
                      'faces':[dict(evidence_data(face), already_confirmed=face.id in existing_ids,
                          frame_context_url=f'/api/v1/review/evidence/{face.id}/frame') for face in faces]}
                      for faces in conflicts]},409)
        db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(selected))
                   .order_by(ReviewEvidence.id).with_for_update()).all()
        assigned = db.scalars(select(ReviewCluster).where(ReviewCluster.active.is_(True),
            ReviewCluster.person_id.is_not(None), ReviewCluster.person_id != payload.person_id)).all()
        if any(set(other.confirmed_ids or []).intersection(selected) for other in assigned if other.id != cluster.id):
            fail("Some selected faces are confirmed as another person in another cluster; correct that confirmation first")
    target = cluster
    if selected != set(cluster.member_ids):
        target = ReviewCluster(batch_id=cluster.batch_id, scene_id=cluster.scene_id,
                               member_ids=[fid for fid in cluster.member_ids if fid in selected])
        cluster.member_ids = [fid for fid in cluster.member_ids if fid not in selected]
        cluster.confirmed_ids = [fid for fid in (cluster.confirmed_ids or []) if fid not in selected]
        db.add(target)
    target.person_id = payload.person_id
    target.state = "assigned" if payload.person_id else payload.state
    target.confirmed_ids = list(target.member_ids) if payload.person_id else []
    cluster.revision += 1
    db.flush()
    audit(db, cluster, "confirm_faces" if payload.person_id else payload.state,
          {"before": before, "after": snapshot(cluster), "confirmed": snapshot(target), "allow_same_frame": payload.allow_same_frame})
    db.commit()
    return cluster_data(db, target)


@router.post("/clusters/{cluster_id}/split")
def split_cluster(cluster_id: str, payload: SplitInput, db: Session = Depends(get_db)):
    cluster = editable(db, cluster_id, payload.revision)
    if cluster.state == "ignored":
        fail("Restore ignored faces before splitting", 422)
    selected = set(payload.face_ids)
    if not selected < set(cluster.member_ids):
        fail("Select some, but not all, faces in this cluster", 422)
    before = snapshot(cluster)
    ordered = [face_id for face_id in cluster.member_ids if face_id in selected]
    cluster.member_ids = [face_id for face_id in cluster.member_ids if face_id not in selected]
    cluster.confirmed_ids = [fid for fid in (cluster.confirmed_ids or []) if fid not in selected]
    cluster.revision += 1
    created = []
    for ids in ([[face_id] for face_id in ordered] if payload.separate_each else [ordered]):
        new = ReviewCluster(batch_id=cluster.batch_id, scene_id=cluster.scene_id, member_ids=ids)
        db.add(new)
        db.flush()
        created.append(snapshot(new))
    audit(db, cluster, "remove_faces" if payload.separate_each else "split", {"before": before, "after": snapshot(cluster), "created": created})
    db.commit()
    return {"created": created}


@router.post("/clusters/{cluster_id}/ignore")
def ignore_faces(cluster_id: str, payload: SplitInput, db: Session = Depends(get_db)):
    cluster = editable(db, cluster_id, payload.revision)
    if cluster.state == "ignored":
        fail("Restore ignored faces before splitting", 422)
    selected = set(payload.face_ids)
    if not selected <= set(cluster.member_ids) or cluster.state == "ignored":
        fail("Select faces from a cluster that is not ignored", 422)
    before = snapshot(cluster)
    if selected == set(cluster.member_ids):
        ignored = cluster
    else:
        ignored = ReviewCluster(batch_id=cluster.batch_id, scene_id=cluster.scene_id,
                                member_ids=[face_id for face_id in cluster.member_ids if face_id in selected])
        cluster.member_ids = [face_id for face_id in cluster.member_ids if face_id not in selected]
        db.add(ignored)
    cluster.confirmed_ids = [fid for fid in (cluster.confirmed_ids or []) if fid not in selected]
    ignored.confirmed_ids = []
    ignored.person_id = None
    ignored.state = "ignored"
    cluster.revision += 1
    db.flush()
    audit(db, cluster, "ignore_faces", {"before": before, "after": snapshot(cluster), "ignored": snapshot(ignored)})
    db.commit()
    return cluster_data(db, ignored)


@router.post("/clusters/merge")
def merge_clusters(payload: MergeInput, db: Session = Depends(get_db)):
    if len(payload.revisions) < 2:
        fail("Select at least two clusters", 422)
    clusters = db.scalars(select(ReviewCluster).where(ReviewCluster.id.in_(payload.revisions))).all()
    if len(clusters) != len(payload.revisions):
        fail("Cluster not found", 404)
    if len({(cluster.batch_id, cluster.scene_id) for cluster in clusters}) != 1:
        fail("Only clusters from the same video and batch can be merged", 422)
    clusters = [editable(db, cluster.id, payload.revisions[cluster.id]) for cluster in clusters]
    if any(cluster.state == "ignored" for cluster in clusters):
        fail("Restore ignored faces before merging", 422)
    before = [snapshot(cluster) for cluster in clusters]
    members = list(dict.fromkeys(face_id for cluster in clusters for face_id in cluster.member_ids))
    for cluster in clusters:
        cluster.active = False
        cluster.revision += 1
    merged = ReviewCluster(batch_id=clusters[0].batch_id, scene_id=clusters[0].scene_id, member_ids=members)
    db.add(merged)
    db.flush()
    audit(db, merged, "merge", {"before": before, "after": snapshot(merged)})
    db.commit()
    return cluster_data(db, merged)


@router.post("/clusters/{cluster_id}/reject/{person_id}")
def reject_suggestion(cluster_id: str, person_id: str, payload: DecisionInput, db: Session = Depends(get_db)):
    cluster = editable(db, cluster_id, payload.revision)
    if db.get(Person, person_id) is None:
        fail("Person not found", 404)
    if db.get(RejectedSuggestion, (cluster_id, person_id)) is None:
        db.add(RejectedSuggestion(cluster_id=cluster_id, person_id=person_id))
    cluster.revision += 1
    audit(db, cluster, "reject_suggestion", {"person_id": person_id})
    db.commit()
    return {"status": "rejected"}


def suggestion_reference_context(db, cluster):
    rejected = set(db.scalars(select(RejectedSuggestion.person_id).where(RejectedSuggestion.cluster_id==cluster.id)))
    references = confirmed_references(db)
    all_ids = {fid for ids in references.values() for fid in ids}
    evidence = {face.id:face for face in db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(all_ids)))}
    names = {person.id:person.name for person in db.scalars(select(Person).where(Person.id.in_(references)))}
    return rejected,references,evidence,names


def ranked_person_matches(db, cluster, queries, selected_faces, min_similarity, include_rejected, context=None, allow_same_frame=False):
    """Prefer credible same-scene references, without changing face cosine scores."""
    if not queries:
        return []
    selected_ids = {face.id for face in selected_faces}
    selected_frames = [face.frame_id for face in selected_faces]
    if not allow_same_frame and len(set(selected_frames)) != len(selected_frames):
        return []  # One person cannot occupy two observations in the same frame.
    rejected,references,evidence,names = context or suggestion_reference_context(db,cluster)
    q = np.asarray([face.vector for face in queries],dtype=np.float32)
    result = []
    for pid, ids in references.items():
        if pid in rejected and not include_rejected:
            continue
        refs = [evidence[fid] for fid in sorted(ids) if fid in evidence and fid not in selected_ids]
        if not allow_same_frame and any(face.frame_id in selected_frames for face in refs):
            continue
        refs = [face for face in refs if face.vector is not None and face.model_key==MODEL_KEY]
        if not refs:
            continue
        local = [face for face in refs if face.scene_id==cluster.scene_id]
        local_matrix = q @ np.asarray([face.vector for face in local],dtype=np.float32).T if local else None
        local_score = float(np.median(np.max(local_matrix,axis=1))) if local else -1.0
        # Weak crops lose useful cosine evidence. Offer a lower-confidence local
        # match for manual review; do not modify its raw similarity or auto-confirm.
        low_quality = float(np.median([face.quality for face in queries])) < 0.50
        local_threshold = 0.30 if low_quality else 0.50
        prefer_local = local_score >= max(local_threshold,min_similarity)
        if prefer_local:
            refs, matrix = local, local_matrix
        else:
            matrix = q @ np.asarray([face.vector for face in refs],dtype=np.float32).T
        score = float(np.clip(np.median(np.max(matrix,axis=1)),-1,1))
        if score < min_similarity:
            continue
        ranked = np.argsort(np.max(matrix,axis=0))[::-1][:3]
        best_query = int(np.argmax(matrix[:,int(ranked[0])]))
        result.append({'person_id':pid,'person_name':names[pid],
            'similarity':round(score,4),'previously_rejected':pid in rejected,'reference_count':len(refs),
            'reference_source':'current_scene' if prefer_local else 'confirmed_people',
            'scene_priority':prefer_local,
            'has_scene_reference':bool(local),
            'weak_match':score < 0.50,
            'local_reference':evidence_data(local[int(np.argmax(np.max(local_matrix,axis=0)))]) if local else None,
            'query_face':evidence_data(queries[best_query]),
            'representatives':[evidence_data(refs[int(i)]) for i in ranked]})
    ordered = sorted(result,key=lambda item:(not item['scene_priority'],-item['similarity'],item['person_id']))
    top = ordered[:3]
    local_candidate = next((item for item in ordered if item['has_scene_reference']),None)
    if local_candidate is not None and local_candidate not in top:
        top = top[:2] + [local_candidate]
    return top


@router.get("/clusters/{cluster_id}/suggestions")
def suggestions(cluster_id: str, face_ids: list[str] | None = Query(default=None), min_similarity: float = Query(-1.0, ge=-1, le=1), include_rejected: bool = False, allow_same_frame: bool = False, db: Session = Depends(get_db)):
    cluster = db.get(ReviewCluster,cluster_id)
    if cluster is None or not cluster.active:
        fail('Review cluster not found',404)
    if cluster.state=='ignored':
        return []
    selected = set(face_ids) if face_ids else set(cluster.member_ids)
    if not selected <= set(cluster.member_ids):
        fail('Select faces belonging to this cluster',422)
    faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(selected)).order_by(ReviewEvidence.id)).all()
    queries = [face for face in faces if face.vector is not None and face.model_key==MODEL_KEY]
    return ranked_person_matches(db,cluster,queries,faces,min_similarity,include_rejected,allow_same_frame=allow_same_frame)


@router.get("/clusters/{cluster_id}/face-suggestions")
def face_suggestions(cluster_id: str, min_similarity: float = Query(-1.0, ge=-1, le=1), include_rejected: bool = False, allow_same_frame: bool = False, db: Session = Depends(get_db)):
    cluster = db.get(ReviewCluster,cluster_id)
    if cluster is None or not cluster.active:
        fail('Review cluster not found',404)
    faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(cluster.member_ids))
        .order_by(ReviewEvidence.timestamp_ms,ReviewEvidence.id)).all()
    context = suggestion_reference_context(db,cluster)
    return [{'face_id':face.id,'has_embedding':face.vector is not None and face.model_key==MODEL_KEY,
        'candidates':ranked_person_matches(db,cluster,[face],[face],min_similarity,include_rejected,context,allow_same_frame=allow_same_frame)
        if face.vector is not None and face.model_key==MODEL_KEY and cluster.state!='ignored' else []} for face in faces]


class SceneDecision(BaseModel):
    revision: int = Field(ge=0)
    fingerprint: str
    complete: bool


@router.get("/batches/{batch_id}/scenes")
def review_scenes(batch_id: str, db: Session = Depends(get_db), include_cluster_ids: bool = True):
    batch = db.get(Batch, batch_id)
    if batch is None:
        fail("Batch not found", 404)
    groups = db.scalars(select(ReviewCluster).where(ReviewCluster.batch_id == batch_id,
                                                   ReviewCluster.active.is_(True))).all()
    linked = db.scalars(select(BatchScene).where(BatchScene.batch_id == batch_id)).all()
    by_scene = {row.scene_id: [] for row in linked}
    for cluster in groups:
        by_scene.setdefault(cluster.scene_id, []).append(cluster)
    from app.models import BatchFrame, FaceObservation, Frame
    observations = {}
    rows = db.execute(select(FaceObservation.id,Frame.scene_id).join(Frame,Frame.id==FaceObservation.frame_id)
        .join(BatchFrame,BatchFrame.frame_id==Frame.id).where(BatchFrame.batch_id==batch_id,
            FaceObservation.model_key==DETECTOR_KEY)).all()
    for face_id,scene_id in rows:
        observations.setdefault(scene_id,set()).add(face_id)
        by_scene.setdefault(scene_id,[])
    scenes = {s.id:s for s in db.scalars(select(StashScene).where(StashScene.id.in_(by_scene)))}
    manual = {}
    for row in db.scalars(select(ScenePerson).where(ScenePerson.scene_id.in_(by_scene))):
        manual.setdefault(row.scene_id,[]).append(row.person_id)
    names = {p.id:p.name for p in db.scalars(select(Person))}
    saved_reviews = {r.scene_id:r for r in db.scalars(select(SceneReview).where(SceneReview.batch_id==batch_id))}
    result = []
    for scene_id, clusters in by_scene.items():
        scene = scenes[scene_id]
        identified, unknown, ignored = set(), set(), set()
        people = set()
        for cluster in clusters:
            confirmed = set(cluster.confirmed_ids or []) if cluster.person_id else set()
            if cluster.state == "ignored":
                ignored.update(cluster.member_ids)
            else:
                identified.update(set(cluster.member_ids) & confirmed)
                unknown.update(set(cluster.member_ids) - confirmed)
                if confirmed:
                    people.add(cluster.person_id)
        manual_ids = sorted(manual.get(scene_id,[]))
        people.update(manual_ids)
        claimed = {fid for cluster in clusters for fid in cluster.member_ids}
        unimported = observations.get(scene_id, set()) - claimed
        digest = hashlib.sha256(json.dumps({"clusters": sorted([snapshot(c) for c in clusters], key=lambda c:c["id"]),
            "unimported": sorted(unimported)}, sort_keys=True).encode()).hexdigest()
        if manual_ids:
            digest = hashlib.sha256(json.dumps([digest,manual_ids]).encode()).hexdigest()
        saved = saved_reviews.get(scene_id)
        complete = bool(saved and saved.complete and saved.fingerprint == digest)
        result.append({"scene_id": scene_id, "title": scene.title or Path(scene.source_path).name,
            "identified_count": len(identified), "unknown_count": len(unknown), "ignored_count": len(ignored),
            "unimported_count": len(unimported), "people": [names[pid] for pid in sorted(people)],
            "manual_people": [{"id":pid,"name":names[pid]} for pid in manual_ids],
            "cluster_ids": [c.id for c in clusters] if include_cluster_ids else [], "complete": complete,
            "changed_since_completion": bool(saved and saved.complete and not complete),
            "revision": saved.revision if saved else 0, "fingerprint": digest,
            "video_url": f"/api/v1/scenes/{scene_id}/video"})
    return sorted(result, key=lambda row: (row["title"], row["scene_id"]))


class ManualPersonInput(BaseModel):
    person_id: str
    fingerprint: str
    revision: int = Field(ge=0)
    present: bool = True


@router.post('/batches/{batch_id}/scenes/{scene_id}/manual-person')
def set_manual_person(batch_id: str, scene_id: str, payload: ManualPersonInput, db: Session = Depends(get_db)):
    lock_batch(db,batch_id)
    db.scalar(select(StashScene).where(StashScene.id == scene_id).with_for_update())
    scene = next((s for s in review_scenes(batch_id,db) if s['scene_id'] == scene_id),None)
    if scene is None:
        fail('Scene not found in this batch',404)
    if scene['fingerprint'] != payload.fingerprint or scene['revision'] != payload.revision:
        fail('Scene changed; refresh before changing manual people')
    if db.get(Person,payload.person_id) is None:
        fail('Person not found',404)
    saved = db.get(ScenePerson,(scene_id,payload.person_id))
    if payload.present and saved is None:
        db.add(ScenePerson(scene_id=scene_id,person_id=payload.person_id))
    elif not payload.present and saved is not None:
        db.delete(saved)
    audit(db,None,'manual_scene_person',{'batch_id':batch_id,'scene_id':scene_id,
        'person_id':payload.person_id,'present':payload.present})
    db.commit()
    return {'present':payload.present}


class SceneRegroup(BaseModel):
    revision: int = Field(ge=0)
    fingerprint: str
    threshold: float = Field(default=0.50, ge=0.10, le=0.50)


@router.post("/batches/{batch_id}/scenes/{scene_id}/regroup")
def regroup_scene(batch_id: str, scene_id: str, payload: SceneRegroup, db: Session = Depends(get_db)):
    batch = lock_batch(db, batch_id)
    if batch.state in {"queued", "processing", "clustering", "committing"}:
        fail("Wait for processing to finish before regrouping")
    scene = next((row for row in review_scenes(batch_id, db) if row["scene_id"] == scene_id), None)
    if scene is None:
        fail("Scene not found in this batch", 404)
    if scene["revision"] != payload.revision or scene["fingerprint"] != payload.fingerprint:
        fail("Scene changed; refresh before regrouping")
    if scene["complete"]:
        fail("Reopen the scene before regrouping")
    existing = db.scalars(select(ReviewCluster).where(ReviewCluster.batch_id == batch_id,
        ReviewCluster.scene_id == scene_id, ReviewCluster.active.is_(True),
        ReviewCluster.state == "pending", ReviewCluster.person_id.is_(None))).all()
    merged_ids = set(db.scalars(select(ReviewDecision.cluster_id).where(ReviewDecision.action=='merge',
        ReviewDecision.cluster_id.in_([c.id for c in existing]))))
    existing = [c for c in existing if not c.confirmed_ids and c.id not in merged_ids]
    ids = {fid for c in existing for fid in c.member_ids}
    faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(ids))).all()
    if len(faces) != len(ids):
        fail("Some review evidence is missing; regrouping was not applied")
    embedded = [face for face in faces if face.vector is not None and face.model_key == MODEL_KEY]
    items = [{"id":face.id,"frame_id":face.frame_id,"quality":face.quality,"vector":face.vector} for face in embedded]
    diagnostics = {}
    appearance_stats = enrich_items(db,items,embedded,get_settings())
    grouped = cluster_faces(items, payload.threshold, diagnostics=diagnostics)
    diagnostics['appearance'] = appearance_stats
    embedded_ids = {face.id for face in embedded}
    grouped += [{"face_ids":[face.id]} for face in faces if face.id not in embedded_ids]
    if not grouped:
        return {"before":0,"after":0,"without_embedding":0}
    before = [snapshot(c) for c in existing]
    for cluster in existing:
        cluster.active = False
        cluster.revision += 1
    for group in grouped:
        db.add(ReviewCluster(batch_id=batch_id,scene_id=scene_id,member_ids=group["face_ids"]))
    audit(db, None, "scene_regrouped", {"batch_id":batch_id,"scene_id":scene_id,"before":before,
        "after_count":len(grouped),"threshold":payload.threshold,"algorithm":ALGORITHM_KEY,"diagnostics":diagnostics})
    db.commit()
    return {"before":len(existing),"after":len(grouped),"without_embedding":len(faces)-len(embedded)}


@router.post("/batches/{batch_id}/scenes/{scene_id}/completion")
def set_scene_completion(batch_id: str, scene_id: str, payload: SceneDecision, db: Session = Depends(get_db)):
    batch = lock_batch(db, batch_id)
    if batch.state in {"queued", "processing", "clustering", "committing"}:
        fail("Wait for processing to finish before completing scene review")
    scene = next((row for row in review_scenes(batch_id, db) if row["scene_id"] == scene_id), None)
    if scene is None:
        fail("Scene not found in this batch", 404)
    if scene["revision"] != payload.revision or scene["fingerprint"] != payload.fingerprint:
        fail("Scene review changed; refresh before marking it complete")
    if payload.complete and scene["unimported_count"]:
        fail("Import the latest face groups before completing this scene")
    saved = db.get(SceneReview, (batch_id, scene_id))
    if saved is None:
        saved = SceneReview(batch_id=batch_id, scene_id=scene_id, revision=0)
        db.add(saved)
    saved.complete, saved.fingerprint = payload.complete, scene["fingerprint"]
    saved.revision += 1
    audit(db, None, "scene_complete" if payload.complete else "scene_reopened",
          {"batch_id": batch_id, "scene_id": scene_id, "unknown_count": scene["unknown_count"]})
    db.commit()
    return {"complete": saved.complete, "revision": saved.revision}


@router.get("/batches/{batch_id}/history")
def review_history(batch_id: str, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.scalars(select(ReviewDecision).outerjoin(ReviewCluster, ReviewDecision.cluster_id == ReviewCluster.id)
                      .where(or_(ReviewCluster.batch_id == batch_id, ReviewDecision.details["batch_id"].as_string() == batch_id)).order_by(ReviewDecision.created_at.desc()).limit(limit)).all()
    return [{"id": row.id, "cluster_id": row.cluster_id, "action": row.action,
             "details": row.details, "created_at": row.created_at} for row in rows]
