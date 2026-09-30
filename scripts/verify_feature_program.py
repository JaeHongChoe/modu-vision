"""Reject lost scope or unsupported completion claims in the feature program."""
import json
from pathlib import Path, PureWindowsPath


REPO_ROOT=Path(__file__).resolve().parents[1]


def _repository_file(value,feature_id,kind):
    if not isinstance(value,str) or not value:
        raise ValueError(f'Missing {kind} path: {feature_id}')
    relative=Path(value)
    if relative.is_absolute() or PureWindowsPath(value).drive or '..' in relative.parts:
        raise ValueError(f'{kind} must use a repository-relative path: {feature_id}')
    root=REPO_ROOT.resolve();resolved=(root/relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f'{kind} leaves the repository: {feature_id}')
    if not resolved.is_file():
        raise ValueError(f'Missing {kind} file: {feature_id}: {value}')


def verify(path: Path) -> dict:
    program=json.loads(path.read_text())
    rows=program['features']
    expected={f'F{i:03}' for i in range(1,124)}
    ids=[r['id'] for r in rows]
    if len(ids)!=123 or len(set(ids))!=123 or set(ids)!=expected:
        raise ValueError('Feature scope must contain each F001–F123 exactly once')
    if type(program.get('scope_count')) is not int or program['scope_count']!=len(rows):
        raise ValueError('scope_count must match the 123 feature rows')
    phases=program.get('phases')
    if not isinstance(phases,dict) or set(phases)!={f'P{i:02}' for i in range(1,11)}:
        raise ValueError('Feature phases must contain P01–P10 exactly once')
    if {row['phase'] for row in rows}!=set(phases):
        raise ValueError('Feature phase assignments must match the declared phases')
    for row in rows:
        if row['phase'] not in program['phases']:
            raise ValueError(f"Unknown phase: {row['id']}")
        if row['implementation'] not in {'pending','in_progress','implemented','integrated'}:
            raise ValueError(f"Invalid implementation state: {row['id']}")
        if row['verification'] not in {'pending','unit','api','ui','real_input','hardware','accepted'}:
            raise ValueError(f"Invalid verification state: {row['id']}")
        if not row['feature'] or not row['acceptance'] or not row['source_files']:
            raise ValueError(f"Missing feature contract: {row['id']}")
        if row['implementation'] in {'implemented','integrated'}:
            if not isinstance(row['source_files'],list):
                raise ValueError(f"Invalid source file list: {row['id']}")
            for source in row['source_files']:_repository_file(source,row['id'],'source')
            evidence=row.get('evidence',[])
            if not isinstance(evidence,list) or any(not isinstance(entry,dict) for entry in evidence):
                raise ValueError(f"Invalid evidence list: {row['id']}")
            kinds={entry.get('kind') for entry in evidence}
            if 'test' not in kinds:
                raise ValueError(f"Implemented features require test evidence: {row['id']}")
            if row['implementation']=='integrated' and 'workflow' not in kinds:
                raise ValueError(f"Integrated features require workflow evidence: {row['id']}")
            for entry in evidence:
                references=[entry[key] for key in ('path','reference','ref') if key in entry]
                if not references:raise ValueError(f"Missing evidence path/reference: {row['id']}")
                for reference in references:_repository_file(reference,row['id'],'evidence')
        if row['verification']=='accepted':
            kinds={e['kind'] for e in row['evidence']}
            if row['implementation']!='integrated' or not {'test','workflow'}<=kinds:
                raise ValueError(f"Acceptance requires integrated implementation and test/workflow evidence: {row['id']}")
    return {'scope':len(rows),'phases':len(program['phases']),
        'accepted':sum(r['verification']=='accepted' for r in rows),
        'pending':sum(r['implementation']=='pending' for r in rows)}


if __name__=='__main__':
    print(json.dumps(verify(Path(__file__).parents[1]/'docs/feature-program.json'),ensure_ascii=False))
