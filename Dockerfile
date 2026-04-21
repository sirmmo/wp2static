FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml README.md ./
COPY wp2static ./wp2static


FROM base AS runtime
RUN pip install --no-cache-dir .

# Mount points:
#   /data/dump.sql   the mysqldump file
#   /data/uploads    the wp-content/uploads tree (optional)
#   /out             where the migrated site tree is written
VOLUME ["/data", "/out"]
WORKDIR /out

ENTRYPOINT ["wp2static"]
CMD ["--help"]


FROM base AS test
COPY tests ./tests
RUN pip install --no-cache-dir ".[test]"
ENTRYPOINT ["pytest"]
CMD ["-q"]
