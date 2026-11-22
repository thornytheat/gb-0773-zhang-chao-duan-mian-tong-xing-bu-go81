from app.config import RULES
from app.errors import TideError
from app.modules.datum.fingerprint import section_fingerprints
from app.modules.journal.manifest import canonical, digest

from .signature import seal, verifies


def trace(reports):
    """把证据钉到所用基准和潮窗：基准逐断面指纹、各船期放行区间与计数。"""
    sections = section_fingerprints(reports['datum']['stations'])
    return {
        'rule_version': RULES,
        'datum': {'fingerprint': digest(canonical(sorted(sections.items()))),
                  'sections': sections,
                  'stations': {name: {'section': spec['section'], 'datum_m': spec['datum_m'],
                                      'bed_m': spec['bed_m'], 'priority': spec['priority'],
                                      'offset_s': spec['offset_s'], 'gap_s': spec['gap_s']}
                               for name, spec in sorted(reports['datum']['stations'].items())}},
        'windows': {vessel: {'windows': [{'start': row['start'], 'end': row['end'],
                                          'start_included': row['start_included'],
                                          'end_included': row['end_included']}
                                         for row in route['windows']],
                             'counts': reports['coverage']['ships'][vessel]['seconds']}
                    for vessel, route in sorted(reports['windows'].items())},
    }


def issue(receipt, sources, reports):
    if not verifies(reports, receipt['result_sha256']):
        raise TideError('INVALID_PROOF', '归档结果校验未通过', 409)
    if set(sources) != set(receipt['inputs']):
        raise TideError('INVALID_PROOF', '归档原件清单不符', 409)
    if any(digest(text) != receipt['inputs'][name] for name, text in sources.items()):
        raise TideError('INVALID_PROOF', '归档原件校验未通过', 409)
    evidence = {'manifest': receipt, 'originals': sources, 'reports': reports, 'trace': trace(reports)}
    return {**evidence, 'seal_sha256': seal(evidence), 'verified': True}
