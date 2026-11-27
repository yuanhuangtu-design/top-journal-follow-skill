"""Versioned journal rankings shared by reports and Feishu synchronization."""

import base64
from functools import lru_cache
import gzip
import json
import os
from pathlib import Path
import re

CAS_PREFIX = '2025版｜'
JCR_PREFIX = '2026发布版（2025数据）｜'


def journal_key(value):
    return re.sub(r'[^\w]', '', str(value).casefold().replace('&', 'and'))


@lru_cache(maxsize=1)
def load_catalog():
    parts = [os.environ.get('JOURNAL_RANKINGS_CAS1_B64_PART1', '').strip(),
             os.environ.get('JOURNAL_RANKINGS_CAS1_B64_PART2', '').strip()]
    if any(parts) and not all(parts):
        raise ValueError('Expanded journal ranking catalog is incomplete')
    encoded = ''.join(parts) if all(parts) else os.environ.get('JOURNAL_RANKINGS_B64', '').strip()
    file_path = os.environ.get('JOURNAL_RANKINGS_FILE', '').strip()
    if encoded:
        catalog = json.loads(gzip.decompress(base64.b64decode(encoded, validate=True)))
    elif file_path:
        catalog = json.loads(Path(file_path).read_text(encoding='utf-8'))
    elif os.environ.get('JOURNAL_RANKINGS_REQUIRED', '').lower() == 'true':
        raise ValueError('Journal ranking catalog is missing')
    else:
        return {}, {}
    expected = {'schema_version': 1, 'cas_version': '2025', 'jcr_release': '2026', 'jcr_data_year': '2025'}
    if any(catalog.get(key) != value for key, value in expected.items()):
        raise ValueError('Journal ranking catalog version mismatch')
    names, identifiers = {}, {}
    for journal in catalog['journals']:
        if not journal.get('cas') or not journal.get('jcr'):
            raise ValueError('Journal ranking catalog has an empty value')
        for name in journal['names']:
            key = journal_key(name)
            if key in names and names[key] != journal:
                raise ValueError('Ambiguous journal name in ranking catalog')
            names[key] = journal
        for identifier in journal['issns']:
            key = identifier.upper()
            if key in identifiers and identifiers[key] != journal:
                raise ValueError('Ambiguous ISSN in ranking catalog')
            identifiers[key] = journal
    return names, identifiers


def paper_rankings(paper):
    names, identifiers = load_catalog()
    matches = [identifiers[value.upper()] for value in paper.get('issns', [])
               if value.upper() in identifiers]
    if not matches:
        matches = [names[journal_key(paper.get(key, ''))] for key in ('journal', 'iso_journal')
                   if journal_key(paper.get(key, '')) in names]
    if matches:
        if any(value != matches[0] for value in matches):
            raise ValueError('Conflicting journal identifiers')
        row = matches[0]
        status = 'matched'
        if '不适用' in row['cas'] and '不适用' in row['jcr']:
            status = 'not_applicable'
        elif '未查到' in row['cas'] or '未查到' in row['jcr']:
            status = 'not_found'
        elif '待核验' in row['jcr']:
            status = 'cas_verified_jcr_unverified'
        return {'cas_partition': row['cas'], 'jcr_quartile': row['jcr'], 'ranking_status': status}
    types = paper.get('publication_types', [])
    if 'Preprint' in types:
        value = '不适用（预印本）'
        return {'cas_partition': value, 'jcr_quartile': value, 'ranking_status': 'not_applicable'}
    if 'Book Chapter' in types:
        value = '不适用（图书章节）'
        return {'cas_partition': value, 'jcr_quartile': value, 'ranking_status': 'not_applicable'}
    return {'cas_partition': CAS_PREFIX + '待核验（期刊资料未覆盖）',
            'jcr_quartile': JCR_PREFIX + '待核验（期刊资料未覆盖）', 'ranking_status': 'unverified'}


def enrich_report(report):
    load_catalog()
    for paper in report['papers']:
        paper.update(paper_rankings(paper))
    report['meta']['journal_rankings'] = {
        'cas_version': '2025大类', 'jcr_release': '2026', 'jcr_data_year': '2025',
        'unverified_pmids': [paper['pmid'] for paper in report['papers']
                             if paper['ranking_status'] in ('unverified', 'cas_verified_jcr_unverified')]}
    return report
