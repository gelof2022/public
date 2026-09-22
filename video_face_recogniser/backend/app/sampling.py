"""Whole-timeline candidate selection, independent of face/identity decisions."""
import math

import cv2
import numpy as np


def choose_candidates(images, fps: float, max_interval: float, budget: int, threshold: float) -> list[int]:
    """Keep temporal anchors plus distinct, sharp transition observations.

    Low-resolution candidates are scanned to EOF before applying the storage budget.
    Anchors are never removed as duplicates: static sections still need coverage.
    """
    count = len(images)
    if not count:
        return []
    if count <= budget and count <= 3:
        return list(range(count))
    duration = count / fps
    interval = min(max_interval, max(1 / fps, duration / 24))
    anchor_count = min(count, budget, max(2, math.ceil(duration / interval) + 1))
    # Reserve some budget for transitions while preserving regular coverage.
    if anchor_count >= budget:
        anchor_count = min(count, max(2, math.ceil(budget * 0.75)))
    differences = np.zeros(count)
    sharpness = np.zeros(count)
    for i, im in enumerate(images):
        sharpness[i] = cv2.Laplacian(im, cv2.CV_64F).var()
        if i:
            differences[i] = np.abs(im.astype(float) - images[i - 1].astype(float)).mean() / 255
    # Endpoints and one sharp representative per time bin guarantee full coverage.
    selected = {0, count - 1}
    for chunk in np.array_split(np.arange(1, count - 1), max(1, anchor_count - 2)):
        if len(chunk) and len(selected) < anchor_count:
            selected.add(int(chunk[np.argmax(sharpness[chunk])]))
    events = []
    for i in range(1, count):
        neighborhood = differences[max(0, i - 4):min(count, i + 5)]
        baseline = float(np.median(neighborhood))
        if differences[i] >= max(threshold * 0.25, baseline * 2.5):
            # Prefer the sharper observation at/just after a transition.
            best = max(range(i, min(count, i + 3)), key=lambda j: sharpness[j])
            events.append((differences[i], best))
    for _, i in sorted(events, reverse=True):
        if len(selected) >= budget:
            break
        nearest = min(selected, key=lambda j: abs(j - i))
        # Suppress near duplicates only among extras, never temporal anchors.
        delta = np.abs(images[i].astype(float) - images[nearest].astype(float)).mean() / 255
        if abs(i - nearest) >= 1 and delta > 0.025:
            selected.add(i)
    return sorted(selected)
