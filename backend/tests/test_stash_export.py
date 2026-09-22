import pytest
from sqlalchemy import select

from app.models import Library, Person, ScenePerson, StashScene, StashPerformerLink
from app.stash_export import export_people
from test_review import review_data as imported_review_data


@pytest.fixture()
def seeded(db_session,client):
    return imported_review_data.__wrapped__(db_session,client)


class FakeStash:
    def __init__(self, people=()):
        self.people=list(people)
        self.scene_people={}
        self.creates=0

    def performers(self):
        return list(self.people)

    def create_performer(self,name):
        self.creates += 1
        value={'id':str(100+self.creates),'name':name}
        self.people.append(value)
        return value

    def add_scene_performers(self,scene,ids):
        self.scene_people.setdefault(scene,{'unrelated'}).update(ids)


def test_manual_and_confirmed_export_reuses_performers_and_preserves_existing(db_session,client,seeded):
    clusters=client.get(f'/api/v1/review/batches/{seeded}/clusters').json()
    person=Person(name='Alice',name_key='alice')
    db_session.add(person)
    db_session.flush()
    db_session.add(ScenePerson(scene_id=clusters[0]['scene_id'],person_id=person.id))
    db_session.commit()
    pid=person.id
    client.post(f"/api/v1/review/clusters/{clusters[1]['id']}/assign",json={'revision':1,'person_id':pid,'all_faces':True})
    stash=FakeStash()
    library=db_session.scalar(select(Library))
    first=export_people(db_session,stash,library)
    assert first=={'created':1,'matched':0,'scenes_synced':2,'errors':[]}
    assert all('unrelated' in ids for ids in stash.scene_people.values())
    person.name='Renamed locally'
    db_session.commit()
    second=export_people(db_session,stash,library)
    assert second['created']==0 and stash.creates==1
    assert db_session.get(StashPerformerLink,(library.id,pid)).performer_id=='101'


def test_name_matching_and_ambiguity(db_session,client,seeded):
    scene=db_session.scalar(select(StashScene))
    person=Person(name='Alice',name_key='alice')
    db_session.add(person)
    db_session.flush()
    db_session.add(ScenePerson(scene_id=scene.id,person_id=person.id))
    db_session.commit()
    library=db_session.scalar(select(Library))
    ambiguous=FakeStash([{'id':'a','name':'Alice'},{'id':'b','name':'ALICE'}])
    result=export_people(db_session,ambiguous,library)
    assert result['created']==0 and result['scenes_synced']==0 and result['errors']
    exact=FakeStash([{'id':'a','name':' alice '}])
    result=export_people(db_session,exact,library)
    assert result['matched']==1 and result['created']==0


def test_unconfirmed_faces_do_not_export(db_session,client,seeded):
    stash=FakeStash()
    result=export_people(db_session,stash,db_session.scalar(select(Library)))
    assert result=={'created':0,'matched':0,'scenes_synced':0,'errors':[]}


def test_stash_uses_atomic_add_not_scene_replacement(monkeypatch):
    from app.stash import StashClient
    client=StashClient('http://localhost','')
    calls=[]
    monkeypatch.setattr(client,'_graphql',lambda q,v:calls.append((q,v)) or {})
    client.add_scene_performers('scene',['2','1','2'])
    assert calls[0][1]=={'input':{'ids':['scene'],'performer_ids':{'ids':['1','2'],'mode':'ADD'}}}
    client.close()
