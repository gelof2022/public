import subprocess
from pathlib import Path

import numpy as np

from app.config import Settings
from app.processing import extract_frames, probe_video, sampling_key, source_fingerprint


def test_source_fingerprint_and_sampling_key_are_stable(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    assert source_fingerprint(video) == source_fingerprint(video)
    assert sampling_key(Settings(frame_sample_interval_seconds=30)) == sampling_key(
        Settings(frame_sample_interval_seconds=30)
    )


def test_probe_video_reads_ffprobe_json(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"1234")
    payload = '{"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"25/1","codec_name":"h264"}],"format":{"duration":"12.5","size":"4","format_name":"mov,mp4"}}'
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, payload, ""))
    result = probe_video(video, 30)
    assert result["duration_seconds"] == 12.5
    assert result["file_size"] == 4
    assert result["width"] == 1920
    assert result["height"] == 1080
    assert result["frame_rate"] == 25.0
    assert result["video_codec"] == "h264"
    assert result["media_format"] == "mov,mp4"
    assert result["media_metadata"]["video"]["codec_name"] == "h264"


def test_extract_frames_preserves_timestamps(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    output = tmp_path / "frames"

    def fake_run(command, **kwargs):
        assert "-frames:v" not in command
        if "rawvideo" in command:
            Path(command[-1]).write_bytes(np.zeros((6, 36, 64), dtype=np.uint8).tobytes())
            return subprocess.CompletedProcess(command, 0, b"", b"")
        assert '-/filter:v' in command
        assert Path(command[command.index('-/filter:v')+1]).is_file()
        output.mkdir(parents=True, exist_ok=True)
        (output / "frame-000001.jpg").write_bytes(b"one")
        (output / "frame-000002.jpg").write_bytes(b"two")
        stderr = "[Parsed_showinfo] n: 0 pts: 0 pts_time:0.000\n[Parsed_showinfo] n: 1 pts: 625 pts_time:2.500"
        return subprocess.CompletedProcess(command, 0, "", stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    frames = extract_frames(video, output, Settings(max_frames_per_video=10))
    assert [timestamp for _, timestamp in frames] == [0, 2500]


def test_short_clip_gets_multiple_samples():
    from app.sampling import choose_candidates
    images = np.zeros((20, 36, 64), dtype=np.uint8)
    chosen = choose_candidates(images, 2, 30, 300, 0.35)
    assert len(chosen) > 5
    assert chosen[0] == 0 and chosen[-1] == 19


def test_frame_budget_spans_entire_video_even_with_early_changes():
    from app.sampling import choose_candidates
    rng = np.random.default_rng(123)
    images = np.zeros((2400, 36, 64), dtype=np.uint8)
    images[:300] = rng.integers(0, 255, (300, 36, 64), dtype=np.uint8)
    chosen = choose_candidates(images, 2, 30, 30, 0.35)
    assert len(chosen) <= 30
    assert chosen[0] == 0 and chosen[-1] == 2399
    assert any(1100 < i < 1300 for i in chosen)
    assert max(np.diff(chosen)) < 250


def test_sampling_cache_version_changed():
    import hashlib
    config = Settings()
    old = hashlib.sha256(b"adaptive-v1:30.0:0.35:300:1920").hexdigest()
    assert sampling_key(config) != old
    assert sampling_key(config) != sampling_key(Settings(frame_scan_fps=1))


def test_large_frame_plan_runs_in_real_ffmpeg(tmp_path):
    import shutil
    import pytest
    from app.processing import target_selection, SHOWINFO_TIMESTAMP
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg executable not installed')
    # More than the failing 145 timestamps; cover the supported maximum of 3000.
    for count in (145,3000):
        targets=[i/10 for i in range(count)]
        script=tmp_path/'selection.ffscript'
        script.write_text(f"setpts=PTS-STARTPTS,select='{target_selection(targets)}',showinfo")
        result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','info','-f','lavfi',
            '-i','color=size=32x32:rate=10:duration=1',
            '-/filter:v',str(script),
            '-fps_mode','vfr','-f','null','-'],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr[-1000:]
        assert [round(float(t),3) for t in SHOWINFO_TIMESTAMP.findall(result.stderr)]==[i/10 for i in range(10)]


def test_balanced_selection_keeps_irregular_targets_in_real_ffmpeg():
    import shutil
    import pytest
    from app.processing import target_selection, SHOWINFO_TIMESTAMP
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg executable not installed')
    result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','info','-f','lavfi',
        '-i','color=size=32x32:rate=10:duration=1',
        '-vf',f"select='{target_selection([0.,.25,.71])}',showinfo",
        '-fps_mode','vfr','-f','null','-'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert [round(float(t),3) for t in SHOWINFO_TIMESTAMP.findall(result.stderr)]==[0.,.3,.8]


def test_media_error_reports_ffmpeg_diagnostics_instead_of_full_command(monkeypatch):
    import pytest
    from app.processing import run_media
    def failed(command,**kwargs):
        raise subprocess.CalledProcessError(234,command,stderr=b'Invalid filter expression')
    monkeypatch.setattr(subprocess,'run',failed)
    with pytest.raises(RuntimeError,match='Invalid filter expression') as error:
        run_media(['ffmpeg','-vf','very-long-command'])
    assert 'very-long-command' not in str(error.value)
