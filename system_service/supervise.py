"""Recover the integrated API after process failure without touching other versions."""
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    command=(['bash',str(ROOT/'experiments/local_embedding/start.sh')]
             if len(sys.argv)>1 and sys.argv[1]=='embedding' else [sys.executable,'-m','system_service.app'])
    while True:
        subprocess.run(command,cwd=ROOT)
        print('Service process exited; restarting in five seconds.',flush=True)
        time.sleep(5)
