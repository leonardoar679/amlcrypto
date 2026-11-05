# Built and smoke-tested by the docker-build job in CI (.github/workflows/ci.yml). It has never
# been built on the development machine, which has no Docker.
# One image, two commands: the default runs the API; `python -m app.worker` runs the worker.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/api

COPY pyproject.toml constraints.txt ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
# Editable on purpose: app.core.config locates alembic.ini and migrations/ relative to the
# source tree, so the code must be imported from /srv/api, not copied into site-packages.
RUN pip install -c constraints.txt -e ".[mysql]"

RUN useradd --system --uid 10001 aml && mkdir /data && chown aml /data
USER aml

ENV DATABASE_URL=sqlite:////data/aml.db
VOLUME ["/data"]
EXPOSE 8000

# Migrating on start is fine for the single container this runs as today; with several
# replicas, run `python -m app.cli migrate` as a separate one-off step instead.
CMD ["sh", "-c", "python -m app.cli migrate && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
