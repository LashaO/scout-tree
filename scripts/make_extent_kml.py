#!/usr/bin/env python3
"""Build KML extent from Airbus ROI GML footprints (official valid-data boundaries).

Usage:
    python scripts/make_extent_kml.py --gml-dir /datasets/airbus_pull --output data/imagery_extent.kml
"""
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
import pyproj
from shapely.ops import transform as shapely_transform
from functools import partial


def geom_to_kml_placemarks(geom, area_km2):
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    else:
        raise ValueError(f"Unexpected type: {type(geom)}")

    parts = []
    for i, poly in enumerate(polys):
        coords_outer = " ".join(f"{x},{y},0" for x, y in poly.exterior.coords)
        inner = ""
        for hole in poly.interiors:
            c = " ".join(f"{x},{y},0" for x, y in hole.coords)
            inner += f"""
          <innerBoundaryIs>
            <LinearRing><coordinates>{c}</coordinates></LinearRing>
          </innerBoundaryIs>"""

        parts.append(f"""
    <Placemark>
      <name>Imagery Extent{f' Part {i+1}' if len(polys) > 1 else ''}</name>
      <description>Pleiades Neo valid-data footprint ({area_km2:.2f} km2 total)</description>
      <Style>
        <LineStyle><color>ff0000ff</color><width>2</width></LineStyle>
        <PolyStyle><color>400000ff</color></PolyStyle>
      </Style>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing><coordinates>{coords_outer}</coordinates></LinearRing>
        </outerBoundaryIs>{inner}
      </Polygon>
    </Placemark>""")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Build KML extent from Airbus ROI GML footprints")
    parser.add_argument("--gml-dir", type=Path, required=True,
                        help="Directory containing Airbus data with MASKS/ROI_*.GML files")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output KML file path")
    parser.add_argument("--source-crs", type=str, default="EPSG:32611",
                        help="Source CRS of GML coordinates (default: EPSG:32611)")
    args = parser.parse_args()

    ns = {
        'gml': 'http://www.opengis.net/gml',
        'hma': 'http://earth.esa.int/hma'
    }

    gml_files = sorted(args.gml_dir.rglob("MASKS/ROI_*.GML"))
    print(f"Found {len(gml_files)} ROI GML files")

    polygons = []
    for gml_path in gml_files:
        label = gml_path.parts[-3] if len(gml_path.parts) >= 3 else gml_path.stem
        tree = ET.parse(gml_path)
        root = tree.getroot()

        poslist_el = root.find('.//gml:posList', ns)
        if poslist_el is None:
            print(f"  {label}: no posList found, skipping")
            continue

        coords_flat = list(map(float, poslist_el.text.strip().split()))
        coords = [(coords_flat[i], coords_flat[i+1]) for i in range(0, len(coords_flat), 2)]

        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        area_km2 = poly.area / 1e6
        print(f"  {label}: {len(coords)} vertices, {area_km2:.2f} km2")
        polygons.append(poly)

    merged = unary_union(polygons)
    total_area = merged.area / 1e6

    print(f"\nIndividual sum: {sum(p.area/1e6 for p in polygons):.2f} km2")
    print(f"Union area:     {total_area:.2f} km2")

    project = partial(
        pyproj.Transformer.from_crs(args.source_crs, "EPSG:4326", always_xy=True).transform
    )
    merged_wgs84 = shapely_transform(project, merged)

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Aerial Imagery Extent</name>
  <description>Union of Airbus ROI footprints. Area: {total_area:.2f} km2</description>
  {geom_to_kml_placemarks(merged_wgs84, total_area)}
</Document>
</kml>
"""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(kml)
    print(f"\nKML saved to: {args.output}")

    bounds = merged_wgs84.bounds
    print(f"Bounding box (WGS84): {bounds[0]:.6f}, {bounds[1]:.6f} -> {bounds[2]:.6f}, {bounds[3]:.6f}")


if __name__ == "__main__":
    main()
