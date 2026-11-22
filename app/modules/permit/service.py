from fractions import Fraction

from app.config import RULES
from app.errors import TideError
from app.modules.datum.fingerprint import section_fingerprints
from app.modules.journal.manifest import canonical, digest
from app.modules.proof.signature import seal, verifies

PERMIT_RULES = 'permit-1.0'
POLICIES = {'cleared_only': '先签已核清的潮窗', 'whole_route': '等全程核完再签'}
POLICY_TEXT = {
    'cleared_only': ('只把已核清且全程达到放行水深的潮窗写入凭据。缺证和不可行时段不写入，'
                     '也不按可行放行；待补正核清后另签新凭据。'),
    'whole_route': ('整段船期不得留有缺证才签发。仍缺证时拒绝签发，'
                     '避免把未核清的水深写成可行。'),
}
FIELDS = ('datum_m', 'bed_m', 'priority', 'offset_s', 'gap_s')


# ---------- 选取与核对 ----------

def _route(reports, vessel):
    ships, routes = reports['coverage']['ships'], reports['windows']
    if vessel not in ships or vessel not in routes:
        raise TideError('NO_VESSEL', '找不到该船期', 404)
    return ships[vessel], routes[vessel]


def _sections_of(route):
    return {row['section'] for piece in route['pieces'] for row in piece['evidence']}


def _covered_pieces(route, start, end):
    return [piece for piece in route['pieces']
            if piece['status'] == 'open' and int(piece['start']) >= start and int(piece['end']) <= end]


def _windows(route):
    return [{
        'start': row['start'], 'end': row['end'], 'start_included': row['start_included'],
        'end_included': row['end_included'],
        'seconds': str(sum(int(piece['seconds']) for piece in
                           _covered_pieces(route, int(row['start']), int(row['end'])))),
        'pieces': [{
            'start': piece['start'], 'end': piece['end'], 'seconds': piece['seconds'],
            'evidence': [{key: row[key] for key in ('section', 'at', 'depth_m', 'lines', 'code', 'status')}
                         for row in piece['evidence']],
        } for piece in _covered_pieces(route, int(row['start']), int(row['end']))],
    } for row in route['windows']]


def _window_key(window):
    return (window['start'], window['end'], window['start_included'], window['end_included'])


def _withheld(route, windows):
    """凭据未覆盖的时段：缺证与否分列，绝不折算成可行。"""
    signed = {piece['start'] for window in windows for piece in window['pieces']}
    rest = [piece for piece in route['pieces'] if piece['start'] not in signed]
    return {
        'unknown': [{'start': p['start'], 'end': p['end'], 'seconds': p['seconds'],
                     'codes': sorted({row['code'] for row in p['evidence']})}
                    for p in rest if p['status'] == 'unknown'],
        'blocked': [{'start': p['start'], 'end': p['end'], 'seconds': p['seconds'],
                     'codes': sorted({row['code'] for row in p['evidence']})}
                    for p in rest if p['status'] == 'blocked'],
        'isolated_points': [{'at': point['at'], 'seconds': '0',
                             'sections': [row['section'] for row in point['evidence']]}
                            for point in route['isolated_points']],
    }


def _statement(policy, windows, ship):
    cleared = sum(int(window['seconds']) for window in windows)
    blocked, unknown = ship['seconds']['blocked'], ship['seconds']['unknown']
    if policy == 'whole_route':
        return (f'本班等全程核完再签：船期无缺证，签发 {len(windows)} 个潮窗共 {cleared} 秒；'
                f'不可行 {blocked} 秒不计入放行。')
    more = '缺证时段不写入凭据，待补正核清后另签。' if Fraction(unknown) > 0 else ''
    return (f'本班先签已核清的潮窗：只签发 {len(windows)} 个潮窗共 {cleared} 秒；'
            f'缺证 {unknown} 秒、不可行 {blocked} 秒不计入放行凭据，也不写成可行。{more}')


def _route_signature(vessel, route, ship):
    """该船期潮窗结论的指纹：订正后据此判断某张旧凭据是否受影响。"""
    return digest(canonical([vessel, route, ship['seconds'], ship['window_s']]))


def _datum(journal, reports):
    """凭据所用基准：逐断面指纹、版本序号和测站原定义，签发后可逐项追回。"""
    sections = section_fingerprints(reports['datum']['stations'])
    latest = journal.latest_datum()
    stations = reports['datum']['stations']
    detail = {}
    for section in sorted(sections):
        current = latest.get(section)
        pinned = current is not None and current['fingerprint'] == sections[section]
        detail[section] = {
            'fingerprint': sections[section],
            'seq': current['seq'] if pinned else None,
            'version_run_id': current['run_id'] if pinned else None,
            'version_created': current['created'] if pinned else None,
            'stations': {name: {field: spec[field] for field in FIELDS}
                         for name, spec in sorted(stations.items()) if spec['section'] == section},
        }
    return {'fingerprint': digest(canonical(sorted(sections.items()))), 'sections': detail}


