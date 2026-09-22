# Video Face Recogniser

A local-first, Docker-deployable companion for Stash that will recognise people in original home-video files. The project is being delivered phase by phase so every milestone remains runnable.

## Current milestone

Dynamic video sampling and the first face-detection increment are implemented:

- responsive browser shell
- FastAPI REST API and OpenAPI docs
- PostgreSQL with pgvector enabled and Alembic migrations
- persistent Redis/RQ worker service, isolated from the API
- Stash connection test and paginated scene metadata import
- safe translation from Stash file paths to the read-only media mount
- searchable, paginated selection across all synchronized scenes
- persistent batches and background video-processing jobs
- FFprobe metadata collection and file change fingerprints
- adaptive FFmpeg sampling using periodic and scene-change signals
- persistent timestamped frames, resumable reuse, progress reporting, and frame previews
- separate workflow, batch, and connection tabs with bulk scene selection
- processed-state filtering, Stash thumbnails, and detailed media metadata

CPU face detection and quality-scored crops are available through **Detect faces** and **View faces**. Local embeddings and within-video grouping are available through **Group faces**. Tracking, cross-video matching, identity decisions, and Stash write-back remain later increments. This milestone never modifies Stash or source videos.

## Architecture

| Service | Purpose |
|---|---|
| `web` | Static browser UI and reverse proxy, exposed on port 8787 by default |
| `api` | Settings, Stash sync, scenes, batches, jobs, and generated-frame endpoints |
| `worker` | Separate RQ process for FFprobe and FFmpeg jobs |
| `postgres` | Durable metadata and future pgvector embeddings |
| `redis` | Worker queue state |
| `migrate` | One-shot database migration run before API/worker startup |

Original videos are mounted at `/media` with Docker's read-only flag. Generated application files use `/appdata`, and PostgreSQL/Redis use named volumes.

## Deployment in the Stash Proxmox LXC

### Prerequisites

- Docker Engine with the Compose plugin
- the repository copied or cloned into the LXC
- the host video library visible inside the LXC (for example at `/data`)
- a Stash API key if authentication is enabled
- enough free space for Docker images and persistent database data

The LXC must permit Docker (commonly nesting and keyctl features in Proxmox). Configure that at the Proxmox host level if Docker does not already run in this container.

### 1. Configure the application

From the repository directory:

```bash
cp .env.example .env
nano .env
```

At minimum, set a strong database password and verify these values:

```dotenv
POSTGRES_PASSWORD=replace-with-a-long-random-password
MEDIA_LIBRARY_PATH=/data
APPDATA_PATH=./appdata
STASH_URL=http://host.docker.internal:9999
STASH_API_KEY=your-stash-api-key
STASH_PATH_PREFIX=/data
MEDIA_PATH_PREFIX=/media
WEB_PORT=8787
```

Path mapping is important. `STASH_PATH_PREFIX` is the path prefix Stash returns, while `MEDIA_PATH_PREFIX` is where this stack sees the same library. With the example values, Stash's `/data/family/clip.mp4` becomes `/media/family/clip.mp4` inside the API and worker containers.

If Stash is another container on a shared Docker network, use its resolvable service/container name instead of `host.docker.internal`, and attach the relevant services to that network. If Stash uses HTTPS with a private certificate, install its CA certificate in the backend image rather than disabling certificate verification.

Protect `.env`; it contains credentials and is excluded from Git.

### 2. Create persistent storage and start

```bash
mkdir -p appdata
chown 10001:10001 appdata
docker compose up -d --build
```

Compose waits for PostgreSQL, runs migrations, then starts the API, worker, and web UI. Open:

```text
http://<LXC-IP>:8787
```

API documentation is available at:

```text
http://<LXC-IP>:8787/api/docs
```

The UI can test Stash connectivity, import and search scenes, select an entire filtered result or the next unprocessed group, create a batch, start background processing, monitor progress, and preview extracted frames. Run **Sync scene metadata and thumbnails** once after upgrading to populate the new Stash thumbnails. Start with a batch of 2–5 short videos before scaling up.

### 3. Verify the deployment

```bash
docker compose ps
curl http://localhost:8787/health
docker compose logs --tail=100 migrate api worker web
```

