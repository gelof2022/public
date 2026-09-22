import math
from types import SimpleNamespace

import numpy as np
import pytest

from app.grouping import cluster_faces, embedding, input_key, make_recognizer


def item(identifier, angle, quality, frame=None):
    vector = np.zeros(128)
    vector[:2] = [math.cos(math.radians(angle)), math.sin(math.radians(angle))]
    return {"id": identifier, "frame_id": frame or identifier, "quality": quality, "vector": vector}


def test_pair_floor_prevents_similarity_chain():
    groups = cluster_faces([item('a', 0, 3), item('b', 40, 2), item('c', 80, 1)])
    assert len(groups) == 2
    assert not any({'a', 'c'} <= set(group['face_ids']) for group in groups)


def test_global_pair_choice_and_simultaneous_faces_stay_separate():
    groups = cluster_faces([item('a', 0, 3), item('b', 80, 2), item('c', 40, 1)])
    assert len(groups) == 2
    assert len(cluster_faces([item('a', 0, 2, 'same'), item('b', 0, 1, 'same')])) == 2


def test_clustering_deterministic_under_input_reordering():
    items = [item('a', 0, 1), item('b', 2, 1), item('c', 90, 1)]
    assert cluster_faces(items) == cluster_faces(list(reversed(items)))


def test_embedding_uses_full_frame_landmarks_and_normalizes():
    class Recognizer:
        def alignCrop(self, image, row):
            assert row.shape == (15,)
            assert row[4:14].tolist() == [10, 20] * 5
            return image
        def feature(self, aligned):
            return np.ones((1, 128), dtype=np.float32)
    face = SimpleNamespace(id='face', bbox=[0,0,50,50], landmarks=[[10,20]]*5, detector_confidence=0.9)
    vector = embedding(np.zeros((100,100,3), dtype=np.uint8), face, Recognizer())
    assert len(vector) == 128
    assert np.linalg.norm(vector) == pytest.approx(1)
    face.landmarks = []
    with pytest.raises(ValueError, match='invalid landmarks'):
        embedding(None, face, Recognizer())


def test_input_key_changes_after_observations_change():
    face = SimpleNamespace(id='a',frame_id='f',usable=True,quality=0.8,model_key='detector')
    before = input_key([face])
    face.id = 'replacement'
    assert input_key([face]) != before


def test_bundled_sface_loads():
    assert make_recognizer() is not None


def test_moderate_similarity_groups_without_allowing_weak_chains():
    assert len(cluster_faces([item('a',0,2),item('b',56,1)])) == 1
    assert len(cluster_faces([item('a',0,2),item('b',56,1)], threshold=0.60)) == 2
    groups=cluster_faces([item('a',0,3),item('b',40,2),item('c',80,1)], threshold=0.40)
    assert len(groups)==2  # The pairwise floor blocks the weak a–c bridge.


def test_same_frame_exclusion_survives_group_merges():
    groups=cluster_faces([item('a',0,3,'frame-a'),item('b',3,2,'frame-b'),item('c',4,1,'frame-a')])
    assert len(groups)==2
    assert not any({'a','c'} <= set(group['face_ids']) for group in groups)


def test_explicit_low_threshold_broadens_without_removing_same_frame_rule():
    assert len(cluster_faces([item('a',0,1),item('b',80,.8)],threshold=.1))==1
    assert len(cluster_faces([item('a',0,1),item('b',80,.8)],threshold=.5))==2
    assert len(cluster_faces([item('a',0,1,'same'),item('b',80,.8,'same')],threshold=.1))==2
