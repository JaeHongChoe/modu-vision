"""Check approved requirement coverage and evidence without inflating acceptance."""
import argparse
import json
from pathlib import Path

STATUSES={'planned','implemented','integration_verified','accepted'}
CHECKS={'gui','persist','reopen','failure','handoff'}


def validate_program(program,root):
    errors=[];rows=program.get('requirements',[])
    identifiers=[r.get('id') for r in rows]
    required={f'U{i:03d}' for i in range(1,34)}
    if set(identifiers)!=required:errors.append('Approved requirement coverage differs: '+str(sorted(required-set(identifiers))))
    if len(identifiers)!=len(set(identifiers)):errors.append('Requirement IDs are duplicated')
    for row in rows:
        rid=row.get('id','unknown');status=row.get('status')
        if status not in STATUSES:errors.append(rid+': invalid implementation status')
        if not row.get('title') or not row.get('owner'):errors.append(rid+': title and owner required')
        for field in ('implementation','evidence'):
            paths=row.get(field,[])
            if status!='planned' and not paths:errors.append(rid+': missing '+field)
            for path in paths:
                file=(root/path).resolve()
                if not file.is_relative_to(root.resolve()) or not file.is_file():errors.append(rid+': missing or external '+field+' file '+str(path))
        checks=row.get('acceptance',{})
        if set(checks)!=CHECKS or any(v not in {'pending','passed','not_applicable'} for v in checks.values()):errors.append(rid+': invalid acceptance record')
        if status=='accepted' and any(checks.get(c)!='passed' for c in CHECKS):errors.append(rid+': accepted requires GUI persistence reopen failure and handoff evidence')
    return errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--program',default='docs/product-upgrade-program.json');parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));args=parser.parse_args()
    root=Path(args.root).resolve();program=json.loads((root/args.program).read_text());errors=validate_program(program,root)
    summary={status:sum(r['status']==status for r in program['requirements']) for status in STATUSES}
    print(json.dumps({'requirements':len(program['requirements']),'states':summary,'errors':errors},ensure_ascii=False,indent=2))
    return int(bool(errors))

if __name__=='__main__':raise SystemExit(main())
