"""Real self-authored development flow; no AML jobs or official evaluation data."""
import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import uuid

from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from candidate.config import read_config


def picture(alternate=False):
    image=Image.new('RGB',(760,400) if alternate else (720,360),'#eeeeee' if alternate else 'white')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',28 if alternate else 32)
    draw=ImageDraw.Draw(image)
    draw.multiline_text((45,45) if alternate else (30,30),
                        'SYNTHETIC HOTEL RECEIPT\nCITY: SUZHOU\nCODE: ZX-4827\nTOTAL: 125.00',
                        font=font,fill='black',spacing=18)
    output=BytesIO();image.save(output,format='PNG')
    return {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()}}


def main():
    config=read_config(ROOT/'secrets/system-service.env')
    if config['SYSTEM_MODE']!='development':raise SystemExit('Self-authored CLI check is for development mode only')
    base='http://127.0.0.1:'+config['SYSTEM_PORT']
    def call(path,payload=None,auth=True):
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+config['SYSTEM_API_KEY']
        request=Request(base+path,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        try:
            with urlopen(request,timeout=240) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    state_path=ROOT/'docs/system-check-task.private.json'
    if state_path.exists():
        state=json.loads(state_path.read_text())
        user=state['user_id']
    else:
        user='dev:system-check:'+uuid.uuid4().hex
        state_path.write_text(json.dumps({'user_id':user,'state':'in_progress'})+'\n')
        state_path.chmod(0o600)
    before=call('/status')[1]['provider_requests']
    original=[{'type':'text','text':'Mira uploaded this hotel receipt.'},picture()]
    records=[original,'I parked my car beside the library.','今天午饭吃了西红柿鸡蛋面。','周五的会议已从上午九点调整为下午三点。']
    payload={'user_id':user,'request_id':'self-authored-write','session_id':'self-authored-session',
             'messages':[{'role':'user','content':value} for value in records]}
    started=time.monotonic()
    code,ack=call('/add',payload)
    assert code==200 and ack=={'success':True,'user_id':user,'request_id':payload['request_id'],'session_id':payload['session_id']},(code,ack)
    once=call('/status')[1]['provider_requests']
    assert call('/add',payload)==(200,ack)
    assert call('/status')[1]['provider_requests']==once,'idempotent retry repeated model work'
    assertions=[]
    for query,expected in [('Find the hotel receipt for reservation ZX-4827.',original),
                           ('Where was the automobile left?',records[1]),('周五几点开会？',records[3])]:
        code,result=call('/search',{'user_id':user,'query':query,'top_k':100})
        assert code==200 and result['data'] and result['data'][0]['content']==expected,(query,code)
        assert len(result['data'])<=100
        assertions.append({'kind':'text_query','source_rank':1})
    # This is a new raster, not merely another encoding of identical pixels.
    code,result=call('/search',{'user_id':user,'query':[picture(alternate=True)],'top_k':100})
    assert code==200 and result['data'][0]['content']==original,(code,'unseen-image evidence retrieval failed')
    assertions.append({'kind':'different_pixels_image_query','source_rank':1,'original_image_preserved':True})
    assert call('/search',{'user_id':user+':other','query':'ZX-4827','top_k':100})==(200,{'data':[]})
    assert call('/search',{'user_id':user,'query':'receipt','top_k':100},auth=False)[0]==401
    conflict=dict(payload,messages=[{'role':'user','content':'different write'}])
    assert call('/add',conflict)[0]==409
    assert call('/search',{'user_id':'eval:unapproved','query':'receipt','top_k':100})[0]==403
    completed_usage=call('/status')[1]['provider_requests']
    process=subprocess.run([sys.executable,'-m','system_service.manage','restart'],cwd=ROOT,capture_output=True,text=True,timeout=40)
    assert process.returncode==0,'service restart failed'
    code,after=call('/search',{'user_id':user,'query':'Find receipt ZX-4827','top_k':100})
    assert code==200 and after['data'][0]['content']==original,'persistent source/index lost after restart'
    status=call('/status')[1]
    assert all(status['readiness'].values()),status['readiness']
    report={'scope':'self-authored development integration; not an AML score or held-out quality estimate',
            'version':status['version'],'mode':status['mode'],'vision_model':config.get('CLI_MODEL'),
            'checks':assertions,'idempotent_model_calls':0,'cross_user_isolation':'passed',
            'authentication':'passed','conflicting_request_id':'409','unapproved_evaluation_scope':'403',
            'restart_persistence':'passed','readiness':status['readiness'],
            'cli_attempts_during_flow':completed_usage.get('vision',0)-before.get('vision',0),
            'elapsed_seconds':round(time.monotonic()-started,2),'new_api_key_used':False,
            'official_evaluation_started':False}
    (ROOT/'docs/system-flow-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    state_path.write_text(json.dumps({'user_id':user,'state':'completed'})+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
