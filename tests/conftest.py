import pytest

from margin import config


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Every test gets its own data folder and never touches the network."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    from margin import transcript
    monkeypatch.setattr(transcript, "fetch_video_meta", lambda video_id: {"title": "Photosynthesis", "author": "Test Channel"})
    yield
