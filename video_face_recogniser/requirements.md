# Home Video Face Recognition App — Requirements

## 1. Purpose

Build a Docker-deployable application for face recognition across a private home-video library.

The application will:

1. Read video/scene metadata from Stash.
2. Process the original source video files directly, rather than relying on Stash sprites.
3. Detect faces and generate high-quality face crops.
4. Generate facial embeddings.
5. Cluster similar faces into candidate identities.
6. Allow manual review of every cluster.
7. Suggest multiple likely known people for each cluster, with ranked confidence scores.
8. Learn from confirmed and rejected identity selections.
9. Support the same person changing appearance over a long time period.
10. Write confirmed people tags back to Stash only after a complete processing batch has been reviewed and explicitly committed.

Stash remains the primary media catalogue and browsing/tagging interface.

This application is a specialised face-recognition, clustering and identity-review engine.

---

## 2. Deployment Environment

### Target environment

The application will run in the existing Proxmox LXC that currently runs Stash.

### Deployment method

The application must be Docker deployable using Docker Compose.

Target startup experience:

```bash
docker compose up -d
```

### Suggested services

Initial architecture should support separate containers/services for:

- `web` — browser-based frontend
- `api` — backend API and orchestration
- `worker` — video and ML processing
- `postgres` — relational database
- `redis` — optional job queue/cache if needed

The architecture may be simplified if there is a strong technical reason, but long-running ML/video processing must not block the web/API process.

### Storage mounts

The application must support:

- read-only access to the source home-video library
- persistent application data
- persistent database storage
- persistent generated face crops/thumbnails
- persistent ML/job state

The source videos must never be modified.

Example:

```yaml
volumes:
  - /data:/media:ro
  - ./appdata:/appdata
```

---

## 3. Existing Video Library

Current raw filesystem library:

- approximately **8,257 video files**
- approximately **531 GiB**
- `/data` filesystem reports approximately **542G**

Current Stash metadata coverage:

- approximately **7,100 sprite-backed Stash scenes**
- approximately **385 GiB**

The new application must treat the raw source library as authoritative for image quality.

Stash metadata should be used for:

- scene IDs
- source paths
- existing scene metadata
- existing tags
- final tag write-back

Do not depend on Stash sprites for face recognition.

---

## 4. Design Priorities

Primary priority:

> **Recognition accuracy over processing speed**

The user is comfortable processing the library in chunks and waiting for slower, higher-quality analysis.

The system should therefore favour:

- better source-frame selection
- higher quality face detection
- stronger face embeddings
- multiple observations of each person
- conservative identity matching
- manual verification
- resumable batch processing

Do not optimise primarily for fastest possible full-library processing.

---

## 5. Expected Number of People

The target library contains approximately **50 recurring known people**.

The design should therefore optimise for:

- a relatively small known-person set
- high accuracy among visually similar family members
- repeated appearances over many years
- user-confirmed identities
- learning from prior identity decisions

The app does not need to be designed primarily for thousands of unrelated identities.

---

## 6. Time Span and Ageing

The home-video archive spans approximately **16 years**.

The same person may appear:

- as a toddler
- as a child
- as a pre-teen
- as a teenager
- as an adult

The application must explicitly support appearance change over time.

### Person model

There should be one canonical `Person` identity.

A person may have multiple appearance-era subclusters or reference groups.

Example:

```text
Person: Josh
├── early childhood
├── primary-school age
├── pre-teen
└── teenage
```

The user should not need to create different people for different ages.

### Temporal information

Where useful, the system should use video dates and temporal continuity as supporting evidence.

Temporal proximity must not override facial evidence, but it may assist ranking and cluster relationships.

---

## 7. Processing Model

Processing must be performed in **user-selected batches**.

A batch may represent:

- selected folders
- selected Stash scenes
- date ranges
- a fixed number of videos
- another practical selectable subset

The user intends to work through the library progressively.

### Batch requirements

Every batch must have persistent state.

Possible states:

```text
created
queued
processing
face_detection_complete
clustering
ready_for_review
review_in_progress
review_complete
ready_to_commit
committing
committed
failed
```

A restart of Docker, the LXC, or the server must not lose batch state.

Processing must be resumable.

Already successfully processed files must not be unnecessarily reprocessed.

---

## 8. Video Processing

Use FFmpeg/ffprobe or equivalent high-quality local tooling.

For each source video, collect at least:

- file path
- Stash scene ID
- duration
- width
- height
- frame rate
- codec
- creation/date metadata where available
- file size
- processing fingerprint/hash or equivalent change detector

### Frame selection

Do not simply sample one frame every fixed number of seconds.

The frame-selection system should favour useful facial evidence.

Consider:

