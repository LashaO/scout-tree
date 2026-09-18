#!/usr/bin/env python3
"""Build vector PMTiles from shapefiles for the Scout Tree.

Codifies the manual ogr2ogr + tippecanoe pipeline:
  1. ogr2ogr: shapefile -> GeoJSON (reprojected to EPSG:4326)
  2. tippecanoe: GeoJSON -> PMTiles

Usage:
    python scripts/build_vector.py --input raw_data/detections.shp --output data/detections.pmtiles
    python scripts/build_vector.py --input raw_data/merged.shp --output data/detections_full.pmtiles \
        --layer-name detections --min-zoom 10 --max-zoom 18
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def _get_gdal_env() -> dict:
    """Return environment for GDAL commands, clearing LD_LIBRARY_PATH if conda detected."""
    env = {**os.environ}
    ld_path = env.get("LD_LIBRARY_PATH", "")
    if "conda" in ld_path or "miniconda" in ld_path or "anaconda" in ld_path:
        env["LD_LIBRARY_PATH"] = ""
    return env


def _run(cmd: list[str], desc: str, env: dict | None = None):
    """Run a shell command with logging."""
    print(f"\n{'=' * 60}")
    print(f"[CMD] {desc}")
    print(f"  {' '.join(cmd)}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print(f"ERROR: Command failed with code {result.returncode}", file=sys.stderr)
        sys.exit(1)


def build_vector_pmtiles(
    input_shp: Path,
    output_pmtiles: Path,
    layer_name: str = "detections",
    min_zoom: int = 10,
    max_zoom: int = 18,
    tmp_dir: Path = Path("/tmp/scout-tree_tiles"),
):
    """Convert shapefile to PMTiles via GeoJSON intermediate."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = tmp_dir / f"{input_shp.stem}.geojson"

    # Step 1: ogr2ogr — shapefile to GeoJSON, reproject to WGS84
    _run(
        ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326",
         str(geojson_path), str(input_shp)],
        f"Convert {input_shp.name} to GeoJSON (EPSG:4326)",
        env=_get_gdal_env(),
    )
    print(f"  GeoJSON: {geojson_path} ({geojson_path.stat().st_size / 1e6:.1f} MB)")

    # Step 2: tippecanoe — GeoJSON to PMTiles
    output_pmtiles.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["tippecanoe",
         "-o", str(output_pmtiles),
         f"-Z{min_zoom}", f"-z{max_zoom}",
         "--drop-densest-as-needed",
         "--extend-zooms-if-still-dropping",
         "-l", layer_name,
         "--force",
         str(geojson_path)],
        f"Build PMTiles (z{min_zoom}-z{max_zoom}, layer={layer_name})",
    )
    print(f"  PMTiles: {output_pmtiles} ({output_pmtiles.stat().st_size / 1e6:.1f} MB)")

    # Clean up intermediate GeoJSON
    print(f"  Cleaning up {geojson_path}")
    geojson_path.unlink()


def main():
    parser = argparse.ArgumentParser(description="Build vector PMTiles from shapefiles")
    parser.add_argument("--input", type=Path, required=True,
                        help="Input shapefile path")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output PMTiles path")
    parser.add_argument("--layer-name", type=str, default="detections",
                        help="Layer name in PMTiles (default: detections)")
    parser.add_argument("--min-zoom", type=int, default=10,
                        help="Minimum zoom level (default: 10)")
    parser.add_argument("--max-zoom", type=int, default=18,
                        help="Maximum zoom level (default: 18)")
    parser.add_argument("--tmp-dir", type=Path, default=Path("/tmp/scout-tree_tiles"),
                        help="Directory for intermediate files (default: /tmp/scout-tree_tiles/)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    build_vector_pmtiles(
        input_shp=args.input,
        output_pmtiles=args.output,
        layer_name=args.layer_name,
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        tmp_dir=args.tmp_dir,
    )

    print(f"\nDone! Output: {args.output}")


if __name__ == "__main__":
    main()
