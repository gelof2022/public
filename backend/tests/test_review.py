import numpy as np
import pytest
from sqlalchemy import select

from app.faces import MODEL_KEY as DETECTOR_KEY
from app.grouping import ALGORITHM_KEY, MODEL_KEY, input_key
from app.models import (Batch, BatchFrame, FaceEmbedding, FaceGrouping, FaceObservation, Frame,
                        Library, ReviewCluster, ReviewDecision, ReviewEvidence, StashScene)


@pytest.fixture()
def review_data(db_session, client):
    library = Library(name="Review", stash_url="", stash_path_prefix="/data", media_path_prefix="/media")
    batch = Batch(name="Review", state="video_processing_complete", selection={})
    db_session.add_all([library, batch])
    db_session.flush()
    for scene_number in range(2):
        scene = StashScene(library_id=library.id, stash_scene_id=str(scene_number),
            title=f"Video {scene_number}", source_path=f"/media/{scene_number}.mp4", stash_path=f"/data/{scene_number}.mp4", existing_tag_ids=[])
        db_session.add(scene)
        db_session.flush()
        faces = []
        for i in range(3):
            frame = Frame(scene_id=scene.id, source_fingerprint="a"*64, sampling_key="b"*64,
                          timestamp_ms=i*1000, relative_path=f"frames/{scene_number}-{i}.jpg")
            db_session.add(frame)
            db_session.flush()
            db_session.add(BatchFrame(batch_id=batch.id, frame_id=frame.id))
            face = FaceObservation(frame_id=frame.id, model_key=DETECTOR_KEY, bbox=[], landmarks=[],
                detector_confidence=0.9, quality=0.8, usable=True, quality_details={}, relative_path=f"faces/{scene_number}-{i}.jpg")
            db_session.add(face)
            db_session.flush()
            vector = np.zeros(128)
            vector[0] = 1
            db_session.add(FaceEmbedding(face_id=face.id, model_key=MODEL_KEY, vector=vector.tolist()))
            faces.append(face)
        db_session.add(FaceGrouping(batch_id=batch.id, scene_id=scene.id, input_key=input_key(faces),
            model_key=MODEL_KEY, algorithm_key=ALGORITHM_KEY,
            groups=[{"face_ids":[face.id for face in faces],"representative_ids":[faces[0].id]}]))
    db_session.commit()
    result = client.post(f"/api/v1/review/batches/{batch.id}/prepare")
    assert result.json()['created'] == 2
    return batch.id


def clusters(client, batch):
    return client.get(f"/api/v1/review/batches/{batch}/clusters").json()


def person(client, name='Alice'):
    response=client.post('/api/v1/review/people',json={'name':name})
    assert response.status_code==201
    return response.json()['id']


def test_review_import_idempotent_and_assignments_survive_regrouping(client, db_session, review_data):
    first=clusters(client,review_data)[0]
    pid=person(client)
    assert client.post(f"/api/v1/review/clusters/{first['id']}/assign",json={'revision':1,'all_faces':True,'person_id':pid}).status_code==200
    for grouping in db_session.scalars(select(FaceGrouping)):
        grouping.groups=[]
    db_session.commit()
    assert client.post(f'/api/v1/review/batches/{review_data}/prepare').json()['created']==0
    saved=next(c for c in clusters(client,review_data) if c['id']==first['id'])
    assert saved['person_id']==pid and len(saved['faces'])==3
    assert client.delete(f'/api/v1/batches/{review_data}').status_code==409
    assert client.get('/api/v1/review/people').json()[0]['reference_count']==3


def test_split_removes_identity_from_separated_faces_and_merge_needs_reassignment(client, review_data):
    original=clusters(client,review_data)[0]
    pid=person(client)
    assigned=client.post(f"/api/v1/review/clusters/{original['id']}/assign",json={'revision':1,'all_faces':True,'person_id':pid}).json()
    result=client.post(f"/api/v1/review/clusters/{original['id']}/split",json={'revision':assigned['revision'],'face_ids':[original['faces'][0]['id']]})
    assert result.status_code==200
    current=[c for c in clusters(client,review_data) if c['scene_id']==original['scene_id']]
    assert len(current)==2
    assert client.get('/api/v1/review/people').json()[0]['reference_count']==2
    merged=client.post('/api/v1/review/clusters/merge',json={'revisions':{c['id']:c['revision'] for c in current}}).json()
    assert merged['person_id'] is None and len(merged['faces'])==3
    assert client.get('/api/v1/review/people').json()[0]['reference_count']==0
    assert client.get(f'/api/v1/review/batches/{review_data}/history').json()[0]['action']=='merge'


def test_suggestions_use_other_videos_and_rejections_persist(client, review_data):
    source,target=clusters(client,review_data)
    pid=person(client)
    client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={'revision':1,'all_faces':True,'person_id':pid})
    suggestions=client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json()
    assert suggestions[0]['person_id']==pid and suggestions[0]['similarity']==1
    assert all(face['scene_id']!=target['scene_id'] for face in suggestions[0]['representatives'])
    response=client.post(f"/api/v1/review/clusters/{target['id']}/reject/{pid}",json={'revision':1})
    assert response.status_code==200
    assert client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json()==[]
    assert client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={'revision':1,'all_faces':True,'person_id':pid}).status_code==409


