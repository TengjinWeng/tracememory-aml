"""Conservative source/query relation matching and entity-scoped state recency."""
import calendar
from datetime import datetime, timezone
import re

FUNCTION_WORDS=set('a an the is are was were be been being to of in on at with and or as for from this that it its positioned located placed appears large small solid upright upward pointing near half area corner side'.split())
UNCERTAIN=re.compile(r'\b(not|no|possibly|perhaps|uncertain|unclear|might|may)\b',re.I)
RELATION=re.compile(r'\b(?:(?:to|on)\s+the\s+)?(left|right)\s+of\b|\b(above|below|over|under|behind|in front of)\b',re.I)
POSITION=re.compile(r'\b(?:on|in|at)\s+(?:the\s+)?(?:(upper|lower|top|bottom)[ -])?(left|right|top|bottom)\b',re.I)


def normalize(text):
    text=text.casefold().replace('violet','purple')
    text=re.sub(r'\b(?:golden[- ]yellow|golden|gold)\b','yellow',text)
    return re.sub(r'\s+',' ',text).strip()


def canonical_relation(subject,relation,obj):
    if relation in ('right','below','under','behind'):
        return obj,{'right':'left','below':'above','under':'above','behind':'front'}[relation],subject
    return subject,{'over':'above','in front of':'front'}.get(relation,relation),obj


def mentions(text,names):
    names={normalize(n) for n in names if isinstance(n,str) and len(n.strip())>1}
    names={n for n in names if re.fullmatch(r'[a-z][a-z -]{1,70}',n) and n not in FUNCTION_WORDS}
    heads={}
    for name in names:heads.setdefault(name.split()[-1],set()).add(name)
    aliases=[(name,name,False) for name in names]
    aliases += [(head,next(iter(values)),True) for head,values in heads.items() if len(values)==1 and len(head)>2 and head not in names]
    found=[]
    for alias,owner,short in sorted(aliases,key=lambda x:-len(x[0])):
        for match in re.finditer(r'\b'+re.escape(alias)+r'\b',text):
            if any(match.start()<end and match.end()>start for start,end,_ in found):continue
            if short:
                previous=re.search(r'([a-z]+)\s+$',text[:match.start()])
                # A bare noun may stand for a known object; a different colored noun may not.
                if previous and previous[1] not in FUNCTION_WORDS:continue
            found.append((match.start(),match.end(),owner))
    return sorted(found)


def spatial_relations(text,names):
    text=normalize(text);objects=mentions(text,names);result=set();positions={}
    for match in RELATION.finditer(text):
        before=[o for o in objects if o[1]<=match.start() and match.start()-o[1]<75]
        after=[o for o in objects if o[0]>=match.end() and o[0]-match.end()<50]
        if not before or not after:continue
        a,b=before[-1],after[0]
        if a[2]==b[2] or re.search(r'[.!?;\n]',text[a[1]:b[0]]) or UNCERTAIN.search(text[a[0]:b[1]]):continue
        result.add(canonical_relation(a[2],match[1] or match[2],b[2]))
    for i,(start,end,name) in enumerate(objects):
        limit=min(len(text),objects[i+1][0] if i+1<len(objects) else len(text),end+85)
        suffix=text[end:limit]
        suffix=re.split(r'[.!?;\n]',suffix,1)[0]
        p=POSITION.search(suffix)
        if not p or UNCERTAIN.search(text[start:end]+suffix[:p.end()]) or re.match(r'\s+of\b',suffix[p.end():]):continue
        horizontal=p[2] if p[2] in ('left','right') else None
        vertical=p[1] or (p[2] if p[2] in ('top','bottom') else None)
        for axis,value in (('x',horizontal),('y',vertical)):
            if value:positions.setdefault((name,axis),set()).add({'upper':'top','lower':'bottom'}.get(value,value))
    for (a,axis),values in positions.items():
        if len(values)!=1:continue
        value=next(iter(values))
        for (b,other_axis),other in positions.items():
            if a==b or axis!=other_axis or len(other)!=1:continue
            if value=='left' and other=={'right'}:result.add((a,'left',b))
            if value=='top' and other=={'bottom'}:result.add((a,'above',b))
    return result


