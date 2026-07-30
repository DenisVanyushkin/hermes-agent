# Stage 1: build pysqlite3 statically against the SQLite amalgamation.
# The distro libsqlite3 (3.46.1 on trixie) is inside the WAL-reset affected
# range and unpatched for the 2026 FTS5/session CVEs. Args are overridable so
# scripts/sqlite_autoupdate.py can rebuild this image against a newer release.
FROM python:3.11-slim AS sqlite_build

ARG SQLITE_AMALGAMATION_URL=https://www.sqlite.org/2026/sqlite-amalgamation-3530400.zip
ARG SQLITE_AMALGAMATION_SHA3=628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl unzip ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 https://github.com/coleifer/pysqlite3 src \
    && curl -fsSLO "${SQLITE_AMALGAMATION_URL}" \
    && python -c "import glob, hashlib, sys; \
p = glob.glob('sqlite-amalgamation-*.zip')[0]; \
d = hashlib.sha3_256(open(p, 'rb').read()).hexdigest(); \
sys.exit(0 if d == '${SQLITE_AMALGAMATION_SHA3}' else (print('sha3 mismatch:', d) or 1))" \
    && unzip -q sqlite-amalgamation-*.zip \
    && cp sqlite-amalgamation-*/sqlite3.c sqlite-amalgamation-*/sqlite3.h src/

RUN printf '%s\n' \
    '[build_ext]' \
    'define=SQLITE_ENABLE_FTS5,SQLITE_ENABLE_JSON1,SQLITE_ENABLE_RTREE,SQLITE_ENABLE_MATH_FUNCTIONS,SQLITE_ENABLE_COLUMN_METADATA,SQLITE_ENABLE_STAT4,SQLITE_ENABLE_DBSTAT_VTAB,SQLITE_SOUNDEX,SQLITE_THREADSAFE' \
    > src/setup.cfg

RUN cd src && pip install --no-cache-dir setuptools wheel \
    && python setup.py bdist_wheel \
    && mkdir -p /wheels && cp dist/*.whl /wheels/

# Fail the build rather than ship a silently-old or crippled SQLite.
# The version/FTS5/load_extension checks alone don't prove the extension is
# statically linked -- a dynamically linked build against distro
# libsqlite3.so would pass them just as well while quietly reintroducing the
# WAL-reset bug. Assert with ldd instead of assuming setup.py did the right
# thing.
RUN pip install --no-cache-dir /wheels/*.whl \
    && SO=$(python -c "import pysqlite3, os, glob; print(glob.glob(os.path.join(os.path.dirname(pysqlite3.__file__), '_sqlite3*.so'))[0])") \
    && if ldd "$SO" | grep -qi libsqlite3; then echo "not statically linked: $SO links libsqlite3" >&2; exit 1; fi \
    && python -c "import pysqlite3, sys; \
v = tuple(int(x) for x in pysqlite3.sqlite_version.split('.')); \
c = pysqlite3.connect(':memory:'); \
c.execute(\"CREATE VIRTUAL TABLE g USING fts5(body, tokenize='trigram')\"); \
c.enable_load_extension(True); \
print('built sqlite', pysqlite3.sqlite_version); \
sys.exit(0 if v >= (3,53,4) else 1)"

# Stage 2: the exporter itself.
FROM python:3.11-slim

WORKDIR /workspace/live-hermes
ENV PYTHONPATH=/workspace/live-hermes

COPY --from=sqlite_build /wheels /wheels
RUN pip install --no-cache-dir 'pydantic>=2,<3' /wheels/*.whl && rm -rf /wheels

# Aliases sqlite3 -> pysqlite3 at interpreter start, before any app import.
COPY deploy/docker/00-pysqlite3-shim.pth /usr/local/lib/python3.11/site-packages/00-pysqlite3-shim.pth

RUN python -c "import sqlite3, sys; \
print('effective sqlite', sqlite3.sqlite_version, sqlite3.__name__); \
sys.exit(0 if tuple(int(x) for x in sqlite3.sqlite_version.split('.')) >= (3,53,4) else 1)"

CMD ["python", "/workspace/live-hermes/deploy/docker/job-intel-exporter.py"]
