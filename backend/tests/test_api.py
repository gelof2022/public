from app.models import Frame, Library, StashScene


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_list_batch(client, db_session):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(
        library=library, stash_scene_id="42", title="Birthday",
        source_path="/media/birthday.mp4", stash_path="/data/birthday.mp4", existing_tag_ids=[],
    )
    db_session.add(scene)
    db_session.commit()
    response = client.post("/api/v1/batches", json={"name": "First batch", "scene_ids": [scene.id]})
    assert response.status_code == 201
    assert response.json()["state"] == "created"
    assert response.json()["scene_count"] == 1
    listed = client.get("/api/v1/batches")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "First batch"


def test_rejects_unknown_scene(client):
    response = client.post(
        "/api/v1/batches", json={"name": "Bad batch", "scene_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert response.status_code == 422


def test_scene_search_matches_source_path(client, db_session):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(
        library=library, stash_scene_id="99", title="",
        source_path="/media/Canon/holiday.mp4", stash_path="/data/Canon/holiday.mp4", existing_tag_ids=[],
    )
    db_session.add(scene)
    db_session.commit()
    response = client.get("/api/v1/scenes?query=Canon&per_page=50")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == scene.id


def test_start_processing_queues_persistent_job(client, db_session, monkeypatch):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(
        library=library, stash_scene_id="100", title="Queued",
        source_path="/media/queued.mp4", stash_path="/data/queued.mp4", existing_tag_ids=[],
    )
    db_session.add(scene)
    db_session.commit()
    batch = client.post("/api/v1/batches", json={"name": "Queue me", "scene_ids": [scene.id]}).json()

    queued = []
    class FakeQueue:
        def __init__(self, connection):
            self.connection = connection
        def enqueue(self, *args, **kwargs):
            queued.append((args, kwargs))

    monkeypatch.setattr("app.main.Queue", FakeQueue)
    response = client.post(f"/api/v1/batches/{batch['id']}/process")
    assert response.status_code == 202
    assert response.json()["state"] == "queued"
    assert response.json()["progress_total"] == 1
    assert queued[0][0][0] == "app.processing.process_batch"


def test_scene_processing_filter_and_bulk_ids(client, db_session):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    done = StashScene(
        library=library, stash_scene_id="201", title="Done", source_path="/media/done.mp4",
        stash_path="/data/done.mp4", existing_tag_ids=[],
    )
    waiting = StashScene(
        library=library, stash_scene_id="202", title="Waiting", source_path="/media/waiting.mp4",
        stash_path="/data/waiting.mp4", existing_tag_ids=[],
    )
    db_session.add_all([done, waiting])
    db_session.flush()
    db_session.add(Frame(
        scene_id=done.id, source_fingerprint="a" * 64, sampling_key="b" * 64,
        timestamp_ms=0, relative_path="frames/done.jpg",
    ))
    db_session.commit()

    processed = client.get("/api/v1/scenes?processed=processed").json()
    assert processed["total"] == 1
    assert processed["items"][0]["processed"] is True
    assert processed["items"][0]["frame_count"] == 1
    unprocessed = client.get("/api/v1/scenes/ids?processed=unprocessed").json()
    assert unprocessed == {"ids": [waiting.id], "total": 1}


def test_delete_batch(client, db_session):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(
        library=library, stash_scene_id="301", title="Delete me", source_path="/media/delete.mp4",
        stash_path="/data/delete.mp4", existing_tag_ids=[],
    )
    db_session.add(scene)
    db_session.commit()
    batch = client.post("/api/v1/batches", json={"name": "Delete me", "scene_ids": [scene.id]}).json()
    response = client.delete(f"/api/v1/batches/{batch['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/v1/batches/{batch['id']}").status_code == 404


def test_active_batch_cannot_be_deleted(client, db_session):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(
        library=library, stash_scene_id="302", title="Active", source_path="/media/active.mp4",
        stash_path="/data/active.mp4", existing_tag_ids=[],
    )
    db_session.add(scene)
    db_session.commit()
    batch = client.post("/api/v1/batches", json={"name": "Active", "scene_ids": [scene.id]}).json()
    db_session.get(__import__("app.models", fromlist=["Batch"]).Batch, batch["id"]).state = "processing"
    db_session.commit()
    assert client.delete(f"/api/v1/batches/{batch['id']}").status_code == 409


def test_frame_gallery_pagination(client, db_session):
    from app.models import Batch, BatchFrame

    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(
        library=library, stash_scene_id="gallery", title="", duration_seconds=125, source_path="/media/holiday.webm",
        stash_path="/data/holiday.webm", existing_tag_ids=[],
    )
    batch = Batch(name="Gallery", state="created", selection={})
    db_session.add_all([scene, batch])
    db_session.flush()
    for timestamp in [2000, 0, 1000]:
        frame = Frame(scene_id=scene.id, source_fingerprint="a" * 64, sampling_key="b" * 64,
                      timestamp_ms=timestamp, relative_path=f"frames/{timestamp}.jpg")
        db_session.add(frame)
        db_session.flush()
        db_session.add(BatchFrame(batch_id=batch.id, frame_id=frame.id))
    db_session.commit()
    path = f"/api/v1/batches/{batch.id}/frames"
    first = client.get(f"{path}?limit=2").json()
    second = client.get(f"{path}?limit=2&offset=2").json()
    assert [f["timestamp_seconds"] for f in first + second] == [0, 1, 2]
    assert first[0]["scene_title"] == "holiday.webm"
    assert first[0]["scene_duration_seconds"] == 125
    assert first[0]["scene_thumbnail_url"] is None
    assert first[0]["video_url"] == f"/api/v1/scenes/{scene.id}/video"
    assert client.get(f"{path}?offset=3").json() == []
    assert client.get("/api/v1/batches/missing/frames").status_code == 404


def test_video_inline_ranges_and_media_boundary(client, db_session, tmp_path):
    from app.config import Settings, get_settings

    media = tmp_path / "media"
    media.mkdir()
    video = media / "clip.webm"
    video.write_bytes(b"0123456789")
    outside = tmp_path / "outside.webm"
    outside.write_bytes(b"private")
    (media / "escape.webm").symlink_to(outside)
    client.app.dependency_overrides[get_settings] = lambda: Settings(media_path_prefix=str(media))
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix=str(media))
    scene = StashScene(library=library, stash_scene_id="play", source_path=str(video),
                       stash_path="/data/clip.webm", existing_tag_ids=[])
    db_session.add(scene)
    db_session.commit()
    url = f"/api/v1/scenes/{scene.id}/video"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/webm"
    assert response.headers["content-disposition"].startswith("inline;")
    partial = client.get(url, headers={"Range": "bytes=2-5"})
    assert partial.status_code == 206
    assert partial.content == b"2345"
    for path in [outside, media / "escape.webm", media / "missing.mp4"]:
        scene.source_path = str(path)
        db_session.commit()
        assert client.get(url).status_code == 404
    assert client.get("/api/v1/scenes/missing/video").status_code == 404


def test_face_detection_queue_and_listing(client, db_session, monkeypatch):
    from app.models import Batch, BatchFrame, FaceObservation
    from app.faces import MODEL_KEY
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(library=library, stash_scene_id="faces", title="People", source_path="/media/people.mp4",
                       stash_path="/data/people.mp4", existing_tag_ids=[])
    batch = Batch(name="Faces", state="video_processing_complete", selection={})
    db_session.add_all([scene, batch])
    db_session.flush()
    frame = Frame(scene_id=scene.id, source_fingerprint="a"*64, sampling_key="b"*64,
                  timestamp_ms=2500, relative_path="frame.jpg")
    db_session.add(frame)
    db_session.flush()
    db_session.add(BatchFrame(batch_id=batch.id, frame_id=frame.id))
    db_session.add(FaceObservation(frame_id=frame.id, model_key=MODEL_KEY, bbox=[1,2,30,40],
        landmarks=[], detector_confidence=0.9, quality=0.5, usable=True, quality_details={}, relative_path="faces/test.jpg"))
    db_session.commit()
    calls = []
    class Queue:
        def __init__(self, **kwargs):
            pass
        def enqueue(self, *args, **kwargs):
            calls.append(args)
    monkeypatch.setattr("app.main.Queue", Queue)
    result = client.get(f"/api/v1/batches/{batch.id}/faces").json()
    assert result[0]["timestamp_seconds"] == 2.5
    assert result[0]["scene_title"] == "People"
    assert client.get(f"/api/v1/batches/{batch.id}/faces?offset=1").json() == []
    assert client.post(f"/api/v1/batches/{batch.id}/detect-faces").status_code == 202
    assert calls[0][0] == "app.faces.process_faces"
    assert client.post(f"/api/v1/batches/{batch.id}/detect-faces").status_code == 409
    assert client.post(f"/api/v1/batches/{batch.id}/process").status_code == 409


def test_grouping_requires_detection_and_hides_stale_results(client, db_session, monkeypatch):
    from app.models import Batch, BatchFrame, FaceAnalysis, FaceObservation, FaceGrouping
    from app.faces import MODEL_KEY
    from app.grouping import MODEL_KEY as EMBED_KEY, ALGORITHM_KEY, input_key
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(library=library, stash_scene_id="groups", source_path="/media/group.mp4", stash_path="/data/group.mp4", existing_tag_ids=[])
    batch = Batch(name="Group", state="video_processing_complete", selection={})
    db_session.add_all([scene,batch])
    db_session.flush()
    frame = Frame(scene_id=scene.id,source_fingerprint="a"*64,sampling_key="b"*64,timestamp_ms=0,relative_path="frame.jpg")
    db_session.add(frame)
    db_session.flush()
    db_session.add(BatchFrame(batch_id=batch.id,frame_id=frame.id))
    db_session.commit()
    assert client.post(f"/api/v1/batches/{batch.id}/group-faces").status_code == 409
    face = FaceObservation(frame_id=frame.id,model_key=MODEL_KEY,bbox=[],landmarks=[],detector_confidence=0.9,
                           quality=0.8,usable=True,quality_details={},relative_path="face.jpg")
    db_session.add(face)
    db_session.flush()
    db_session.add(FaceAnalysis(frame_id=frame.id,model_key=MODEL_KEY,face_count=1))
    db_session.add(FaceGrouping(batch_id=batch.id,scene_id=scene.id,input_key=input_key([face]),model_key=EMBED_KEY,
                               algorithm_key=ALGORITHM_KEY,groups=[{"face_ids":[face.id],"representative_ids":[face.id]}]))
    db_session.commit()
    assert client.get(f"/api/v1/batches/{batch.id}/face-groups").json()[0]['status'] == 'ready'
    face.usable=False
    db_session.commit()
    stale=client.get(f"/api/v1/batches/{batch.id}/face-groups").json()[0]
    assert stale['status']=='stale' and stale['groups']==[]
    calls=[]
    class Queue:
        def __init__(self, **kwargs): pass
        def enqueue(self, *args, **kwargs): calls.append(args)
    monkeypatch.setattr('app.main.Queue',Queue)
    assert client.post(f"/api/v1/batches/{batch.id}/group-faces").status_code==202
    assert calls[0][0]=='app.grouping.process_groups'
    assert client.post(f"/api/v1/batches/{batch.id}/group-faces").status_code==409
