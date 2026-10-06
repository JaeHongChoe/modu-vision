"""Retain one actual clean-source GUI case; never decide quality or acceptance.

Inputs are explicit owned Playwright JSON and harness receipts. No test, app,
network operation or approval is started by this read-only collector.
"""
import argparse,hashlib,json,re,subprocess
from pathlib import Path

MAX_BYTES=8*1024*1024

def unique(pairs):
    value={}
    for k,v in pairs:
        if k in value:raise ValueError('Duplicate receipt key')
        value[k]=v
    return value

def regular(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file():raise ValueError('Receipt/artifact must be an unlinked regular file')
    return path

def raw(path):
    with regular(path).open('rb') as f:value=f.read(MAX_BYTES+1)
    if len(value)>MAX_BYTES:raise ValueError('Receipt exceeds bounded size')
    return value

def sha(value):return hashlib.sha256(value).hexdigest()

def collect(repo,report,manifest):
    repo=Path(repo).resolve();report=regular(report);manifest=regular(manifest)
    report_raw=raw(report);manifest_raw=raw(manifest)
    results=json.loads(report_raw,object_pairs_hook=unique);harness=json.loads(manifest_raw,object_pairs_hook=unique)
    source=harness.get('source') or {};commit=source.get('commit')
    if (harness.get('schema')!='modu-vision.e2e-evidence/v1' or harness.get('receipt')!='HarnessReceipt'
            or source.get('dirty') is not False or not isinstance(commit,str) or not re.fullmatch('[0-9a-f]{40}',commit)
            or harness.get('status')!='passed' or harness.get('expected_status')!='passed' or harness.get('result')!='pass'
            or harness.get('mode') not in {'browser','electron'}):raise ValueError('An actual expected passed clean-source GUI harness receipt is required')
    title=harness.get('test','');prefix,separator,case=title.partition(' › ')
    if not separator or not case or Path(prefix).name!=prefix or not prefix.endswith('.spec.ts'):raise ValueError('Exact harness GUI case identity is required')
    selected=[]
    def walk(suites):
        for suite in suites:
            for spec in suite.get('specs',[]):
                for test in spec.get('tests',[]):
                    if spec.get('file')==prefix and spec.get('title')==case and test.get('projectName')==harness.get('project'):selected.append(test)
            walk(suite.get('suites',[]))
    walk(results.get('suites',[]))
    if len(selected)!=1:raise ValueError('Exact GUI case must occur once in the actual report')
    test=selected[0];attempts=test.get('results',[])
    if (test.get('expectedStatus')!='passed' or test.get('status')!='expected' or len(attempts)!=1
            or attempts[0].get('status')!='passed' or attempts[0].get('retry')!=0 or attempts[0].get('errors')):
        raise ValueError('Failed, skipped, expected-failure or retried GUI cases are not accepted')
    path='scripts/e2e/'+prefix;file=regular(repo/path);test_bytes=raw(file)
    saved=subprocess.check_output(['git','show',commit+':'+path],cwd=repo)
    if test_bytes!=saved:raise ValueError('GUI spec differs from its recorded source commit')
    artifacts=[];directory=report.parent
    for index,name in enumerate(harness.get('screenshots',[])):
        screenshot=regular(name)
        if not screenshot.is_relative_to(directory):raise ValueError('Screenshot escaped the explicit owned run')
        artifacts.append({'id':'screenshot-'+str(index),'sha256':sha(raw(screenshot))})
    if not artifacts:raise ValueError('Actual GUI screenshot evidence is required')
    return {'schema':'modu-vision.gui-execution/v1','source_sha':commit,'source_dirty':False,
        'test':path+'::'+case,'test_source_sha256':sha(test_bytes),'mode':harness['mode'],'platform':harness['platform'],
        'status':'passed','expected_status':'passed','result':'expected','attempts':1,
        'report_sha256':sha(report_raw),'harness_sha256':sha(manifest_raw),'artifacts':artifacts,
        'quality_approved':False,'independent_review_accepted':False,
        'scope':'Executed GUI case only; no human model quality, device, release or feature acceptance inferred'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    value=collect(a.repo,a.report,a.manifest)
    if any(q.is_symlink() for q in (a.output,*a.output.parents)):raise ValueError('Output cannot follow links')
    with a.output.open('x',encoding='utf-8') as out:out.write(json.dumps(value,indent=2)+'\n')
    print(json.dumps({'status':'recorded_GUI_case','source_sha':value['source_sha'],'test':value['test'],'quality_approved':False}))
if __name__=='__main__':main()
