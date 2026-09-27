"""Exercise authentication and request validation over real HTTP sockets."""
import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

import pytest
from hugmunn.core.remote import Credentials
from hugmunn.core.webserver import RemoteServer


class Bridge:
    def __init__(self):
        self.settings = []
    def snapshot(self):
        return {'messages': [], 'model': 'test'}
    def apply_setting(self, key, value):
        self.settings.append((key, value))
        if key == 'invalid':
            raise ValueError('Unknown setting.')
        return self.snapshot()
    def cancel(self): pass
    def send(self, text):
        yield {'kind': 'content', 'text': text}


@pytest.fixture
def remote():
    bridge = Bridge()
    server = RemoteServer(bridge, port=0, credentials=Credentials.create('user', 'long-test-password'))
    server.start()
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    def request(path, body=None, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(server.url() + path.lstrip('/'), data=data,
            headers={'Content-Type': 'application/json', 'X-Hugmunn-Request': '1', **(headers or {})})
        return opener.open(req, timeout=5)
    yield server, bridge, request, jar
    server.stop()


def login(request):
    return request('/api/login', {'username': 'user', 'password': 'long-test-password'})


def test_login_cookie_logout_and_no_secret_in_url(remote):
    server, bridge, request, jar = remote
    assert '?' not in server.url()
    assert request('/').status == 200
    with pytest.raises(urllib.error.HTTPError) as exc:
        request('/api/state')
    assert exc.value.code == 401
    response = login(request)
    assert 'HttpOnly' in response.headers['Set-Cookie']
    assert 'SameSite=Strict' in response.headers['Set-Cookie']
    assert request('/api/state').status == 200
    request('/api/logout', {})
    with pytest.raises(urllib.error.HTTPError):
        request('/api/state')


def test_credentials_are_salted_and_saved_without_password(tmp_path, monkeypatch):
    monkeypatch.setenv('HUGMUNN_CONFIG_DIR', str(tmp_path))
    a = Credentials.create('user', 'long-test-password')
    b = Credentials.create('user', 'long-test-password')
    assert a.digest != b.digest
    a.save()
    file = tmp_path / 'remote-auth.json'
    assert 'long-test-password' not in file.read_text()
    assert file.stat().st_mode & 0o077 == 0
    assert Credentials.load().matches('user', 'long-test-password')
    assert not a.matches('other', 'long-test-password')


def test_cross_origin_and_missing_csrf_header_cannot_mutate(remote):
    _, bridge, request, _ = remote
    login(request)
    for headers in ({'Origin': 'https://attacker.invalid'}, {'X-Hugmunn-Request': ''}):
        with pytest.raises(urllib.error.HTTPError) as exc:
            request('/api/setting', {'key': 'theme', 'value': 'light'}, headers)
        assert exc.value.code == 403
    assert bridge.settings == []


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{broken', b'"string"'])
def test_invalid_json_is_rejected_without_affecting_app(remote, raw):
    _, bridge, request, _ = remote
    login(request)
    with pytest.raises(urllib.error.HTTPError) as exc:
        request('/api/setting', raw=raw)
    assert exc.value.code == 400
    assert bridge.settings == []


def test_login_is_rate_limited_and_reset_revokes_sessions(remote):
    server, _, request, _ = remote
    login(request)
    server.rotate_token()
    with pytest.raises(urllib.error.HTTPError):
        request('/api/state')
    server.limiter.max_failures = 2
    for expected in (401, 401, 429):
        with pytest.raises(urllib.error.HTTPError) as exc:
            request('/api/login', {'username': 'user', 'password': 'wrong'})
        assert exc.value.code == expected


def test_cookie_expiry_and_tampering(remote):
    server, _, request, jar = remote
    login(request)
    token = next(iter(jar)).value
    server.sessions._items[token] = 0
    with pytest.raises(urllib.error.HTTPError):
        request('/api/state')
    with pytest.raises(urllib.error.HTTPError):
        request('/api/state', headers={'Cookie': 'hugmunn_session=invalid'})


def test_approval_requires_boolean_and_settings_errors_are_json(remote):
    _, _, request, _ = remote
    login(request)
    for route, body in [('/api/approve', {'id': '1', 'allowed': 'false'}),
                        ('/api/setting', {'key': 'invalid', 'value': 1})]:
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(route, body)
        assert exc.value.code == 400
        assert 'error' in json.loads(exc.value.read())
