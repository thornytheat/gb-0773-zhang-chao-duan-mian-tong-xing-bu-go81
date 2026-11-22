import pytest

from app.errors import TideError
from app.modules.journal.service import record
from app.modules.journal.store import Journal
from app.modules.permit import service as permits
from app.modules.proof.signature import seal
from app.source import load_sources


def _journal(client):
    return client.post('/api/journal', json={'sample': 'dirty'}).json['run_id']


def _permit(client, key, vessel='海棠17', policy='cleared_only'):
    return client.post(f'/api/journal/{key}/permit', json={'vessel': vessel, 'policy': policy})


def _correct(client, key, sources):
    return client.post(f'/api/journal/{key}/correct', json={'sources': sources})


def test_partial_clearance_signs_only_cleared_windows(client):
    key = _journal(client)
    response = _permit(client, key)
    assert response.status_code == 201
    body = response.json['permit']
    assert [(row['start'], row['end']) for row in body['windows']] == \
           [('1795362300', '1795363200'), ('1795364400', '1795365000')]
    assert body['decision']['cleared_s'] == '1500'
    assert body['coverage']['unknown'] == '600'
    withheld = body['withheld']
    assert [(row['start'], row['end']) for row in withheld['unknown']] == \
           [('1795363200', '1795363500'), ('1795364100', '1795364400')]
    assert all(row['seconds'] == '300' for row in withheld['unknown'])
    assert '缺证 600 秒' in body['decision']['statement']
    assert '不写成可行' in body['decision']['statement']


def test_signed_windows_never_include_unknown(client):
    key = _journal(client)
    body = _permit(client, key).json['permit']
    signed = {piece['start'] for window in body['windows'] for piece in window['pieces']}
    withheld = {row['start'] for row in body['withheld']['unknown'] + body['withheld']['blocked']}
    assert not signed & withheld
    assert all(row['status'] == 'open' for window in body['windows'] for piece in window['pieces']
               for row in piece['evidence'])


def test_whole_route_refuses_uncleared(client):
    key = _journal(client)
    response = _permit(client, key, policy='whole_route')
    assert (response.status_code, response.json['code']) == (422, 'ROUTE_UNVERIFIED')


def test_whole_route_when_fully_cleared(client):
    key = client.post('/api/journal', json={'sample': 'clean'}).json['run_id']
    response = _permit(client, key, policy='whole_route')
    assert response.status_code == 201
    body = response.json['permit']
    assert body['decision']['cleared_s'] == '3000'
    assert body['withheld'] == {'unknown': [], 'blocked': [], 'isolated_points': []}
    assert '等全程核完再签' in body['decision']['statement']


def test_unknown_policy_rejected(client):
    key = _journal(client)
    assert _permit(client, key, policy='whatever').json['code'] == 'BAD_POLICY'


def test_retry_same_permit_reuses_record(client):
    key = _journal(client)
    first = _permit(client, key).json
    second = _permit(client, key).json
    assert second['permit_id'] == first['permit_id'] and second['reused'] is True
    assert len(client.get('/api/journal').json['runs']) == 1
    assert second['permit']['seal_sha256'] == first['permit']['seal_sha256']


def test_permit_traces_datum_and_windows(client):
    key = _journal(client)
    body = _permit(client, key).json['permit']
    assert body['permit_id'] and body['seal_sha256']
    assert body['run']['run_id'] == key
    assert set(body['datum']['sections']) == {'上游外闸', '下游浅滩'}
    for section, row in body['datum']['sections'].items():
        assert row['fingerprint'] and row['seq'] is not None and row['version_run_id'] == key
        assert row['stations'] and all(set(spec) == {'datum_m', 'bed_m', 'priority', 'offset_s', 'gap_s'}
                                       for spec in row['stations'].values())
    for window in body['windows']:
        assert window['pieces']
        for piece in window['pieces']:
            assert {row['section'] for row in piece['evidence']} <= set(body['datum']['sections'])
    unsigned = {**body, 'seal_sha256': None}
    unsigned.pop('seal_sha256')
    assert seal(unsigned) == body['seal_sha256']


