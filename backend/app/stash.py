from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx


class StashError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportedScene:
    stash_scene_id: str
    title: str | None
    scene_date: date | None
    stash_path: str
    source_path: str
    file_size: int | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None
    video_codec: str | None
    media_format: str | None
    audio_codec: str | None
    bit_rate: int | None
    source_created_at: datetime | None
    source_modified_at: datetime | None
    thumbnail_url: str | None
    existing_tag_ids: list[str]
    stash_updated_at: datetime | None


class StashClient:
    def __init__(self, url: str, api_key: str, timeout: float = 30.0):
        if not url:
            raise StashError("STASH_URL is not configured")
        headers = {"ApiKey": api_key} if api_key else {}
        self.client = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.client.post("/graphql", json={"query": query, "variables": variables or {}})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise StashError(f"Unable to contact Stash: {exc}") from exc
        if payload.get("errors"):
            message = "; ".join(error.get("message", "GraphQL error") for error in payload["errors"])
            raise StashError(message)
        return payload["data"]

    def version(self) -> str:
        data = self._graphql("query Version { version { version } }")
        version = data.get("version") or {}
        return version.get("version", "unknown")

    def fetch_asset(self, url: str) -> tuple[bytes, str]:
        parsed = urlsplit(url)
        target = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
        try:
            response = self.client.get(target)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StashError(f"Unable to retrieve Stash image: {exc}") from exc
        return response.content, response.headers.get("content-type", "image/jpeg")

    def performers(self):
        result = []
        page = 1
        while True:
            rows = self._graphql("query($page:Int!){findPerformers(filter:{page:$page,per_page:100,sort: \"id\",direction:ASC}){performers{id name}}}", {'page':page})['findPerformers']['performers']
            result.extend(rows)
            if len(rows) < 100:
                return result
            page += 1

    def create_performer(self, name):
        return self._graphql('mutation($input:PerformerCreateInput!){performerCreate(input:$input){id name}}',
                             {'input':{'name':name}})['performerCreate']

    def add_scene_performers(self, scene_id, performer_ids):
        # Atomic ADD preserves concurrent Stash edits and unrelated performers.
        self._graphql('mutation($input:BulkSceneUpdateInput!){bulkSceneUpdate(input:$input){id}}',
            {'input':{'ids':[scene_id],'performer_ids':{'ids':sorted(set(performer_ids)),'mode':'ADD'}}})

    def iter_scenes(self, per_page: int = 100) -> Iterator[tuple[dict[str, Any], int]]:
        query = """
        query Scenes($page: Int!, $perPage: Int!) {
          findScenes(filter: {page: $page, per_page: $perPage, sort: "id", direction: ASC}) {
            count
            scenes {
              id title date updated_at
              paths { screenshot }
              tags { id }
              files {
                path size duration width height frame_rate video_codec
                format audio_codec bit_rate created_at mod_time
              }
            }
          }
        }
        """
        page = 1
        while True:
            result = self._graphql(query, {"page": page, "perPage": per_page})["findScenes"]
            scenes = result["scenes"]
            for scene in scenes:
                yield scene, int(result["count"])
            if len(scenes) < per_page:
                break
            page += 1


def map_stash_path(path: str, stash_prefix: str, media_prefix: str) -> str:
    source = PurePosixPath(path.replace("\\", "/"))
    old_root = PurePosixPath(stash_prefix.replace("\\", "/"))
    try:
        relative = source.relative_to(old_root)
    except ValueError as exc:
        raise StashError(f"Scene path {path!r} is outside STASH_PATH_PREFIX {stash_prefix!r}") from exc
    return str(PurePosixPath(media_prefix.replace("\\", "/")) / relative)


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def normalise_scene(scene: dict[str, Any], stash_prefix: str, media_prefix: str) -> ImportedScene | None:
    files = scene.get("files") or []
    if not files:
        return None
    primary = files[0]
    stash_path = primary["path"]
    return ImportedScene(
        stash_scene_id=str(scene["id"]), title=scene.get("title"), scene_date=parse_date(scene.get("date")),
        stash_path=stash_path, source_path=map_stash_path(stash_path, stash_prefix, media_prefix),
        file_size=int(primary["size"]) if primary.get("size") is not None else None,
        duration_seconds=primary.get("duration"), width=primary.get("width"), height=primary.get("height"),
        frame_rate=primary.get("frame_rate"), video_codec=primary.get("video_codec"),
        media_format=primary.get("format"), audio_codec=primary.get("audio_codec"),
        bit_rate=int(primary["bit_rate"]) if primary.get("bit_rate") is not None else None,
        source_created_at=parse_datetime(primary.get("created_at")),
        source_modified_at=parse_datetime(primary.get("mod_time")),
        thumbnail_url=(scene.get("paths") or {}).get("screenshot"),
        existing_tag_ids=[str(tag["id"]) for tag in scene.get("tags", [])],
        stash_updated_at=parse_datetime(scene.get("updated_at")),
    )
