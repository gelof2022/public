# Changelog

## 0.9.9 — 2026-09-15

- Retry failed original playback through Stash’s MP4 stream, proxying credentials and range responses server-side.
- Add persistent Settings for detection confidence, recognition similarity, regroup similarity and Stash playback fallback.
- Add light/dark appearance covering forms, scene review, status badges and playback dialogs.
- Preserve existing cached faces and decisions; capture the detection default when queuing new jobs.

## 0.9.8 — 2026-09-15

- Build balanced FFmpeg timestamp-selection expressions so long sampling plans do not exceed parser depth. Preserve the existing sampling choices and cache keys.
- Report FFmpeg diagnostic output and concise timeout errors instead of dumping the full command.

## 0.9.7 — 2026-09-13

- Page Review scenes in groups of five and fetch clusters/frames only for the visible scenes or opened scene.
- Batch cluster metadata and scene-summary database reads instead of querying separately for every cluster or scene; omit embedding vectors from cluster display queries.
- Prevent overlapping batch refreshes, poll only the visible Batches tab, and bound stage-check concurrency.
- Add Workflow Awaiting Review and Review batch filters, including Select all matches support.

## 0.9.6 — 2026-09-13

- Resolve a single best remaining identity per scene face before collapsing to one row per person.
- Rejecting a pairing automatically reveals the next eligible identity and moves replacement proposals down the queue.
- Remove the overview Refresh suggestions button and expose manual person tagging directly on scene cards.

## 0.9.5 — 2026-09-13

- Replace overview cluster suggestions with one row per proposed person: best scene face, People reference, name, Confirm and Reject.
- Generate matches from confirmed People references, then check independent-frame support and competing identities; cap repeat-sample influence. Already identified people stay in the comparison pool without duplicate rows.
- Confirm only the displayed face. Persist rejections, regenerate from other evidence, and move replacement proposals to the bottom.
- Show exact faces behind same-frame confirmation conflicts in detailed Review, with frame viewing and an explicit audited mirror/reflection exception.
- In detailed Review, prioritise same-video references at 0.30 similarity for low-quality queries; preserve raw scores and label weak matches.

## 0.9.4 — 2026-09-12

- Show three distinct outstanding face groups on each scene-card suggestion page, with one best person match per group.
- Add previous/next group navigation, reject suggestion and ignore selected faces controls.
- Refresh matches after selection changes and discard stale suggestion responses.

## 0.9.3 — 2026-09-12

- Size the archived-batch checkbox correctly and add scene-level confirmed-face correction.
- Show paired scene/reference faces and process larger outstanding clusters first.
- Prefer credible same-scene face references, fall back to confirmed People references, and exclude same-frame identity conflicts in suggestions and confirmation.

## 0.9.2 — 2026-09-12

- Add Archive/Restore batches and an archived-batch visibility checkbox (migration 0013); preserve all review history.
- Show lazy-loaded top-three person choices on scene cards, with face selection and direct confirmation for the next outstanding group.

## 0.9.1 — 2026-09-12

- Colour batch process and Review buttons amber/green using separate completion checks.
- Remove redundant Confirm all faces and keep cluster tool tabs sticky.
- Preserve manually merged groups during Regroup; use a 0.10–0.50 similarity slider.
- Advance right after ignoring a cluster, or focus the next remaining face for partial ignores.

## 0.9.0 — 2026-09-12

- Add manual scene person tagging without creating trusted face references (migration 0011).
- Add Connection → Sync to Stash for additive performer export, name matching, automatic name-only creation and persistent mappings (migration 0012).
- Preserve existing Stash performers and tags; ambiguous names are skipped and reported.

## 0.8.1 — 2026-09-12

- Select all cluster faces by default and offer top-three cluster person confirmations. Deselect exceptions before confirming; individual face suggestions remain expandable.

## 0.8.0 — 2026-09-12

- Add versioned, cached MobileNetV2 context features solely for conservative intra-video clustering.
- Add duplicate-aware weighting, diverse representatives, bounded decision diagnostics and timing.
- Preserve face-only Person suggestions and manual review decisions.
- Add migration 0010 and configurable crop/assistance thresholds; see CLUSTERING.md for calibration and limits.

## 0.7.3 — 2026-09-12

- Fix View in frame for re-detection candidates before acceptance, using their saved source frame and bounding box.

## 0.7.2 — 2026-09-12

- Remove face counts beside identified people.
- Restore all scene faces below the frame strip, including unknown and ignored observations.
- Keep View in frame first and playback second on one line.

## 0.7.1 — 2026-09-12

- Move identified people beneath scene actions, collapsed by person with a thumbnail and count.
- Colour-code Complete and Awaiting review scene statuses.
- Add View in frame beside face playback, with a thin yellow outline on the original extracted frame.
- Explain unavailable original frames or face locations without altering review data.

All notable changes to Video Face Recogniser will be recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning.

## [0.7.0] - 2026-09-12

### Added

