#!/usr/bin/python3.12
import argparse, json, pathlib, subprocess, time

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    w=sub.add_parser('write'); w.add_argument('--path',required=True); w.add_argument('--reason',required=True); w.add_argument('--tree-rc',type=int,default=0); w.add_argument('--checkout',required=True)
    c=sub.add_parser('clear'); c.add_argument('--path',required=True)
    a=ap.parse_args(); p=pathlib.Path(a.path)
    if a.cmd=='clear': p.unlink(missing_ok=True); return
    paths=[]; classification='preflight_other'
    if a.tree_rc==3:
        classification='dirty_tree'; out=subprocess.run(['git','-C',a.checkout,'status','--porcelain','--untracked-files=no'],capture_output=True,text=True,check=False).stdout
        paths=[line[3:] for line in out.splitlines()[:5]]
    elif a.tree_rc==4: classification='dirty_tree'; paths=['sitecustomize.py']
    p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps({'timestamp':time.time(),'classification':classification,'reason':a.reason,'paths':paths})+'\n'); tmp.replace(p)
if __name__=='__main__': main()
