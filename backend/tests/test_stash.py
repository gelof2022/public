import pytest

from app.stash import StashError, map_stash_path, normalise_scene


def test_path_mapping_is_prefix_aware():
    assert map_stash_path("/data/family/movie.mp4", "/data", "/media") == "/media/family/movie.mp4"


def test_path_mapping_rejects_outside_path():
    with pytest.raises(StashError):
        map_stash_path("/database/movie.mp4", "/data", "/media")


def test_normalise_scene_uses_original_file():
    scene = normalise_scene(
        {"id": "7", "title": "Holiday", "date": "2020-01-02", "updated_at": "2026-01-01T00:00:00Z",
         "paths": {"screenshot": "/scene/7/screenshot"},
         "tags": [{"id": "3"}], "files": [{"path": "/data/a.mp4", "size": "123", "duration": 4.5,
                                                "frame_rate": 25.0, "format": "mp4", "audio_codec": "aac",
                                                "bit_rate": 1000000, "created_at": "2020-01-01T00:00:00Z",
                                                "mod_time": "2020-01-02T00:00:00Z"}]},
        "/data", "/media",
    )
    assert scene is not None
    assert scene.source_path == "/media/a.mp4"
    assert scene.file_size == 123
    assert scene.frame_rate == 25.0
    assert scene.thumbnail_url == "/scene/7/screenshot"
    assert scene.media_format == "mp4"
    assert scene.audio_codec == "aac"
    assert scene.bit_rate == 1000000
    assert scene.existing_tag_ids == ["3"]