def test_rename_validation_and_cross_video_merge_rejected(client, review_data):
    pid=person(client)
    assert client.post('/api/v1/review/people',json={'name':' alice '}).status_code==409
    assert client.post('/api/v1/review/people',json={'name':'   '}).status_code==422
    assert client.patch(f'/api/v1/review/people/{pid}',json={'name':'Alicia'}).status_code==200
    current=clusters(client,review_data)
    assert client.post('/api/v1/review/clusters/merge',json={'revisions':{c['id']:c['revision'] for c in current}}).status_code==422
    first=current[0]
    assert client.post(f"/api/v1/review/clusters/{first['id']}/split",json={'revision':1,'face_ids':[f['id'] for f in first['faces']]}).status_code==422


def test_snapshot_survives_detector_record_deletion(client, db_session, review_data):
    first=clusters(client,review_data)[0]
    identifier=first['faces'][0]['id']
    face=db_session.get(FaceObservation,identifier)
    db_session.delete(db_session.get(FaceEmbedding,(identifier,MODEL_KEY)))
    db_session.delete(face)
    db_session.commit()
    assert db_session.get(ReviewEvidence,identifier).vector is not None
    saved=next(c for c in clusters(client,review_data) if c['id']==first['id'])
    assert len(saved['faces'])==3


def test_conflicting_assignment_for_shared_evidence_is_blocked(client, db_session, review_data):
    first=clusters(client,review_data)[0]
    alice,bob=person(client),person(client,'Bob')
    client.post(f"/api/v1/review/clusters/{first['id']}/assign",json={'revision':1,'all_faces':True,'person_id':alice})
    other=Batch(name='Other',selection={},state='video_processing_complete')
    db_session.add(other)
    db_session.flush()
    duplicate=ReviewCluster(batch_id=other.id,scene_id=first['scene_id'],member_ids=[f['id'] for f in first['faces']])
    db_session.add(duplicate)
    db_session.commit()
    assert client.post(f'/api/v1/review/clusters/{duplicate.id}/assign',json={'revision':1,'all_faces':True,'person_id':bob}).status_code==409
    assert db_session.scalars(select(ReviewDecision).where(ReviewDecision.action=='confirm_faces')).all()


def test_ignore_faces_removes_references_and_survives_import(client, review_data):
    source, target = clusters(client, review_data)
    pid = person(client)
    client.post(f"/api/v1/review/clusters/{source['id']}/assign", json={'revision':1,'all_faces':True,'person_id':pid})
    ignored = client.post(f"/api/v1/review/clusters/{source['id']}/ignore", json={
        'revision':2,'face_ids':[source['faces'][0]['id']]}).json()
    assert ignored['state'] == 'ignored' and ignored['person_id'] is None
    assert client.get('/api/v1/review/people').json()[0]['reference_count'] == 2
    assert client.get(f"/api/v1/review/clusters/{ignored['id']}/suggestions").json() == []
    assert client.post(f'/api/v1/review/batches/{review_data}/prepare').json()['created'] == 0
    source = next(c for c in clusters(client, review_data) if c['id'] == source['id'])
    assert client.post(f"/api/v1/review/clusters/{source['id']}/ignore", json={
        'revision':source['revision'],'face_ids':[f['id'] for f in source['faces']]}).status_code == 200
    assert client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json() == []
    restored = client.post(f"/api/v1/review/clusters/{ignored['id']}/assign", json={
        'revision':ignored['revision'],'state':'pending'}).json()
    assert restored['state'] == 'pending'
    client.post(f"/api/v1/review/clusters/{restored['id']}/assign", json={
        'revision':restored['revision'],'all_faces':True,'person_id':pid})
    assert client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json()[0]['reference_count'] == 1


def test_ignore_validates_membership_and_revision(client, review_data):
    source, target = clusters(client, review_data)
    url = f"/api/v1/review/clusters/{source['id']}/ignore"
    assert client.post(url,json={'revision':1,'face_ids':[]}).status_code == 422
    assert client.post(url,json={'revision':1,'face_ids':[target['faces'][0]['id']]}).status_code == 422
    assert client.post(url,json={'revision':9,'face_ids':[source['faces'][0]['id']]}).status_code == 409


def test_partial_confirmation_and_legacy_labels(client, db_session, review_data):
    source, target = clusters(client, review_data)
    pid = person(client)
    legacy = db_session.get(ReviewCluster, source['id'])
    legacy.person_id, legacy.state = pid, 'assigned'
    db_session.commit()
    people = client.get('/api/v1/review/people').json()
    assert people[0]['reference_count'] == 0 and people[0]['representatives'] == []
    assert people[0]['review_required_count'] == 3
    assert client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json() == []
    face_id = source['faces'][0]['id']
    url = f"/api/v1/review/clusters/{source['id']}/assign"
    assert client.post(url, json={'revision':1,'person_id':pid}).status_code == 422
    confirmed = client.post(url, json={'revision':1,'person_id':pid,'face_ids':[face_id]}).json()
    assert confirmed['confirmed_ids'] == [face_id]
    remaining = next(c for c in clusters(client, review_data) if c['id'] == source['id'])
    assert remaining['confirmed_ids'] == [] and len(remaining['faces']) == 2
    people = client.get('/api/v1/review/people').json()
    assert people[0]['reference_count'] == 1 and people[0]['review_required_count'] == 2
    assert [f['id'] for f in people[0]['representatives']] == [face_id]
    assert client.post(f'/api/v1/review/batches/{review_data}/prepare').json()['created'] == 0
    response = client.post(f"/api/v1/review/clusters/{confirmed['id']}/assign", json={
        'revision':confirmed['revision'],'face_ids':[face_id],'state':'pending'})
    assert response.status_code == 200
    assert client.get('/api/v1/review/people').json()[0]['reference_count'] == 0


