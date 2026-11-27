"""Check representative published records against queries without date limits."""
import json
from pathlib import Path
from literature_search import build_query_from_parsed, parse_natural_query, search_pubmed, fetch_summaries
from retrieval_policy import group_query
from review_selection import decision


def main():
    root=Path(__file__).resolve().parents[1]
    config=json.loads((root/'config/daily_config.json').read_text(encoding='utf-8'))
    examples=json.loads((root/'config/coverage_examples.json').read_text(encoding='utf-8'))
    for group,pmids in examples.items():
        query=build_query_from_parsed(parse_natural_query(group_query(config,group)),config['journals'][group])
        query+=' AND ('+' OR '.join(pmid+'[PMID]' for pmid in pmids)+')'
        found,_=search_pubmed(query,500)
        if set(found)!=set(pmids):
            raise RuntimeError(f'Known-item coverage failed in {group}: {sorted(set(pmids)-set(found))}')
        policy=config.get('group_ranking_filters',{}).get(group)
        if policy and any(decision(paper,policy)!='eligible' for paper in fetch_summaries(found)):
            raise RuntimeError(f'Known-item ranking gate failed in {group}')
        print(f'[COVERAGE] {group}: {len(found)}/{len(pmids)} examples found')


if __name__=='__main__': main()
