# ======================================================================
#  BACKEND CONTAINER
# ======================================================================
#  A Dockerfile is a recipe for building an IMAGE -- a complete, frozen
#  snapshot of an operating system with our app and all its dependencies
#  already installed.
#
#  WHY BOTHER?
#  "It works on my machine" stops being a problem. The image that runs on
#  Render is byte-for-byte the same one you can run locally, down to the
#  system libraries. No version drift, no missing packages.
#
#  Build it:  docker build -t docminds-api .
#  Run it:    docker run -p 8000:8000 --env-file .env docminds-api
# ======================================================================

# ---- Base image ------------------------------------------------------
# `slim` is a stripped-down Debian with Python pre-installed. The full image
# is around 1 GB; slim is roughly 150 MB. We pin 3.11 rather than using
# `latest`, so a future Python release cannot silently break the build.
# Pinned to a digest as well as a tag: a tag can be re-pointed, a digest
# cannot. Dependabot (docker ecosystem) bumps the digest when the tag moves.
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

# ---- System packages -------------------------------------------------
# These are NOT Python packages -- they are operating system programs that
# our Python libraries call out to:
#
#   tesseract-ocr      the actual OCR engine. `pytesseract` is only a thin
#                      wrapper around this binary; without it, OCR fails.
#   tesseract-ocr-eng  the English language training data.
#   libgl1 / libglib   shared graphics libraries that Pillow and PyMuPDF
#                      need in order to load and rasterise images.
#   gcc / g++          C compilers, needed to build any dependency that
#                      ships without a prebuilt wheel.
#
# `--no-install-recommends` skips optional extras, and deleting the apt
# lists afterwards keeps the final image noticeably smaller. Both commands
# are chained into ONE `RUN` on purpose: each RUN creates a new image layer,
# so cleaning up in a separate step would leave the deleted files sitting in
# the previous layer, still taking up space.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# All later commands run from this directory inside the container.
WORKDIR /app

# ---- Python dependencies --------------------------------------------
# Copying requirements.txt BEFORE the source code is a deliberate and
# important optimisation. Docker caches each layer and reuses it while its
# inputs are unchanged. Because the application code changes far more often
# than the dependency list, this ordering means editing a Python file
# rebuilds in seconds, whereas copying everything first would reinstall
# every dependency on every single build.
COPY requirements.txt .

# `--no-cache-dir` stops pip keeping its download cache inside the image,
# which would add several hundred wasted megabytes.
# setuptools and wheel too, not just pip: the base image's copies carry known
# CVEs (setuptools vendors jaraco.context and wheel), and Trivy flags them.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ---- Pre-download the embedding model -------------------------------
# Without this, the model is fetched on the first upload -- meaning the very
# first user waits ~30 extra seconds, and the app breaks entirely if the
# host has no outbound internet. Baking it into the image makes container
# start-up predictable.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('all-MiniLM-L6-v2')"

# ---- Application code -----------------------------------------------
COPY . .

# Where uploaded files are written. Note this lives inside the container's
# own filesystem, which is EPHEMERAL: it is wiped whenever the container
# restarts. That is fine for a demo, but a production deployment should
# mount a persistent volume here or store uploads in object storage.
RUN mkdir -p /app/uploads

# ---- Runtime settings ------------------------------------------------
# PYTHONUNBUFFERED=1 makes print() appear in the logs immediately instead of
# being held in a buffer. Without it, a crash can lose the very log lines
# that would have explained it.
ENV PYTHONUNBUFFERED=1
# Stops Python writing .pyc files, which are useless in a disposable container.
ENV PYTHONDONTWRITEBYTECODE=1

# Purely documentation: it records which port the app listens on. It does not
# actually publish the port -- that is done by `-p` or by the host platform.
EXPOSE 8000

# ---- Start command ---------------------------------------------------
# `sh -c` is used so that ${PORT} is expanded by the shell at runtime.
# Render (and most hosts) assign a port dynamically through that environment
# variable, so hard-coding 8000 would make the service unreachable.
#
# We run migrations first: `alembic upgrade head` brings the database schema
# up to date before the API starts accepting traffic. That makes deployments
# self-applying -- there is no separate manual migration step to forget.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