def test_top_three_includes_low_scores_and_same_video_without_self_matches(client, db_session, review_data):
    source, target = clusters(client, review_data)
    source_id = source['id']
    for index, face in enumerate(source['faces']):
        pid = person(client, f'Person {index}')
        current = next(c for c in clusters(client, review_data) if c['id'] == source_id)
        response = client.post(f'/api/v1/review/clusters/{source_id}/assign', json={
            'revision':current['revision'],'person_id':pid,'face_ids':[face['id']]})
        assert response.status_code == 200
        evidence = db_session.get(ReviewEvidence, face['id'])
        vector = np.zeros(128)
        vector[0] = 0.1 * index
        vector[1] = np.sqrt(1-vector[0]**2)
        evidence.vector = vector.tolist()
        db_session.commit()
    target_record = db_session.get(ReviewCluster, target['id'])
    target_record.scene_id = source['scene_id']
    db_session.commit()
    results = client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json()
    assert len(results) == 3
    assert [r['similarity'] for r in results] == [0.2, 0.1, 0.0]
    assigned = next(c for c in clusters(client, review_data) if c['person_id'])
    matches = client.get(f"/api/v1/review/clusters/{assigned['id']}/suggestions").json()
    assert all(f['id'] not in assigned['member_ids'] for match in matches for f in match['representatives'])
    assert client.get(f"/api/v1/review/clusters/{target['id']}/suggestions", params={'face_ids':source['faces'][0]['id']}).status_code == 422


def test_reference_removal_returns_face_to_review_and_preserves_others(client, review_data):
    source, target = clusters(client, review_data)
    pid = person(client)
    client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':pid,'all_faces':True})
    refs = client.get(f'/api/v1/review/people/{pid}/references').json()
    assert len(refs) == 3 and refs[0]['confirmations'][0]['batch_name'] == 'Review'
    face = refs[0]
    revisions = {c['cluster_id']:c['revision'] for c in face['confirmations']}
    removed = client.post(f'/api/v1/review/people/{pid}/references/remove',json={
        'face_ids':[face['id']],'revisions':revisions})
    assert removed.status_code == 200 and removed.json()['removed_faces'] == 1
    remaining = client.get(f'/api/v1/review/people/{pid}/references').json()
    assert len(remaining) == 2 and face['id'] not in [f['id'] for f in remaining]
    returned = next(c for c in clusters(client, review_data) if face['id'] in c['member_ids'])
    assert returned['state'] == 'pending' and returned['person_id'] is None
    assert client.post(f'/api/v1/review/batches/{review_data}/prepare').json()['created'] == 0
    suggestions = client.get(f"/api/v1/review/clusters/{target['id']}/suggestions").json()
    assert suggestions[0]['reference_count'] == 2
    assert client.post(f'/api/v1/review/people/{pid}/references/remove',json={
        'face_ids':[face['id']],'revisions':revisions}).status_code == 409


def test_reference_removal_requires_all_shared_confirmations(client, db_session, review_data):
    source = clusters(client, review_data)[0]
    pid = person(client)
    confirmed = client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':pid,'all_faces':True}).json()
    other = Batch(name='Other',state='video_processing_complete',selection={})
    db_session.add(other)
    db_session.flush()
    duplicate = ReviewCluster(batch_id=other.id,scene_id=source['scene_id'],member_ids=source['member_ids'],
                              confirmed_ids=source['member_ids'],person_id=pid,state='assigned')
    db_session.add(duplicate)
    db_session.commit()
    url = f'/api/v1/review/people/{pid}/references/remove'
    assert client.post(url,json={'face_ids':source['member_ids'],
        'revisions':{confirmed['id']:confirmed['revision']}}).status_code == 409
    result = client.post(url,json={'face_ids':source['member_ids'],
        'revisions':{confirmed['id']:confirmed['revision'],duplicate.id:duplicate.revision}})
    assert result.status_code == 200 and result.json()['updated_clusters'] == 2
    assert client.get('/api/v1/review/people').json()[0]['reference_count'] == 0


