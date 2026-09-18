FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip gdal-bin libgdal-dev \
    git make g++ sqlite3 libsqlite3-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Build tippecanoe from source
RUN git clone https://github.com/felt/tippecanoe.git /tmp/tippecanoe \
    && cd /tmp/tippecanoe && make -j$(nproc) && make install \
    && rm -rf /tmp/tippecanoe

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY frontend/ /app/frontend/
COPY server/ /app/server/
COPY scripts/ /app/scripts/

WORKDIR /app
EXPOSE 8080

CMD ["python3", "server/serve.py"]