- Scene-only re-detection using existing frames, controlled by a confidence slider and explicit trigger.
- Queued preview generation with progress, comparison counts, selective acceptance and discard.
- Accepted new faces enter outstanding review; existing detections, identities and ignored faces are preserved.
- Durable bounding boxes, duplicate matching and stale-preview protection; migration 0009.

### Changed

- Scene overview cards contain scrollable frame strips and identified-face rows within the same responsive box.

## [0.6.1] - 2026-09-11

### Changed

- Outstanding face clusters share one scene review box with Clusters and Suggestions sub-options.
- Grouping uses global average-link merges at 0.50 similarity, retaining a 0.25 pair floor and same-frame exclusions.

### Added

- Scrollable extracted frames in the scene header, with timestamp playback and scene-filtered pagination.
- Explicit Regroup outstanding faces action with adjustable similarity, using cached embeddings while preserving identity confirmations and reviewed exclusions.
- No new migration from 0.6.0.

## [0.6.0] - 2026-09-11

### Added

- Scenes is the default Review view, showing scene status, identified people and remaining unknown faces.
- Explicit scene Complete/Reopen actions allow completion with unknown background faces, while preserving identities and observations.
- Completion detects changed review data and newly detected faces; stale edits are rejected.
- Top-three suggestions per individual face, avoiding cluster-average rankings that obscure minority identities.
- Full-range minimum similarity control and optional reconsideration of previously rejected candidates.

### Changed

- Status filters are independent checkboxes. Completed scenes are excluded from supporting review queues by default.
- Migration 0008 adds scene completion records without changing existing face confirmations.

## [0.5.3] - 2026-09-11

### Fixed

- Candidate cards show selectable current-cluster faces immediately beside confirmation buttons, clearly separated from stored reference examples.
- Face selections synchronize across candidate cards and submit each face only once.

### Added

- Status filters in Clusters and Suggestions; outstanding-only is the default.
- Batch-wide confirmed, outstanding, unresolved and ignored cluster counts, plus confirmed and outstanding face counts.
- People reference review with all stored references, source batches, and removal of selected incorrect confirmations across those batches.
- Reference removal returns faces to outstanding review, preserves other confirmations, and rejects stale edits.
- No new migration from 0.5.2.

## [0.5.2] - 2026-09-11

### Fixed

- Confirmation explicitly targets selected faces or all faces; partial confirmation keeps remaining faces separate without confirming them.
- People and matching references include only explicitly confirmed face IDs. Unconfirm selected faces corrects previous decisions.
- Refresh ranks up to three eligible people without a minimum similarity cutoff, including other clusters in the same video and excluding self matches and rejected people.
- Suggestions show loading, results, missing-reference explanations, and errors; selected faces can drive comparison.
- Import can fill missing review embeddings after Group faces completes.

### Upgrade

- Migration 0007 adds explicit face confirmation records. Earlier cluster labels and history remain intact but need face confirmation before contributing reference examples.

## [0.5.1] - 2026-09-11

### Fixed

- Suggestions provides direct assignment and Create and assign even when no matching person is suggested.
- Suggestions refresh on entry and after decisions; explicit refresh controls use current confirmed references.

### Added

- Person suggestions in Clusters alongside assignment controls.
- Ignore selected faces or entire clusters, excluding them from suggestions and reference faces in that batch, with restoration and durable history.
- No new database migration from 0.5.0.

## [0.5.0] - 2026-09-11

### Added

- Review menu after Batches with Clusters, People, and Suggestions tabs and a batch selector.
- Durable manual cluster splitting, removal into singletons, same-video merging, identity assignment, and unresolved status.
- Named people with confirmed reference faces and cross-video similarity suggestions requiring manual confirmation.
- Persistent rejections, decision history, stale-edit protection, and safeguards against conflicting identity assignments.
- Review snapshots survive automatic regrouping and detector record replacement.

### Upgrade

- Migration 0006 adds review and identity tables. Back up before upgrading.
- Import existing completed groups from Review; reviewed batches are retained to preserve decisions.

## [0.4.1] - 2026-09-10

### Changed

- Face clusters display as a horizontally scrollable row of representative-face cards, each with only its observation count underneath.
- Clicking a card reveals the cluster's individual faces below the row; selecting another switches the detail panel, and clicking the selected card closes it.
- Timestamps and quality information remain on individual faces, not cluster cards.
- Existing groups and All observations remain available; no reprocessing or new migration from 0.4.0.

## [0.4.0] - 2026-09-10

### Added

- Local SFace 128-dimensional embeddings, aligned using five landmarks and stored as versioned, normalized pgvector records.
- Group faces background jobs reuse embeddings and cluster independently within each video.
- Conservative complete-link insertion with a 0.60 minimum cosine similarity, ambiguity margin of 0.05, and same-frame exclusions.
- Expandable groups with representative crops, counts and timestamp ranges; singleton and low-quality observations stay accessible alongside All observations.
- Input fingerprints hide stale grouping results after observations change. No original observations are removed by grouping.

