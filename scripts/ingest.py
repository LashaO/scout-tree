#!/usr/bin/env python3
"""Unified CLI for ingesting raw data into the Scout Tree.

Usage:
    python scripts/ingest.py raster --type rgb --input-dir raw_data/imagery/ --output-dir data/
    python scripts/ingest.py raster --type ned --input-dir raw_data/imagery/ --output-dir data/
    python scripts/ingest.py raster --type ndvi --input-dir raw_data/imagery/ --output-dir data/
    python scripts/ingest.py raster --type all --input-dir raw_data/imagery/ --output-dir data/
    python scripts/ingest.py vector --input raw_data/detections.shp --output data/detections.pmtiles
    python scripts/ingest.py all --input-dir raw_data/imagery/ --vector-input raw_data/merged.shp --output-dir data/
"""
import argparse
import sys
from pathlib import Path


def cmd_raster(args):
    from build_raster import build_rgb, build_ned, build_ndvi

    args.output_dir.mkdir(parents=True, exist_ok=True)

    ned_vrt = None
    if args.type in ("rgb", "all"):
        build_rgb(args.input_dir, args.output_dir, args.tmp_dir)
    if args.type in ("ned", "all"):
        ned_vrt = build_ned(args.input_dir, args.output_dir, args.tmp_dir)
    if args.type in ("ndvi", "all"):
        build_ndvi(args.input_dir, args.output_dir, args.tmp_dir, ned_vrt)


def cmd_vector(args):
    from build_vector import build_vector_pmtiles

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


def cmd_all(args):
    from build_raster import build_rgb, build_ned, build_ndvi
    from build_vector import build_vector_pmtiles

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  FULL INGESTION PIPELINE")
    print("=" * 60)

    # Raster layers
    if args.input_dir and args.input_dir.exists():
        build_rgb(args.input_dir, args.output_dir, args.tmp_dir)
        ned_vrt = build_ned(args.input_dir, args.output_dir, args.tmp_dir)
        build_ndvi(args.input_dir, args.output_dir, args.tmp_dir, ned_vrt)
    else:
        print(f"Skipping raster ingestion (--input-dir not provided or doesn't exist)")

    # Vector layers
    if args.vector_input and args.vector_input.exists():
        build_vector_pmtiles(
            input_shp=args.vector_input,
            output_pmtiles=args.output_dir / "detections_full.pmtiles",
            tmp_dir=args.tmp_dir,
        )
    else:
        print(f"Skipping vector ingestion (--vector-input not provided or doesn't exist)")

    print("\nFull ingestion complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Scout Tree data ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- raster subcommand ---
    p_raster = subparsers.add_parser("raster", help="Build raster PMTiles (RGB/NED/NDVI)")
    p_raster.add_argument("--type", choices=["rgb", "ned", "ndvi", "all"], required=True,
                          help="Which raster layer(s) to build")
    p_raster.add_argument("--input-dir", type=Path, required=True,
                          help="Directory containing source imagery")
    p_raster.add_argument("--output-dir", type=Path, default=Path("data"),
                          help="Output directory for PMTiles (default: data/)")
    p_raster.add_argument("--tmp-dir", type=Path, default=Path("/tmp/scout-tree_tiles"),
                          help="Directory for intermediate files")
    p_raster.set_defaults(func=cmd_raster)

    # --- vector subcommand ---
    p_vector = subparsers.add_parser("vector", help="Build vector PMTiles from shapefile")
    p_vector.add_argument("--input", type=Path, required=True,
                          help="Input shapefile path")
    p_vector.add_argument("--output", type=Path, required=True,
                          help="Output PMTiles path")
    p_vector.add_argument("--layer-name", type=str, default="detections",
                          help="Layer name in PMTiles (default: detections)")
    p_vector.add_argument("--min-zoom", type=int, default=10)
    p_vector.add_argument("--max-zoom", type=int, default=18)
    p_vector.add_argument("--tmp-dir", type=Path, default=Path("/tmp/scout-tree_tiles"))
    p_vector.set_defaults(func=cmd_vector)

    # --- all subcommand ---
    p_all = subparsers.add_parser("all", help="Run full ingestion pipeline")
    p_all.add_argument("--input-dir", type=Path, default=None,
                       help="Directory containing source imagery")
    p_all.add_argument("--vector-input", type=Path, default=None,
                       help="Input shapefile for vector layer")
    p_all.add_argument("--output-dir", type=Path, default=Path("data"),
                       help="Output directory for PMTiles (default: data/)")
    p_all.add_argument("--tmp-dir", type=Path, default=Path("/tmp/scout-tree_tiles"))
    p_all.set_defaults(func=cmd_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    # Add scripts/ to path so sibling imports work
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
