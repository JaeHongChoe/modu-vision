"""Check individual UI scenario references; never approve a complete feature.

Curated actions supplement the declaration-only AST inventory. Unlisted helper,
menu and shortcut actions remain pending, even if every listed scenario passes.
"""
import argparse
import hashlib
from pathlib import Path
import sys
import json

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import check_service_plan as gate

SCENARIOS=('success','empty','invalid','error','cancel','reopen','handoff')
KIND='curated_action_evidence_not_complete_feature_acceptance'

def check(program,value,root):
    root=Path(root);errors=[];verified=pending=waived=actions_count=0
    result={'schema_version':1,'scope':KIND,'accepted_features':0}
    def finish():
        return {**result,'ok':not errors,'errors':errors,'actions':actions_count,
                'verified_scenarios':verified,'pending_scenarios':pending,'not_required_scenarios':waived}
    if (not isinstance(value,dict) or set(value)!={'schema_version','kind','sources','records'}
            or type(value.get('schema_version')) is not int or value['schema_version']!=1
            or value.get('kind')!=KIND or not isinstance(value.get('sources'),dict)
            or not isinstance(value.get('records'),list)):
        errors.append('Action registry schema is invalid');return finish()
    known={row['id'] for row in program['requirements']}
    owners={row['legacy_id']:row['service_requirements'] for row in program['legacy_coverage']}
    sources=value['sources']
    for relative,sha in sources.items():
        path=root/relative
        if (not relative.startswith('src/renderer/') or not relative.endswith('.tsx')
                or '..' in relative.split('/') or '\\' in relative or not gate._SHA256.fullmatch(gate._text(sha))
                or any(p.is_symlink() for p in (path,*path.parents))):
            errors.append('Invalid action source binding: '+relative);continue
        try:
            with path.open('rb') as handle:raw=handle.read(1024*1024+1)
            if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=sha:
                errors.append('Action source bytes changed: '+relative)
        except OSError:errors.append('Action source is missing: '+relative)
    ids=[row.get('id') if isinstance(row,dict) else None for row in value['records']]
    if any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or set(ids)!=set(owners):
        errors.append('One action record per legacy feature is required')
    for row in value['records']:
        if (not isinstance(row,dict) or set(row)!={'id','actions','remaining'}
                or row.get('id') not in owners or not isinstance(row.get('actions'),list)
                or len(gate._text(row.get('remaining')))<10):
            errors.append('Invalid action feature record');continue
        seen=set()
        for action in row['actions']:
            label=row['id']+'.action'
            if (not isinstance(action,dict) or set(action)!={'id','label','source','scenarios'}
                    or not gate._RECEIPT_NAME.fullmatch(gate._text(action.get('id')))
                    or not gate._text(action.get('label')) or action.get('source') not in sources
                    or not isinstance(action.get('scenarios'),dict) or set(action['scenarios'])!=set(SCENARIOS)):
                errors.append(label+': invalid action or incomplete seven-scenario list');continue
            if action['id'] in seen:errors.append(label+': duplicate action ID')
            seen.add(action['id']);actions_count+=1
            for scenario in SCENARIOS:
                dimension=action['scenarios'][scenario]
                problems=gate._dimension_errors(label+'.'+scenario,dimension,'gui',owners[row['id']],known,root)
                errors.extend(problems)
                if not problems:
                    state=dimension['state']
                    if state=='verified':verified+=1
                    elif state=='pending':pending+=1
                    else:waived+=1
    return finish()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT);args=parser.parse_args()
    value=gate._read_json(args.root/'docs/service-action-evidence.json')
    program=gate._read_json(args.root/'docs/service-upgrade-program.json')
    report=check(program,value,args.root)
    print(json.dumps(report,ensure_ascii=True));return 0 if report['ok'] else 1

if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=True));sys.exit(2)