def _body(journal, receipt, reports, vessel, policy, issued_at):
    if policy not in POLICIES:
        raise TideError('BAD_POLICY', '取舍策略只能是 cleared_only 或 whole_route')
    ship, route = _route(reports, vessel)
    if policy == 'whole_route' and Fraction(ship['seconds']['unknown']) > 0:
        raise TideError('ROUTE_UNVERIFIED', '仍有未核清的水深，不能按“等全程核完”签发', 422)
    windows = _windows(route)
    if not windows:
        raise TideError('NO_CLEARED_WINDOW', '本船期没有已核清且全程达标的潮窗，不能签发', 422)
    datum = _datum(journal, reports)
    body = {
        'permit_rules': PERMIT_RULES, 'issued_at': issued_at,
        'run': {key: receipt[key] for key in
                ('run_id', 'inputs', 'result_sha256', 'rule_version', 'started_at')},
        'vessel': vessel,
        'order': {key: route['order'][key] for key in
                  ('line', 'draft_m', 'clearance_m', 'threshold_m', 'start', 'end', 'requested')},
        'requested': route['requested'],
        'policy': policy, 'policy_text': POLICY_TEXT[policy],
        'decision': {'choice': policy, 'cleared_s': str(sum(int(w['seconds']) for w in windows)),
                     'statement': _statement(policy, windows, ship)},
        'coverage': {**ship['seconds'], 'window_s': ship['window_s']},
        'route_sha256': _route_signature(vessel, route, ship),
        'datum': datum, 'windows': windows, 'withheld': _withheld(route, windows),
    }
    body['permit_id'] = digest(canonical([PERMIT_RULES, RULES, body['run'], vessel, policy,
                                          [_window_key(w) for w in windows],
                                          {s: [row['fingerprint'], row['seq'], row['version_run_id']]
                                           for s, row in datum['sections'].items()}]))
    body['seal_sha256'] = seal(body)
    return body


# ---------- 基准与补正的影响 ----------

def run_state(journal, receipt, reports):
    """本簿的整体状态：逐船期核过影响后汇总，与能否签发保持一致。"""
    blocks = {vessel: sign_block(journal, receipt, reports, vessel)
              for vessel in reports['coverage']['ships']}
    affected = [{'vessel': vessel, 'code': block['code'], 'message': block['message'],
                 'windows': [[w['start'], w['end']] for w in
                             _route(reports, vessel)[1]['windows']]}
                for vessel, block in blocks.items() if block]
    if not affected:
        return {'signable': True, 'status': 'valid', 'code': None, 'message': '本簿结论仍可作为签发依据',
                'successors': [], 'stale': {}, 'affected_routes': []}
    codes = {row['code'] for row in affected}
    status = 'superseded' if 'PERMIT_SUPERSEDED' in codes else 'stale'
    code = 'PERMIT_SUPERSEDED' if status == 'superseded' else 'DATUM_CHANGED'
    first = next(row for row in affected if row['code'] == code)
    stale = next((block['stale'] for block in blocks.values()
                  if block and block['code'] == code and block.get('stale')), {})
    if len(affected) < len(blocks):
        status = 'partial'
        return {'signable': True, 'status': status, 'code': code,
                'message': f'部分船期结论已失效（{first["vessel"]}），其余船期仍可按本簿签发',
                'successors': journal.successors(receipt['run_id']), 'stale': stale,
                'affected_routes': affected}
    return {'signable': False, 'status': status, 'code': code, 'message': first['message'],
            'successors': journal.successors(receipt['run_id']), 'stale': stale,
            'affected_routes': affected}


def _terminal(journal, run_id):
    """补正链末端：无后代即本簿；唯一末端即最新补正；多末端视为歧义。"""
    descendants = journal.descendants(run_id)
    terminals = [key for key in descendants if not journal.successors(key)]
    return descendants, terminals


