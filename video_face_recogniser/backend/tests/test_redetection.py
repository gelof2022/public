from contextlib import nullcontext

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app import redetection
from app.config import Settings, get_settings
from app.main import app
from app.models import FaceObservation, Frame, ProcessingJob, ReviewEvidence, SceneRedetection
from test_review import review_data as imported_review_data


@pytest.fixture()
def review_seed(db_session, client):
    return imported_review_data.__wrapped__(db_session, client)


@pytest.fixture()
def setup_redetection(db_session, client, review_seed, tmp_path, monkeypatch):
    config = Settings(appdata_path=tmp_path)
    app.dependency_overrides[get_settings] = lambda:config
    monkeypatch.setattr(redetection, 'get_settings', lambda:config)
    monkeypatch.setattr(redetection, 'SessionLocal', lambda:nullcontext(db_session))
    queued, thresholds = [], []

    class FakeQueue:
        def __init__(self, **kwargs):
            pass
        def enqueue(self, *args, **kwargs):
            queued.append((args, kwargs))
    monkeypatch.setattr(redetection, 'Queue', FakeQueue)
    monkeypatch.setattr(redetection, 'make_detector', lambda threshold:thresholds.append(threshold))
    monkeypatch.setattr(redetection, 'make_recognizer', lambda:object())
    monkeypatch.setattr(redetection, 'embedding', lambda *args:[1.0]+[0.0]*127)

    def detections(image, detector):
        return [{'bbox':box, 'landmarks':[[10,10]]*5, 'detector_confidence':0.65,
                 'quality':0.8, 'usable':True, 'quality_details':{},
                 'crop':np.full((20,20,3),100,dtype=np.uint8)} for box in [[10,10,20,20],[70,70,20,20]]]
    monkeypatch.setattr(redetection, 'detect_observations', detections)
    for face in db_session.scalars(select(FaceObservation)):
        face.bbox = [10,10,20,20]
    for face in db_session.scalars(select(ReviewEvidence)):
        face.bbox = [10,10,20,20]
    for frame in db_session.scalars(select(Frame)):
        path = tmp_path/frame.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), np.full((120,120,3),100,dtype=np.uint8))
    db_session.commit()
    scene = client.get(f'/api/v1/review/batches/{review_seed}/scenes').json()[0]
    return {'batch':review_seed,'scene':scene,'queued':queued,'thresholds':thresholds,'config':config}


def start(client, setup, threshold=0.35):
    response = client.post(f"/api/v1/review/batches/{setup['batch']}/scenes/{setup['scene']['scene_id']}/redetection",
                           json={'threshold':threshold})
    assert response.status_code == 202, response.text
    return response.json()['id']


def test_preview_reuses_frames_and_only_selected_additions_change_review(client, db_session, setup_redetection):
    setup = setup_redetection
    scene = setup['scene']
    url = f"/api/v1/review/batches/{setup['batch']}/scenes"
    assert client.post(f"{url}/{scene['scene_id']}/completion",json={
        'revision':scene['revision'],'fingerprint':scene['fingerprint'],'complete':True}).status_code==200
    original_ids = set(db_session.scalars(select(FaceObservation.id)))
    frames = set(db_session.scalars(select(Frame.id)))
    run_id = start(client, setup)
    assert setup['queued'][0][0][0] == 'app.redetection.process_redetection'
    redetection.process_redetection(run_id)
    run = db_session.get(SceneRedetection,run_id)
    assert run.state=='ready' and run.summary['frames']==3 and run.summary['new']==3
    assert setup['thresholds']==[0.35]
    assert all(db_session.get(Frame,p['frame_id']).scene_id==scene['scene_id'] for p in run.proposals)
    assert next(s for s in client.get(url).json() if s['scene_id']==scene['scene_id'])['complete']
    accepted_id = run.proposals[0]['id']
    result = client.post(f'/api/v1/review/redetections/{run_id}/apply',json={'face_ids':[accepted_id]})
    assert result.status_code==200 and result.json()['accepted']==1
    assert set(db_session.scalars(select(FaceObservation.id)))==original_ids
    assert set(db_session.scalars(select(Frame.id)))==frames
    assert db_session.get(ReviewEvidence, accepted_id).vector is not None
    updated = next(s for s in client.get(url).json() if s['scene_id']==scene['scene_id'])
    assert not updated['complete'] and updated['unknown_count']==scene['unknown_count']+1
    assert client.post(f'/api/v1/review/redetections/{run_id}/apply',json={'face_ids':[accepted_id]}).status_code==409
    next_id = start(client, setup)
    redetection.process_redetection(next_id)
    assert db_session.get(SceneRedetection,next_id).summary['new']==2


