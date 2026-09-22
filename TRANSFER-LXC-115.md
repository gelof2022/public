# Install 0.9.9 on LXC 115

Wait for active processing to finish. Copy the image archive to `/opt/video-face-recogniser/releases/0.9.9/`. The existing Compose project lives in `/opt/video-face-recogniser` (not `appdata`). The source archive is optional when using the image-only commands below.

Run the entire block in the LXC. Adjust the pg_dump database and user if your environment does not use the defaults.

```sh
(
set -e
cd /opt/video-face-recogniser
docker load -i releases/0.9.9/video-face-recogniser-0.9.9-images-linux-amd64.tar.gz
docker compose -f compose.yaml -f compose.images.yaml exec -T postgres pg_dump -U face_recogniser face_recogniser > "releases/0.9.9/backup-$(date +%Y%m%d-%H%M%S).sql"
cp compose.images.yaml compose.images.yaml.before-0.9.9
cat > compose.images.yaml <<'YAML'
services:
  api:
    image: video-face-recogniser-backend:0.9.9
  worker:
    image: video-face-recogniser-backend:0.9.9
  migrate:
    image: video-face-recogniser-backend:0.9.9
  web:
    image: video-face-recogniser-web:0.9.9
YAML
docker compose -f compose.yaml -f compose.images.yaml config --images
docker compose -f compose.yaml -f compose.images.yaml up -d --no-build --pull never
docker compose -f compose.yaml -f compose.images.yaml ps
curl -fsS http://localhost:8787/health
)
```

Health should report 0.9.9. Migration 0010 adds the separate intra-video appearance cache and grouping diagnostics; existing identities, ignored faces, extracted frames and scene completion records remain intact.

Old automatic groups become stale. Run **Group faces** to recompute, then import where appropriate. For existing Review scenes, **Regroup outstanding faces** recomputes only pending unassigned groups. Confirmed, assigned, ignored and unresolved groups remain intact. As before, explicit Regroup replaces pending manual splits.

Person suggestions remain face-only. See CLUSTERING.md for model provenance, conservative defaults, diagnostics, performance and calibration guidance. The model is bundled; there are no runtime downloads. First-time scene Regroup can take longer while missing contextual features are computed.

Defaults work with the existing compose.yaml. To tune settings while retaining that file, add matching `environment` overrides under both `api` and `worker` in compose.images.yaml and recreate both services. For example, `APPEARANCE_ENABLED: "false"` disables appearance assistance. All crop and rule settings are documented in CLUSTERING.md and .env.example. The updated source compose.yaml forwards these settings from .env; the old compose.yaml does not automatically forward new variables.

## Manual tagging and Stash export

Migrations 0011 and 0012 add manual scene people and performer mappings. Open Review scene to Add person to scene without a visible face. Create names in People first. Connection → Sync to Stash exports all explicit manual scene assignments and confirmed face assignments, including scenes still awaiting review. Suggestions, ignored faces and unconfirmed cluster labels are excluded.

Sync automatically creates a name-only performer if none matches, reuses a unique case-insensitive exact name match, and remembers the Stash ID per library. Multiple name matches are skipped with an error; resolve them in Stash before retrying. Existing links survive local renaming; sync does not rename Stash performers. It only adds scene associations, so removing a local assignment does not remove it from Stash. Perform that removal in Stash when needed. No images or embeddings are exported. The new button writes to Stash only when clicked; installation does not trigger export.

0.9.1 adds independent amber/green batch progress buttons, sticky cluster tools, rightward ignore navigation and the 0.10–0.50 regroup slider. Manual merges are preserved. No new migration is needed from 0.9.0.

0.9.2 requires migration 0013 for batch archiving. Archive hides a batch while preserving identities, references and audit history; Show archived batches provides Restore. Scene overview cards now offer direct person confirmation for the next outstanding group with embeddings, with expandable selected faces. Confirmed faces move into Identified faces and the next group is offered. No batches are automatically archived during installation.

0.9.3 fixes the archived checkbox, adds scene-card correction under Identified faces → person → Correct identified faces, and displays the scene face next to the reference face in suggestions. Larger outstanding groups are offered first. Current-scene confirmed references receive priority at cosine ≥0.50 (and must meet any higher selected suggestion threshold); otherwise the wider confirmed reference pool is used. Scores are not artificially boosted. Same-frame conflicts are excluded and blocked at confirmation. Existing assignments are not rewritten. No new migration is required from 0.9.2.

0.9.4 shows three distinct outstanding groups per scene-card page, each with its best person match. Previous/Next groups browses the queue without changing decisions. Reject suggestion dismisses that person for the group and offers the next eligible match; Ignore selected faces removes the selected observations from outstanding review. All faces start selected, and changing selection refreshes the match. No new migration is needed from 0.9.3.

0.9.5 replaces overview cluster suggestions with a scene-wide person list. Each person has one row with the strongest eligible scene face, a People reference, name, Confirm and Reject. Confirm records only the displayed face, identifies the person in the scene, and leaves other observations outstanding. Rejection is durable: the displayed face will not be proposed again for that person in this batch/scene, and any replacement moves to the bottom. Once evidence is exhausted, that person drops out of the proposal list. Existing reference correction remains under Identified faces.

Initial matching uses explicitly confirmed People references; independent-frame support strengthens ranking (capped at two extra frames), while a materially stronger competing identity excludes an anchor. Already confirmed people stay in the comparison pool without duplicate rows. Suggestions remain manual and accuracy on your videos has not been measured.

Detailed Review adds inline same-frame conflict previews and an unchecked mirror/reflection exception. Enable it for the current group, then confirm; the exception is audited. Automatic clustering and other identity conflicts retain their constraints. Low-quality detailed queries (median quality below 0.50) prioritise current-video confirmed references at cosine ≥0.30 instead of 0.50; higher user-selected thresholds still apply. Weak scores remain visible and unchanged. No new migration is required.

0.9.6 enforces both one best remaining suggestion per face and one row per person. Rejected face/person pairs no longer compete, so the next eligible identity can be suggested automatically. Replacement proposals move down the queue. Refresh suggestions is removed from overview cards; Add person to scene is now available there as well as in detailed Review. No migration is needed.

0.9.7 addresses large Review batches. Overview loads five scenes at a time, with scene-scoped cluster and frame requests. Cluster metadata and scene summaries use batched queries. Batch polling runs only on the visible Batches tab, at ten-second intervals without overlap and with at most two simultaneous stage checks. Workflow now has Awaiting Review only and Review batch filters. To select Batch 3 for smaller processing batches, choose Batch 3, tick Awaiting Review only, and select the desired scenes. Without a specific batch, status comes from the newest non-archived batch containing each scene. Changed completion fingerprints count as awaiting review. Creating a new batch does not erase or migrate existing review history. No database migration is required.

0.9.8 fixes long-video frame extraction by balancing the FFmpeg selection expression. Existing successful frame caches remain valid. Retry the failed processing batch to extract the missing video and reuse cached successful videos. No migration is required.

0.9.9 adds automatic Stash MP4 fallback on playback failure and Settings for detection, recognition and regroup defaults, playback fallback, and light/dark appearance. Defaults are saved atomically in appdata/preferences.json and shared by API and worker. No new migration is required. Existing cached detections and review decisions are retained.