A healthy response resembles:

```json
{"status":"ok","version":"0.9.9","database":"ok"}
```

Confirm the media mount is read-only and a known file is visible:

```bash
docker compose exec api sh -c 'find /media -type f | head'
docker inspect video-face-recogniser-api-1 --format '{{json .Mounts}}'
```

### Updating

Back up first, fetch/copy the new release, then rebuild. The migration service upgrades the schema before the application starts.

```bash
docker compose down
docker compose up -d --build
docker compose ps
```

Do not use `docker compose down -v` during a normal update; `-v` deletes the PostgreSQL and Redis volumes.

### Backups

Back up both database records and generated application data:

```bash
docker compose exec -T postgres pg_dump -U face_recogniser face_recogniser > face-recogniser.sql
tar -czf face-recogniser-appdata.tar.gz appdata/
```

The source media does not need an application-specific backup because this app never modifies it, but it should of course remain covered by the library's normal backup plan.

### Stop or remove containers

```bash
docker compose stop
```

To remove containers while preserving named volumes:

```bash
docker compose down
```

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `face_recogniser` | Database name |
| `POSTGRES_USER` | `face_recogniser` | Database user |
| `POSTGRES_PASSWORD` | required | Database password |
| `STASH_URL` | none | Stash base URL reachable from the API container |
| `STASH_API_KEY` | none | Stash API key; never returned to the browser |
| `STASH_PATH_PREFIX` | `/data` | Prefix on paths returned by Stash |
| `MEDIA_PATH_PREFIX` | `/media` | Corresponding path inside API/worker containers |
| `MEDIA_LIBRARY_PATH` | `/data` | Video library path on the Docker host/LXC |
| `APPDATA_PATH` | `./appdata` | Host path for generated application files |
| `FRAME_SCAN_FPS` | `2` | Low-resolution analysis rate; brief events between samples can be missed |
| `FRAME_SAMPLE_INTERVAL_SECONDS` | `30` | Periodic target ceiling; short videos use a smaller interval and the total frame budget can require wider spacing |
| `FRAME_SCENE_THRESHOLD` | `0.35` | Candidate-change sensitivity; compared against local median change (lower admits more transitions) |
| `MAX_FRAMES_PER_VIDEO` | `300` | Safety cap on retained frames per video |
| `FRAME_MAX_WIDTH` | `1920` | Maximum generated-frame width; smaller videos are not enlarged |
| `PROCESSING_JOB_TIMEOUT_SECONDS` | `21600` | FFprobe/FFmpeg and RQ job timeout |
| `WEB_PORT` | `8787` | Port exposed by the web service |
| `CORS_ORIGINS` | `http://localhost:8787` | Comma-separated allowed browser origins |
| `LOG_LEVEL` | `INFO` | Backend logging level |

## Local development and tests

