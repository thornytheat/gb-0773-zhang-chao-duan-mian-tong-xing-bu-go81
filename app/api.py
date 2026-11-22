from flask import Blueprint, current_app, jsonify, request

from app.errors import TideError
from app.flow import STEPS, calculate
from app.modules.journal.service import record
from app.modules.permit import service as permits
from app.modules.proof.service import issue
from app.source import load_sources

api = Blueprint('api', __name__, url_prefix='/api')


def journal():
    return current_app.extensions['tide_journal']


def _payload():
    if request.method == 'POST':
        return request.get_json(silent=True)
    return {'sample': request.args.get('sample', 'dirty')}


def _issue(key):
    receipt, sources, reports = journal().get(key)
    return issue(receipt, sources, reports)


@api.route('/stage/<stage>', methods=['GET', 'POST'])
def stage_result(stage):
    if stage not in STEPS:
        raise TideError('NO_STAGE', '找不到潮窗模块', 404)
    if 'run' in request.args and request.method == 'GET':
        _, _, reports = journal().get(request.args['run'])
    else:
        reports = calculate(load_sources(_payload()), stage)
    return jsonify(reports[stage])


@api.get('/journal')
def list_journal():
    rows = []
    for receipt in journal().list():
        state = permits.run_state(journal(), receipt, journal().get(receipt['run_id'])[2])
        rows.append({**receipt, 'validity': {field: state[field] for field in ('status', 'code', 'message')}})
    return jsonify(runs=rows)


@api.post('/journal')
def publish_journal():
    result = record(journal(), load_sources(request.get_json(silent=True)))
    return jsonify(result), 200 if result['reused'] else 201


@api.get('/journal/<key>')
def inspect_journal(key):
    receipt, sources, reports = journal().get(key)
    state = permits.run_state(journal(), receipt, reports)
    return jsonify(run_id=key, manifest=receipt, coverage=reports['coverage'],
                   lineage={'parent': journal().parent(key), 'successors': journal().successors(key)},
                   validity={field: state[field] for field in
                             ('status', 'code', 'message', 'affected_routes')},
                   permits=[{'permit_id': row['permit_id'], 'status': row['status']}
                            for row in permits.for_run(journal(), key)])


@api.post('/journal/<key>/rerun')
def repeat_journal(key):
    manifest, sources, _ = journal().get(key)
    return jsonify(record(journal(), sources, manifest))


@api.post('/journal/<key>/correct')
def correct_journal(key):
    journal().get(key)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {'sources'}:
        raise TideError('BAD_SOURCE', '补正须提交三份潮汐资料')
    result = record(journal(), load_sources(payload), corrects=key)
    return jsonify(result), 200 if result['reused'] else 201


@api.post('/journal/<key>/permit')
def issue_permit(key):
    payload = request.get_json(silent=True) or {}
    vessel, policy = payload.get('vessel'), payload.get('policy')
    receipt, _, reports = journal().get(key)
    vessel = vessel or next(iter(reports['coverage']['ships']))
    result = permits.issue(journal(), key, vessel, policy or 'cleared_only')
    return jsonify(result), 200 if result['reused'] else 201


@api.get('/permit/<permit_id>')
def inspect_permit(permit_id):
    return jsonify(permits.inspect(journal(), permit_id))


@api.get('/permit/<permit_id>/export')
def download_permit(permit_id):
    evidence = permits.evidence(journal(), permit_id)
    response = jsonify(evidence)
    response.headers['Content-Disposition'] = f'attachment; filename="permit-{permit_id[:12]}.json"'
    return response


@api.get('/export/<key>')
def download(key):
    receipt, sources, reports = journal().get(key)
    state = permits.run_state(journal(), receipt, reports)
    response = jsonify({**_issue(key),
                        'validity': {field: state[field] for field in
                                     ('status', 'code', 'message', 'affected_routes')}})
    response.headers['Content-Disposition'] = f'attachment; filename="tide-{key[:12]}.json"'
    return response