def test_scene_completion_allows_unknowns_and_reopens_after_changes(client, review_data):
    scenes = client.get(f'/api/v1/review/batches/{review_data}/scenes').json()
    scene = scenes[0]
    assert scene['unknown_count'] == 3 and scene['identified_count'] == 0
    url = f"/api/v1/review/batches/{review_data}/scenes/{scene['scene_id']}/completion"
    body = {'revision':scene['revision'],'fingerprint':scene['fingerprint'],'complete':True}
    assert client.post(url,json=body).status_code == 200
    saved = next(s for s in client.get(f'/api/v1/review/batches/{review_data}/scenes').json() if s['scene_id']==scene['scene_id'])
    assert saved['complete'] and saved['unknown_count']==3
    assert client.post(url,json=body).status_code == 409
    cluster = next(c for c in clusters(client,review_data) if c['scene_id']==scene['scene_id'])
    pid = person(client)
    client.post(f"/api/v1/review/clusters/{cluster['id']}/assign",json={
        'revision':cluster['revision'],'face_ids':[cluster['faces'][0]['id']],'person_id':pid})
    changed = next(s for s in client.get(f'/api/v1/review/batches/{review_data}/scenes').json() if s['scene_id']==scene['scene_id'])
    assert not changed['complete'] and changed['changed_since_completion']
    assert changed['identified_count']==1 and changed['unknown_count']==2 and changed['people']==['Alice']
    assert client.post(url,json={'revision':changed['revision'],'fingerprint':changed['fingerprint'],'complete':False}).status_code==200


def test_per_face_top_three_preserves_minority_identity_and_low_scores(client, db_session, review_data):
    source,target=clusters(client,review_data)
    for index,face in enumerate(source['faces']):
        pid=person(client,f'Person {index}')
        current=next(c for c in clusters(client,review_data) if c['id']==source['id'])
        assert client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
            'revision':current['revision'],'person_id':pid,'face_ids':[face['id']]}).status_code==200
        vector=np.zeros(128)
        vector[index]=1
        db_session.get(ReviewEvidence,face['id']).vector=vector.tolist()
    for index,face in enumerate(target['faces']):
        vector=np.zeros(128)
        vector[index]=1
        db_session.get(ReviewEvidence,face['id']).vector=vector.tolist()
    db_session.commit()
    url=f"/api/v1/review/clusters/{target['id']}/face-suggestions"
    rows=client.get(url).json()
    assert len(rows)==3 and all(len(row['candidates'])==3 for row in rows)
    for index,row in enumerate(rows):
        assert row['candidates'][0]['person_name']==f'Person {index}'
        assert row['candidates'][0]['similarity']==1
    narrowed=client.get(url,params={'min_similarity':0.5}).json()
    assert all(len(row['candidates'])==1 for row in narrowed)
    pid=rows[0]['candidates'][0]['person_id']
    client.post(f"/api/v1/review/clusters/{target['id']}/reject/{pid}",json={'revision':target['revision']})
    assert all(len(row['candidates'])==2 for row in client.get(url).json())
    assert all(len(row['candidates'])==3 for row in client.get(url,params={'include_rejected':'true'}).json())


def test_scene_regroup_merges_pending_groups_and_preserves_confirmations(client, review_data):
    source=clusters(client,review_data)[0]
    pid=person(client)
    confirmed=client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':pid,'face_ids':[source['faces'][0]['id']]}).json()
    remaining=next(c for c in clusters(client,review_data) if c['id']==source['id'])
    client.post(f"/api/v1/review/clusters/{source['id']}/split",json={
        'revision':remaining['revision'],'face_ids':[remaining['faces'][0]['id']]})
    scene=next(s for s in client.get(f'/api/v1/review/batches/{review_data}/scenes').json() if s['scene_id']==source['scene_id'])
    url=f"/api/v1/review/batches/{review_data}/scenes/{source['scene_id']}/regroup"
    payload={'revision':scene['revision'],'fingerprint':scene['fingerprint'],'threshold':0.5}
    result=client.post(url,json=payload)
    assert result.status_code==200 and result.json()=={'before':2,'after':1,'without_embedding':0}
    saved=next(c for c in clusters(client,review_data) if c['id']==confirmed['id'])
    assert saved['person_id']==pid and len(saved['confirmed_ids'])==1
    assert client.post(url,json=payload).status_code==409
    assert client.post(f'/api/v1/review/batches/{review_data}/prepare').json()['created']==0


def test_scene_header_frames_filter_before_pagination(client, review_data):
    scene=clusters(client,review_data)[0]['scene_id']
    url=f'/api/v1/batches/{review_data}/frames'
    first=client.get(url,params={'scene_id':scene,'limit':2,'offset':0}).json()
    second=client.get(url,params={'scene_id':scene,'limit':2,'offset':2}).json()
    assert len(first)==2 and len(second)==1
    assert all(frame['scene_id']==scene for frame in first+second)
    assert [frame['timestamp_seconds'] for frame in first+second]==[0,1,2]


def test_original_frame_context_uses_saved_face_location(client, db_session, review_data, tmp_path, monkeypatch):
    from app.config import get_settings
    first = clusters(client, review_data)[0]['faces'][0]
    evidence = db_session.get(ReviewEvidence, first['id'])
    frame = db_session.get(Frame, evidence.frame_id)
    monkeypatch.setattr(get_settings(), 'appdata_path', tmp_path)
    url = f"/api/v1/review/evidence/{evidence.id}/frame"
    assert client.get(url).status_code == 404
    path = tmp_path / frame.relative_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b'frame')
    assert client.get(url).status_code == 409
    evidence.bbox = [10, 20, 30, 40]
    db_session.commit()
    result = client.get(url)
    assert result.status_code == 200
    assert result.json() == {'url': f'/generated/{frame.relative_path}', 'bbox': [10, 20, 30, 40]}
    assert client.get('/api/v1/review/evidence/missing/frame').status_code == 404


