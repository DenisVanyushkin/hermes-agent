import json, os, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
HELPER=ROOT/'scripts/job_intel_shadow_failure_record.py'; NOTIFIER=ROOT/'scripts/job_intel_shadow_notifier.py'; PREFLIGHT=ROOT/'scripts/job_intel_shadow_preflight.sh'
def run(*args,**kw): return subprocess.run(args,text=True,capture_output=True,**kw)
def test_helper_dirty_and_clear(tmp_path):
 r=tmp_path/'r'; run('git','init','-q',str(r),check=True); (r/'tracked.txt').write_text('a'); run('git','-C',str(r),'add','.'); run('git','-C',str(r),'-c','user.email=x@y','-c','user.name=x','commit','-qm','i'); (r/'tracked.txt').write_text('b'); f=tmp_path/'f.json'; run(str(HELPER),'write','--path',str(f),'--reason','tracked modifications','--tree-rc','3','--checkout',str(r),check=True); d=json.loads(f.read_text()); assert d['classification']=='dirty_tree' and d['paths']==['tracked.txt']; run(str(HELPER),'clear','--path',str(f),check=True); assert not f.exists()
def test_notifier_transitions(tmp_path):
 f=tmp_path/'f'; s=tmp_path/'s'; out=tmp_path/'out'; cmd=tmp_path/'send'; cmd.write_text('#!/bin/sh\nprintf %s "$3" >"$OUT"'); cmd.chmod(0o755); env={**os.environ,'OUT':str(out)}
 def n(event): return run(str(NOTIFIER),event,'--failure-file',str(f),'--state-file',str(s),'--send-command',str(cmd),'--invocation-start','0',env=env)
 f.write_text(json.dumps({'timestamp':9999999999,'classification':'dirty_tree','reason':'r','paths':['a.py']})); assert n('failure').returncode==0 and 'грязное дерево' in out.read_text(); f.unlink(); assert n('success').returncode==0 and 'восстановлен' in out.read_text(); out.unlink(); assert n('success').returncode==0 and not out.exists()
def test_notifier_failure_and_contract(tmp_path):
 bad=tmp_path/'bad'; bad.write_text('#!/bin/sh\nexit 7'); bad.chmod(0o755); assert run(str(NOTIFIER),'failure','--send-command',str(bad),'--state-file',str(tmp_path/'s')).returncode!=0; text=PREFLIGHT.read_text(); assert 'job_intel_shadow_failure_record.py' in text and '/var/lib/job-intel/state/shadow-preflight-failure.json' in text and 'JOB_INTEL_TREE_STATE_ONLY' not in text
