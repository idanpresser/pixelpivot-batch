from fastapi.testclient import TestClient
from app.batch_api.main import app
import os
import tempfile
import shutil

def test_register_hot_folder_returns_200():
    # Use real temporary directories to satisfy validation/watchdog
    source_dir = tempfile.mkdtemp()
    target_dir = tempfile.mkdtemp()
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/hotfolder/register", json={
                "source_dir": source_dir,
                "target_dir": target_dir,
                "target_format": ["avif"],
                "tool": ["ffmpeg"],
            })
            assert response.status_code == 200, f"Error: {response.json()}"
            assert "watcher_id" in response.json()
    finally:
        shutil.rmtree(source_dir)
        shutil.rmtree(target_dir)

def test_list_hot_folders_returns_registered_folder():
    source_dir = tempfile.mkdtemp()
    target_dir = tempfile.mkdtemp()
    try:
        with TestClient(app) as client:
            # Ensure at least one is registered
            client.post("/api/v1/hotfolder/register", json={
                "source_dir": source_dir, "target_dir": target_dir,
                "target_format": ["avif"], "tool": ["ffmpeg"],
            })
            response = client.get("/api/v1/hotfolder/list")
            assert response.status_code == 200
            dirs = [h["source_dir"] for h in response.json()]
            assert source_dir in dirs
    finally:
        shutil.rmtree(source_dir)
        shutil.rmtree(target_dir)

def test_register_rejects_nonexistent_source_dir():
    with TestClient(app) as client:
        resp = client.post("/api/v1/hotfolder/register", json={
            "source_dir": "/definitely/does/not/exist/12345",
            "target_dir": "/tmp/out",
            "target_format": ["webp"],
            "tool": ["magick"],
            "category": ["general"],
        })
        assert resp.status_code == 400
        assert "does not exist" in resp.json()["detail"]