- scene-change detection
- periodic sampling within long shots
- higher sampling around detected faces
- avoiding near-duplicate frames
- image sharpness
- motion blur
- face size
- head pose
- occlusion
- exposure

Every retained face observation must preserve the exact timestamp in the video.

---

## 9. Face Detection

The system must detect all usable faces in selected frames.

For every detected face store at least:

- video ID
- Stash scene ID
- timestamp
- source frame reference
- bounding box
- detector confidence
- face quality score
- face crop path
- embedding
- model/version information

### Face crop quality

Face crops must be generated directly from the original video frame.

Do not use low-resolution Stash sprite images as the source.

Crops should:

- preserve enough resolution for manual identification
- include a reasonable margin around the face
- optionally store aligned and unaligned variants if useful
- be suitable for display in the review UI

### Quality filtering

Very poor observations may be excluded from clustering.

Examples:

- extremely small face
- severe blur
- extreme occlusion
- unusable exposure
- detection artefact

Rejected low-quality observations may still be recorded for audit/debug purposes if useful.

---

## 10. ML Stack

The implementation should evaluate strong local/open-source models suitable for home-video face recognition.

Likely candidates include:

- InsightFace
- SCRFD / RetinaFace for face detection
- ArcFace-family embeddings

The final selection should prioritise accuracy and maintainability.

Model choice must not be tightly hard-coded throughout the application.

Store:

- detector name
- detector version
- embedding model name
- embedding model version
- relevant model configuration

This must allow embeddings or detections to be regenerated later when models are upgraded.

---

## 11. Face Embeddings

Generate a high-quality embedding for every accepted face observation.

Embeddings should remain associated with the individual observation, independently of the identity currently assigned to it.

This distinction is important:

```text
Face observation != Person
```

A face observation is evidence.

Its assignment to a person can later change.

### Vector storage

Prefer PostgreSQL with `pgvector` unless benchmarking demonstrates a compelling reason to use a separate vector database.

---

## 12. Initial Identity Discovery

The user does **not** want to preload identities using reference photographs.

The system should start by clustering faces detected from the videos.

Workflow:

```text
videos
  ↓
frames
  ↓
faces
  ↓
embeddings
  ↓
clusters
  ↓
manual identity review
```

Clustering should be conservative.

It is preferable to create two clusters for the same person that the user can merge than to incorrectly combine two different people into one cluster.

---

## 13. Cluster Review

The user will manually review **all clusters**.

The review workflow is therefore a core application feature and must receive significant UX attention.

For each cluster show:

- several high-quality representative faces
- diverse appearances within the cluster
- number of face observations
- number of videos/scenes represented
- approximate date range
- existing person assignment if any
- ranked candidate identities
- match confidence for each candidate

### Review actions

The user must be able to:

- assign cluster to an existing person
- create a new person
- merge clusters
- split an incorrect cluster
- move individual faces between clusters
- mark a detection as `not a face`
- ignore a cluster/person
- leave a cluster unresolved
- rename a person

---

## 14. Identity Suggestions

Once known people have been established, every reviewed cluster should receive **multiple ranked person suggestions**.

Example:

```text
Suggested identity

1. Josh       91%
2. Sam        63%
3. Allen      38%
4. Unknown
```

Always expose multiple candidates rather than only the top match.

### Confidence

Do not simply label raw cosine similarity as a probability.

Internally retain:

- raw embedding similarity
- nearest-neighbour information
- cluster-level similarity
- supporting observations
- any calibration values

The UI may display a human-friendly confidence score, but it should be calibrated from real user-confirmed/rejected examples where possible.

---

## 15. Learning from User Decisions

The system must improve from manual selections.

When the user confirms a person:

- add the accepted cluster/observations to that person's reference evidence
- improve future suggestion ranking
- retain appearance diversity
- retain useful age-era representation

When the user rejects a suggested person:

- retain that negative feedback
- use it to reduce repeated false suggestions where technically practical

The system must not rely on a single "best portrait" per person.

Each person should have multiple representative embeddings covering:

- ages
- head angles
- lighting conditions
- facial hair changes
- glasses
- appearance changes

The learning mechanism should initially favour transparent, deterministic or explainable techniques rather than requiring complex model retraining.

Full neural-network fine-tuning is not required for V1.

---

## 16. Person and Era Representation

Suggested conceptual model:

```text
Person
  ├── PersonReferenceSet
  │     ├── era/date range
  │     └── representative embeddings
  │
  └── assigned Face/Cluster observations
```

Era subclusters may be automatically inferred or created through clustering.

The user must be able to merge age-separated clusters into the same canonical person.

---

## 17. Stash Integration

Stash remains the final source of truth for:

- scene catalogue
- media browsing
- user-facing people tags

