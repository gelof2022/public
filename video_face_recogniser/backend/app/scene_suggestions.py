"""Person-presence proposals derived from independent face evidence in a scene."""
import hashlib
import json

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.grouping import MODEL_KEY
from app.models import BatchScene, Person, ReviewCluster, ReviewDecision, ReviewEvidence, ScenePerson
from app.review import (DecisionInput, assign_person, audit, confirmed_references, evidence_data,
                        fail, lock_batch)

router = APIRouter(prefix='/api/v1/review', tags=['review'])


def person_proposals(db, batch_id, scene_id, minimum=None):
    from app.preferences import read_preferences
    if minimum is None:
        minimum = read_preferences().recognition_threshold
    clusters = db.scalars(select(ReviewCluster).where(ReviewCluster.batch_id==batch_id,
        ReviewCluster.scene_id==scene_id, ReviewCluster.active.is_(True))).all()
    if not clusters and db.get(BatchScene,(batch_id,scene_id)) is None:
        fail('Scene not found in this batch',404)
    present = set(db.scalars(select(ScenePerson.person_id).where(ScenePerson.scene_id==scene_id)))
    owners = {}
    for cluster in clusters:
        confirmed = set(cluster.confirmed_ids or []) if cluster.person_id else set()
        if confirmed and cluster.state=='assigned':
            present.add(cluster.person_id)
        if cluster.state not in ('ignored','unresolved'):
            for fid in set(cluster.member_ids)-confirmed:
                owners.setdefault(fid,cluster)
    faces = db.scalars(select(ReviewEvidence).where(ReviewEvidence.id.in_(owners),
        ReviewEvidence.model_key==MODEL_KEY, ReviewEvidence.vector.is_not(None))
        .order_by(ReviewEvidence.id)).all()
    if not faces:
        return []
    # Start every candidate against explicitly confirmed People references. Never
    # promote an unconfirmed suggestion into the reference pool.
    refs = confirmed_references(db)
    ids = {fid for fids in refs.values() for fid in fids}
    evidence = {face.id:face for face in db.scalars(select(ReviewEvidence).where(
        ReviewEvidence.id.in_(ids),ReviewEvidence.model_key==MODEL_KEY,
        ReviewEvidence.vector.is_not(None)))}
    people = db.scalars(select(Person).where(Person.id.in_(refs)).order_by(Person.id)).all()
    rejected = {}
    face_rejection_order = {}
    events = db.scalars(select(ReviewDecision).where(ReviewDecision.action=='reject_scene_person',
        ReviewDecision.details['batch_id'].as_string()==batch_id,
        ReviewDecision.details['scene_id'].as_string()==scene_id)
        .order_by(ReviewDecision.created_at,ReviewDecision.id)).all()
    for index,event in enumerate(events):
        state = rejected.setdefault(event.details['person_id'],{'faces':set(),'order':0})
        state['faces'].add(event.details['face_id'])
        state['order']=index+1
        face_rejection_order[event.details['face_id']]=index+1
    q = np.asarray([face.vector for face in faces],dtype=np.float32)
    scores, references = {}, {}
    for person in people:
        usable = [evidence[fid] for fid in sorted(refs[person.id]) if fid in evidence and fid not in owners]
        if not usable:
            continue
        matrix = q @ np.asarray([face.vector for face in usable],dtype=np.float32).T
        best = np.argmax(matrix,axis=1)
        scores[person.id] = np.clip(matrix[np.arange(len(faces)),best],-1,1)
        references[person.id] = [usable[int(i)] for i in best]
    if not scores:
        return []
    person_ids = list(scores)
    all_scores = np.stack(list(scores.values()))
    for row,pid in enumerate(person_ids):
        dismissed_faces = rejected.get(pid,{}).get('faces',set())
        for column,face in enumerate(faces):
            if face.id in dismissed_faces:
                all_scores[row,column] = -np.inf
    # Resolve competition per face first, then collapse winning faces per person.
    # A rejection removes that pair from competition so the next identity can win.
    winners = np.argmax(all_scores,axis=0)
    strongest = np.max(all_scores,axis=0)
    result = []
    for person in people:
        if person.id in present or person.id not in scores:
            continue
        direct = scores[person.id]
        dismissed = rejected.get(person.id,{'faces':set(),'order':0})
        eligible = [i for i,face in enumerate(faces) if face.id not in dismissed['faces']
            and direct[i]>=minimum and person_ids[int(winners[i])]==person.id]
        if not eligible:
            continue
        anchor = max(eligible,key=lambda i:(float(direct[i]),faces[i].quality,faces[i].id))
        likeness = q @ q[anchor]
        supporting_frames = {faces[i].frame_id for i in eligible
            if direct[i]>=0.30 and strongest[i]-direct[i]<=0.03 and likeness[i]>=0.35}
        supporting_frames.discard(faces[anchor].frame_id)
        # At most two independent frames strengthen ranking. Twenty repeat samples
        # do not create twenty proposals or unlimited confidence.
        support = min(2,len(supporting_frames))
        raw = float(direct[anchor])
        score = raw + 0.04*support
        face = faces[anchor]
        cluster = owners[face.id]
        token = hashlib.sha256(json.dumps([person.id,face.id,cluster.id,cluster.revision,
            sorted(dismissed['faces']),round(raw,6)],sort_keys=True).encode()).hexdigest()
        result.append({'person_id':person.id,'person_name':person.name,
            'query_face':evidence_data(face),'reference_face':evidence_data(references[person.id][anchor]),
            'similarity':round(raw,4),'rank_score':round(score,4),'supporting_frames':len(supporting_frames),
            'weak_match':raw<0.50,'previously_rejected':bool(dismissed['order']),
            'rejection_order':max(dismissed['order'],face_rejection_order.get(face.id,0)),
            'alternative_after_rejection':bool(dismissed['order'] or face_rejection_order.get(face.id,0)),
            'cluster_id':cluster.id,'revision':cluster.revision,'token':token})
    return sorted(result,key=lambda r:(r['rejection_order'],r['previously_rejected'],-r['rank_score'],r['person_id']))


@router.get('/batches/{batch_id}/scenes/{scene_id}/person-suggestions')
def scene_person_suggestions(batch_id: str, scene_id: str, db: Session = Depends(get_db)):
    return person_proposals(db,batch_id,scene_id)


class PersonProposalDecision(BaseModel):
    person_id: str
    token: str


@router.post('/batches/{batch_id}/scenes/{scene_id}/person-suggestions/{action}')
def decide_person_proposal(batch_id: str, scene_id: str, action: str,
                           payload: PersonProposalDecision, db: Session = Depends(get_db)):
    if action not in ('confirm','reject'):
        fail('Unknown suggestion action',422)
    lock_batch(db,batch_id)
    proposal = next((r for r in person_proposals(db,batch_id,scene_id) if r['person_id']==payload.person_id),None)
    if proposal is None or proposal['token']!=payload.token:
        fail('Suggestion changed; refresh the scene before deciding')
    if action=='confirm':
        # Confirm only the visible evidence. Other observations remain outstanding.
        return assign_person(proposal['cluster_id'],DecisionInput(revision=proposal['revision'],
            person_id=payload.person_id,face_ids=[proposal['query_face']['id']]),db)
    audit(db,None,'reject_scene_person',{'batch_id':batch_id,'scene_id':scene_id,
        'person_id':payload.person_id,'face_id':proposal['query_face']['id'],'token':proposal['token']})
    db.commit()
    return {'status':'rejected'}
