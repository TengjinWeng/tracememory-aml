"""Local lifecycle/status commands; never print keys or reset model budgets."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from candidate.config import read_config


def initialize():
    path=ROOT/'secrets/system-service.env'
    path.parent.mkdir(mode=0o700,exist_ok=True)
    if not path.exists():
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write('SYSTEM_MODE=development\nSYSTEM_PORT=18089\nCLI_MODEL=gpt-5.6-sol\nMAX_CLI_REQUESTS=20\nSYSTEM_API_KEY='+secrets.token_urlsafe(36)+'\n')
    return read_config(path)


def session(name,command):
    if subprocess.run(['tmux','has-session','-t',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['tmux','new-session','-d','-s',name,command],check=True)


def status(config):
    headers={'Authorization':'Bearer '+config['SYSTEM_API_KEY']}
    request=Request('http://127.0.0.1:'+config['SYSTEM_PORT']+'/status',headers=headers)
    with urlopen(request,timeout=5) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['up','down','status','restart','check','preflight'])
    args=parser.parse_args()
    config=initialize()
    python=ROOT/'.venv-candidate/bin/python'
    if args.action in ('down','restart'):
        subprocess.run(['tmux','kill-session','-t','tracememory-system'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if args.action=='down':
            print('Integrated API stopped; embedding and other service versions remain available.')
            return
    if args.action in ('up','restart'):
        if config['SYSTEM_MODE']=='development':
            command='cd '+shlex.quote(str(ROOT))+' && '+shlex.quote(str(python))+' -m system_service.supervise embedding >>'+shlex.quote(str(ROOT/'local-embedding.log'))+' 2>&1'
            session('tracememory-embedding',command)
        command='cd '+shlex.quote(str(ROOT))+' && env OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4 '+shlex.quote(str(python))+' -m system_service.supervise >>'+shlex.quote(str(ROOT/'system-service.log'))+' 2>&1'
        session('tracememory-system',command)
        for _ in range(40):
            try:
                print(json.dumps(status(config),ensure_ascii=False))
                return
            except Exception:time.sleep(.25)
        raise SystemExit('Integrated API did not become available; inspect its private log')
    if args.action=='check':
        subprocess.run([str(python),str(ROOT/'system_service/check_flow.py')],check=True)
    elif args.action=='preflight':
        report=status(config)
        report['preflight']={'functional_readiness':all(report['readiness'].values()),
                             'specified_model_mode':config['SYSTEM_MODE']=='specified',
                             'aml_key_file_present':(ROOT/'secrets/aml-key.txt').is_file(),
                             'registered_version_smoke_verified':False,
                             'full_capacity_and_cost_approved':False}
        print(json.dumps(report,ensure_ascii=False))
    else:print(json.dumps(status(config),ensure_ascii=False))


if __name__=='__main__':main()
