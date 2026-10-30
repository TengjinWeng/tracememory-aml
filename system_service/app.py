"""Integrated Add/Search system; mode and model eligibility are explicit."""
import hmac
import json
import os
from pathlib import Path
import threading
from urllib.request import urlopen

from candidate.config import read_config
from candidate.providers import SpecifiedModels, ProviderError
from memory_service import ContractError, MemoryHandler, MemoryServer
from system_service.providers import DevelopmentModels, EMBEDDING_MODEL, EMBEDDING_REVISION
from system_service.ranking import AlgorithmStore
from system_service.legacy_retention import purge_due

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.3.2-system'


def load_settings(root=ROOT):
    config=read_config(root/'secrets/system-service.env')
    if len(config.get('SYSTEM_API_KEY',''))<24:
        raise ValueError('missing private system service key')
    if config.get('SYSTEM_MODE') not in ('development','specified'):
        raise ValueError('SYSTEM_MODE must explicitly be development or specified')
    return config


class Operations:
    def __init__(self,store,mode,root=ROOT):
        self.store,self.mode=store,mode
        self.root=root
        self.closed=threading.Event()
        self.thread=threading.Thread(target=self._clean,daemon=True)
        self.thread.start()

    def _clean(self):
        while not self.closed.is_set():
            try:
                self.store.purge_expired()
                purge_due(self.root)
            except Exception:self.store.metric('retention_failures')
            self.closed.wait(3600)

    def add(self,payload):
        return self._call('add',payload)

    def search(self,payload):
        return self._call('search',payload)

    def _call(self,operation,payload):
        if self.mode=='development' and not str(payload.get('user_id','')).startswith('dev:'):
            raise ContractError('development mode accepts only self-authored dev: user scopes',403)
        try:
            result=getattr(self.store,operation)(payload)
            self.store.metric(operation+'_success')
            return result
        except ProviderError:
            self.store.metric(operation+'_dependency_failure')
            raise ContractError('model dependency unavailable; inspect authenticated status and private configuration',503) from None

    def readiness(self):
        if self.mode=='specified':
            # Actual provider success is only established by explicit tests, not by presence of keys.
            with self.store.provider.db() as db:
                kinds={r[0] for r in db.execute('SELECT DISTINCT kind FROM api_cache')}
            return {'specified_llm_validated':'llm' in kinds,'specified_embedding_validated':'embedding' in kinds}
        cache=getattr(self.store.provider,'cache',None)
        states=cache.states() if cache else {}
        usage=cache.usage() if cache else {}
        checks={'local_embedding':False,'vision_validated':states.get('vision')=='validated',
                'vision_attempt_budget':bool(cache and usage.get('vision',0)<cache.limits['vision'])}
        try:
            with urlopen('http://127.0.0.1:18088/health',timeout=3) as response:data=json.load(response)
            checks['local_embedding']=data.get('model')==EMBEDDING_MODEL and data.get('revision')==EMBEDDING_REVISION
        except Exception:pass
        return checks


class Handler(MemoryHandler):
    server_version='TraceMemory/'+VERSION

    def do_GET(self):
        if self.path=='/health':
            return self.respond(200,{'status':'ok','version':VERSION,'mode':self.server.mode,
                                     'academic_model_configuration':self.server.mode=='specified'})
        if self.path not in ('/ready','/status'):
            return super().do_GET()
        if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.key):
            return self.respond(401,{'detail':{'reason':'invalid authentication'}})
        checks=self.server.store.readiness()
        if self.path=='/ready':
            return self.respond(200 if all(checks.values()) else 503,{'checks':checks,'official_evaluation':False})
        report=self.server.store.store.status()
        report.update({'version':VERSION,'mode':self.server.mode,'readiness':checks,
                       'official_evaluation_ready':False,'retention_inactive_days':29})
        provider=self.server.store.store.provider
        if hasattr(provider,'cache'):
            usage=provider.cache.usage()
            report.update({'provider_requests':usage,'provider_states':provider.cache.states(),
                           'vision_model':provider.cli_model,'embedding_model':EMBEDDING_MODEL,
                           'development_cli_attempts_remaining':max(0,provider.cache.limits['vision']-usage.get('vision',0))})
        return self.respond(200,report)


def make_server(root=ROOT,port=None,provider_override=None):
    config=load_settings(root)
    mode=config['SYSTEM_MODE']
    directory=root/'data-system'/mode
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if provider_override is not None:
        provider=provider_override
    elif mode=='development':
        provider=DevelopmentModels(directory,cli_limit=int(config.get('MAX_CLI_REQUESTS','20')),
                                   cli_model=config.get('CLI_MODEL','gpt-5.6-sol'))
    else:
        models=read_config(root/'secrets/model-apis.env')
        if not all(models.get(k) for k in ('OPENAI_API_KEY','OPENAI_BASE_URL','DASHSCOPE_API_KEY','DASHSCOPE_BASE_URL')):
            raise ValueError('specified model keys/URLs missing')
        provider=SpecifiedModels(models,directory/'provider-cache.sqlite3')
    identity=getattr(provider,'identity',None) or 'specified:gpt-4o-mini:text-embedding-v4:'+str(provider.config.get('EMBEDDING_DIMENSIONS','1024'))
    store=AlgorithmStore(directory/'memory.sqlite3',provider=provider,identity=identity)
    server=MemoryServer(('127.0.0.1',int(config.get('SYSTEM_PORT','18089')) if port is None else port),None,config['SYSTEM_API_KEY'])
    server.RequestHandlerClass=Handler
    server.mode=mode
    capacity=int(config.get('SYSTEM_HTTP_CAPACITY','2' if mode=='development' else '16'))
    if not 1<=capacity<=64:raise ValueError('invalid system HTTP capacity')
    server.slots=threading.BoundedSemaphore(capacity)
    server.store=Operations(store,mode,root)
    return server


def main():
    server=make_server()
    print(json.dumps({'event':'listening','version':VERSION,'mode':server.mode,'port':server.server_port}),flush=True)
    try:server.serve_forever()
    finally:
        server.store.closed.set()
        server.server_close()


if __name__=='__main__':main()