def test_correction_links_without_new_retry_archive(client):
    key = _journal(client)
    sources = load_sources({'sample': 'dirty'})
    sources['observations'] += '\n'
    first = _correct(client, key, sources)
    assert first.status_code == 201
    child = first.json['run_id']
    assert first.json['parent'] == key and child != key
    repeat = _correct(client, key, sources)
    assert repeat.status_code == 200 and repeat.json['reused'] is True
    assert repeat.json['run_id'] == child
    assert len(client.get('/api/journal').json['runs']) == 2
    assert client.get(f'/api/journal/{key}').json['lineage']['successors'] == [child]


def test_same_inputs_correction_keeps_original(client):
    key = _journal(client)
    repeat = _correct(client, key, load_sources({'sample': 'dirty'}))
    assert repeat.status_code == 200 and repeat.json['reused'] is True
    assert repeat.json['run_id'] == key
    assert len(client.get('/api/journal').json['runs']) == 1


def test_correction_supersedes_old_permit(client):
    key = _journal(client)
    old = _permit(client, key).json
    sources = load_sources({'sample': 'dirty'})
    sources['observations'] = sources['observations'].replace(
        'U1,2026-11-23T00:20:00+08:00,0,cm,BAD', 'U1,2026-11-23T00:20:00+08:00,200,cm,OK')
    child = _correct(client, key, sources).json['run_id']
    state = client.get(f'/api/permit/{old["permit_id"]}').json
    assert state['status'] == 'superseded' and state['signable'] is False
    assert client.get(f'/api/permit/{old["permit_id"]}/export').status_code == 409
    assert _permit(client, key).json['code'] == 'PERMIT_SUPERSEDED'
    assert client.get(f'/api/journal/{key}').status_code == 200
    fresh = _permit(client, child).json
    assert fresh['status'] == 'valid' and fresh['permit_id'] != old['permit_id']


def test_unchanged_correction_keeps_old_permit_valid(client):
    key = _journal(client)
    old = _permit(client, key).json
    sources = load_sources({'sample': 'dirty'})
    sources['vessels'] = sources['vessels'].replace('海棠17,2.3', '海棠17,2.30')
    child = _correct(client, key, sources).json['run_id']
    assert child != key and client.get(f'/api/journal/{key}').json['lineage']['successors'] == [child]
    state = client.get(f'/api/permit/{old["permit_id"]}').json
    assert state['status'] == 'valid'
    assert client.get(f'/api/permit/{old["permit_id"]}/export').status_code == 200


def test_datum_update_invalidates_only_affected(client):
    key = _journal(client)
    old = _permit(client, key).json
    sources = load_sources({'sample': 'dirty'})
    sources['stations'] = sources['stations'].replace('D1,下游浅滩,-0.15,-0.5,1,600,600',
                                                      'D1,下游浅滩,-0.35,-0.5,1,600,600')
    fresh = client.post('/api/journal', json={'sources': sources}).json['run_id']
    state = client.get(f'/api/permit/{old["permit_id"]}').json
    assert state['status'] == 'stale' and state['code'] == 'DATUM_CHANGED'
    assert set(state['stale']) == {'下游浅滩'}
    assert state['affected_windows'] == [{'start': '1795362300', 'end': '1795363200'},
                                         {'start': '1795364400', 'end': '1795365000'}]
    assert client.get(f'/api/permit/{old["permit_id"]}/export').status_code == 409
    assert _permit(client, key).json['code'] == 'DATUM_CHANGED'
    assert client.get(f'/api/journal/{fresh}').json['validity']['code'] is None


def test_datum_version_moves_with_new_run(client):
    key = _journal(client)
    _permit(client, key)
    sources = load_sources({'sample': 'dirty'})
    sources['stations'] = sources['stations'].replace('D1,下游浅滩,-0.15,-0.5,1,600,600',
                                                      'D1,下游浅滩,-0.05,-0.5,1,600,600')
    fresh = client.post('/api/journal', json={'sources': sources}).json['run_id']
    journal = client.application.extensions['tide_journal']
    latest = journal.latest_datum()
    assert latest['上游外闸']['run_id'] == key
    assert latest['下游浅滩']['run_id'] == fresh
    assert latest['下游浅滩']['seq'] > latest['上游外闸']['seq']


