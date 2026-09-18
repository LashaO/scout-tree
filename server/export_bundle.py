"""Generate export bundles: GeoPackage + HTML snapshot pages."""
import base64
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from shapely.geometry import Point

BASE_DIR = Path(os.environ.get("GIS_VIEWER_BASE_DIR",
    str(Path(__file__).resolve().parent.parent)))

CAPTURES_PATH = BASE_DIR / "data" / "captures.json"

HEALTH_COLORS = {
    'normal': '#2ecc71', 'dead': '#e74c3c',
    'firedamage': '#e67e22', 'topdownthreat': '#f1c40f',
}


def _load_captures():
    with open(CAPTURES_PATH) as f:
        manifest = json.load(f)
    return {k: v for k, v in manifest["captures"].items() if v.get("visible")}


def _find_raster_for_capture(captures, cap_id):
    """Find the RGB raster file for a capture from its source paths."""
    cap = captures.get(cap_id, {})
    source = cap.get("source", {})
    rgb_paths = source.get("rgb", [])
    for p in rgb_paths:
        if os.path.exists(p):
            return p
    exp_dir = cap.get("inference", {}).get("exp_dir", "")
    if exp_dir:
        tif = Path(exp_dir) / "tifs" / f"{cap_id}.tif"
        if tif.exists():
            return str(tif)
    return None


def _find_detections_shapefile(captures, cap_id, det_tag):
    """Find the detections shapefile for a capture/model combo."""
    cap = captures.get(cap_id, {})
    exp_dir = cap.get("inference", {}).get("exp_dir", "")
    if exp_dir:
        shp = Path(exp_dir) / det_tag / "detections" / "detections.shp"
        if shp.exists():
            return str(shp)
    return None


