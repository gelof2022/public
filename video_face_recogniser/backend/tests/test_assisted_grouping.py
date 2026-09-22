import math

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.appearance import contextual_box, enrich_items, feature, make_model, model_key
from app.config import Settings
from app.grouping import ALGORITHM_KEY, algorithm_key, cluster_faces, pose_bucket
from app.models import AppearanceEmbedding, FaceGrouping, FaceObservation, Frame, ReviewEvidence
from test_review import review_data as imported_review_data


def item(name, angle, seconds=0, appearance=0, frame=None, quality=.8):
    radians = math.radians(angle)
    vector = np.zeros(128)
    vector[:2] = [math.cos(radians),math.sin(radians)]
    app = np.zeros(1280)
    app[:2] = [math.cos(math.radians(appearance)),math.sin(math.radians(appearance))]
    return dict(id=name,frame_id=frame or name,scene_id='scene',quality=quality,vector=vector,
                timestamp_ms=seconds*1000,appearance=app)


@pytest.mark.parametrize('angle,appearance,seconds,expected',[
    (10,180,5000,1), # strong face ignores clothing and temporal disagreement
    (56,180,5000,1), # normal existing face threshold
    (62,0,18,1), # .469 face plus appearance/time
    (62,70,18,2),
    (70,0,1,2), # weak face cannot be rescued
    (62,0,31,2), # temporal gate only for assisted
])
def test_face_first_rules(angle,appearance,seconds,expected):
    assert len(cluster_faces([item('a',0),item('b',angle,seconds,appearance)]))==expected


def test_hard_constraints_and_missing_support():
    a,b = item('a',0),item('b',62,1,frame='a')
    assert len(cluster_faces([a,b]))==2
    b['frame_id']='b'
    b['scene_id']='another'
    assert len(cluster_faces([a,b]))==2
    b['scene_id']='scene'
    b.pop('appearance')
    assert len(cluster_faces([a,b]))==2
    b['appearance']=a['appearance']
    b.pop('timestamp_ms')
    assert len(cluster_faces([a,b]))==2
    assert len(cluster_faces([a,item('b',62,1)],config=Settings(appearance_enabled=False)))==2


def test_assistance_checks_worst_pair_not_just_centroid():
    # a-b merge normally; c has one strong appearance/face edge but weak a-c face.
    result=cluster_faces([item('a',0),item('b',40),item('c',80)])
    assert len(result)==2


def test_duplicate_evidence_does_not_dominate_and_observations_are_retained():
    # a-b normal merge, but c conflicts with a; repeating b must not overcome the average.
    base=[item('a',0,100,quality=1),item('b',50,0,quality=.9),item('c',100,200)]
    repeated=base+[item(f'b{i}',50,i*.1,quality=.85) for i in range(1,20)]
    diagnostic={}
    result=cluster_faces(repeated,diagnostics=diagnostic)
    assert len(result)==len(cluster_faces(base))==2
    assert sum(len(g['face_ids']) for g in result)==len(repeated)
    assert len(diagnostic['duplicate_families'][0])==20
    assert all(not {'a','c'} <= set(g['face_ids']) for g in result)
    assert len(diagnostic['decisions'])<=200


def test_duplicate_families_do_not_chain_across_time_or_same_frame():
    diagnostic={}
    cluster_faces([item('a',0,0),item('b',0,2),item('c',0,4)],diagnostics=diagnostic)
    assert max(map(len,diagnostic['duplicate_families']))==2
    result=cluster_faces([item('a',0,0,frame='x'),item('b',0,1),item('c',0,2,frame='x')])
    assert not any({'a','c'}<=set(g['face_ids']) for g in result)


def test_representatives_include_diverse_views_and_skip_duplicates():
    faces=[item('front',0,0,quality=1),item('copy',1,1,quality=.99),
           item('left',20,20,quality=.8),item('right',-20,40,quality=.8)]
    for f,nose in zip(faces,[50,50,35,65]):
        f['landmarks']=[[30,30],[70,30],[nose,50],[35,70],[65,70]]
    assert pose_bucket(faces[0])=='front'
    assert set(cluster_faces(faces)[0]['representative_ids'])=={'front','left','right'}


def test_rules_and_crop_settings_are_versioned():
    config=Settings()
    assert len(ALGORITHM_KEY)==64
    assert ALGORITHM_KEY != 'average-link-agglomerative-v2:cos0.50:floor0.25:no-same-frame'
    assert algorithm_key(config)!=algorithm_key(Settings(grouping_appearance_min=.95))
    assert model_key(config)!=model_key(Settings(appearance_crop_below=1.5))


def test_crop_geometry_bounds_and_ambiguity():
    config=Settings()
    box,reason=contextual_box((500,500,3),[200,100,50,50],config)
    assert box==[162,87,125,163] and reason=='suitable'
    assert contextual_box((500,500,3),[200,460,50,40],config)[0] is None
    assert contextual_box((500,500,3),[200,100,20,20],config)[0] is None
    assert contextual_box((500,500,3),[200,100,50,50],config,[[220,180,40,40]])[1]=='crowded_context'