def spatial_agreement(query,sections,names):
    wanted=spatial_relations(query,names)
    if not wanted:return 0.
    observed=set().union(*(spatial_relations(section,names) for section in sections))
    score=0
    for a,relation,b in wanted:
        support=(a,relation,b) in observed
        conflict=(b,relation,a) in observed
        score+=int(support)-int(conflict)
    return score/len(wanted)


TOPICS={
    'home':(r'\b(live|lives|living|home|reside|resides|address)\b|住|居住|住址',r'\b(live|lives|living|home|reside|resides|moved|address)\b|居住|住址|搬家|住在'),
    'job':(r'\b(job|role|occupation|profession|employer|work)\b|工作|职位|职业',r'\b(job|role|occupation|profession|works?|promoted|employed|employer|joined)\b|工作|职位|职业|升职|入职'),
    'preference':(r'\b(prefer|prefers|preference|favourite|favorite|like|likes)\b|喜欢|偏好|最爱',r'\b(prefers?|preference|favourite|favorite|likes?|dislikes?)\b|喜欢|偏好|最爱'),
    'meeting':(r'\b(meeting|conference)\b|会议|开会',r'\b(meeting|conference)\b|会议|开会'),
}
LATEST=re.compile(r'\b(now|currently|current|latest|present)\b|现在|目前|最新|如今',re.I)
MONTHS={name.casefold():index for index,name in enumerate(calendar.month_name) if name}


def as_of_month(text):
    text=text.casefold()
    day=re.search(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b|(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日',text)
    if day:
        year,month,date=map(int,day.groups()[:3] if day[1] else day.groups()[3:])
        try:return datetime(year,month,date,23,59,59,tzinfo=timezone.utc).timestamp()
        except ValueError:return None
    iso=re.search(r'\b(20\d{2})[-/](0?[1-9]|1[0-2])\b|(20\d{2})年\s*(\d{1,2})月',text)
    if iso:
        year,month=(int(iso[1]),int(iso[2])) if iso[1] else (int(iso[3]),int(iso[4]))
    else:
        match=re.search(r'\b('+'|'.join(MONTHS)+r')\s+(20\d{2})\b',text)
        if not match:return None
        year,month=int(match[2]),MONTHS[match[1]]
    if not 1<=month<=12:return None
    return datetime(year,month,calendar.monthrange(year,month)[1],23,59,59,tzinfo=timezone.utc).timestamp()


def state_adjustments(query,docs,evidence,dense):
    if re.search(r'\b(compare|between|versus|before|after)\b|对比|比较|之前|之后|之间',query,re.I):return {}
    topic=next((patterns for patterns in TOPICS.values() if re.search(patterns[0],query,re.I)),None)
    cutoff=as_of_month(query)
    if not topic or (not LATEST.search(query) and cutoff is None):return {}
    # Only entity-bearing state evidence participates. A newer unrelated upload is not a state update.
    query_fold=query.casefold()
    anchors={entity for doc in docs.values() for entity in doc['entities']
             if re.fullmatch(r'[a-z][a-z0-9_-]{1,40}',entity) and re.search(r'\b'+re.escape(entity)+r'\b',query_fold)}
    anchors-=FUNCTION_WORDS|{'find','where','what','which','current','currently','now','latest'}
    anchors.update(re.findall(r'([\u3400-\u9fff]{2,4}?)(?=现在|目前|最新|的|住在)',query))
    if not anchors:return {}
    candidates={}
    best_dense=max(dense.values(),default=0)
    for identity,doc in docs.items():
        text='\n'.join(evidence.get(identity,{}).get('sections',[]))
        if not text or not re.search(topic[1],text,re.I):continue
        linked=any((a in doc['entities']) if re.fullmatch(r'[a-z0-9_-]+',a) else a in text for a in anchors)
        if not linked or dense.get(identity,0)<best_dense*.5:continue
        try:stamp=datetime.fromisoformat(doc['created'].replace('Z','+00:00')).timestamp()
        except (ValueError,OverflowError):continue
        candidates[identity]=stamp
    eligible={identity:stamp for identity,stamp in candidates.items() if cutoff is None or stamp<=cutoff}
    if not eligible:return {}
    newest=max(eligible.values())
    return {identity:(.16 if stamp==newest else -.02) if (cutoff is None or stamp<=cutoff) else -.12
            for identity,stamp in candidates.items()}
