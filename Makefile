.PHONY: build run dev stop shell ingest-all ingest-raster ingest-vector

build:
	docker compose build

run:
	docker compose up -d

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

stop:
	docker compose down

shell:
	docker compose run --rm viewer bash

ingest-all:
	docker compose run --rm viewer python3 scripts/ingest.py all \
		--input-dir raw_data/imagery/ --vector-input raw_data/merged.shp --output-dir data/

ingest-raster:
	docker compose run --rm viewer python3 scripts/ingest.py raster \
		--type all --input-dir raw_data/imagery/ --output-dir data/

ingest-vector:
	docker compose run --rm viewer python3 scripts/ingest.py vector \
		--input raw_data/merged.shp --output data/detections_full.pmtiles