def test_confirmed_and_ignored_faces_survive_redetection(client, db_session, setup_redetection):
    setup = setup_redetection
    cluster = next(c for c in client.get(f"/api/v1/review/batches/{setup['batch']}/clusters").json() if c['scene_id']==setup['scene']['scene_id'])
    person = client.post('/api/v1/review/people',json={'name':'Reference'}).json()
    confirmed = client.post(f"/api/v1/review/clusters/{cluster['id']}/assign",json={
        'revision':cluster['revision'],'person_id':person['id'],'face_ids':[cluster['faces'][0]['id']]}).json()
    remaining = next(c for c in client.get(f"/api/v1/review/batches/{setup['batch']}/clusters").json() if c['id']==cluster['id'])
    ignored = client.post(f"/api/v1/review/clusters/{cluster['id']}/ignore",json={
        'revision':remaining['revision'],'face_ids':remaining['member_ids']}).json()
    run_id = start(client,setup)
    redetection.process_redetection(run_id)
    run = db_session.get(SceneRedetection,run_id)
    assert run.summary['matched_existing']==3
    client.post(f'/api/v1/review/redetections/{run_id}/apply',json={'face_ids':[run.proposals[0]['id']]})
    groups = client.get(f"/api/v1/review/batches/{setup['batch']}/clusters").json()
    assert next(c for c in groups if c['id']==confirmed['id'])['confirmed_ids']==confirmed['confirmed_ids']
    assert next(c for c in groups if c['id']==ignored['id'])['state']=='ignored'
    assert client.get('/api/v1/review/people').json()[0]['reference_count']==1


def test_stale_preview_cannot_apply_and_discard_leaves_decisions(client, db_session, setup_redetection):
    setup = setup_redetection
    run_id = start(client,setup)
    redetection.process_redetection(run_id)
    run = db_session.get(SceneRedetection,run_id)
    cluster = next(c for c in client.get(f"/api/v1/review/batches/{setup['batch']}/clusters").json() if c['scene_id']==setup['scene']['scene_id'])
    client.post(f"/api/v1/review/clusters/{cluster['id']}/assign",json={'revision':cluster['revision'],'state':'unresolved'})
    assert client.post(f'/api/v1/review/redetections/{run_id}/apply',json={'face_ids':[run.proposals[0]['id']]}).status_code==409
    assert client.post(f'/api/v1/review/redetections/{run_id}/discard').status_code==200
    assert db_session.get(ReviewEvidence,run.proposals[0]['id']) is None


def test_threshold_and_active_job_validation(client, setup_redetection):
    setup = setup_redetection
    url=f"/api/v1/review/batches/{setup['batch']}/scenes/{setup['scene']['scene_id']}/redetection"
    assert client.post(url,json={'threshold':0.01}).status_code==422
    assert client.post(url,json={'threshold':1.01}).status_code==422
    start(client,setup)
    assert client.post(url,json={'threshold':0.4}).status_code==409


def test_missing_frame_fails_without_changing_evidence(client, db_session, setup_redetection):
    setup=setup_redetection
    run_id=start(client,setup)
    run=db_session.get(SceneRedetection,run_id)
    frame=db_session.get(Frame,run.frame_ids[0])
    (setup['config'].appdata_path/frame.relative_path).unlink()
    before=set(db_session.scalars(select(ReviewEvidence.id)))
    redetection.process_redetection(run_id)
    assert run.state=='failed' and 'unavailable' in run.error
    assert db_session.get(ProcessingJob,run.job_id).state=='failed'
    assert set(db_session.scalars(select(ReviewEvidence.id)))==before


def test_queue_failure_restores_batch(client, db_session, setup_redetection, monkeypatch):
    setup=setup_redetection
    class BrokenQueue:
        def __init__(self, **kwargs):
            pass
        def enqueue(self, *args, **kwargs):
            raise RuntimeError('Redis unavailable')
    monkeypatch.setattr(redetection,'Queue',BrokenQueue)
    response=client.post(f"/api/v1/review/batches/{setup['batch']}/scenes/{setup['scene']['scene_id']}/redetection",json={'threshold':0.4})
    assert response.status_code==503
    assert client.get(f"/api/v1/batches/{setup['batch']}").json()['state']=='video_processing_complete'
    assert db_session.scalar(select(SceneRedetection)).state=='failed'


def test_apply_rejects_empty_and_foreign_selection(client, db_session, setup_redetection):
    run_id=start(client,setup_redetection)
    redetection.process_redetection(run_id)
    url=f'/api/v1/review/redetections/{run_id}/apply'
    assert client.post(url,json={'face_ids':[]}).status_code==422
    assert client.post(url,json={'face_ids':['not-a-preview-face']}).status_code==422
    assert db_session.get(SceneRedetection,run_id).state=='ready'


def test_candidate_frame_is_available_before_acceptance(client, db_session, setup_redetection):
    setup = setup_redetection
    run_id = start(client, setup)
    redetection.process_redetection(run_id)
    data = client.get(f"/api/v1/review/batches/{setup['batch']}/scenes/{setup['scene']['scene_id']}/redetection").json()
    candidate = data['proposals'][0]
    assert db_session.get(ReviewEvidence, candidate['id']) is None
    result = client.get(candidate['frame_context_url'])
    assert result.status_code == 200
    assert result.json()['bbox'] == [70,70,20,20]
    frame = db_session.get(Frame,candidate['frame_id'])
    assert result.json()['url'] == f'/generated/{frame.relative_path}'
    assert client.get(f'/api/v1/review/redetections/{run_id}/candidates/missing/frame').status_code == 404
    (setup['config'].appdata_path / frame.relative_path).unlink()
    assert client.get(candidate['frame_context_url']).status_code == 404