def test_run_state_reports_stale_sections(client):
    key = _journal(client)
    journal = client.application.extensions['tide_journal']
    receipt, _, reports = journal.get(key)
    assert permits.run_state(journal, receipt, reports)['signable'] is True
    sources = load_sources({'sample': 'dirty'})
    sources['stations'] = sources['stations'].replace('U1,上游外闸,0,-1,1,0,600',
                                                      'U1,上游外闸,0.1,-1,1,0,600')
    client.post('/api/journal', json={'sources': sources})
    state = permits.run_state(journal, *journal.get(key)[::2])
    assert state['status'] == 'stale' and '上游外闸' in state['stale']
    assert state['affected_routes'] and state['affected_routes'][0]['vessel']


def test_permit_unknown_id(client):
    response = client.get('/api/permit/absent')
    assert (response.status_code, response.json['code']) == (404, 'NO_PERMIT')


def test_permit_export_header(client):
    key = _journal(client)
    permit_id = _permit(client, key).json['permit_id']
    response = client.get(f'/api/permit/{permit_id}/export')
    assert response.status_code == 200
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert response.json['verified'] is True
    assert response.json['windows'] and response.json['datum']['sections']


def test_export_run_carries_datum_trace(client):
    key = _journal(client)
    body = client.get(f'/api/export/{key}').json
    assert body['trace']['datum']['fingerprint']
    assert set(body['trace']['datum']['sections']) == {'上游外闸', '下游浅滩'}
    assert body['trace']['windows']['海棠17']['counts']['unknown'] == '600'


def test_unaffected_vessel_still_signable(client):
    key = _journal(client)
    old = _permit(client, key).json
    sources = load_sources({'sample': 'dirty'})
    sources['vessels'] = sources['vessels'].replace('甬航05,2.4,0.2', '甬航05,2.6,0.2')
    child = _correct(client, key, sources).json['run_id']
    assert client.get(f'/api/permit/{old["permit_id"]}').json['status'] == 'valid'
    assert client.get(f'/api/permit/{old["permit_id"]}/export').status_code == 200
    assert _permit(client, key, vessel='海棠17').status_code == 200
    assert _permit(client, key, vessel='甬航05').json['code'] == 'PERMIT_SUPERSEDED'
    run = client.get(f'/api/journal/{key}').json['validity']
    assert run['status'] == 'partial' and run['code'] == 'PERMIT_SUPERSEDED'
    assert [row['vessel'] for row in run['affected_routes']] == ['甬航05']
    # 补正后吃水提高，该船期已无核清达标潮窗，只能拒绝签发而不是把缺证写成可行
    blocked = _permit(client, child, vessel='甬航05')
    assert (blocked.status_code, blocked.json['code']) == (422, 'NO_CLEARED_WINDOW')
    assert _permit(client, child, vessel='海棠17').status_code == 201


def test_datum_update_leaves_other_section_routes_alone(client):
    key = _journal(client)
    sources = load_sources({'sample': 'dirty'})
    sources['stations'] = sources['stations'].replace('D1,下游浅滩,-0.15,-0.5,1,600,600',
                                                      'D1,下游浅滩,-0.35,-0.5,1,600,600')
    client.post('/api/journal', json={'sources': sources})
    state = client.get(f'/api/journal/{key}').json['validity']
    assert state['status'] == 'stale'
    assert all(row['code'] == 'DATUM_CHANGED' for row in state['affected_routes'])
    assert all('下游浅滩' in row['message'] for row in state['affected_routes'])


def test_permit_key_order_is_independent(client):
    key = _journal(client)
    first = _permit(client, key).json['permit_id']
    second = _permit(client, key, policy='whole_route').json.get('permit_id')
    assert second is None
    assert _permit(client, key).json['permit_id'] == first


def test_issue_requires_matching_archive(client):
    journal = client.application.extensions['tide_journal']
    with pytest.raises(TideError) as error:
        permits.issue(journal, 'missing', '海棠17', 'cleared_only')
    assert error.value.code == 'NO_RUN'


def test_permit_and_record_share_store(tmp_path):
    journal = Journal(tmp_path / 'one.db')
    sources = load_sources({'sample': 'dirty'})
    key = record(journal, sources)['run_id']
    first = permits.issue(journal, key, '海棠17', 'cleared_only')
    second = permits.issue(journal, key, '海棠17', 'cleared_only')
    assert first['permit_id'] == second['permit_id'] and second['reused'] is True
    reopened = Journal(tmp_path / 'one.db')
    assert reopened.get_permit(first['permit_id']) == first['permit']
    assert reopened.parent(key) is None
