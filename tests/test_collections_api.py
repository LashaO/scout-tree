"""Tests for the Collections API."""
import json
import os

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

import server.collections_api as collections_api
from server.collections_api import collections_routes


class FakeAuthMiddleware:
    """Test middleware that injects auth_user without checking credentials."""

    def __init__(self, app, username: str = "testuser"):
        self.app = app
        self.username = username

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["auth_user"] = self.username
        await self.app(scope, receive, send)


def _make_client(tmp_path) -> TestClient:
    """Build a TestClient wired to a temp data directory."""
    # Point storage at tmp_path
    collections_api.DATA_DIR = str(tmp_path)

    app = Starlette(routes=[Mount("/api", routes=collections_routes)])
    app = FakeAuthMiddleware(app)
    return TestClient(app, raise_server_exceptions=True)


# ---- Tests ----


def test_whoami(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/whoami")
    assert resp.status_code == 200
    assert resp.json() == {"username": "testuser"}


def test_list_empty(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/collections")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_get(tmp_path):
    client = _make_client(tmp_path)

    # Create
    payload = {
        "name": "Dead Trees",
        "grove": "Site A",
        "items": [
            {"id": "item_1", "status": "pending", "centroid": [-118.75, 36.46],
             "properties": {"score": 0.92}, "tag": "gf_2025"},
        ],
    }
    resp = client.post("/api/collections", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Dead Trees"
    assert data["grove"] == "Site A"
    assert data["id"].startswith("coll_")
    assert len(data["items"]) == 1
    assert data["created_at"]
    assert data["updated_at"]

    coll_id = data["id"]

    # Get
    resp2 = client.get(f"/api/collections/{coll_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == coll_id
    assert resp2.json()["name"] == "Dead Trees"


def test_get_not_found(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/collections/coll_nonexistent")
    assert resp.status_code == 404


def test_update(tmp_path):
    client = _make_client(tmp_path)

    # Create first
    resp = client.post("/api/collections", json={"name": "Original"})
    coll_id = resp.json()["id"]
    original_updated = resp.json()["updated_at"]

    # Update
    resp2 = client.put(
        f"/api/collections/{coll_id}",
        json={
            "name": "Renamed",
            "grove": "Site E",
            "items": [
                {"id": "item_1", "status": "kept", "centroid": [-118.75, 36.46],
                 "properties": {}, "tag": "m_2025", "reviewed_at": "2026-04-23T12:00:00Z"},
            ],
            "dismissed": [
                {"centroid": [120.5, 40.25], "dismissed_at": "2026-04-23T12:05:00Z"},
            ],
        },
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["name"] == "Renamed"
    assert data["grove"] == "Site E"
    assert len(data["items"]) == 1
    assert len(data["dismissed"]) == 1
    assert data["updated_at"] >= original_updated


def test_update_not_found(tmp_path):
    client = _make_client(tmp_path)
    resp = client.put("/api/collections/coll_nonexistent", json={"name": "X"})
    assert resp.status_code == 404


def test_delete(tmp_path):
    client = _make_client(tmp_path)

    # Create
    resp = client.post("/api/collections", json={"name": "Doomed"})
    coll_id = resp.json()["id"]

    # Delete
    resp2 = client.delete(f"/api/collections/{coll_id}")
    assert resp2.status_code == 200
    assert resp2.json()["deleted"] == coll_id

    # Confirm gone
    resp3 = client.get(f"/api/collections/{coll_id}")
    assert resp3.status_code == 404


def test_delete_not_found(tmp_path):
    client = _make_client(tmp_path)
    resp = client.delete("/api/collections/coll_nonexistent")
    assert resp.status_code == 404


def test_list_shows_counts(tmp_path):
    client = _make_client(tmp_path)

    items = [
        {"id": "item_1", "status": "kept", "centroid": [0, 0], "properties": {}},
        {"id": "item_2", "status": "kept", "centroid": [0, 0], "properties": {}},
        {"id": "item_3", "status": "pending", "centroid": [0, 0], "properties": {}},
    ]
    dismissed = [
        {"centroid": [1, 1], "dismissed_at": "2026-04-23T12:00:00Z"},
    ]

    resp = client.post("/api/collections", json={
        "name": "Counts Test",
        "items": items,
        "dismissed": dismissed,
    })
    assert resp.status_code == 201

    # List
    resp2 = client.get("/api/collections")
    assert resp2.status_code == 200
    summaries = resp2.json()
    assert len(summaries) == 1

    s = summaries[0]
    assert s["kept"] == 2
    assert s["pending"] == 1
    assert s["dismissed"] == 1
    assert s["total_items"] == 3


def test_export_csv(tmp_path):
    client = _make_client(tmp_path)

    items = [
        {
            "id": "item_1", "status": "kept", "centroid": [-118.75, 36.46],
            "tag": "gf_2025", "reviewed_at": "2026-04-23T12:00:00Z",
            "properties": {
                "score": 0.95, "pred_sp": "seqmat", "pred_hl": "normal",
                "p_seqmat": 0.9, "p_seqsp": 0.05, "p_tree": 0.05,
                "p_dead": 0.01, "p_firedama": 0.02, "p_normal": 0.95, "p_topdownt": 0.02,
                "bbox_w": 12.5, "bbox_h": 11.0,
                "source_capture": "gf_aug25", "detection_tag": "yolo26x",
            },
        },
        {
            "id": "item_2", "status": "pending", "centroid": [-118.76, 36.47],
            "properties": {"score": 0.80},
        },
        {
            "id": "item_3", "status": "kept", "centroid": [-118.77, 36.48],
            "tag": "gf_2025", "reviewed_at": "2026-04-23T12:30:00Z",
            "properties": {"score": 0.88, "pred_sp": "tree", "pred_hl": "dead"},
        },
    ]

    resp = client.post("/api/collections", json={
        "name": "Export Test",
        "grove": "Site A",
        "items": items,
    })
    coll_id = resp.json()["id"]

    # Export CSV
    resp2 = client.get(f"/api/collections/{coll_id}/export/csv")
    assert resp2.status_code == 200
    assert resp2.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in resp2.headers.get("content-disposition", "")

    # Parse CSV
    import csv
    import io

    reader = csv.DictReader(io.StringIO(resp2.text))
    rows = list(reader)

    # Only kept items (2 of 3)
    assert len(rows) == 2
    assert rows[0]["item_id"] == "item_1"
    assert rows[0]["centroid_lat"] == "36.46"
    assert rows[0]["centroid_lon"] == "-118.75"
    assert rows[0]["score"] == "0.95"
    assert rows[0]["pred_sp"] == "seqmat"
    assert rows[0]["grove"] == "Site A"
    assert rows[0]["collection"] == "Export Test"

    assert rows[1]["item_id"] == "item_3"


def test_export_csv_no_kept(tmp_path):
    client = _make_client(tmp_path)

    resp = client.post("/api/collections", json={
        "name": "No Kept",
        "items": [
            {"id": "item_1", "status": "pending", "centroid": [0, 0], "properties": {}},
        ],
    })
    coll_id = resp.json()["id"]

    resp2 = client.get(f"/api/collections/{coll_id}/export/csv")
    assert resp2.status_code == 400
    assert "No kept items" in resp2.json()["error"]


def test_export_csv_not_found(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/collections/coll_nonexistent/export/csv")
    assert resp.status_code == 404