def test_manual_scene_person_does_not_create_references(client, db_session, review_data):
    pid=person(client,'Manual presence')
    url=f'/api/v1/review/batches/{review_data}/scenes'
    scene=client.get(url).json()[0]
    before=client.get('/api/v1/review/people').json()
    payload={'person_id':pid,'revision':scene['revision'],'fingerprint':scene['fingerprint'],'present':True}
    endpoint=f"{url}/{scene['scene_id']}/manual-person"
    assert client.post(endpoint,json=payload).status_code==200
    updated=next(s for s in client.get(url).json() if s['scene_id']==scene['scene_id'])
    assert updated['manual_people']==[{'id':pid,'name':'Manual presence'}]
    assert updated['identified_count']==scene['identified_count']
    assert updated['unknown_count']==scene['unknown_count']
    assert 'Manual presence' in updated['people']
    assert client.get('/api/v1/review/people').json()==before
    assert client.post(endpoint,json=payload).status_code==409
    payload.update(fingerprint=updated['fingerprint'],revision=updated['revision'],present=False)
    assert client.post(endpoint,json=payload).status_code==200
    assert next(s for s in client.get(url).json() if s['scene_id']==scene['scene_id'])['manual_people']==[]


def test_regroup_preserves_manually_merged_cluster(client, review_data):
    original=clusters(client,review_data)[0]
    result=client.post(f"/api/v1/review/clusters/{original['id']}/split",json={
        'revision':original['revision'],'face_ids':[original['faces'][0]['id']]})
    assert result.status_code==200
    parts=[c for c in clusters(client,review_data) if c['scene_id']==original['scene_id']]
    merged=client.post('/api/v1/review/clusters/merge',json={'revisions':{c['id']:c['revision'] for c in parts}}).json()
    url=f'/api/v1/review/batches/{review_data}/scenes'
    scene=next(s for s in client.get(url).json() if s['scene_id']==original['scene_id'])
    response=client.post(f"{url}/{scene['scene_id']}/regroup",json={
        'revision':scene['revision'],'fingerprint':scene['fingerprint'],'threshold':.1})
    assert response.status_code==200,response.text
    assert next(c for c in clusters(client,review_data) if c['id']==merged['id'])==merged


def test_batch_stage_completion_is_independent(client, db_session, review_data):
    from app.models import BatchScene, FaceAnalysis, ProcessingJob
    scene_ids={f.scene_id for f in db_session.scalars(select(Frame))}
    for sid in scene_ids:
        db_session.add(BatchScene(batch_id=review_data,scene_id=sid,state='complete'))
    db_session.commit()
    url=f'/api/v1/batches/{review_data}/stages'
    assert client.get(url).json()=={'frames':True,'detection':False,'grouping':False,'review':False}
    for frame in db_session.scalars(select(Frame)):
        db_session.add(FaceAnalysis(frame_id=frame.id,model_key=DETECTOR_KEY,face_count=1))
    db_session.add(ProcessingJob(batch_id=review_data,job_type='face_grouping',state='complete',payload={}))
    db_session.commit()
    assert client.get(url).json()=={'frames':True,'detection':True,'grouping':True,'review':False}
    scenes_url=f'/api/v1/review/batches/{review_data}/scenes'
    for scene in client.get(scenes_url).json():
        client.post(f"{scenes_url}/{scene['scene_id']}/completion",json={
            'revision':scene['revision'],'fingerprint':scene['fingerprint'],'complete':True})
    assert client.get(url).json()['review'] is True
    grouping=db_session.scalar(select(FaceGrouping))
    grouping.algorithm_key='old'
    db_session.commit()
    assert client.get(url).json()['grouping'] is False


def test_archive_restores_batch_and_preserves_review_history(client, review_data):
    before=clusters(client,review_data)
    response=client.post(f'/api/v1/batches/{review_data}/archive')
    assert response.status_code==200 and response.json()['archived'] is True
    assert all(b['id']!=review_data for b in client.get('/api/v1/batches').json())
    assert any(b['id']==review_data for b in client.get('/api/v1/batches?include_archived=true').json())
    assert clusters(client,review_data)==before
    assert client.post(f'/api/v1/batches/{review_data}/archive?archived=false').status_code==200
    assert any(b['id']==review_data for b in client.get('/api/v1/batches').json())


def test_archive_rejects_active_job(client,db_session,review_data):
    from app.models import ProcessingJob
    db_session.add(ProcessingJob(batch_id=review_data,job_type='face_grouping',state='queued',payload={}))
    db_session.commit()
    assert client.post(f'/api/v1/batches/{review_data}/archive').status_code==409


