FROM python:3.12-slim
RUN useradd -m skysift
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
# Editable install so that config/ (resolved relative to the source tree) is found at run time.
RUN pip install --no-cache-dir -e ".[s3]"
RUN mkdir -p /data && chown skysift:skysift /data
USER skysift
ENV PYTHONUNBUFFERED=1
ENV SKYSIFT_DATA_DIR=/data SKYSIFT_BLOB_URL=file:///data/blobs SKYSIFT_DATABASE_URL=sqlite:////data/engine.db SKYSIFT_RETENTION=stream
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "skysift"]
CMD ["--help"]
