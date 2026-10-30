"""New self-authored spatial/state diagnostics, isolated from all official data."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import statistics
import sys
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from candidate.config import read_config
from memory_service import canonical

USER='dev:spatial-state-diagnostic-v2'


def picture(square_above,alternate=False):
    image=Image.new('RGB',(720,650) if alternate else (640,600),'#f4f4f4' if alternate else 'white')
    draw=ImageDraw.Draw(image)
    draw.text((25,20),'SELF-AUTHORED GEOMETRY',fill='black')
    cx=360 if alternate else 320
    square_y,star_y=(180,440) if square_above else (440,180)
    draw.rectangle((cx-70,square_y-70,cx+70,square_y+70),fill='#8935bb')
    points=[]
    for i in range(10):
        angle=-math.pi/2+i*math.pi/5;radius=86 if i%2==0 else 36
        points.append((cx+radius*math.cos(angle),star_y+radius*math.sin(angle)))
    draw.polygon(points,fill='#e5b91e')
    stream=BytesIO();image.save(stream,format='PNG')
    return {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}}


def fixture():
    jan=1767225600000;jun=1780272000000;october=1790812800000
    records={
        'square-above':([picture(True)],jan),'square-below':([picture(False)],jan),
        'home-old':('Lea currently lives in Rome. Her current home is in Rome.',jan),
        'home-new':('Lea moved from Rome to Porto. Porto is her new home.',jun),
        'home-distractor':('Lea saved a restaurant receipt from yesterday. The bill was 42 euros.',october),
        'other-person':('Owen currently lives in Berlin and moved there this week.',october),
        'job-old':('Lea works as a designer at Lumos. Her current job is designer.',jan),
        'job-new':('Lea was promoted to research manager at Lumos; that is her new role.',jun),
        'drink-old':('Owen prefers black tea. His favourite drink is black tea.',jan),
        'drink-new':('Owen no longer prefers tea. His favourite drink is now sparkling water.',jun),
        'drink-distractor':('Owen bought tickets for a museum and saved the receipt.',october),
        'meeting-old':('The Atlas meeting is currently scheduled for 09:00 on Friday.',jan),
        'meeting-new':('The Atlas meeting was rescheduled from 09:00 to 15:00 on Friday.',jun),
        'chinese-old':('林悦现在住在南京，常住地址是南京。',jan),
        'chinese-new':('林悦搬家到了苏州，苏州是她的新住址。',jun),
        'chinese-distractor':('林悦昨天保存了一张书店收据。',october),
    }
    cases=[
        ('above','Find the image with the square above the star.',['square-above']),
        ('below','Find the image with the square below the star.',['square-below']),
        ('inverse_relation','Find the image where the star is below the square.',['square-above']),
        ('inverse_relation_other','Find the image where the star is above the square.',['square-below']),
        ('different_image_query',[picture(True,True)],['square-above']),
        ('current_home','Where does Lea currently live?',['home-new']),
        ('historical_home','Where did Lea live as of January 2026?',['home-old']),
        ('current_job','What is Lea\'s current job?',['job-new']),
        ('historical_job','What was Lea\'s job as of January 2026?',['job-old']),
        ('current_preference','What drink does Owen prefer now?',['drink-new']),
        ('historical_preference','What drink did Owen prefer in January 2026?',['drink-old']),
        ('current_meeting','What is the current time for the Atlas meeting?',['meeting-new']),
        ('historical_meeting','What time was the Atlas meeting scheduled for in January 2026?',['meeting-old']),
        ('chinese_update','林悦现在住在哪里？',['chinese-new']),
        ('newer_unrelated_person','Where does Owen currently live?',['other-person']),
    ]
    return records,cases


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',choices=['before','after'],required=True)
    args=parser.parse_args()
    config=read_config(ROOT/'secrets/system-service.env')
    if config['SYSTEM_MODE']!='development':raise SystemExit('Development mode required')
    def call(path,payload=None):
        request=Request('http://127.0.0.1:'+config['SYSTEM_PORT']+path,
                        data=None if payload is None else canonical(payload).encode(),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+config['SYSTEM_API_KEY']})
        with urlopen(request,timeout=300) as response:return json.load(response)
    before=call('/status');records,cases=fixture();ids={};start=time.monotonic()
    if args.label=='before' and before['development_cli_attempts_remaining']<3:
        raise SystemExit('Three attempts must remain inside the existing cap; cap is not increased')
    for i in range(35):
        records['noise-'+str(i)]=('Unrelated office archive item '+str(i)+' about stationery and routine paperwork.',1767225600000)
    for label,(content,stamp) in records.items():
        request_id='state-diagnostic-v2:'+label
        ids[label]='mem_'+hashlib.sha256(canonical([USER,request_id,0]).encode()).hexdigest()
        assert call('/add',{'user_id':USER,'request_id':request_id,'session_id':'self-authored:'+label,
                            'messages':[{'role':'user','content':content,'timestamp':stamp}]})['success']
    rows=[]
    for label,query,expected in cases:
        results=call('/search',{'user_id':USER,'query':query,'top_k':100})['data']
        ranks={target:next((i+1 for i,r in enumerate(results) if r['id']==ids[target]),None) for target in expected}
        rows.append({'case':label,'required_source_ranks':ranks,'all_sources_at_1':all(v==1 for v in ranks.values()),
                     'all_sources_at_5':all(v is not None and v<=5 for v in ranks.values()),
                     'top_source_labels':[next((name for name,identity in ids.items() if identity==r['id']),'unknown') for r in results[:3]]})
    assert call('/search',{'user_id':USER+':absent','query':'Lea','top_k':100})=={'data':[]}
    after=call('/status')
    report={'created_at':datetime.now(timezone.utc).isoformat(),'label':args.label,'service_version':after['version'],
            'records':len(records),'image_records':2,'case_count':len(rows),'source_recall_at_1':statistics.mean(r['all_sources_at_1'] for r in rows),
            'all_required_sources_at_5':statistics.mean(r['all_sources_at_5'] for r in rows),'cases':rows,
            'cli_attempts_during_run':after['provider_requests'].get('vision',0)-before['provider_requests'].get('vision',0),
            'cli_attempts_total':after['provider_requests'].get('vision',0),'elapsed_seconds':round(time.monotonic()-start,2),
            'cross_user_isolation':'passed','official_evaluation_started':False,
            'limitations':'Self-authored diagnostic, defined before this round of ranking changes, reused for debugging. Not an independent statistical benchmark or an official score. Warm caches differ from cold runs.'}
    (ROOT/'docs'/('spatial-state-'+args.label+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
