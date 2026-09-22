import cv2
import numpy as np

from app.faces import MODEL_KEY, analyse_frame, detect_observations, face_quality, make_detector
from app.config import Settings
from app.models import FaceAnalysis, FaceObservation, Frame, Library, StashScene
from sqlalchemy import select


def test_bundled_detector_loads_and_blank_has_no_faces():
    assert detect_observations(np.zeros((320, 320, 3), dtype=np.uint8), make_detector()) == []


def test_quality_rejects_blank_and_small_faces():
    blank = np.zeros((60, 60, 3), dtype=np.uint8)
    score, usable, details = face_quality(blank, 60)
    assert score == 0 and not usable
    assert details["exposure"] == 0
    textured = np.random.default_rng(5).integers(20, 230, (60, 60, 3), dtype=np.uint8)
    assert face_quality(textured, 60)[1]
    assert not face_quality(textured, 16)[1]


def test_detection_coordinates_and_crops_are_clipped():
    class Detector:
        def setInputSize(self, size):
            assert size == (100, 100)
        def detect(self, image):
            return None, np.array([[-10, -5, 50, 45, *([20, 20] * 5), 0.95]], dtype=np.float32)
    result = detect_observations(np.full((100, 100, 3), 128, dtype=np.uint8), Detector())
    assert result[0]["bbox"] == [0, 0, 40, 40]
    assert result[0]["crop"].shape == (50, 50, 3)
    assert not result[0]["usable"]


def test_no_face_result_is_cached(db_session, tmp_path):
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(library=library, stash_scene_id="blank", source_path="/media/blank.mp4", stash_path="/data/blank.mp4", existing_tag_ids=[])
    db_session.add(scene)
    db_session.flush()
    frame = Frame(scene_id=scene.id, source_fingerprint="a"*64, sampling_key="b"*64,
                  timestamp_ms=0, relative_path="blank.jpg")
    db_session.add(frame)
    db_session.commit()
    cv2.imwrite(str(tmp_path / "blank.jpg"), np.zeros((320, 320, 3), dtype=np.uint8))
    config = Settings(appdata_path=tmp_path)
    analyse_frame(db_session, frame, config, make_detector())
    assert db_session.get(FaceAnalysis, (frame.id, MODEL_KEY)).face_count == 0
    assert db_session.scalars(select(FaceObservation)).all() == []
    analyse_frame(db_session, frame, config, None)  # cache reuse must not call a detector


def test_face_crop_persistence_and_missing_crop_recovery(db_session, tmp_path):
    class Detector:
        def setInputSize(self, size):
            pass
        def detect(self, image):
            return None, np.array([[10, 10, 50, 50, *([20, 20] * 5), 0.95]], dtype=np.float32)
    library = Library(name="Test", stash_url="http://stash", stash_path_prefix="/data", media_path_prefix="/media")
    scene = StashScene(library=library, stash_scene_id="face", source_path="/media/face.mp4", stash_path="/data/face.mp4", existing_tag_ids=[])
    db_session.add(scene)
    db_session.flush()
    frame = Frame(scene_id=scene.id, source_fingerprint="a"*64, sampling_key="b"*64,
                  timestamp_ms=12500, relative_path="face.jpg")
    db_session.add(frame)
    db_session.commit()
    cv2.imwrite(str(tmp_path / "face.jpg"), np.full((100, 100, 3), 128, dtype=np.uint8))
    config = Settings(appdata_path=tmp_path)
    analyse_frame(db_session, frame, config, Detector())
    first = db_session.scalars(select(FaceObservation)).one()
    first_id = first.id
    (tmp_path / first.relative_path).unlink()
    analyse_frame(db_session, frame, config, Detector())
    current = db_session.scalars(select(FaceObservation)).one()
    assert current.id != first_id
    assert (tmp_path / current.relative_path).is_file()
    assert db_session.get(FaceAnalysis, (frame.id, MODEL_KEY)).face_count == 1


def test_detector_uses_requested_confidence(monkeypatch):
    seen=[]
    monkeypatch.setattr(cv2.FaceDetectorYN,'create',lambda *args:seen.append(args) or object())
    make_detector(0.35)
    assert seen[0][3]==0.35
