"""Explicit, additive scene-person export to the configured local Stash."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Library, Person, ReviewCluster, ScenePerson, StashPerformerLink, StashScene
from app.stash import StashClient, StashError

router = APIRouter(prefix='/api/v1/stash',tags=['stash'])


def export_people(db, client, library):
    scenes = {s.id:s for s in db.scalars(select(StashScene).where(StashScene.library_id==library.id))}
    assignments = {}
    for tag in db.scalars(select(ScenePerson).where(ScenePerson.scene_id.in_(scenes))):
        assignments.setdefault(tag.scene_id,set()).add(tag.person_id)
    for cluster in db.scalars(select(ReviewCluster).where(ReviewCluster.scene_id.in_(scenes),
            ReviewCluster.active.is_(True),ReviewCluster.state=='assigned',ReviewCluster.person_id.is_not(None))):
        if set(cluster.confirmed_ids or []) & set(cluster.member_ids):
            assignments.setdefault(cluster.scene_id,set()).add(cluster.person_id)
    result = {'created':0,'matched':0,'scenes_synced':0,'errors':[]}
    remote = client.performers()
    remote_ids = {str(p['id']) for p in remote}
    resolved = {}
    for pid in sorted({pid for ids in assignments.values() for pid in ids}):
        person = db.get(Person,pid)
        link = db.get(StashPerformerLink,(library.id,pid))
        try:
            if link and link.performer_id in remote_ids:
                resolved[pid] = link.performer_id
                continue
            matches = [p for p in remote if ' '.join(p['name'].split()).casefold() == ' '.join(person.name.split()).casefold()]
            if len(matches)>1:
                raise StashError(f'Multiple Stash performers named {person.name}; resolve duplicate names in Stash and retry')
            if matches:
                performer = matches[0]
                result['matched'] += 1
            else:
                performer = client.create_performer(person.name)
                remote.append(performer)
                remote_ids.add(str(performer['id']))
                result['created'] += 1
            if link is None:
                link = StashPerformerLink(library_id=library.id,person_id=pid)
                db.add(link)
            link.performer_id = str(performer['id'])
            resolved[pid] = link.performer_id
            db.flush()
        except StashError as exc:
            result['errors'].append(str(exc))
    for sid, ids in sorted(assignments.items()):
        if not ids <= resolved.keys():
            result['errors'].append(f"Skipped scene {scenes[sid].stash_scene_id}: unresolved performer")
            continue
        try:
            client.add_scene_performers(scenes[sid].stash_scene_id,[resolved[pid] for pid in ids])
            result['scenes_synced'] += 1
        except StashError as exc:
            result['errors'].append(f"Scene {scenes[sid].stash_scene_id}: {exc}")
    db.commit()
    return result


@router.post('/export-people')
def sync_people(db: Session = Depends(get_db), config: Settings = Depends(get_settings)):
    # Serialize exports for this Stash connection until all mappings are committed.
    library = db.scalar(select(Library).where(Library.stash_url==config.stash_url).with_for_update())
    if library is None:
        raise HTTPException(409,'Import scene metadata from the configured Stash connection first')
    client = StashClient(config.stash_url,config.stash_api_key)
    try:
        return export_people(db,client,library)
    except StashError as exc:
        raise HTTPException(502,str(exc)) from exc
    finally:
        client.close()