def test_bundled_appearance_model_is_deterministic_normalized():
    net=make_model()
    image=np.random.default_rng(42).integers(0,256,(240,240,3),dtype=np.uint8)
    first=feature(image,[0,0,240,240],net)
    assert len(first)==1280 and np.linalg.norm(first)==pytest.approx(1)
    np.testing.assert_allclose(first,feature(image,[0,0,240,240],net),atol=1e-6)


@pytest.fixture()
def seeded(db_session,client):
    return imported_review_data.__wrapped__(db_session,client)


def test_cache_reuse_and_crop_key_invalidation(db_session,client,seeded,tmp_path,monkeypatch):
    import app.appearance as module
    face=db_session.scalar(select(FaceObservation))
    frame=db_session.get(Frame,face.frame_id)
    face.bbox=[200,100,50,50]
    db_session.commit()
    path=tmp_path/frame.relative_path
    path.parent.mkdir(parents=True)
    cv2.imwrite(str(path),np.ones((500,500,3),dtype=np.uint8)*100)
    calls=[]
    monkeypatch.setattr(module,'make_model',lambda:None)
    monkeypatch.setattr(module,'feature',lambda *args:calls.append(1) or [1.]+[0.]*1279)
    config=Settings(appdata_path=tmp_path)
    items=[dict(id=face.id,frame_id=face.frame_id)]
    assert enrich_items(db_session,items,[face],config)['computed']==1
    assert enrich_items(db_session,items,[face],config)['cached']==1
    assert len(calls)==1
    assert enrich_items(db_session,items,[face],Settings(appdata_path=tmp_path,appearance_crop_below=1.5))['computed']==1
    assert len(db_session.scalars(select(AppearanceEmbedding)).all())==2


def test_appearance_never_affects_person_suggestions(db_session,client,seeded):
    clusters=client.get(f'/api/v1/review/batches/{seeded}/clusters').json()
    source,target=clusters
    pid=client.post('/api/v1/review/people',json={'name':'Reference'}).json()['id']
    client.post(f"/api/v1/review/clusters/{source['id']}/assign",json={'revision':1,'person_id':pid,'all_faces':True})
    url=f"/api/v1/review/clusters/{target['id']}/suggestions"
    before=client.get(url).json()
    for f in db_session.scalars(select(ReviewEvidence)):
        db_session.add(AppearanceEmbedding(face_id=f.id,scene_id=f.scene_id,model_key=model_key(Settings()),
            input_key='x'*64,vector=[-1.]+[0.]*1279,details={}))
    db_session.commit()
    assert client.get(url).json()==before
    assert client.get('/api/v1/review/people').json()[0]['reference_count']==3


def test_old_grouping_is_stale_without_destroying_review(db_session,client,seeded):
    before=client.get(f'/api/v1/review/batches/{seeded}/clusters').json()
    for grouping in db_session.scalars(select(FaceGrouping)):
        grouping.algorithm_key='previous-version'
    db_session.commit()
    assert all(g['status']=='stale' for g in client.get(f'/api/v1/batches/{seeded}/face-groups').json())
    assert client.post(f'/api/v1/review/batches/{seeded}/prepare').json()['created']==0
    assert client.get(f'/api/v1/review/batches/{seeded}/clusters').json()==before


def test_recompute_populates_cache_and_preserves_manual_review(db_session,client,seeded,tmp_path,monkeypatch):
    from contextlib import nullcontext
    from app import grouping, appearance
    from app.models import ProcessingJob
    config=Settings(appdata_path=tmp_path)
    for face in db_session.scalars(select(FaceObservation)):
        face.bbox=[200,100,50,50]
    for frame in db_session.scalars(select(Frame)):
        path=tmp_path/frame.relative_path
        path.parent.mkdir(parents=True,exist_ok=True)
        cv2.imwrite(str(path),np.full((500,500,3),100,dtype=np.uint8))
    for saved in db_session.scalars(select(FaceGrouping)):
        saved.algorithm_key='old'
    job=ProcessingJob(batch_id=seeded,job_type='face_grouping',state='queued',payload={'previous_state':'video_processing_complete'})
    db_session.add(job)
    db_session.commit()
    before=client.get(f'/api/v1/review/batches/{seeded}/clusters').json()
    monkeypatch.setattr(grouping,'SessionLocal',lambda:nullcontext(db_session))
    monkeypatch.setattr(grouping,'get_settings',lambda:config)
    monkeypatch.setattr(grouping,'make_recognizer',lambda:object())
    monkeypatch.setattr(appearance,'make_model',lambda:object())
    calls=[]
    monkeypatch.setattr(appearance,'feature',lambda *args:calls.append(1) or [1.]+[0.]*1279)
    grouping.process_groups(seeded,job.id)
    assert job.state=='complete',job.error
    assert len(calls)==6
    assert all(g.algorithm_key==ALGORITHM_KEY and g.diagnostics['appearance']['computed']==3
               for g in db_session.scalars(select(FaceGrouping)))
    assert client.get(f'/api/v1/review/batches/{seeded}/clusters').json()==before
    grouping.process_groups(seeded,job.id)
    assert len(calls)==6
    assert client.post(f'/api/v1/review/batches/{seeded}/prepare').json()['created']==0