def _render_snapshot(captures, cap_id, det_tag, centroid, bbox_w, bbox_h):
    """Render a PNG snapshot of a location in a specific capture."""
    raster_path = _find_raster_for_capture(captures, cap_id)
    if not raster_path:
        return None

    bw = float(bbox_w or 5)
    bh = float(bbox_h or 5)
    side = 4 * max(bw, bh, 5)
    half_m = side / 2

    clon, clat = centroid
    d_lat = half_m / 111320
    d_lng = half_m / (111320 * np.cos(np.radians(clat)))

    west, south = clon - d_lng, clat - d_lat
    east, north = clon + d_lng, clat + d_lat

    try:
        with rasterio.open(raster_path) as src:
            window = from_bounds(west, south, east, north, src.transform)
            data = src.read([1, 2, 3], window=window)
            if data.size == 0:
                return None
            rgb = np.moveaxis(data, 0, -1)
            if rgb.dtype != np.uint8:
                p2, p98 = np.percentile(rgb[rgb > 0], [2, 98]) if rgb.max() > 0 else (0, 1)
                rgb = np.clip((rgb - p2) / max(p98 - p2, 1) * 255, 0, 255).astype(np.uint8)
    except Exception:
        return None

    fig, ax = plt.subplots(1, 1, figsize=(3.4, 3.4), dpi=100)
    ax.imshow(rgb, extent=[west, east, south, north], aspect='auto')

    shp_path = _find_detections_shapefile(captures, cap_id, det_tag)
    if shp_path:
        try:
            dets = gpd.read_file(shp_path, bbox=(west, south, east, north))
            for _, row in dets.iterrows():
                color = HEALTH_COLORS.get(row.get('pred_hl', ''), '#ccc')
                geom = row.geometry
                if geom and geom.geom_type == 'Polygon':
                    xs, ys = geom.exterior.xy
                    ax.plot(xs, ys, color=color, linewidth=1, alpha=0.8)
        except Exception:
            pass

    ax.plot(clon, clat, 'o', color='red', markersize=6, markeredgecolor='white', markeredgewidth=1.5)

    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.axis('off')
    plt.tight_layout(pad=0)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _build_snapshot_html(captures, item, all_captures):
    """Build a self-contained HTML page with history snapshots for one item."""
    centroid = item.get("centroid", [0, 0])
    props = item.get("properties", {})
    bbox_w = props.get("bbox_w", 5)
    bbox_h = props.get("bbox_h", 5)
    det_tag = item.get("source_detection_tag", "")

    B = 0.002
    clon, clat = centroid
    relevant = [(cid, cap) for cid, cap in all_captures.items()
                if cap.get("bounds", {}).get("wgs84") and
                clon >= cap["bounds"]["wgs84"][0] - B and clon <= cap["bounds"]["wgs84"][2] + B and
                clat >= cap["bounds"]["wgs84"][1] - B and clat <= cap["bounds"]["wgs84"][3] + B]

    images_html = ""
    for cap_id, cap in relevant:
        png_data = _render_snapshot(all_captures, cap_id, det_tag, centroid, bbox_w, bbox_h)
        if png_data:
            b64 = base64.b64encode(png_data).decode()
            images_html += f"""
            <div style="text-align:center; display:inline-block; margin:8px;">
                <div style="font-weight:600; margin-bottom:4px;">{cap.get('title', cap_id)}</div>
                <img src="data:image/png;base64,{b64}" style="width:340px; height:340px; border-radius:6px; border:2px solid #ddd;" />
            </div>"""

    props_rows = "".join(
        f"<tr><td style='padding:2px 8px; font-weight:600'>{k}</td><td style='padding:2px 8px'>{v}</td></tr>"
        for k, v in props.items() if v is not None
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Tree {item.get('id', '')} — History</title>
<style>body{{font-family:-apple-system,sans-serif;margin:20px;background:#f5f5f5;}}
.card{{background:#fff;border-radius:8px;padding:16px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,0.1);}}
table{{border-collapse:collapse;}} td{{border-bottom:1px solid #eee;}}</style></head>
<body>
<div class="card">
<h2>Tree {item.get('id', '')} — {props.get('pred_sp', 'tree')}</h2>
<p>Location: {clat:.6f}, {clon:.6f} | Health: <b>{props.get('pred_hl', '?')}</b> | Score: {props.get('score', '?')}</p>
{f'<p>Tag: <em>{item.get("tag", "")}</em></p>' if item.get("tag") else ''}
</div>
<div class="card"><h3>History</h3><div style="display:flex;flex-wrap:wrap;justify-content:center;">{images_html}</div></div>
<div class="card"><h3>Properties</h3><table>{props_rows}</table></div>
</body></html>"""


def generate_bundle(collection: dict) -> bytes:
    """Generate a zip bundle with GeoPackage, GeoJSON, and HTML snapshots."""
    captures = _load_captures()
    kept = [i for i in collection.get("items", []) if i.get("status") == "kept"]

    if not kept:
        raise ValueError("No kept items to export")

    records = []
    for item in kept:
        centroid = item.get("centroid", [0, 0])
        props = item.get("properties", {})
        records.append({
            "geometry": Point(centroid[0], centroid[1]),
            "item_id": item.get("id", ""),
            "tag": item.get("tag", ""),
            "collection": collection.get("name", ""),
            "grove": collection.get("grove", ""),
            "source_capture": item.get("source_capture", ""),
            "detection_tag": item.get("source_detection_tag", ""),
            "reviewed_at": item.get("reviewed_at", ""),
            "snapshot": f"snapshots/{item.get('id', 'unknown')}.html",
            "centroid_lat": centroid[1],
            "centroid_lon": centroid[0],
            **{k: v for k, v in props.items()},
        })

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

    buf = io.BytesIO()
    safe_name = collection.get("name", "collection").replace(" ", "_").replace("/", "-")[:50]
    prefix = f"{safe_name}/"

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as tmp:
            gpkg_path = tmp.name
        gdf.to_file(gpkg_path, driver='GPKG')
        zf.write(gpkg_path, f"{prefix}collection.gpkg")
        os.unlink(gpkg_path)

        geojson_str = gdf.to_json(indent=2)
        zf.writestr(f"{prefix}collection.geojson", geojson_str)

        for item in kept:
            html = _build_snapshot_html(captures, item, captures)
            zf.writestr(f"{prefix}snapshots/{item.get('id', 'unknown')}.html", html)

        readme = f"""# {collection.get('name', 'Collection')}

Grove: {collection.get('grove', 'N/A')}
Items: {len(kept)}
Exported: {collection.get('updated_at', 'N/A')}

## Files
- collection.gpkg — GeoPackage with all kept items (point geometry at centroid)
- collection.geojson — Same data as GeoJSON
- snapshots/ — HTML history pages per tree (open in any browser)

## QGIS Integration
1. Load collection.gpkg in QGIS
2. Right-click the layer → Properties → Actions → Add Action
3. Type: Open URL, Name: "Show History"
4. Action scope: Feature, Canvas
5. Action text: [% "snapshot" %]
6. Click OK — now clicking any tree opens its history page

## Data Source Note
Aug 2025 OneAtlas captures have different radiometric properties from earlier
reflectance captures. Health predictions may differ systematically across
these data sources.
"""
        zf.writestr(f"{prefix}README.txt", readme)

    buf.seek(0)
    return buf.getvalue()
