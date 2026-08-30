# The studio, as a container. Build and run:
#     docker build -t mockupgen .
#     docker run --env-file .env -p 5000:5000 -v ./data:/app/data mockupgen
#
# The local-model stack (SAM 2.1, torch) is not installed here -- see
# requirements-ml.txt for the machine that runs those.
FROM python:3.12-slim

# libgl/libglib: what OpenCV loads even in its headless build.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The user first, and the copy already owned by it: a chown -R afterwards
# writes a second copy of everything into another layer.
RUN useradd --create-home --uid 10001 studio \
    && mkdir -p data uploads outputs templates_data draft_templates logs models \
    && chown -R studio:studio /app
COPY --chown=studio:studio . .
USER studio

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "run_server.py"]
