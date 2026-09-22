# Intra-video clustering, version 0.8.0

## Scope and identity boundary

SFace remains the only embedding read by Person references and cross-video suggestions. `AppearanceEmbedding` is a separate, scene-scoped cache keyed by observation ID and model/crop version. No appearance columns, references or scores were added to Person. Automatic grouping and explicit scene Regroup use the same engine. Sampling, detection and manual assignment/merge/split/ignore/rejection behaviour are unchanged.

## Model and contextual crop

Bundled MobileNetV2 opset 7 from the [ONNX Model Zoo](https://github.com/onnx/models/tree/main/validated/vision/classification/mobilenet), Apache 2.0, 13.5 MiB. See `backend/models/MOBILENET-LICENSE`. Original weights SHA256: `c1c513582d56afceff8516c73804e484c81c6a830712ab6d682253f4a3cd042f`.

OpenCV 4.11 CPU DNN reads the existing ONNX file; no new Python inference dependency. Features are the 1280-dimensional pooled activation (`mobilenetv20_features_pool0_fwd`), not the 1000 class predictions. Resize the contextual crop to 224×224, RGB, scale to 0–1, ImageNet mean/std normalization, then L2-normalize the output. Inference is local and deterministic within normal floating-point tolerance. No downloads occur at runtime.

Crop expansion defaults: 0.75 face widths on each side, 0.25 face heights above, 2 face heights below. Clamp to image bounds. Skip faces smaller than 32 pixels, crops retaining less than 60% of the requested area, crops without at least half a face-height below the face, and crops containing another observed face centre. Expanded crops are computed from existing extracted frames only, not saved as additional image files. Cache suitable features and unsuitable reasons; missing source frames remain retryable.

This general ImageNet model is not a trained person re-identification model. Background and uniform similarity are real confounders. It supplies supporting evidence only; no recognition accuracy claim is made from the smoke test.

## Rules and calibration defaults

| Setting | Default | Meaning |
|---|---:|---|
| Normal face threshold | 0.50 | Existing weighted average-link face merge; scene Regroup retains its threshold control |
| Worst face pair floor | 0.25 | Existing hard floor, even for normal merges |
| `GROUPING_ASSISTED_MIN` | 0.45 | Every cross-cluster face pair must pass for assistance |
| `GROUPING_APPEARANCE_MIN` | 0.90 | Every cross-cluster appearance pair must pass |
| `GROUPING_ASSISTED_SECONDS` | 30 | Maximum time separation across the proposed assisted merge |
| `GROUPING_DUPLICATE_MIN` | 0.97 | Minimum face cosine within a near-duplicate family |
| `GROUPING_DUPLICATE_SECONDS` | 3 | Maximum separation within a near-duplicate family |
| `APPEARANCE_ENABLED` | true | Disable for face-only grouping, retaining duplicate handling and representatives |

A normal face merge is unaffected by appearance or time disagreement. Below the normal threshold, assistance requires all three face/appearance/time gates; missing context means no assistance. No global face/clothing weighted sum is used. Same-frame observations and observations from different scenes never merge. Group count is discovered, and uncertain faces remain separate.

Duplicate families are built deterministically in quality order with complete cross-member similarity/time checks and same-frame exclusions. Families share total weight 1 in average-link calculations. No observations are deleted, and the worst-pair and cannot-link checks still inspect all members. This prevents extra samples of the same pose dominating independent observations.

Representatives start with the highest-quality face, then prefer distinct landmark nose/eye geometry buckets and face-embedding diversity balanced with quality. At most one representative comes from each duplicate family. Pose buckets are approximate image-left/front/image-right views, not calibrated head angles. Automatic group APIs expose the diverse representative IDs; Person reference selection is deliberately unchanged.

## Persistence, recomputation and diagnostics

Migration `0010_appearance` adds `appearance_embeddings` with pgvector(1280), nullable feature, crop/reason metadata, scene ID, version/input hashes and timestamp. Observation ID is intentionally not a cascading FK to canonical face observations: accepted review evidence can survive canonical re-detection. Deleting a scene clears its contextual cache. FaceGrouping gains a bounded JSON diagnostics field. No existing groups or manual decisions are rewritten by migration.

The new SHA256 algorithm key includes rule defaults, appearance enablement, model hash, crop geometry, duplicate logic and representative version. Old automatic groups become stale. Run the existing Group faces action to recompute; import respects existing claimed review observations. Explicit scene Regroup affects only pending unassigned groups, preserving confirmed, assigned, ignored and unresolved groups. As before, it replaces pending manual splits only when explicitly requested.

Regroup may compute missing appearance features synchronously for the scene; batch grouping runs on the existing RQ worker. Large scenes can therefore make Regroup slower on its first use. Subsequent use reuses cached vectors. Existing frames and face embeddings are reused. Unsuitable/missing contexts fall back to normal face logic. After restoring previously missing source frames, use scene Regroup to retry missing context; an unchanged already-saved automatic grouping may still be reused by the batch grouping cache.

`GET /api/v1/batches/{id}/face-groups` exposes diagnostics for current automatic groups. Scene regroup diagnostics are retained in the existing review audit history. They include score, worst face pair, minimum appearance score, maximum time gap, conflict, reason and actual merge records. Up to 200 candidate evaluations and 200 actual merges are retained, plus aggregate reason counts and duplicate families (linear in observation count). Candidate evaluations mean eligible/separate, not necessarily a final merge; stale heap entries are discarded. Timings include grouping seconds and appearance seconds/computed/cache/unavailable counts. Appearance timings are also logged once per scene, not per pair.

## Validation and limitations

Deterministic tests cover face-first gates, weak/missing appearance, time windows, same-frame and cross-scene constraints, duplicate weighting and anti-chaining, diverse representatives, crop bounds/crowding, persistence reuse, configuration versioning, stale automatic groups, and unchanged Person suggestions. Existing review tests remain applicable.

Local native CPU sanity check on the existing 512×512 sample: about 6.5 ms per contextual crop across 20 inferences; 300 synthetic observations clustered in about 30 ms in a no-merge case. These are development-machine measurements, not LXC throughput or quality benchmarks. Matrix-based agglomeration still has quadratic storage and can cost more for dense merging scenes. Appearance needs one additional 1280-float feature per suitable observation (~5 KiB raw plus database overhead), plus a 13.5 MiB bundled model. Cache reuse avoids repeat inference.

Calibrate against labelled real video scenes before relaxing defaults: examine false merges first, same-clothing/background distractors, profile pairs, crop clipping, crowded shots, and representative quality. Compare assisted versus `APPEARANCE_ENABLED=false` using the same frames and review decisions. The smoke test establishes model/preprocessing/cache operation, not improved recognition accuracy. Conservative all-pair assistance can intentionally leave extra clusters.

Docker/PostgreSQL integration: upgrading a seeded 0.7.3 database to 0010 succeeded. Six actual MobileNet embeddings were persisted across two sample scenes in 0.199 seconds of appearance processing, including per-scene model setup and database work, on the development Docker host. Recomputed grouping left review clusters, Person references, original observations/frames and completed scene states unchanged. This is not an LXC benchmark.

## Review adjustments in 0.9.1

Explicit scene Regroup now accepts 0.10–0.50 and preserves active manually merged clusters using their existing merge audit records. At thresholds below 0.25, its face pair floor follows the selected threshold. Lower values intentionally allow much broader face matches; same-frame exclusions still apply. Default automatic batch grouping is unchanged. Manual splits of otherwise pending groups can still be replaced by explicit Regroup.

## Review identification in 0.9.6

The overview proposes people present in the scene, independently of cluster boundaries. Every outstanding embedded face is first compared with explicitly confirmed People references. Each face first chooses its highest-scoring remaining identity, with a deterministic tie break. Rejected face/person pairs are excluded from this competition. Winning anchors need cosine ≥0.20; only then are winning observations collapsed into one row per person. This guarantees uniqueness of both the displayed face and person. The strongest eligible anchor is displayed, with quality breaking ties. Candidates are corroborated by other frames whose direct match is ≥0.30, within 0.03 of the strongest identity, and whose face cosine to the anchor is ≥0.35. Ranking adds 0.04 per supporting frame, capped at two frames; displayed similarity remains the raw reference cosine. The ranking score is not a probability. Same-frame repetitions cannot corroborate an anchor.

Confirmed people remain competitors but are not proposed again. Confirming a person confirms only the displayed face, never every supporting observation. Rejections are stored in review history per batch, scene, person and displayed face. After rejection, the next eligible person for that face can win. Replacement proposals, including alternative faces for the rejected person, move down the queue; exhausted candidates disappear. No unconfirmed suggestion becomes a reference. These are review heuristics, not a measured accuracy improvement.

Automatic grouping retains same-frame exclusions. Detailed manual identification has a per-confirmation mirror/reflection exception, unchecked by default and recorded in the audit. Conflicts return the selected and already-confirmed observations for visual correction. Other-person confirmation conflicts remain blocked.

Detailed suggestion ranking uses confirmed SFace references only. For query groups with median quality below 0.50, local priority starts at cosine 0.30; other groups retain 0.50. Any higher user-selected similarity floor takes precedence. Scores are never boosted. A current-video candidate is retained among up to three eligible candidates even if it does not qualify for local priority; weak matches are labelled for manual assessment. Faces without embeddings still need manual assignment.
