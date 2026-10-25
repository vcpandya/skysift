FROM python:3.12-slim
RUN useradd -m skysift
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
# the stored extinction records (spec 12.3), fixed at build: a target without one cannot get a dossier inside the
# image (the dossier exits 2), since tools/ is not copied; run tools/extinction.py for it outside and rebuild
COPY docs/candidates ./docs/candidates
# Editable install so that config/ (resolved relative to the source tree) is found at run time.
# Behind a TLS-inspecting proxy or antivirus, pass its root certificate as a build secret
# (docker build --secret id=pipcert,src=root.pem ...); production builds pass nothing and verify PyPI as usual.
RUN --mount=type=secret,id=pipcert \
    if [ -f /run/secrets/pipcert ]; then export PIP_CERT=/run/secrets/pipcert; fi; \
    pip install --no-cache-dir -e ".[s3,pg,ui]"
RUN mkdir -p /data && chown skysift:skysift /data
USER skysift
ENV PYTHONUNBUFFERED=1
ENV SKYSIFT_DATA_DIR=/data SKYSIFT_BLOB_URL=file:///data/blobs SKYSIFT_DATABASE_URL=sqlite:////data/engine.db SKYSIFT_RETENTION=stream
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "skysift"]
CMD ["--help"]
