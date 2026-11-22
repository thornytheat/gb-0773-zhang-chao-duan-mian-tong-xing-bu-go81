from collections import defaultdict

from app.modules.journal.manifest import canonical, digest

DEFINITION_FIELDS = ('datum_m', 'bed_m', 'priority', 'offset_s', 'gap_s')


def section_fingerprints(stations):
    """按断面汇总测站定义，得到可追溯的基准指纹。"""
    grouped = defaultdict(list)
    for name, spec in stations.items():
        grouped[spec['section']].append([name, *[spec[field] for field in DEFINITION_FIELDS]])
    return {section: digest(canonical([section, sorted(rows)])) for section, rows in grouped.items()}


def datum_fingerprint(sections):
    return digest(canonical(sorted(sections.items())))


def datum_snapshot(reports):
    """签发证据中随附的基准快照：整体指纹、逐断面指纹及测站原定义。"""
    stations = reports['datum']['stations']
    sections = section_fingerprints(stations)
    detail = {}
    for section in sorted(sections):
        detail[section] = {
            'fingerprint': sections[section],
            'stations': {
                name: {field: spec[field] for field in DEFINITION_FIELDS}
                for name, spec in sorted(stations.items()) if spec['section'] == section
            },
        }
    return {'fingerprint': datum_fingerprint(sections), 'sections': detail}