def sign_block(journal, receipt, reports, vessel):
    """逐船期判定本簿能否签发：只有被补正或基准更新实际影响的结论才拦下。"""
    ship, route = _route(reports, vessel)
    signature = _route_signature(vessel, route, ship)
    stale = journal.stale_sections(
        {section: {'fingerprint': fingerprint, 'seq': None}
         for section, fingerprint in section_fingerprints(reports['datum']['stations']).items()})
    stale_used = {section: row for section, row in stale.items() if section in _sections_of(route)}
    _, terminals = _terminal(journal, receipt['run_id'])
    if len(terminals) > 1:
        return {'code': 'PERMIT_SUPERSEDED', 'message': '补正链分叉，本簿不能再签', 'terminals': terminals}
    if terminals:
        current = terminals[0]
        _, _, latest = journal.get(current)
        try:
            other_ship, other_route = _route(latest, vessel)
        except TideError:
            return {'code': 'PERMIT_SUPERSEDED', 'message': '补正后该船期已不成立，本簿不能再签',
                    'terminals': terminals}
        if _route_signature(vessel, other_route, other_ship) != signature:
            return {'code': 'PERMIT_SUPERSEDED',
                    'message': f'补正后该船期潮窗已变化，本簿不能再签，须按簿 {current[:12]} 重签',
                    'terminals': terminals, 'current_run_id': current}
    if stale_used:
        return {'code': 'DATUM_CHANGED',
                'message': f'断面 {sorted(stale_used)[0]} 的测站基准已更新，本船期受影响的潮窗失效，须重算',
                'stale': stale_used, 'terminals': terminals}
    return None


def permit_state(journal, body):
    """已有凭据是否仍可签发/下载：补正或基准变化后要按当前资料重算。"""
    used = {row['section'] for window in body['windows'] for piece in window['pieces']
            for row in piece['evidence']}
    stale_all = journal.stale_sections({section: {'fingerprint': row['fingerprint'], 'seq': row['seq']}
                                        for section, row in body['datum']['sections'].items()})
    stale = {section: row for section, row in stale_all.items() if section in used}
    affected = [{'start': w['start'], 'end': w['end']} for w in body['windows']
                if {row['section'] for piece in w['pieces'] for row in piece['evidence']} & set(stale)]
    _, terminals = _terminal(journal, body['run']['run_id'])
    if len(terminals) > 1:
        return _verdict('superseded', 'PERMIT_SUPERSEDED', '补正链分叉，旧凭据不能再签',
                        stale, affected, terminals)
    if terminals:
        current = terminals[0]
        _, _, latest = journal.get(current)
        try:
            ship, route = _route(latest, body['vessel'])
        except TideError:
            return _verdict('superseded', 'PERMIT_SUPERSEDED', '补正后该船期已不成立，旧凭据不能再签',
                            stale, affected, terminals)
        if _route_signature(body['vessel'], route, ship) != body['route_sha256']:
            return _verdict('superseded', 'PERMIT_SUPERSEDED',
                            f'补正后潮窗或水深证据已变化，旧凭据不能再签，须按簿 {current[:12]} 重签',
                            stale, affected, terminals)
    if stale:
        return _verdict('stale', 'DATUM_CHANGED',
                        f'断面 {sorted(stale)[0]} 的测站基准已更新，受影响的潮窗失效，不能再签',
                        stale, affected, [])
    return _verdict('valid', None, '凭据有效', {}, [], [])


def _verdict(status, code, message, stale, affected, successors):
    return {'status': status, 'signable': status == 'valid', 'code': code, 'message': message,
            'stale': stale, 'affected_windows': affected, 'successors': successors}


# ---------- 对外 ----------

def _gate(journal, receipt, reports, vessel):
    block = sign_block(journal, receipt, reports, vessel)
    if block is not None:
        raise TideError(block['code'], block['message'], 409)


def issue(journal, run_id, vessel, policy):
    receipt, _, reports = journal.get(run_id)
    if not verifies(reports, receipt['result_sha256']):
        raise TideError('INVALID_PROOF', '归档结果校验未通过', 409)
    _gate(journal, receipt, reports, vessel)
    body = _body(journal, receipt, reports, vessel, policy, receipt['started_at'])
    created = journal.publish_permit(body)
    stored = journal.get_permit(body['permit_id'])
    if stored['seal_sha256'] != body['seal_sha256']:
        raise TideError('PERMIT_CHANGED', '同一补正签出不一致的凭据', 409)
    return {'permit_id': stored['permit_id'], 'permit': stored, 'reused': not created,
            **permit_state(journal, stored)}


def preview(journal, receipt, reports, vessel, policy):
    _gate(journal, receipt, reports, vessel)
    return _body(journal, receipt, reports, vessel, policy, receipt['started_at'])


def inspect(journal, permit_id):
    body = journal.get_permit(permit_id)
    return {'permit_id': permit_id, 'permit': body, **permit_state(journal, body)}


def evidence(journal, permit_id):
    result = inspect(journal, permit_id)
    if not result['signable']:
        raise TideError(result['code'], result['message'], 409)
    return {**result['permit'],
            'validity': {key: result[key] for key in ('status', 'code', 'message')}, 'verified': True}


def for_run(journal, run_id):
    return [inspect(journal, permit_id) for permit_id in journal.permit_ids_for(run_id)]
