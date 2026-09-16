#!/usr/bin/python3.12
import argparse, datetime, json, pathlib, subprocess, sys

def show(unit, prop):
    r=subprocess.run(['systemctl','show',unit,f'-p{prop}','--value'],text=True,capture_output=True,check=False); return r.stdout.strip()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('event',choices=['failure','success']); ap.add_argument('--failure-file',default='/var/lib/job-intel/state/shadow-preflight-failure.json'); ap.add_argument('--state-file',default='/var/lib/job-intel/state/shadow-notify-state.json'); ap.add_argument('--send-command',nargs='+',default=['/home/hermes/.hermes/hermes-agent/venv/bin/python','-m','hermes_cli.main','send']); ap.add_argument('--unit',default='job-intel-shadow-collection.service'); ap.add_argument('--timer',default='job-intel-shadow-collection.timer'); ap.add_argument('--invocation-start',type=float); a=ap.parse_args(); sf=pathlib.Path(a.state_file); state=json.loads(sf.read_text()).get('status') if sf.exists() else 'unknown'; start=a.invocation_start or 0
    failure=pathlib.Path(a.failure_file); data=json.loads(failure.read_text()) if failure.exists() else None; fresh=bool(data and float(data.get('timestamp',0))>start)
    nxt=show(a.timer,'NextElapseUSecRealtime') or 'неизвестно'
    if a.event=='success' and state!='failed': return 0
    if a.event=='success': msg='✅ Job Intel shadow: восстановлен после сбоя.'
    elif fresh and data['classification']=='dirty_tree': msg='⚠️ Job Intel shadow: грязное дерево canonical checkout; commit или revert: '+', '.join(data.get('paths',[]))+'. Следующий запуск: '+nxt
    elif fresh: msg=f"⚠️ Job Intel shadow: ошибка preflight: {data.get('reason','')}. Следующий запуск: {nxt}"
    else: msg=f"⚠️ Job Intel shadow: ошибка коллекции (Result={show(a.unit,'Result')}, ExecMainStatus={show(a.unit,'ExecMainStatus')}). Следующий запуск: {nxt}"
    r=subprocess.run(a.send_command+['--to','telegram',msg],check=False)
    if r.returncode: print('shadow notifier: send failed',file=sys.stderr); return r.returncode
    sf.parent.mkdir(parents=True,exist_ok=True); sf.write_text(json.dumps({'status':'failed' if a.event=='failure' else 'recovered'})+'\n'); return 0
if __name__=='__main__': raise SystemExit(main())