Use Python 3.12. From the `backend` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check app tests
```

On PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.

Run the browser gallery regression check from the repository root with `node --test web/tests/gallery.test.cjs`.

For a packaged update to LXC 115, see [TRANSFER-LXC-115.md](TRANSFER-LXC-115.md).

Validate the Compose file and build images with:

```bash
docker compose config
docker compose build
```

## Security notes

- Processing is local; no private images, videos, or embeddings are sent to cloud services.
- The source library mount is read-only in both API and worker containers.
- The current API has no user authentication. Bind the web port only to a trusted LAN, firewall it appropriately, or place it behind an authenticated reverse proxy before exposing it beyond the LXC.
- Stash writes remain deliberately absent. Later write-back will require explicit reviewed-batch confirmation and an audit trail.


## Dynamic sampling and face detection (0.3.0)

Use **Reprocess** on a small batch to apply the new sampler. Its versioned cache key leaves older frames intact. The scanner reads the entire video, temporarily storing 64×36 grayscale samples at 2 fps (~16 MiB per hour). It selects temporal anchors, sharp representatives within time bins, and distinct transition observations. A second FFmpeg pass extracts the selected source-video frames at the configured width, preserving their actual timestamps. The frame cap limits retained output across the full timeline rather than truncating the start of the video. Near-duplicate transition candidates are suppressed; temporal anchors are retained even in static footage to preserve coverage. This is a heuristic, not a guarantee of capturing every brief appearance.

After processing, choose **Detect faces**. YuNet runs on the extracted frames and saves crops with a margin at the extracted-frame resolution. **View faces** shows all observations, including those marked low quality. Quality is an uncalibrated heuristic based on face size, blur and exposure, not identity confidence; pose and occlusion are not explicitly scored yet. No person identification or automatic clustering occurs. Face analysis has its own model/version cache, including zero-face results, and can be repeated without re-extracting frames. Detection does not yet feed back into frame sampling.

The bundled model is OpenCV Zoo's [YuNet 2023mar](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), distributed under its MIT license in `backend/models/YUNET-LICENSE`. SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. It runs locally through OpenCV 4.11; no private video or face data is uploaded and runtime requires no model download.

In 0.3.1, each video row has Frames/Faces view buttons and counts. View faces opens those rows directly in Faces view. Face cards show detection score separately from image quality, plus timestamp and expandable quality details. Neither score is identity confidence; identity suggestions remain a later feature. Upgrading from 0.3.0 requires no new migration or reprocessing.


## Embeddings and within-video groups (0.4.0)

After Detect faces completes, choose **Group faces**, then **View faces**. Each video's Faces view shows expandable groups, up to three quality-ranked representative crops, observation counts, and timestamp ranges. Singleton faces remain separate; low-quality observations are excluded from embeddings/grouping but retained under All observations. Clicking a crop opens playback at its timestamp.

SFace aligns faces using the saved five landmarks on the extracted frame, generates 128-dimensional L2-normalized features, and stores them with their model key in pgvector. Rerunning grouping reuses these features. Grouping is deterministic quality-ordered complete-link insertion: a face must have cosine similarity at least 0.60 to every group member, must not share a source frame with another member, and must have at least a 0.05 advantage over another qualifying group. Ambiguous faces form singleton groups. These are conservative starting settings, not calibrated on the family library; groups are suggestions, not verified identities. Group numbering is local to each video and may change when observations change. Tracking, cross-video matching, and manual assignment are not included yet.

Grouping has its own input fingerprint and algorithm key. Changes to detector outputs make previous group results stale; the UI asks you to rerun Group faces. This never rewrites or removes original face observations. Embeddings from each model version remain separate.

The bundled [OpenCV Zoo SFace 2021dec model](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) is accompanied by its Apache-2.0 license in `backend/models/SFACE-LICENSE`. SHA-256: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`. It runs locally and requires no runtime downloads. Migration 0005 creates the new tables automatically; back up before upgrading from 0.3.x.

In 0.4.1, clusters appear in a horizontal row with one representative image and a face count per card. Select a card to inspect the individual faces below the row. Cluster cards have no timestamp labels; individual face cards retain timestamps and quality details. Existing groups do not need regenerating.


### Manual review (0.5.0)

**Review**, immediately after **Batches**, contains **Clusters**, **People**, and **Suggestions**. Select a batch and import its latest completed face groups. Split selected faces into a new group, remove them into individual groups, or merge groups from the same video. Merged groups need fresh identity confirmation.

Create and rename people in **People**, then assign clusters or leave them unresolved. Confirmed faces become reference examples. **Suggestions** compares pending clusters with confirmed references from other videos and offers manual confirmation or rejection; similarity scores are not identity probabilities.

Review snapshots and decision history are stored separately from automatic grouping, so regrouping does not overwrite corrections or assignments. Newly detected observations can be imported later. Batches with review history cannot be deleted. Migration 0006 runs on upgrade; back up the database first.

In 0.5.1, both Clusters and Suggestions offer direct assignment, creating a person and assigning them, and refreshing person suggestions. Suggestions refresh when opened and after decisions, using current confirmed references from other videos. Rejected candidates remain hidden. Ignore selected faces or an entire cluster to exclude them from review suggestions and references in that batch; restore ignored groups from Clusters. No detector rerun or new migration from 0.5.0 is needed.

