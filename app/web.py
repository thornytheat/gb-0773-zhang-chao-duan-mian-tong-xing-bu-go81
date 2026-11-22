from fractions import Fraction

from flask import Blueprint, redirect, render_template, request, url_for

from app.api import journal
from app.clock import instant
from app.config import RULES
from app.errors import TideError
from app.flow import calculate
from app.modules.journal.service import record
from app.modules.permit import service as permits
from app.reasons import REASONS
from app.source import load_sources

web = Blueprint('tide_desk', __name__)
CHAPTERS = {'coverage': '船期覆盖勾稽', 'readings': '潮尺读数采信', 'datum': '站点基准统一',
            'crossings': '水位阈值交点', 'windows': '全程可行潮窗', 'journal': '运行留档', 'proof': '潮窗证据签发'}
STATES = {'open': '可行', 'blocked': '不可行', 'unknown': '缺证'}
PERMIT_STATES = {'valid': '有效', 'stale': '基准已更新', 'superseded': '已被补正取代'}
RUN_STATES = {'valid': '本簿可作为签发依据', 'stale': '基准已更新，须重算',
              'superseded': '已被补正取代，须重算', 'partial': '部分船期仍可签发'}


def _run(key, sample):
    if key:
        return journal().get(key), key
    key = record(journal(), load_sources({'sample': sample}))['run_id']
    return journal().get(key), key


@web.get('/')
@web.get('/desk/<chapter>')
def desk(chapter='coverage'):
    if chapter not in CHAPTERS:
        raise TideError('NO_STAGE', '找不到潮窗模块', 404)
    sample = request.args.get('sample', 'dirty')
    key = request.args.get('run')
    if chapter in ('journal', 'proof'):
        (receipt, sources, reports), key = _run(key, sample)
    elif key:
        receipt, sources, reports = journal().get(key)
    else:
        sources, receipt = load_sources({'sample': sample}), None
        reports = calculate(sources)
    vessel = request.args.get('vessel', next(iter(reports['coverage']['ships'])))
    if vessel not in reports['coverage']['ships']:
        raise TideError('NO_VESSEL', '找不到该船期', 404)
    policy = request.args.get('policy', 'cleared_only')
    permit = block = seal = None
    state = None
    state = permits.run_state(journal(), receipt, reports) if receipt is not None else None
    if chapter == 'proof' and receipt is not None:
        try:
            permit = permits.preview(journal(), receipt, reports, vessel, policy)
        except TideError as error:
            block = {'code': error.code, 'message': str(error)}
        seal = permit['seal_sha256'] if permit else None
    return render_template('base.html', chapter=chapter, chapters=CHAPTERS, states=STATES, sample=sample,
                           receipt=receipt, key=key, reports=reports, vessel=vessel, reason=REASONS,
                           gauge_fill=min(100, float(Fraction(reports['windows'][vessel]['order']['threshold_m'])) * 25),
                           seal=seal, time=instant, rule=RULES,
                           permit=permit, block=block, permit_state=state, policy=policy,
                           policies=permits.POLICIES, policy_text=permits.POLICY_TEXT,
                           permit_states=PERMIT_STATES, run_states=RUN_STATES,
                           permits=permits.for_run(journal(), key) if key else [],
                           lineage={'parent': journal().parent(key) if key else None,
                                    'successors': journal().successors(key) if key else []},
                           history=journal().list() if chapter == 'journal' else [])


@web.post('/desk/correct/<key>')
def correct(key):
    journal().get(key)
    form = request.form
    payload = {'sources': {name: form.get(name, '') for name in ('observations', 'stations', 'vessels')}}
    result = record(journal(), load_sources(payload), corrects=key)
    return redirect(url_for('tide_desk.desk', chapter='journal', run=result['run_id']))