def test_suggestions_prefer_confirmed_scene_faces_and_block_same_frame(client,db_session,review_data):
    source,target=clusters(client,review_data)
    local_pid=person(client,'Local')
    global_pid=person(client,'Global')
    local_id=source['faces'][0]['id']
    local=db_session.get(ReviewEvidence,local_id)
    local.vector=[.6,.8]+[0.]*126
    db_session.commit()
    assert client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':local_pid,'face_ids':[local_id]}).status_code==200
    assert client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':global_pid,'all_faces':True}).status_code==200
    query=next(c for c in clusters(client,review_data) if c['id']==source['id'])
    url=f"/api/v1/review/clusters/{query['id']}/suggestions"
    result=client.get(url).json()
    assert [r['person_id'] for r in result]==[local_pid,global_pid]
    assert result[0]['similarity']==.6 and result[1]['similarity']==1
    assert result[0]['reference_source']=='current_scene'
    assert result[0]['representatives'][0]['id']==local_id
    assert result[0]['query_face']['id'] in query['member_ids']
    local.vector=[.2,float(np.sqrt(.96))]+[0.]*126
    db_session.commit()
    assert client.get(url).json()[0]['person_id']==global_pid
    conflict=db_session.get(ReviewEvidence,query['faces'][0]['id'])
    conflict.frame_id=local.frame_id
    db_session.commit()
    assert local_pid not in [r['person_id'] for r in client.get(url).json()]
    perface=client.get(f"/api/v1/review/clusters/{query['id']}/face-suggestions").json()
    assert local_pid not in [r['person_id'] for r in next(f for f in perface if f['face_id']==conflict.id)['candidates']]
    response=client.post(f"/api/v1/review/clusters/{query['id']}/assign",json={
        'revision':query['revision'],'person_id':local_pid,'face_ids':[conflict.id]})
    assert response.status_code==409


def test_partial_cluster_can_use_its_other_confirmed_face_as_reference(client,db_session,review_data):
    source=clusters(client,review_data)[0]
    pid=person(client,'Partial')
    saved=db_session.get(ReviewCluster,source['id'])
    saved.person_id=pid
    saved.state='assigned'
    saved.confirmed_ids=[source['faces'][0]['id']]
    db_session.commit()
    query_id=source['faces'][1]['id']
    result=client.get(f"/api/v1/review/clusters/{source['id']}/suggestions?face_ids={query_id}").json()
    assert result[0]['person_id']==pid and result[0]['reference_source']=='current_scene'
    assert result[0]['representatives'][0]['id']==saved.confirmed_ids[0]


def test_mirror_conflicts_show_faces_and_require_explicit_override(client,db_session,review_data):
    source,target=clusters(client,review_data)
    pid=person(client,'Mirror')
    a,b=[db_session.get(ReviewEvidence,f['id']) for f in source['faces'][:2]]
    b.frame_id=a.frame_id
    db_session.commit()
    url=f"/api/v1/review/clusters/{source['id']}/assign"
    payload={'revision':1,'person_id':pid,'face_ids':[a.id,b.id]}
    response=client.post(url,json=payload)
    assert response.status_code==409
    detail=response.json()['detail']
    assert detail['code']=='same_frame_conflict'
    assert {f['id'] for f in detail['conflicts'][0]['faces']}=={a.id,b.id}
    assert all(f['url'] and f['frame_context_url'] for f in detail['conflicts'][0]['faces'])
    assert client.post(url,json={**payload,'allow_same_frame':True}).status_code==200
    decision=db_session.scalar(select(ReviewDecision).where(ReviewDecision.action=='confirm_faces'))
    assert decision.details['allow_same_frame'] is True
    query=db_session.get(ReviewEvidence,target['faces'][0]['id'])
    query.frame_id=a.frame_id
    db_session.commit()
    suggestion_url=f"/api/v1/review/clusters/{target['id']}/suggestions?face_ids={query.id}"
    assert client.get(suggestion_url).json()==[]
    assert client.get(suggestion_url+'&allow_same_frame=true').json()[0]['person_id']==pid
    perface=client.get(f"/api/v1/review/clusters/{target['id']}/face-suggestions?allow_same_frame=true").json()
    assert next(f for f in perface if f['face_id']==query.id)['candidates'][0]['person_id']==pid
    response=client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':pid,'face_ids':[query.id]})
    assert response.status_code==409
    faces=response.json()['detail']['conflicts'][0]['faces']
    assert {f['id'] for f in faces if f['already_confirmed']}=={a.id,b.id}


def test_low_quality_queries_prioritise_local_reference_without_boosting_score(client,db_session,review_data):
    source,target=clusters(client,review_data)
    local_pid=person(client,'Local weak')
    global_pid=person(client,'Global strong')
    local=db_session.get(ReviewEvidence,source['faces'][0]['id'])
    local.vector=[.35,float(np.sqrt(1-.35**2))]+[0.]*126
    for f in source['faces'][1:]:
        db_session.get(ReviewEvidence,f['id']).quality=.2
    db_session.commit()
    assert client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':local_pid,'face_ids':[local.id]}).status_code==200
    assert client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':global_pid,'all_faces':True}).status_code==200
    url=f"/api/v1/review/clusters/{source['id']}/suggestions"
    result=client.get(url).json()
    assert result[0]['person_id']==local_pid and result[0]['similarity']==.35
    assert result[0]['weak_match'] is True and result[0]['scene_priority'] is True
    assert result[0]['local_reference']['id']==local.id
    assert client.get(url+'?min_similarity=0.5').json()[0]['person_id']==global_pid
    perface=client.get(f"/api/v1/review/clusters/{source['id']}/face-suggestions").json()
    assert all(row['candidates'][0]['person_id']==local_pid for row in perface)


