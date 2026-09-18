"""Collections API — CRUD + CSV export for saved tree detection collections.

Storage: JSON files in DATA_DIR/collections/{username}/, one file per collection.
"""
import csv
import io
import json
import os
import time
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# Base data directory — overridable for tests
DATA_DIR = os.environ.get(
    "COLLECTIONS_DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data"),
)


def _collections_dir(username: str) -> str:
    """Return (and ensure exists) the per-user collections directory."""
    d = os.path.join(DATA_DIR, "collections", username)
    os.makedirs(d, exist_ok=True)
    return d


def _collection_path(username: str, coll_id: str) -> str:
    """Return the file path for a single collection."""
    # Sanitize id to prevent path traversal
    safe_id = os.path.basename(coll_id)
    return os.path.join(_collections_dir(username), f"{safe_id}.json")


def _read_collection(username: str, coll_id: str) -> dict | None:
    """Read a collection from disk, or None if not found."""
    path = _collection_path(username, coll_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def _write_collection(username: str, coll: dict) -> None:
    """Write a collection to disk."""
    path = _collection_path(username, coll["id"])
    with open(path, "w") as f:
        json.dump(coll, f, indent=2)


def _delete_collection_file(username: str, coll_id: str) -> bool:
    """Delete a collection file. Returns True if it existed."""
    path = _collection_path(username, coll_id)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def _list_collections(username: str) -> list[dict]:
    """List all collections for a user."""
    d = _collections_dir(username)
    results = []
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(d, fname)
        try:
            with open(path, "r") as f:
                coll = json.load(f)
            results.append(coll)
        except (json.JSONDecodeError, OSError):
            continue
    return results


def _get_username(request: Request) -> str:
    """Extract the authenticated username from the request scope."""
    return request.scope.get("auth_user", "anonymous")


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _summarize(coll: dict) -> dict:
    """Build a summary dict with kept/dismissed/pending counts."""
    items = coll.get("items", [])
    kept = sum(1 for it in items if it.get("status") == "kept")
    pending = sum(1 for it in items if it.get("status") == "pending")
    dismissed_count = len(coll.get("dismissed", []))
    return {
        "id": coll["id"],
        "name": coll.get("name", ""),
        "grove": coll.get("grove", ""),
        "created_at": coll.get("created_at", ""),
        "updated_at": coll.get("updated_at", ""),
        "kept": kept,
        "dismissed": dismissed_count,
        "pending": pending,
        "total_items": len(items),
    }


# ---- Endpoint handlers ----

async def whoami(request: Request) -> JSONResponse:
    """GET /api/whoami — return the authenticated username."""
    return JSONResponse({"username": _get_username(request)})


async def list_collections(request: Request) -> JSONResponse:
    """GET /api/collections — list all collections for the authenticated user."""
    username = _get_username(request)
    colls = _list_collections(username)
    summaries = [_summarize(c) for c in colls]
    return JSONResponse(summaries)


async def create_collection(request: Request) -> JSONResponse:
    """POST /api/collections — create a new collection."""
    username = _get_username(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    now = _now_iso()
    coll_id = f"coll_{int(time.time() * 1000)}"

    coll = {
        "id": coll_id,
        "name": body.get("name", ""),
        "grove": body.get("grove", ""),
        "created_at": now,
        "updated_at": now,
        "generation": body.get("generation", {}),
        "review_index": body.get("review_index", 0),
        "items": body.get("items", []),
        "dismissed": body.get("dismissed", []),
    }

    _write_collection(username, coll)
    return JSONResponse(coll, status_code=201)


async def get_collection(request: Request) -> JSONResponse:
    """GET /api/collections/{id} — get a single collection."""
    username = _get_username(request)
    coll_id = request.path_params["id"]
    coll = _read_collection(username, coll_id)
    if coll is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return JSONResponse(coll)


async def update_collection(request: Request) -> JSONResponse:
    """PUT /api/collections/{id} — update a collection."""
    username = _get_username(request)
    coll_id = request.path_params["id"]
    coll = _read_collection(username, coll_id)
    if coll is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    # Update allowed fields
    for field in ("name", "grove", "review_index", "items", "dismissed", "generation"):
        if field in body:
            coll[field] = body[field]

    coll["updated_at"] = _now_iso()
    _write_collection(username, coll)
    return JSONResponse(coll)


async def delete_collection(request: Request) -> JSONResponse:
    """DELETE /api/collections/{id} — delete a collection."""
    username = _get_username(request)
    coll_id = request.path_params["id"]
    if not _delete_collection_file(username, coll_id):
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return JSONResponse({"deleted": coll_id})


async def export_csv(request: Request) -> Response:
    """GET /api/collections/{id}/export/csv — export kept items as CSV."""
    username = _get_username(request)
    coll_id = request.path_params["id"]
    coll = _read_collection(username, coll_id)
    if coll is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)

    items = coll.get("items", [])
    kept = [it for it in items if it.get("status") == "kept"]

    if not kept:
        return JSONResponse(
            {"error": "No kept items to export"}, status_code=400
        )

    columns = [
        "item_id", "centroid_lat", "centroid_lon", "tag", "source_capture",
        "detection_tag", "reviewed_at", "collection", "grove",
        "score", "pred_sp", "pred_hl",
        "p_seqmat", "p_seqsp", "p_tree",
        "p_dead", "p_firedama", "p_normal", "p_topdownt",
        "bbox_w", "bbox_h",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    for item in kept:
        props = item.get("properties", {})
        centroid = item.get("centroid", [None, None])
        row = {
            "item_id": item.get("id", ""),
            "centroid_lat": centroid[1] if len(centroid) > 1 else "",
            "centroid_lon": centroid[0] if len(centroid) > 0 else "",
            "tag": item.get("tag", ""),
            "source_capture": props.get("source_capture", ""),
            "detection_tag": props.get("detection_tag", ""),
            "reviewed_at": item.get("reviewed_at", ""),
            "collection": coll.get("name", ""),
            "grove": coll.get("grove", ""),
            "score": props.get("score", ""),
            "pred_sp": props.get("pred_sp", ""),
            "pred_hl": props.get("pred_hl", ""),
            "p_seqmat": props.get("p_seqmat", ""),
            "p_seqsp": props.get("p_seqsp", ""),
            "p_tree": props.get("p_tree", ""),
            "p_dead": props.get("p_dead", ""),
            "p_firedama": props.get("p_firedama", ""),
            "p_normal": props.get("p_normal", ""),
            "p_topdownt": props.get("p_topdownt", ""),
            "bbox_w": props.get("bbox_w", ""),
            "bbox_h": props.get("bbox_h", ""),
        }
        writer.writerow(row)

    csv_content = buf.getvalue()
    safe_name = coll.get("name", coll_id).replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}.csv"

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def export_bundle(request: Request) -> Response:
    """GET /api/collections/{id}/export/bundle — export kept items as zip bundle."""
    username = _get_username(request)
    coll_id = request.path_params["id"]
    coll = _read_collection(username, coll_id)
    if coll is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)

    try:
        from server.export_bundle import generate_bundle
        zip_bytes = generate_bundle(coll)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Export failed: {e}"}, status_code=500)

    safe_name = coll.get("name", coll_id).replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}.zip"

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- Route table (mounted at /api by serve.py) ----

collections_routes = [
    Route("/whoami", whoami, methods=["GET"]),
    Route("/collections", list_collections, methods=["GET"]),
    Route("/collections", create_collection, methods=["POST"]),
    Route("/collections/{id}", get_collection, methods=["GET"]),
    Route("/collections/{id}", update_collection, methods=["PUT"]),
    Route("/collections/{id}", delete_collection, methods=["DELETE"]),
    Route("/collections/{id}/export/csv", export_csv, methods=["GET"]),
    Route("/collections/{id}/export/bundle", export_bundle, methods=["GET"]),
]
