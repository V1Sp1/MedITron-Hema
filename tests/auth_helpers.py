"""Real hashed account fixture for API suites that exercise physician routes."""
PASSWORD = 'test-only-physician-password-2026'


def acknowledge(client, data_kind='synthetic'):
    from backend.privacy import POLICY_VERSION
    client.headers['X-Hema-Client'] = '1'
    response = client.post('/api/privacy/acknowledge', json={
        'policyVersion': POLICY_VERSION, 'dataKind': data_kind, 'researchOnly': True})
    assert response.status_code == 200, response.text
    return response.json()


def authenticate(app, client, username='test.doctor'):
    if not any(user['username'] == username for user in app.state.auth.list_users()):
        app.state.auth.create_user(username, PASSWORD, 'Тестовый врач')
    client.headers['X-Hema-Client'] = '1'
    response = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    acknowledge(client)
    return response.json()['user']
