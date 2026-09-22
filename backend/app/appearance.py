"""Offline contextual visual features, exclusively for intra-video grouping."""
import hashlib
import json
import logging
import time
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select

from app.models import AppearanceEmbedding, FaceObservation, Frame, ReviewEvidence, utcnow

MODEL_PATH = Path(__file__).resolve().parents[1] / 'models' / 'mobilenetv2-7.onnx'
MODEL_SHA256 = 'c1c513582d56afceff8516c73804e484c81c6a830712ab6d682253f4a3cd042f'
POOL = 'onnx_node!mobilenetv20_features_pool0_fwd'


def model_key(config):
    return hashlib.sha256(json.dumps([MODEL_SHA256, 'opencv4.11:pool1280:rgb224:imagenet:l2:crop-v1',
        config.appearance_crop_side, config.appearance_crop_above, config.appearance_crop_below]).encode()).hexdigest()


def make_model():
    if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError('Bundled MobileNetV2 checksum mismatch')
    net = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    return net


def contextual_box(shape, bbox, config, neighbors=()):
    if not bbox or len(bbox) != 4 or not np.isfinite(bbox).all():
        return None, 'missing_location'
    x, y, w, h = bbox
    if min(w, h) < 32:
        return None, 'small_face'
    height, width = shape[:2]
    left, top = max(0, int(x-w*config.appearance_crop_side)), max(0, int(y-h*config.appearance_crop_above))
    right, bottom = min(width, int(x+w*(1+config.appearance_crop_side))), min(height, int(y+h*(1+config.appearance_crop_below)))
    expected = w*(1+2*config.appearance_crop_side)*h*(1+config.appearance_crop_above+config.appearance_crop_below)
    if right <= left or bottom <= top or (right-left)*(bottom-top) < expected*0.6 or bottom < y+1.5*h:
        return None, 'clipped_context'
    for other in neighbors:
        if not other or len(other) != 4:
            continue
        ox, oy, ow, oh = other
        # Any other face centre inside the contextual crop makes it ambiguous.
        if left <= ox+ow/2 <= right and top <= oy+oh/2 <= bottom:
            return None, 'crowded_context'
    return [left, top, right-left, bottom-top], 'suitable'


def feature(image, box, net):
    x, y, w, h = box
    rgb = cv2.cvtColor(cv2.resize(image[y:y+h, x:x+w], (224,224)), cv2.COLOR_BGR2RGB).astype(np.float32)/255
    rgb = (rgb-np.array([.485,.456,.406],dtype=np.float32))/np.array([.229,.224,.225],dtype=np.float32)
    net.setInput(rgb.transpose(2,0,1)[None])
    vector = net.forward(POOL).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if vector.shape != (1280,) or not np.isfinite(vector).all() or norm < 1e-8:
        raise RuntimeError('Invalid MobileNet appearance embedding')
    return (vector/norm).tolist()


def enrich_items(db, items, faces, config):
    """Populate only same-scene features; cache unsuitable crops too. No Person access."""
    start = time.perf_counter()
    stats = {'computed':0, 'cached':0, 'unavailable':0}
    key = model_key(config)
    frame_ids = {face.frame_id for face in faces}
    frames = {f.id:f for f in db.scalars(select(Frame).where(Frame.id.in_(frame_ids)))}
    originals = {f.id:f for f in db.scalars(select(FaceObservation).where(FaceObservation.frame_id.in_(frame_ids)))}
    locations = {}
    for face in [*originals.values(), *db.scalars(select(ReviewEvidence).where(ReviewEvidence.frame_id.in_(frame_ids)))]:
        if getattr(face, 'bbox', None):
            locations.setdefault(face.frame_id, {})[face.id] = face.bbox
    sources = {face.id:face for face in faces}
    net, image, previous = None, None, None
    for item in sorted(items, key=lambda i:(i['frame_id'],i['id'])):
        face = sources[item['id']]
        frame = frames.get(face.frame_id)
        original = originals.get(face.id)
        item['timestamp_ms'] = frame.timestamp_ms if frame else getattr(face, 'timestamp_ms', None)
        item['scene_id'] = frame.scene_id if frame else getattr(face, 'scene_id', None)
        item['landmarks'] = getattr(face, 'landmarks', None) or getattr(original, 'landmarks', None)
        bbox = getattr(face, 'bbox', None) or getattr(original, 'bbox', None)
        if not config.appearance_enabled or frame is None or not bbox:
            stats['unavailable'] += 1
            continue
        neighbors = [b for fid,b in sorted(locations.get(frame.id, {}).items()) if fid != face.id]
        fingerprint = hashlib.sha256(json.dumps([frame.id,frame.source_fingerprint,frame.sampling_key,bbox,neighbors]).encode()).hexdigest()
        cached = db.get(AppearanceEmbedding, (face.id,key))
        if cached and cached.input_key == fingerprint:
            item['appearance'] = cached.vector
            stats['cached'] += 1
            continue
        if previous != frame.id:
            image = cv2.imread(str(config.appdata_path/frame.relative_path))
            previous = frame.id
        if image is None:
            stats['unavailable'] += 1
            continue
        box, reason = contextual_box(image.shape,bbox,config,neighbors)
        vector = None
        if box:
            if net is None:
                net = make_model()
            vector = feature(image,box,net)
            stats['computed'] += 1
        else:
            stats['unavailable'] += 1
        if cached is None:
            cached = AppearanceEmbedding(face_id=face.id,model_key=key,scene_id=frame.scene_id)
            db.add(cached)
        cached.input_key, cached.vector = fingerprint, vector
        cached.details, cached.created_at = {'crop':box,'reason':reason}, utcnow()
        item['appearance'] = vector
    db.flush()
    stats['seconds'] = round(time.perf_counter()-start,4)
    stats['model_key'] = key
    logging.getLogger(__name__).info('appearance_processing %s',json.dumps(stats))
    return stats