### Upgrade

- Migration 0005 adds embeddings and grouping tables; back up before upgrading.
- Existing detected faces can be grouped without re-extraction or re-detection. Run Detect faces first if a batch's analysis is incomplete.
- Thresholds are starting heuristics, not calibrated identity confidence. Cross-video comparisons and manual identity assignment remain later stages.

## [0.3.1] - 2026-09-10

### Added

- Each video's gallery row has Frames and Faces views with counts; switching views preserves the scene context.
- Face cards show exact timestamps, detection scores, image-quality scores, usability, and expandable size/sharpness/exposure details.
- Low-quality observations show the applicable size, blur, and exposure reasons.

### Changed

- View faces follows Detect faces in batch actions and opens the per-video Faces views.
- No new migration or reprocessing is needed when upgrading from 0.3.0. Identity suggestions are not implemented yet.

## [0.3.0] - 2026-09-08

### Added

- Two-pass dynamic frame sampling: scan the entire timeline at 2 fps, retain temporal anchors plus distinct transition candidates, then extract high-resolution frames.
- Duration-aware periodic target (approximately 24 anchors for short clips) and frame-budget allocation across the whole video; no first-300-frame cutoff.
- Independent CPU YuNet face detection with bundled, checksum-verified MIT-licensed weights; no runtime model downloads.
- Timestamped face crops, bounding boxes, landmarks, detector confidence, and heuristic size/sharpness/exposure quality scores.
- Separate Detect faces and View faces controls, resumable versioned detections, and face-analysis migration.

### Fixed

- Missing cached frame files trigger regeneration without duplicate database records.
- Persist actual extracted-frame dimensions.

### Upgrade notes

- Back up before upgrading; migration 0004 adds face tables.
- Existing frames remain viewable. Use Reprocess to apply dynamic sampling, then Detect faces.
- This is the first face-engine increment. Embeddings, identity matching, tracking, and face-driven resampling are not implemented yet.

## [0.2.4] - 2026-09-08

### Added

- Click an extracted frame to play its source video at that frame's timestamp in an in-page player; direct-link downloads are avoided. Unsupported media shows an error.
- Scene duration appears beside the scene title in the frame gallery.

### Notes

- Sampling remains unchanged: 30-second periodic interval plus visual scene-change selection, capped at 300 frames by default. It is not duration-aware.
- No database migration or frame reprocessing is needed for these gallery changes.

## [0.2.3] - 2026-09-08

### Fixed

- Web image sets readable static-file permissions regardless of host permissions, preventing Nginx 403 responses.

- Frame gallery retrieves all pages and groups frames chronologically into full-width scene rows.
- Explicit frame-query joins avoid ambiguous SQL joins; pagination has a stable tie-breaker.
- Play scene links work from the gallery and serve media inline with its detected MIME type and byte-range support.
- Playback is restricted to the configured media directory, including resolved symlinks.
- Missing scene titles fall back to filenames; absent thumbnails no longer generate broken image requests.
- Gallery failures are displayed and failed processing requests re-enable their buttons.

### Deployment

- No database migration or configuration change required from 0.2.2.

## [0.2.2] - 2026-09-04

### Added

- Working tab navigation and batch delete controls.
- Safe batch deletion for non-active, non-committed batches; active and committed batches remain protected.


## [0.2.1] - 2026-09-04

### Added

- Dedicated Workflow, Batches, and Connection tabs with a full-width scene workflow.
- Stash-generated scene thumbnails and expanded Stash/FFprobe technical metadata.
- Processed-scene indicators and filters, date filtering, page selection, all-match selection, and next-unprocessed selection.
- Per-scene batch error details using filenames and paths instead of internal identifiers.

### Fixed

- Automatically prepare `/appdata/frames` with the worker user's ownership before backend processes start.

## [0.2.0] - 2026-09-04

### Added

- Searchable, paginated scene selection across the full synchronized Stash catalogue.
- Persistent RQ video-processing jobs with per-scene progress and error reporting.
- FFprobe metadata refresh and source-file change fingerprints.
- Adaptive FFmpeg frame extraction combining periodic and scene-change sampling.
- Resumable frame reuse, timestamp storage, generated-frame gallery, and Phase 2 database migration.

## [0.1.1] - 2026-09-04

### Fixed

- Use the Stash `VideoFile.frame_rate` GraphQL field when synchronizing scenes, restoring compatibility with Stash v0.31.1.

## [0.1.0] - 2026-09-03

### Added

- Docker Compose foundation with web, API, worker, PostgreSQL/pgvector, Redis, and migration services.
- FastAPI health, settings, Stash connection, scene synchronization, and batch endpoints.
- Persistent Phase 1 database schema and initial Alembic migration.
- Browser interface for Stash synchronization and batch creation.
- Read-only source-media mounts and persistent application storage.
- Backend tests, lint checks, and deployment documentation.
- Application version display in the browser UI.
