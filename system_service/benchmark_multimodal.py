"""Self-authored multimodal regression corpus, using the running development service only."""
import argparse
import base64
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import statistics
import sys
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from candidate.config import read_config
from memory_service import canonical
import hashlib

USER = 'dev:multimodal-regression-v1'


def picture(kind, alternate=False):
    canvas = Image.new('RGB', (820, 520) if alternate else (760, 460), '#f2f2f2' if alternate else 'white')
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 30)
    if kind.startswith('receipt'):
        code, day, amount = {'receipt-a': ('TK-7402', 'WEDNESDAY', '86.00'),
                             'receipt-b': ('TK-7408', 'THURSDAY', '86.00'),
                             'receipt-c': ('JK-8914', 'WEDNESDAY', '18.00')}[kind]
        text = 'SYNTHETIC SERVICE RECEIPT\nSHOP: VELA\nBOOKING: '+code+'\nDAY: '+day+'\nTOTAL: '+amount
        draw.multiline_text((45, 50) if alternate else (25, 30), text, font=font, fill='black', spacing=20)
    else:
        draw.text((25, 20), 'SYNTHETIC SHAPE CARD', font=font, fill='black')
        left = kind == 'shapes-left'
        circle_x, triangle_x = (190, 565) if left else (565, 190)
        draw.ellipse((circle_x-65, 170, circle_x+65, 300), fill='#175cdb')
        draw.polygon([(triangle_x, 165), (triangle_x-80, 310), (triangle_x+80, 310)], fill='#ee7711')
    stream = BytesIO(); canvas.save(stream, format='PNG')
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}}


def fixture():
    # No evaluator data or generated answers are used. Labels are source identifiers only.
    long_context = 'Routine archive notes about paper, pencils and office supplies. ' * 290
    records = {
        'receipt-a': [{'type': 'text', 'text': long_context+'\nNila named the storage locker CobaltPass TR-9817.'}, picture('receipt-a')],
        'receipt-b': [{'type': 'text', 'text': 'Nila saved another Vela booking slip.'}, picture('receipt-b')],
        'receipt-c': [{'type': 'text', 'text': 'Owen saved this Vela cafe receipt.'}, picture('receipt-c')],
        'shapes-left': [picture('shapes-left')], 'shapes-right': [picture('shapes-right')],
        'bridge': 'The blue bicycle belongs to Nila. Its repair booking reference is TK-7402.',
        'old-location': 'Nila currently lives in Lyon. Her current home address is in Lyon.',
        'new-location': 'Nila moved from Lyon to Oslo; Oslo is her new home.',
        'chinese': '林悦的绿色背包放在书房的木柜里，里面有一本天文笔记。',
    }
    cases = [
        ('identifier_in_long_image_record', 'Find the original booking slip TK-7402.', ['receipt-a']),
        ('near_duplicate_identifier', 'Find receipt TK-7408.', ['receipt-b']),
        ('visual_spatial_left', 'Find the picture with a blue circle to the left of an orange triangle.', ['shapes-left']),
        ('visual_spatial_right', 'Find the picture with a blue circle to the right of an orange triangle.', ['shapes-right']),
        ('cross_language_ocr', '找一下预约编号 TK-7408 的原始票据图片。', ['receipt-b']),
        ('unseen_image_pixels', [picture('receipt-b', alternate=True)], ['receipt-b']),
        ('latest_state', 'Where does Nila currently live?', ['new-location']),
        ('two_hop_source_evidence', 'Find the repair booking slip for the blue bicycle and the ownership evidence.', ['receipt-a', 'bridge']),
        ('long_record_tail', 'Find the source containing the locker name CobaltPass TR-9817.', ['receipt-a']),
        ('chinese_source', '林悦把天文笔记放在哪里了？', ['chinese']),
    ]
    return records, cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    if args.label not in ('before', 'after', 'verification'):
        raise SystemExit('Use before, after or verification')
    config = read_config(ROOT/'secrets/system-service.env')
    if config['SYSTEM_MODE'] != 'development':
        raise SystemExit('This corpus may only be used in development mode')
    def call(path, payload=None):
        req = Request('http://127.0.0.1:'+config['SYSTEM_PORT']+path,
                      data=None if payload is None else canonical(payload).encode(),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+config['SYSTEM_API_KEY']})
        with urlopen(req, timeout=600) as response: return json.load(response)
    before = call('/status')
    if before.get('development_cli_attempts_remaining', 0) < 6 and args.label == 'before':
        raise SystemExit('At least six already-authorized CLI attempts must remain; cap is never changed')
    records, cases = fixture()
    started = time.monotonic()
    for i in range(45):
        records['noise-'+str(i)] = 'Routine unrelated archive entry '+str(i)+' about office supplies, meeting notes and a cafe visit.'
    ids = {}
    for name, content in records.items():
        request_id = 'multimodal-regression-v1:'+name
        ids[name] = 'mem_'+hashlib.sha256(canonical([USER, request_id, 0]).encode()).hexdigest()
        message = {'role': 'user', 'content': content}
        if name in ('old-location', 'new-location'):
            message['timestamp'] = 1767225600000 if name == 'old-location' else 1780272000000
        ack = call('/add', {'user_id': USER, 'request_id': request_id, 'session_id': 'self-authored:'+name, 'messages': [message]})
        assert ack.get('success') is True
    rows = []
    for name, query, expected in cases:
        t = time.monotonic()
        result = call('/search', {'user_id': USER, 'query': query, 'top_k': 100})['data']
        ranks = {target: next((i+1 for i,r in enumerate(result) if r['id'] == ids[target]), None) for target in expected}
        rows.append({'case': name, 'required_source_ranks': ranks,
                     'all_sources_at_1': all(v is not None and v <= 1 for v in ranks.values()),
                     'all_sources_at_5': all(v is not None and v <= 5 for v in ranks.values()),
                     'latency_ms': round((time.monotonic()-t)*1000, 2),
                     'top_source_labels': [next((n for n,identity in ids.items() if identity == r['id']), 'unknown') for r in result[:5]]})
    assert call('/search', {'user_id': USER+':isolated', 'query': 'TK-7402', 'top_k': 100}) == {'data': []}
    after = call('/status')
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'label': args.label,
              'service_version': after['version'], 'vision_model': after['vision_model'],
              'embedding_model': after['embedding_model'], 'records': len(records), 'image_records': 5,
              'case_count': len(rows), 'source_recall_at_1': statistics.mean(r['all_sources_at_1'] for r in rows if len(r['required_source_ranks']) == 1),
              'all_required_sources_at_5': statistics.mean(r['all_sources_at_5'] for r in rows),
              'cli_attempts_during_run': after['provider_requests'].get('vision', 0)-before['provider_requests'].get('vision', 0),
              'cli_attempts_total': after['provider_requests'].get('vision', 0),
              'elapsed_seconds': round(time.monotonic()-started, 2), 'cases': rows,
              'cross_user_isolation': 'passed', 'official_evaluation_started': False,
              'limitations': 'Small self-authored diagnostic corpus, reused for A/B debugging; not held-out, not an official score. Follow-up runs reuse scoped extraction/vector caches.'}
    output = ROOT/'docs'/('multimodal-regression-'+args.label+'.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
