#!/usr/bin/env python3
"""Build NED, RGB, and NDVI raster PMTiles for the Scout Tree.

Usage:
    python scripts/build_raster.py --rgb  --input-dir raw_data/imagery/ --output-dir data/
    python scripts/build_raster.py --ned  --input-dir raw_data/imagery/ --output-dir data/
    python scripts/build_raster.py --ndvi --ned-dir raw_data/ned/ --rgb-dir raw_data/rgb/
    python scripts/build_raster.py --all  --input-dir raw_data/imagery/ --output-dir data/

All intermediate files go to --tmp-dir (default: /tmp/scout-tree_tiles/).
Final .pmtiles files are written to --output-dir.

Source imagery: Pleiades Neo PMS-FS JP2 files
  - NED: Band 1=NIR, Band 2=Red Edge, Band 3=Deep Blue
  - RGB: Band 1=Red, Band 2=Green, Band 3=Blue
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window


def _get_gdal_env() -> dict:
    """Return environment for GDAL commands, clearing LD_LIBRARY_PATH if conda detected."""
    env = {**os.environ}
    ld_path = env.get("LD_LIBRARY_PATH", "")
    if "conda" in ld_path or "miniconda" in ld_path or "anaconda" in ld_path:
        env["LD_LIBRARY_PATH"] = ""
    return env


GDAL_ENV = _get_gdal_env()


def run_gdal(cmd: list[str], desc: str = ""):
    """Run a GDAL CLI command with system libraries."""
    print(f"\n{'=' * 60}")
    print(f"[GDAL] {desc or ' '.join(cmd)}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, env=GDAL_ENV)
    if result.returncode != 0:
        print(f"ERROR: Command failed with code {result.returncode}", file=sys.stderr)
        sys.exit(1)


def find_jp2_files(input_dir: Path, pattern: str) -> list[Path]:
    """Find JP2 files matching pattern, filtering out tiny edge tiles."""
    files = sorted(input_dir.rglob(pattern))
    files = [f for f in files if f.stat().st_size > 50_000]
    print(f"Found {len(files)} JP2 files matching '{pattern}' in {input_dir} (excluding tiny edge tiles)")
    return files


def build_vrt(jp2_files: list[Path], output_vrt: Path, label: str, tmp_dir: Path) -> Path:
    """Build a VRT mosaic from JP2 files."""
    file_list = tmp_dir / f"{label}_filelist.txt"
    file_list.write_text("\n".join(str(f) for f in jp2_files))
    run_gdal(
        ["gdalbuildvrt", "-input_file_list", str(file_list),
         "-srcnodata", "0", "-vrtnodata", "0",
         str(output_vrt)],
        f"Build {label} VRT mosaic ({len(jp2_files)} files)"
    )
    return output_vrt


def sample_percentiles(vrt_path: Path, pct_low: float = 2, pct_high: float = 98,
                       max_sample_pixels: int = 50_000_000) -> list[tuple[float, float]]:
    """Compute per-band percentiles by sampling the raster."""
    with rasterio.open(vrt_path) as src:
        total_pixels = src.width * src.height
        stride = max(1, int(np.sqrt(total_pixels / max_sample_pixels)))
        print(f"  Sampling with stride={stride} from {src.width}x{src.height} ({src.count} bands)")

        samples = [[] for _ in range(src.count)]
        block_h = 512
        for row_off in range(0, src.height, block_h * stride):
            h = min(block_h, src.height - row_off)
            win = Window(0, row_off, src.width, h)
            data = src.read(window=win)
            for b in range(src.count):
                valid = data[b][data[b] > 0]
                if len(valid) > 0:
                    samples[b].append(valid[::stride])

        percentiles = []
        for b in range(src.count):
            if samples[b]:
                all_samples = np.concatenate(samples[b])
                lo = float(np.percentile(all_samples, pct_low))
                hi = float(np.percentile(all_samples, pct_high))
            else:
                lo, hi = 0.0, 255.0
            percentiles.append((lo, hi))
            print(f"  Band {b + 1}: p{pct_low}={lo:.1f}, p{pct_high}={hi:.1f}")

        return percentiles


def normalize_to_uint8(vrt_path: Path, output_path: Path,
                       percentiles: list[tuple[float, float]]):
    """Apply percentile stretch and write uint8 GeoTIFF with windowed I/O."""
    with rasterio.open(vrt_path) as src:
        profile = src.profile.copy()
        profile.update(
            dtype="uint8",
            driver="GTiff",
            compress="deflate",
            tiled=True,
            blockxsize=512,
            blockysize=512,
            BIGTIFF="YES",
        )
        for key in ["quality", "reversible"]:
            profile.pop(key, None)

        print(f"  Writing normalized raster: {output_path}")
        with rasterio.open(output_path, "w", **profile) as dst:
            block_h = 512
            total_rows = src.height
            for row_off in range(0, total_rows, block_h):
                h = min(block_h, total_rows - row_off)
                win = Window(0, row_off, src.width, h)
                data = src.read(window=win).astype(np.float32)

                for b in range(src.count):
                    lo, hi = percentiles[b]
                    band = data[b]
                    mask = band > 0
                    if hi > lo:
                        band[mask] = np.clip((band[mask] - lo) / (hi - lo) * 255, 0, 255)
                    else:
                        band[mask] = 128
                    data[b] = band

                dst.write(data.astype(np.uint8), window=win)

                if row_off % (block_h * 200) == 0 and row_off > 0:
                    print(f"  {100 * row_off / total_rows:.0f}% complete...")

    print(f"  Done: {output_path} ({output_path.stat().st_size / 1e6:.1f} MB)")


def compute_ndvi_colormapped(ned_vrt: Path, rgb_vrt: Path, output_path: Path,
                             cmap_name: str = "RdYlGn",
                             ndvi_min: float = -0.2, ndvi_max: float = 0.8):
    """Compute NDVI from NED (NIR) and RGB (Red), apply colormap, write 3-band uint8."""
    import matplotlib.cm as cm

    try:
        cmap = cm.get_cmap(cmap_name, 256)
    except (AttributeError, TypeError):
        cmap = cm.colormaps[cmap_name].resampled(256)
    lut = (cmap(np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)

    with rasterio.open(ned_vrt) as ned_ds, rasterio.open(rgb_vrt) as rgb_ds:
        assert ned_ds.width == rgb_ds.width and ned_ds.height == rgb_ds.height, \
            f"Dimension mismatch: NED {ned_ds.width}x{ned_ds.height} vs RGB {rgb_ds.width}x{rgb_ds.height}"

        profile = ned_ds.profile.copy()
        profile.update(
            count=3,
            dtype="uint8",
            driver="GTiff",
            compress="deflate",
            tiled=True,
            blockxsize=512,
            blockysize=512,
            BIGTIFF="YES",
        )
        for key in ["quality", "reversible"]:
            profile.pop(key, None)

        print(f"  Computing NDVI: {ned_ds.width}x{ned_ds.height}")
        print(f"  NDVI range [{ndvi_min}, {ndvi_max}] -> colormap '{cmap_name}'")
        print(f"  Writing: {output_path}")

        with rasterio.open(output_path, "w", **profile) as dst:
            block_h = 512
            total_rows = ned_ds.height
            for row_off in range(0, total_rows, block_h):
                h = min(block_h, total_rows - row_off)
                win = Window(0, row_off, ned_ds.width, h)

                nir = ned_ds.read(1, window=win).astype(np.float32)
                red = rgb_ds.read(1, window=win).astype(np.float32)

                denom = nir + red
                valid = denom > 0
                ndvi = np.zeros_like(nir)
                ndvi[valid] = (nir[valid] - red[valid]) / denom[valid]

                scaled = np.clip(
                    (ndvi - ndvi_min) / (ndvi_max - ndvi_min) * 255, 0, 255
                ).astype(np.uint8)

                rgb_out = np.zeros((3, h, ned_ds.width), dtype=np.uint8)
                for c in range(3):
                    rgb_out[c] = lut[scaled, c]

                rgb_out[:, ~valid] = 0

                dst.write(rgb_out, window=win)

                if row_off % (block_h * 200) == 0 and row_off > 0:
                    print(f"  {100 * row_off / total_rows:.0f}% complete...")

    print(f"  Done: {output_path} ({output_path.stat().st_size / 1e6:.1f} MB)")


def tif_to_mbtiles(tif_path: Path, mbtiles_path: Path, tile_format: str = "PNG"):
    """Convert GeoTIFF to MBTiles via gdal_translate."""
    cmd = ["gdal_translate", "-of", "MBTiles",
           "-co", f"TILE_FORMAT={tile_format}"]
    if tile_format == "JPEG":
        cmd += ["-co", "QUALITY=85"]
    cmd += [str(tif_path), str(mbtiles_path)]
    run_gdal(cmd, f"Convert to MBTiles ({tile_format})")


def add_overviews(mbtiles_path: Path):
    """Add overview zoom levels to MBTiles."""
    run_gdal(
        ["gdaladdo", "-r", "average", str(mbtiles_path), "2", "4", "8", "16", "32"],
        "Add overview levels"
    )


def convert_to_pmtiles(mbtiles_path: Path, pmtiles_path: Path):
    """Convert MBTiles to PMTiles using Python pmtiles library."""
    import sqlite3
    from pmtiles.convert import mbtiles_to_pmtiles
    from pmtiles.tile import TileType
    print(f"\n{'=' * 60}")
    print(f"[PMTiles] Converting {mbtiles_path.name} -> {pmtiles_path.name}")
    print(f"{'=' * 60}")
    conn = sqlite3.connect(str(mbtiles_path))
    maxzoom = int(conn.execute(
        "SELECT value FROM metadata WHERE name='maxzoom'"
    ).fetchone()[0])
    fmt_row = conn.execute(
        "SELECT value FROM metadata WHERE name='format'"
    ).fetchone()
    tile_fmt = fmt_row[0] if fmt_row else "unknown"
    conn.close()
    print(f"  maxzoom={maxzoom}, format={tile_fmt}")
    mbtiles_to_pmtiles(str(mbtiles_path), str(pmtiles_path), maxzoom)

    fmt_to_type = {"png": TileType.PNG, "jpg": TileType.JPEG,
                   "jpeg": TileType.JPEG, "webp": TileType.WEBP,
                   "pbf": TileType.MVT}
    expected_type = fmt_to_type.get(tile_fmt, None)
    if expected_type:
        with open(pmtiles_path, "r+b") as f:
            f.seek(99)
            current = f.read(1)[0]
            if current != expected_type.value:
                f.seek(99)
                f.write(bytes([expected_type.value]))
                print(f"  Fixed tile_type: {current} -> {expected_type.value} ({expected_type.name})")

    print(f"  Output: {pmtiles_path} ({pmtiles_path.stat().st_size / 1e6:.1f} MB)")


def build_rgb(input_dir: Path, output_dir: Path, tmp_dir: Path, file_pattern: str = "*RGB*.JP2"):
    """Full RGB pipeline: VRT -> normalize -> MBTiles -> overviews -> PMTiles."""
    print("\n" + "=" * 60)
    print("  BUILDING RGB LAYER (full extent)")
    print("=" * 60)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    rgb_files = find_jp2_files(input_dir, file_pattern)
    rgb_vrt = tmp_dir / "mosaic_rgb.vrt"
    build_vrt(rgb_files, rgb_vrt, "RGB", tmp_dir)

    print("\nComputing percentiles...")
    percentiles = sample_percentiles(rgb_vrt)

    rgb_norm = tmp_dir / "rgb_normalized.tif"
    print("\nNormalizing RGB to uint8...")
    normalize_to_uint8(rgb_vrt, rgb_norm, percentiles)

    rgb_mbtiles = tmp_dir / "rgb_full.mbtiles"
    tif_to_mbtiles(rgb_norm, rgb_mbtiles, "JPEG")

    add_overviews(rgb_mbtiles)

    output = output_dir / "rgb_full.pmtiles"
    convert_to_pmtiles(rgb_mbtiles, output)

    print(f"\nRGB (full extent) layer complete: {output}")


def build_ned(input_dir: Path, output_dir: Path, tmp_dir: Path, file_pattern: str = "*NED*.JP2"):
    """Full NED pipeline: VRT -> normalize -> MBTiles -> overviews -> PMTiles."""
    print("\n" + "=" * 60)
    print("  BUILDING NED LAYER")
    print("=" * 60)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    ned_files = find_jp2_files(input_dir, file_pattern)
    ned_vrt = tmp_dir / "mosaic_ned.vrt"
    build_vrt(ned_files, ned_vrt, "NED", tmp_dir)

    print("\nComputing percentiles...")
    percentiles = sample_percentiles(ned_vrt)

    ned_norm = tmp_dir / "ned_normalized.tif"
    print("\nNormalizing NED to uint8...")
    normalize_to_uint8(ned_vrt, ned_norm, percentiles)

    ned_mbtiles = tmp_dir / "ned.mbtiles"
    tif_to_mbtiles(ned_norm, ned_mbtiles, "JPEG")

    add_overviews(ned_mbtiles)

    output = output_dir / "ned.pmtiles"
    convert_to_pmtiles(ned_mbtiles, output)

    print(f"\nNED layer complete: {output}")
    return ned_vrt


def build_ndvi(input_dir: Path, output_dir: Path, tmp_dir: Path,
               ned_vrt: Path | None = None):
    """Full NDVI pipeline: VRTs -> NDVI computation -> colormap -> MBTiles -> PMTiles."""
    print("\n" + "=" * 60)
    print("  BUILDING NDVI LAYER")
    print("=" * 60)

    tmp_dir.mkdir(parents=True, exist_ok=True)

    if ned_vrt is None or not ned_vrt.exists():
        ned_files = find_jp2_files(input_dir, "*NED*.JP2")
        ned_vrt = tmp_dir / "mosaic_ned.vrt"
        build_vrt(ned_files, ned_vrt, "NED", tmp_dir)

    rgb_vrt = tmp_dir / "mosaic_rgb.vrt"
    if not rgb_vrt.exists():
        rgb_files = find_jp2_files(input_dir, "*RGB*.JP2")
        build_vrt(rgb_files, rgb_vrt, "RGB", tmp_dir)

    ndvi_tif = tmp_dir / "ndvi_colormapped.tif"
    print("\nComputing NDVI with RdYlGn colormap...")
    compute_ndvi_colormapped(ned_vrt, rgb_vrt, ndvi_tif)

    ndvi_mbtiles = tmp_dir / "ndvi.mbtiles"
    tif_to_mbtiles(ndvi_tif, ndvi_mbtiles, "JPEG")

    add_overviews(ndvi_mbtiles)

    output = output_dir / "ndvi.pmtiles"
    convert_to_pmtiles(ndvi_mbtiles, output)

    print(f"\nNDVI layer complete: {output}")


def main():
    parser = argparse.ArgumentParser(description="Build raster PMTiles for Scout Tree")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rgb", action="store_true", help="Build full-extent RGB PMTiles")
    group.add_argument("--ned", action="store_true", help="Build NED PMTiles only")
    group.add_argument("--ndvi", action="store_true", help="Build NDVI PMTiles only")
    group.add_argument("--all", action="store_true", help="Build RGB, NED, and NDVI")

    parser.add_argument("--input-dir", type=Path, required=True,
                        help="Directory containing source imagery (JP2/TIF files)")
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Output directory for PMTiles (default: data/)")
    parser.add_argument("--tmp-dir", type=Path, default=Path("/tmp/scout-tree_tiles"),
                        help="Directory for intermediate files (default: /tmp/scout-tree_tiles/)")
    parser.add_argument("--file-pattern", type=str, default=None,
                        help="Custom glob pattern for input files (overrides defaults)")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.rgb or args.all:
        pattern = args.file_pattern or "*RGB*.JP2"
        build_rgb(args.input_dir, args.output_dir, args.tmp_dir, pattern)

    ned_vrt = None
    if args.ned or args.all:
        pattern = args.file_pattern or "*NED*.JP2"
        ned_vrt = build_ned(args.input_dir, args.output_dir, args.tmp_dir, pattern)
    if args.ndvi or args.all:
        build_ndvi(args.input_dir, args.output_dir, args.tmp_dir, ned_vrt)

    print(f"\nAll done! Intermediate files are in {args.tmp_dir}")
    print(f"Clean up with: rm -rf {args.tmp_dir}")


if __name__ == "__main__":
    main()