In 0.5.2, check individual observations before **Confirm selected faces**, or explicitly use **Confirm all faces**. Partial confirmation separates the confirmed faces from the remainder. **Unconfirm selected faces** removes incorrect confirmations. People counts and matching use explicit confirmations only. Migration 0007 preserves earlier cluster names as labels needing review; confirm the intended faces to restore reference examples. Suggestions ranks up to three eligible identities without a score cutoff, including other clusters in the same video. A low score is not a confident match. Rejected people and the query cluster's own observations remain excluded.

In 0.5.3, candidate cards clearly separate stored references from selectable current-cluster faces beside the confirmation buttons. Selections stay synchronized across candidates. Clusters and Suggestions offer Outstanding, Confirmed, Unresolved, Ignored and All status filters, with batch-wide cluster/face progress totals. Completed and ignored clusters are excluded from the default outstanding queue. People → Review reference faces lists all stored references and their source batches; removing incorrect confirmations returns those faces to pending review across all listed batches. Images and history remain intact. No migration from 0.5.2.


### Scene review (0.6.0)

Review opens on **Scenes**. The overview reports complete and awaiting-review scenes with identified people and identified, unknown and ignored face counts. Open a video to review named faces and remaining unknowns together. Mark a scene complete once its key people have been reviewed; unknown background faces do not block completion. Completion is per scene within a batch, can be reopened, and becomes stale when review data changes or new face observations arrive. Unimported detected faces must be imported before completion.

Clusters and Suggestions are supporting tools with status checkboxes; completed scenes are hidden by default. Each unknown face can show its own top three candidate identities. The minimum similarity defaults to −1, the full score range. An optional checkbox includes previously rejected candidates. Only people with eligible confirmed reference embeddings can appear. Confirmation buttons beside a candidate identify exactly that query face.

Migration 0008 stores scene completion separately from face confirmation. Existing identity decisions remain intact.


### Scene review improvements (0.6.1)

Scene headers include a horizontally scrollable strip of all extracted batch frames for that video, with timestamp playback links. Outstanding clusters share one box with representative cards; Clusters and Suggestions are sub-options within the scene. Select a cluster to inspect its members or compare per-face suggestions.

Grouping now merges the globally strongest compatible groups using average cross-group cosine similarity (default 0.50), a 0.25 minimum pair score and same-frame exclusions. These are heuristic starting settings, not measured identity accuracy. **Regroup outstanding faces** applies it to existing pending, unassigned review groups using cached embeddings. Confirmed, ignored, unresolved and labelled groups are retained; pending manual splits are replaced and recorded in history. Similarity can be adjusted from 0.40 to 0.70. Missing embeddings remain singletons. New automatic grouping jobs use the new versioned algorithm.


### Scene re-detection (0.9.9)

Each scene overview box places a horizontally scrollable frame strip on the left, with separate labelled rows of confirmed faces below it. Both remain contained within the box; narrower screens stack the scene summary above the media.

Open a scene and set the **Detection confidence** slider (0.10–0.99, default 0.80). Press **Re-detect faces** to queue a preview using only its existing extracted frames. Moving the slider has no processing side effects. Lower confidence finds additional potential faces and more false detections.

Preview summaries distinguish new candidates, existing faces detected again, and existing faces not found at that confidence. Review crops and **Add selected new faces**, or **Discard preview**. Existing observations, confirmed identities, ignored faces and manual groups are retained. Accepted additions are Review evidence and start as unassigned singletons; use scene regrouping and identity suggestions normally. No frame extraction or canonical detector-cache replacement occurs.

Re-detection is staged separately from scene completion. Running or discarding a preview does not reopen completed scenes; accepting additions changes the review fingerprint and returns the scene to awaiting review. Changed frames or review decisions invalidate a preview before application. Migration 0009 retains bounding boxes for durable duplicate matching, and backfills them from existing observations. If an older reviewed face has no recoverable location, the operation is blocked rather than risking a duplicate of an ignored face.

See [CLUSTERING.md](CLUSTERING.md) for the intra-video clustering rules, model, configuration, diagnostics and calibration guidance.

### Large batches and reprocessing

Review displays five scenes per page and loads only their faces and frames. In Workflow, choose a Review batch and tick Awaiting Review only to select unfinished scenes for another processing batch. Select all matches honours these filters. Without a batch selection, the newest active batch determines each scene’s status. Existing review history remains in its original batch.