The new application should communicate with Stash through its API.

### Required Stash functions

The app must be able to:

- connect to the configured Stash instance
- retrieve scenes
- retrieve scene paths
- retrieve existing tags
- map/create people tags as required
- add confirmed people tags to scenes
- preserve existing tags

The app should maintain an internal mapping such as:

```text
Internal Person
    ↓
Stash Tag ID
```

Do not assume that person names and Stash tag IDs are interchangeable.

---

## 18. No Immediate Stash Writes

Face processing and manual review must **not** immediately modify Stash.

During a batch, all proposed tag changes remain local and pending.

The system must wait until:

1. all videos in the batch are processed
2. clustering is complete
3. the user has reviewed the clusters
4. the batch reaches `ready_to_commit`
5. the user explicitly chooses to apply the batch

Only then may Stash be updated.

---

## 19. Batch Commit to Stash

Before committing, show a clear summary.

Example:

```text
Batch complete

250 videos processed
1,840 usable face observations
73 clusters reviewed
18 known people identified
146 Stash scenes will receive new people tags
12 unresolved clusters remain
0 existing tags will be removed
```

Provide a detailed preview of proposed changes where practical.

### Commit behaviour

On explicit user confirmation:

- add the confirmed people tags to the relevant Stash scenes
- do not remove unrelated existing tags
- record every Stash change made
- record original relevant state where required for rollback
- mark the batch committed only after successful API operations

Partial failures must be recoverable.

---

## 20. Audit and Rollback

Every Stash write should be auditable.

Store:

- batch ID
- scene ID
- tag/person added
- timestamp
- result
- previous relevant state
- Stash API response/error where useful

The design should support undoing a committed batch where practical.

At minimum, the system must know which tags it added.

---

## 21. User Interface

The UI should focus on recognition workflow rather than replacing Stash.

Primary screens:

### Dashboard

Show:

- current batch
- queued jobs
- processing status
- completed batches
- failures
- model information

### Create Batch

Allow selection of videos/scenes by practical criteria.

### Processing Status

Show:

- videos completed / total
- faces detected
- processing failures
- current step
- estimated progress based on completed work

### Cluster Review

This is the key UI.

Show large, high-quality face images rather than low-resolution sprite sheets.

Support keyboard-efficient review where practical.

### People

Show:

- name
- representative faces
- appearance-era groups
- number of assigned clusters/faces
- date range
- Stash tag mapping

### Batch Commit

Show final pending Stash changes and require explicit commit.

### Settings

At minimum:

- Stash URL
- Stash API credentials/token
- media mount mapping
- processing parameters
- model selection
- confidence thresholds
- face quality thresholds

---

## 22. Database Model

Initial schema should include concepts equivalent to:

```text
Library
Video
Batch
BatchVideo
Frame
Face
FaceEmbedding
Cluster
ClusterFace
Person
PersonReference
PersonCluster
IdentitySuggestion
IdentityDecision
StashScene
StashTagMapping
PendingTagChange
CommittedTagChange
ProcessingJob
ModelVersion
AppSetting
```

Exact naming may differ.

### Important principles

- source files are immutable
- face observations are retained independently of identity decisions
- identity decisions are auditable
- model/version information is retained
- batch processing is persistent
- Stash write-back is transactional/recoverable where practical

---

## 23. Background Jobs

Long-running operations must execute in background workers.

Expected job types include:

- Stash metadata sync
- source-file probe
- frame extraction
- face detection
- quality analysis
- embedding generation
- clustering
- identity suggestion generation
- representative-face selection
- Stash batch commit
- model migration/reprocessing

Jobs must have:

- persistent status
- retry handling
- meaningful error messages
- idempotency where practical
- resumability

---

## 24. Incremental Processing

The system must avoid unnecessary work.

If a video has already been processed with the current relevant models/settings and has not changed, it should not require full reprocessing.

Changing one component should permit targeted regeneration.

Examples:

- new clustering algorithm → reuse existing embeddings
- new confidence calibration → reuse embeddings
- new embedding model → regenerate embeddings but possibly reuse face detections
- new face detector → redetect faces
- UI change → no video reprocessing

This is a major architectural requirement.

---

## 25. Hardware Support

Initial target is the existing Linux Proxmox LXC environment.

The app should support CPU execution.

Architecture should permit optional acceleration where available later.

Do not make NVIDIA GPU access a mandatory requirement for V1 unless hardware inspection shows it is already available and useful.

ML execution backend should be configurable.

---

## 26. Privacy and Security

This application processes private family videos and biometric face data.

Default requirements:

- local-only processing
- no cloud face-recognition APIs
- no external face/image uploads
- no telemetry containing images or embeddings
- no source media modification
- Stash credentials stored securely
- application data remains within the user's environment