def test_local_candidate_keeps_a_slot_even_when_three_global_matches_are_stronger(client,db_session,review_data):
    source,target=clusters(client,review_data)
    local_pid=person(client,'Local weak')
    local=db_session.get(ReviewEvidence,source['faces'][0]['id'])
    local.vector=[.1,float(np.sqrt(.99))]+[0.]*126
    db_session.commit()
    client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':local_pid,'face_ids':[local.id]})
    remaining=target
    for i,face in enumerate(target['faces']):
        pid=person(client,f'Other {i}')
        assert client.post(f"/api/v1/review/clusters/{remaining['id']}/assign",json={
            'revision':remaining['revision'],'person_id':pid,'face_ids':[face['id']]}).status_code==200
        remaining=next(c for c in clusters(client,review_data) if c['id']==target['id'])
    result=client.get(f"/api/v1/review/clusters/{source['id']}/suggestions").json()
    assert len(result)==3 and result[2]['person_id']==local_pid
    assert result[2]['weak_match'] and not result[2]['scene_priority']


def test_scene_person_proposals_deduplicate_and_confirm_only_displayed_face(client,db_session,review_data):
    source,target=clusters(client,review_data)
    pid=person(client,'John')
    assert client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':pid,'all_faces':True}).status_code==200
    url=f"/api/v1/review/batches/{review_data}/scenes/{source['scene_id']}/person-suggestions"
    proposals=client.get(url).json()
    assert len(proposals)==1 and proposals[0]['person_id']==pid
    assert proposals[0]['supporting_frames']==2
    assert proposals[0]['reference_face']['id'] in target['member_ids']
    assert proposals[0]['query_face']['id'] in source['member_ids']
    assert proposals[0]['similarity']==1 and proposals[0]['rank_score']==1.08
    response=client.post(url+'/confirm',json={'person_id':pid,'token':proposals[0]['token']})
    assert response.status_code==200,response.text
    assert response.json()['confirmed_ids']==[proposals[0]['query_face']['id']]
    assert client.get(url).json()==[]
    scene=next(s for s in client.get(f'/api/v1/review/batches/{review_data}/scenes').json() if s['scene_id']==source['scene_id'])
    assert scene['people']==['John'] and scene['identified_count']==1 and scene['unknown_count']==2
    assert client.post(url+'/confirm',json={'person_id':pid,'token':proposals[0]['token']}).status_code==409


def test_scene_rejection_regenerates_once_per_person_at_bottom_and_persists(client,db_session,review_data):
    source,target=clusters(client,review_data)
    john=person(client,'John')
    paul=person(client,'Paul')
    assert client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':john,'face_ids':[target['member_ids'][0]]}).status_code==200
    remaining=next(c for c in clusters(client,review_data) if c['id']==target['id'])
    assert client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':remaining['revision'],'person_id':paul,'all_faces':True}).status_code==200
    url=f"/api/v1/review/batches/{review_data}/scenes/{source['scene_id']}/person-suggestions"
    initial=client.get(url).json()
    assert len(initial)==1  # All three faces have the same best person.
    first=initial[0]
    payload={'person_id':first['person_id'],'token':first['token']}
    assert client.post(url+'/reject',json=payload).status_code==200
    updated=client.get(url).json()
    assert len(updated)==2 and updated[-1]['person_id']==first['person_id']
    assert len({r['query_face']['id'] for r in updated})==len(updated)
    assert len({r['person_id'] for r in updated})==len(updated)
    assert updated[-1]['previously_rejected'] is True
    assert updated[-1]['query_face']['id']!=first['query_face']['id']
    assert client.post(url+'/reject',json=payload).status_code==409
    assert client.get(url).json()==updated
    history=client.get(f'/api/v1/review/batches/{review_data}/history').json()
    assert any(row['action']=='reject_scene_person' for row in history)
    assert all(c['state']=='pending' for c in clusters(client,review_data) if c['scene_id']==source['scene_id'])


def test_scene_proposals_reject_stronger_competitor_and_do_not_count_same_frame_support(client,db_session,review_data):
    source,target=clusters(client,review_data)
    john=person(client,'John')
    paul=person(client,'Paul')
    ref=db_session.get(ReviewEvidence,target['member_ids'][0])
    ref.vector=[.6,.8]+[0.]*126
    original=[db_session.get(ReviewEvidence,fid) for fid in source['member_ids']]
    for face in original:
        face.frame_id=original[0].frame_id
    db_session.commit()
    client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':john,'face_ids':[ref.id]})
    remaining=next(c for c in clusters(client,review_data) if c['id']==target['id'])
    client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':remaining['revision'],'person_id':paul,'all_faces':True})
    url=f"/api/v1/review/batches/{review_data}/scenes/{source['scene_id']}/person-suggestions"
    result=client.get(url).json()
    assert len(result)==1 and result[0]['person_id']==paul
    assert result[0]['supporting_frames']==0 and result[0]['rank_score']==1


def test_present_person_remains_competitor_without_getting_a_duplicate_row(client,db_session,review_data):
    source,target=clusters(client,review_data)
    john=person(client,'John already here')
    paul=person(client,'Paul')
    for fid in target['member_ids']:
        db_session.get(ReviewEvidence,fid).vector=[.6,.8]+[0.]*126
    db_session.commit()
    client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={
        'revision':1,'person_id':john,'face_ids':[source['member_ids'][0]]})
    client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':paul,'all_faces':True})
    url=f"/api/v1/review/batches/{review_data}/scenes/{source['scene_id']}/person-suggestions"
    assert client.get(url).json()==[]


