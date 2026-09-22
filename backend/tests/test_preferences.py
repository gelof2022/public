import httpx
from app.config import Settings, get_settings
from app.main import app
from app.models import Library, StashScene
from app.preferences import read_preferences


def test_preferences_persist_and_validate(client, tmp_path):
    config = Settings(appdata_path=tmp_path)
    app.dependency_overrides[get_settings] = lambda: config
    value = client.get('/api/v1/preferences').json()
    value.update(theme='light', detection_threshold=0.65, recognition_threshold=0.3)
    assert client.post('/api/v1/preferences', json=value).status_code == 200
    assert read_preferences(config).theme == 'light'
    assert read_preferences(config).detection_threshold == 0.65
    assert client.post('/api/v1/preferences', json={**value, 'recognition_threshold':2}).status_code == 422
    assert read_preferences(config).recognition_threshold == 0.3


def test_stash_fallback_proxies_range_without_exposing_credentials(client, db_session, tmp_path, monkeypatch):
    config = Settings(appdata_path=tmp_path, stash_url='http://stash', stash_api_key='private-key')
    app.dependency_overrides[get_settings] = lambda: config
    scene = StashScene(library=Library(name='Test', stash_url='http://stash', stash_path_prefix='/data',
        media_path_prefix='/media'), stash_scene_id='42', title='MOV', source_path='/media/a.mov',
        stash_path='/data/a.mov', existing_tag_ids=[])
    db_session.add(scene)
    db_session.commit()
    from app.stash import StashClient
    original = StashClient.__init__
    def transport(request):
        assert request.url.host == 'stash'
        if request.url.path == '/graphql':
            return httpx.Response(200, json={'data':{'findScene':{'sceneStreams':[
                {'url':'http://other-host/scene/42/stream.mp4?apikey=secret','mime_type':'video/mp4','label':'MP4 HD (720p)'}]}}})
        assert request.headers['range'] == 'bytes=0-3'
        return httpx.Response(206, stream=httpx.ByteStream(b'test'), headers={
            'content-type':'video/mp4','content-range':'bytes 0-3/4','content-length':'4'})
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.client.close()
        self.client = httpx.Client(base_url='http://stash', transport=httpx.MockTransport(transport))
    monkeypatch.setattr(StashClient, '__init__', init)
    response = client.get(f'/api/v1/scenes/{scene.id}/stash-video', headers={'Range':'bytes=0-3'})
    assert response.status_code == 206
    assert response.content == b'test'
    assert response.headers['content-range'] == 'bytes 0-3/4'
    assert 'secret' not in str(response.headers)