The application may download public ML model files during setup if explicitly configured, but it must not transmit private video content externally.

---

## 27. Non-Goals for V1

Do not spend significant development effort duplicating Stash functionality.

The following are not required for V1:

- general-purpose video library browser
- Plex-like media playback interface
- advanced multi-person video search
- photo-library management
- cloud sync
- mobile-native application
- automatic Stash tagging before review
- full neural-network face model fine-tuning
- replacing Stash

A simple video-preview/jump-to-timestamp capability inside the review workflow may still be useful.

---

## 28. Quality Goals

The system should prioritise:

1. low false-positive identity assignments
2. useful clustering
3. high-quality review images
4. reliable suggestions across age changes
5. learning from user decisions
6. recoverable batch processing
7. safe Stash integration

When uncertain, the system should ask the user to decide rather than silently making a weak identity assignment.

---

## 29. Proposed Implementation Phases

### Phase 1 — Foundation

Build:

- repository structure
- Docker Compose
- PostgreSQL
- migrations
- API
- frontend shell
- settings
- Stash connection test
- Stash scene import
- batch model

No ML required yet.

### Phase 2 — Video Processing

Build:

- ffprobe metadata collection
- FFmpeg frame extraction
- intelligent frame sampling
- persistent jobs
- processing status UI
- generated-frame storage

### Phase 3 — Face Engine

Build:

- face detector
- face quality evaluation
- high-quality crop generation
- face alignment if required
- embeddings
- pgvector storage
- model/version tracking

### Phase 4 — Clustering

Build:

- embedding clustering
- representative-face selection
- cluster statistics
- conservative thresholds
- review UI

### Phase 5 — People and Identity Learning

Build:

- canonical Person records
- cluster-to-person assignment
- multiple ranked suggestions
- similarity scoring
- confidence calibration framework
- accepted/rejected decision storage
- reference embedding sets
- age/era subclusters
- merge/split workflows

### Phase 6 — Stash Write-back

Build:

- Person ↔ Stash tag mapping
- pending tag changes
- final batch summary
- explicit commit
- audit log
- retry/recovery
- rollback support where practical

### Phase 7 — Accuracy Improvement

Benchmark and tune:

- frame-selection strategy
- detector choice
- embedding model
- clustering thresholds
- family-member confusion cases
- ageing behaviour
- confidence calibration
- representative-face selection

### Phase 8 — Operational Hardening

Add:

- backup guidance
- upgrade/migration process
- worker health monitoring
- error handling
- reprocessing controls
- resource limits
- logging
- diagnostics

---

## 30. Development Approach for Claude / Codex

Do not attempt to build the entire application in a single step.

Work phase by phase.

For each phase:

1. inspect the existing repository
2. describe the proposed change
3. implement a small coherent increment
4. add/update database migrations
5. add tests
6. update documentation
7. verify Docker build
8. verify existing functionality has not regressed

Prefer working software after each milestone.

Do not create placeholder architecture that cannot be exercised.

---

## 31. Technical Decision Principles

When implementation choices are unclear, prefer:

- Python for ML/backend processing
- PostgreSQL for durable application state
- pgvector for embeddings
- FFmpeg/ffprobe for video access
- established face-recognition models rather than building models from scratch
- Docker Compose deployment
- REST or another simple well-documented API between frontend/backend
- simple maintainable frontend architecture
- explicit database migrations
- idempotent jobs
- immutable source-media handling

Accuracy and maintainability are more important than cleverness.

---

## 32. Core User Journey

The target V1 workflow is:

```text
1. Open app
2. Sync Stash scenes
3. Select a batch of videos
4. Start processing
5. App reads original videos
6. App extracts useful frames
7. App detects faces
8. App generates embeddings
9. App clusters similar faces
10. User reviews every cluster
11. App shows multiple likely people + confidence
12. User confirms/rejects/creates/merges/splits
13. App learns from those decisions
14. Review completes
15. App shows proposed Stash tag changes
16. User explicitly commits batch
17. App adds confirmed people tags to Stash scenes
18. User browses the tagged videos in Stash
19. Repeat with next batch
```

---

## 33. Critical Architectural Rule

The most important rule for the implementation is:

> **Do not couple face detection, embeddings, clustering, identity assignment and Stash tagging into one irreversible pipeline.**

They must be separable layers.

A better model is:

```text
Source Video
    ↓
Face Observation
    ↓
Embedding
    ↓
Cluster
    ↓
Person Decision
    ↓
Pending Stash Tags
    ↓
Explicit Batch Commit
```

This ensures the system can improve its clustering, identity logic and ML models over time without repeatedly decoding the entire video library or corrupting prior tagging decisions.