def test_only_best_identity_per_face_and_rejection_reveals_next_even_with_lower_score(client,db_session,review_data):
    source,target=clusters(client,review_data)
    john=person(client,'John best')
    paul=person(client,'Paul next')
    query_id=source['member_ids'][0]
    for fid in source['member_ids'][1:]:
        db_session.get(ReviewEvidence,fid).vector=[0.,0.,1.]+[0.]*125
    for fid in target['member_ids'][1:]:
        db_session.get(ReviewEvidence,fid).vector=[.6,.8]+[0.]*126
    db_session.commit()
    client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':1,'person_id':john,'face_ids':[target['member_ids'][0]]})
    remaining=next(c for c in clusters(client,review_data) if c['id']==target['id'])
    client.post(f"/api/v1/review/clusters/{target['id']}/assign",json={
        'revision':remaining['revision'],'person_id':paul,'all_faces':True})
    url=f"/api/v1/review/batches/{review_data}/scenes/{source['scene_id']}/person-suggestions"
    first=client.get(url).json()
    assert len(first)==1 and first[0]['person_id']==john and first[0]['query_face']['id']==query_id
    assert client.post(url+'/reject',json={'person_id':john,'token':first[0]['token']}).status_code==200
    second=client.get(url).json()
    assert len(second)==1 and second[0]['person_id']==paul and second[0]['query_face']['id']==query_id
    assert second[0]['similarity']==.6 and second[0]['alternative_after_rejection'] is True
    assert client.post(url+'/reject',json={'person_id':paul,'token':second[0]['token']}).status_code==200
    assert client.get(url).json()==[]


def test_review_cluster_metadata_reads_are_bounded_and_scene_scoped(client,db_session,review_data):
    from sqlalchemy import event
    originals=clusters(client,review_data)
    source=originals[0]
    for _ in range(200):
        db_session.add(ReviewCluster(batch_id=review_data,scene_id=source['scene_id'],member_ids=source['member_ids']))
    db_session.commit()
    statements=[]
    def record(connection,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(db_session.bind,'before_cursor_execute',record)
    try:
        result=client.get(f'/api/v1/review/batches/{review_data}/clusters?scene_ids={source["scene_id"]}')
    finally:
        event.remove(db_session.bind,'before_cursor_execute',record)
    assert result.status_code==200
    assert len(result.json())==201
    assert all(row['scene_id']==source['scene_id'] for row in result.json())
    assert next(row for row in result.json() if row['id']==source['id'])==source
    assert len(statements)<=5,statements
    summary=client.get(f'/api/v1/review/batches/{review_data}/scenes?include_cluster_ids=false').json()
    assert all(row['cluster_ids']==[] for row in summary)
    full=client.get(f'/api/v1/review/batches/{review_data}/scenes').json()
    assert [row['fingerprint'] for row in summary]==[row['fingerprint'] for row in full]


def test_workflow_awaiting_review_filter_matches_ids_and_honours_latest_active_batch(client,db_session,review_data):
    from app.models import BatchScene
    from datetime import datetime,timezone,timedelta
    scenes=client.get(f'/api/v1/review/batches/{review_data}/scenes').json()
    source=scenes[0]
    base=f'/api/v1/scenes?awaiting_review=true&review_batch={review_data}'
    assert client.get(base).json()['total']==2
    response=client.post(f'/api/v1/review/batches/{review_data}/scenes/{source["scene_id"]}/completion',json={
        'revision':source['revision'],'fingerprint':source['fingerprint'],'complete':True})
    assert response.status_code==200
    visible=client.get(base).json()
    ids=client.get(f'/api/v1/scenes/ids?awaiting_review=true&review_batch={review_data}').json()
    assert visible['total']==ids['total']==1
    assert [s['id'] for s in visible['items']]==ids['ids']
    # A changed review snapshot reopens the scene and restores it to the filter.
    cluster=db_session.scalar(select(ReviewCluster).where(ReviewCluster.scene_id==source['scene_id']))
    cluster.revision+=1
    db_session.commit()
    assert client.get(base).json()['total']==2
    newer=Batch(name='Newer review',state='video_processing_complete',selection={},created_at=datetime.now(timezone.utc)+timedelta(seconds=1))
    db_session.add(newer)
    db_session.flush()
    db_session.add(BatchScene(batch_id=newer.id,scene_id=source['scene_id']))
    db_session.commit()
    latest=client.get(f'/api/v1/review/batches/{newer.id}/scenes').json()[0]
    client.post(f'/api/v1/review/batches/{newer.id}/scenes/{source["scene_id"]}/completion',json={
        'revision':latest['revision'],'fingerprint':latest['fingerprint'],'complete':True})
    assert source['scene_id'] not in client.get('/api/v1/scenes/ids?awaiting_review=true').json()['ids']
    assert source['scene_id'] in client.get(f'/api/v1/scenes/ids?awaiting_review=true&review_batch={review_data}').json()['ids']
    newer.archived=True
    db_session.commit()
    assert source['scene_id'] in client.get('/api/v1/scenes/ids?awaiting_review=true').json()['ids']
