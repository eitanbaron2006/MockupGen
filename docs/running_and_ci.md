# Running the studio, and what checks it

## The three dependency lists

| file | what it holds |
|---|---|
| `requirements.txt` | what the server needs to run: Flask, Pillow, numpy, OpenCV (headless), scipy, and the Google GenAI client |
| `requirements-dev.txt` | the above plus pytest and ruff |
| `requirements-ml.txt` | the optional local-model stack: torch and ultralytics, for the SAM 2.1 detection mode |

The ML stack is separate on purpose. It is gigabytes, only the Local AI
detection mode reaches it, and neither the studio nor its tests need it. Put
the weights in `models/` (`sam2.1_l.pt`, `RealESRGAN_x4plus.pth`) on a machine
that will run them.

OpenCV is the **headless** build: the server draws no windows, and the desktop
build drags in X11 for nothing.

## In a container

```bash
docker build -t mockupgen .
docker run --env-file .env -p 5000:5000 \
  -v ./data:/app/data -v ./templates_data:/app/templates_data mockupgen
```

or, with the volumes already described:

```bash
docker compose up --build
```

The image carries the code and its dependencies, **not** the catalog, the
templates or the renders — those are volumes. Baking them in doubles the image
and ships a catalog that is stale the moment a template is edited.

`.env` has to provide `SECRET_KEY` and `ADMIN_PASSWORD`. The server refuses to
start on the development default key, which is printed in this repository and
would let anyone forge an admin session.

The container runs as an unprivileged user and answers `GET /api/health`, which
is also its Docker healthcheck.

## What CI runs

`.github/workflows/ci.yml`, on every push and pull request:

1. **Lint** — `ruff check .`
2. **Test** — `python -m pytest tests/ -q`
3. **Docker** — builds the image, starts it, and waits for `/api/health` to
   answer. A container that builds but does not boot fails the build.

## What the linter is for

`ruff.toml` selects the rules that catch mistakes — undefined names, unused
imports, a closure that captured a loop variable — plus import order. Style
opinions that would rewrite working code for taste are left off, and the few
rules that are switched off are switched off in that file with the reason
written next to them.

```bash
ruff check .          # what CI runs
ruff check --fix .    # the safe fixes
```
