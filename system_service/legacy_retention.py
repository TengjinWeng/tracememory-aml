"""Apply private, completed-task retention plans to the retained protocol database."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def purge_due(root,now=None,force=False):
    root=Path(root)
    path=root/'docs/legacy-retention.private.json'
    if not path.exists():return 0
    plan=json.loads(path.read_text())
    current=time.time() if now is None else now
    due=[job for job in plan.get('jobs',[]) if job.get('state')=='pending'
         and job.get('confirmed_completed') is True and (force or job.get('delete_after',float('inf'))<=current)]
    if not due:return 0
    # No plan-supplied path or SQL identifier is used: only this project's retained baseline.
    database=root/'data/memory.sqlite3'
    if not database.is_file():raise RuntimeError('retained protocol database missing')
    removed=0
    db=sqlite3.connect(database,timeout=30)
    try:
        db.execute('PRAGMA secure_delete=ON')
        with db:
            for job in due:
                scopes=job.get('user_ids')
                if not isinstance(scopes,list) or not scopes or any(not isinstance(u,str) or not u or len(u)>4096 for u in scopes):
                    raise ValueError('invalid completed-task retention scope')
                for user in scopes:
                    db.execute('DELETE FROM memories WHERE user_id=?',(user,))
                    db.execute('DELETE FROM requests WHERE user_id=?',(user,))
                    removed+=1
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('VACUUM')
    finally:db.close()
    for job in due:job.update(state='purged',purged_at=current)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.retention-',delete=False) as stream:
        os.chmod(stream.name,0o600)
        json.dump(plan,stream,ensure_ascii=False,indent=2);stream.write('\n')
        temporary=stream.name
    os.replace(temporary,path)
    return removed
