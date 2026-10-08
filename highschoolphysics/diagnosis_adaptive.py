"""Evidence-led branches. Existing card/session evidence remains immutable."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .diagnosis import dumps, now, fingerprint
from .diagnosis_reasoning import REPORTS, REASONS, CATEGORIES
from .errors import InvalidRequest, StateConflict

CATEGORIES={**CATEGORIES,'knowledge_gap':('基础知识需补查：遗忘或理解困难','先回顾本次受阻动作的概念含义，再用一个不同例子检查；暂不区分遗忘与长期未理解。')}
KINDS={'knowledge','condition','model','plan','formula','calculation','check'}
REPORT_KIND={'knowledge_gap':'knowledge','knowledge_retrieval':'model','condition':'condition','model':'model','plan':'plan','execution':'formula','unsure':'condition'}
RECALL={'knowledge':('forgot','never_understood','not_recalled','unsure'),'condition':('missed','untranslated','term_unclear','unsure'),'model':('forgot','not_recalled','model_unrecognized','unsure'),'plan':('forgot','not_recalled','chain_broken','unsure'),'formula':('forgot','not_recalled','formula_misused','unsure'),'calculation':('arithmetic','formula_misused','unsure'),'check':('unchecked','arithmetic','unsure')}
CATEGORY={'knowledge':'knowledge_unclear','condition':'condition_decoding','model':'model_identification','plan':'knowledge_organization','formula':'formula_application','calculation':'calculation','check':'result_check'}


def validate(content):
    if not isinstance(content,dict) or content.get('version')!='adaptive-v1':raise InvalidRequest('分支诊断版本无效')
    actions=content.get('actions')
    if not isinstance(actions,list) or not 3<=len(actions)<=6:raise InvalidRequest('分支诊断须有3—6个必要动作')
    def probe(p):
        if not isinstance(p,dict):raise InvalidRequest('分支检查无效')
        for key,limit in (('prompt',500),('explanation',800)):
            if not isinstance(p.get(key),str) or not p[key].strip() or len(p[key])>limit:raise InvalidRequest('分支检查文本无效')
        opts=p.get('options');answer=p.get('correct')
        if not isinstance(opts,list) or not 2<=len(opts)<=4 or any(not isinstance(o,str) or not o.strip() or len(o)>300 for o in opts):raise InvalidRequest('分支选项无效')
        if type(answer) is not int or not 0<=answer<len(opts):raise InvalidRequest('分支答案无效')
    for i,a in enumerate(actions):
        probe(a)
        if a.get('kind') not in KINDS or not isinstance(a.get('name'),str) or not 0<len(a['name'])<=100:raise InvalidRequest('动作归类无效')
        if not isinstance(a.get('cue'),str) or not a['cue'].strip() or len(a['cue'])>500:raise InvalidRequest('最小线索无效')
        parents=a.get('prerequisites')
        if not isinstance(parents,list) or any(type(p) is not int or not 0<=p<i for p in parents) or len(set(parents))!=len(parents):raise InvalidRequest('必要动作依赖无效')
        for key in ('foundation','practice','verify'):
            if key in a:probe(a[key])
    for key in ('foundation','practice','verify'):probe(content.get(key))
    for a in [content]+actions:
        if 'practice' in a and 'verify' in a and a['practice']['prompt']==a['verify']['prompt']:raise InvalidRequest('独立再检查必须使用不同题面')
    return content


def migrate(c):
    version=c.execute("select version from app_schema_migrations where feature='adaptive_diagnosis'").fetchone()
    if version and version[0]>21:raise RuntimeError('Adaptive diagnosis schema is newer than this application')
    c.execute('''create table if not exists adaptive_diagnostic_cards(
        card_id text not null references diagnostic_cards(id), version text not null,
        content_json text not null, created_at text not null, primary key(card_id,version))''')
    # Install reviewed content against the exact question fingerprint, never against a title.
    entries=json.loads((Path(__file__).with_name('diagnostic_data')/'adaptive-reviewed-20261008.json').read_text())
    for e in entries:
        content=validate(e['content'])
        for card in c.execute('select id from diagnostic_cards where question_id=? and fingerprint=?',(e['question_id'],e['fingerprint'])).fetchall():
            c.execute('insert or ignore into adaptive_diagnostic_cards values(?,?,?,?)',(card[0],content['version'],dumps(content),now()))
    c.execute("insert or ignore into app_schema_migrations(feature,version) values('adaptive_diagnosis',21)");c.commit()


def content_for(c,card):
    from .learning_graph import effective
    original=json.loads(card['card_json']);reviewed,_,_=effective(c,card)
    if reviewed.get('adaptive'):return reviewed['adaptive']
    # A later teacher correction wins over this separately reviewed supplement.
    if reviewed!=original:return None
    row=c.execute('select content_json from adaptive_diagnostic_cards where card_id=? and version=?',(card['id'],'adaptive-v1')).fetchone()
    if row:return json.loads(row[0])
    return json.loads(card['card_json']).get('adaptive')


def events(c,s):
    return [dict(action=r['action'],**json.loads(r['result_json'])) for r in c.execute('select action,result_json from diagnostic_events where session_id=? order by created_at,id',(s['id'],))]


def route(s,content,evs):
    answers={e['node']:e for e in evs if e['action']=='probe'}
    blocked=next((e for e in evs if e['action']=='probe' and e['node'].startswith('a:') and e['passed'] is not True),None)
    recall=next((e for e in evs if e['action']=='locate'),None)
    if s['self_report']=='time':return None if recall else 'recall'
    if blocked:
        if not recall:return 'recall'
        if s['mode']=='quick':return None
        index=int(blocked['node'][2:]);action=content['actions'][index]
        for parent in action['prerequisites']:
            node='a:'+str(parent)
            if node not in answers:return node
            if answers[node]['passed'] is not True:return None
        if 'foundation' not in answers:return 'foundation'
        if answers['foundation']['passed'] is not True:return None
        if 'cue' not in answers:return 'cue'
        return None
    if s['mode']=='quick':
        kind=REPORT_KIND.get(s['self_report'],'condition')
        start=next((i for i,a in enumerate(content['actions']) if a['kind']==kind),0)
        targets=list(range(start,min(start+2,len(content['actions']))))
    else:targets=list(range(len(content['actions'])))
    for i in targets:
        if 'a:'+str(i) not in answers:return 'a:'+str(i)
    if s['self_report']!='unsure' and not recall:return 'recall'
    return None


def summarize(s,content,evs):
    answers=[e for e in evs if e['action']=='probe'];observed={e['node']:e for e in answers}
    blocked=next((e for e in answers if e['node'].startswith('a:') and e['passed'] is not True),None)
    recall=next((e for e in reversed(evs) if e['action']=='locate'),None)
    correction=next((e for e in reversed(evs) if e['action']=='revise'),None)
    confirmation=next((e.get('confirmation','') for e in reversed(evs) if e['action']=='finish'),'')
    category='insufficient';basis=[];cautions=['这是本次选择表现的原因候选，不能还原考试当时的全过程，也不代表整个知识点的掌握。'];alternatives=[];location=''
    if recall:basis.append('最初作答回顾：'+REASONS[recall['reason']][0])
    if s['self_report']=='time':category='answer_context';cautions.append('作答时间与遗漏不直接判为学科能力不足。')
    elif blocked:
        index=int(blocked['node'][2:]);a=content['actions'][index];location=a['name']+'：'+a['prompt']
        basis.append('本次首次受阻动作：'+a['prompt']+('（选择了说不清）' if blocked['passed'] is None else '（未通过）'))
        parents=[observed.get('a:'+str(i)) for i in a['prerequisites']]
        parent_block=next((p for p in parents if p and p['passed'] is not True),None)
        f=observed.get('foundation');cue=observed.get('cue')
        if parent_block:
            earlier=content['actions'][int(parent_block['node'][2:])];location=earlier['name']+'：'+earlier['prompt']
            basis.append('更前面的必要动作也未通过，后续困难可能受它影响。');alternatives=[CATEGORY[earlier['kind']],'knowledge_unclear']
        elif f and f['passed'] is False:
            category='knowledge_forgotten' if recall and recall['reason']=='forgot' else 'knowledge_unclear' if recall and recall['reason']=='never_understood' else 'knowledge_gap'
            basis.append('相关基础含义检查未通过，先处理基础前提。')
            alternatives=['knowledge_forgotten','knowledge_unclear']
            cautions.append('遗忘还是长期未理解，仍需结合你的回顾和后续检查区分。')
        elif f and f['passed'] is True and all(p and p['passed'] is True for p in parents):
            basis.append('已列出的前置动作及相关基础含义检查通过。')
            if f.get('assisted'):
                cautions.append('此次追问发生在看过讲解之后，不能当作原先就已掌握的独立证据。');category='insufficient'
            cautions.append('基础检查只覆盖一个必要前提，不能排除其他未检查的理解困难。')
            category=CATEGORY[a['kind']]
            if a['kind']=='knowledge':category='insufficient';alternatives=['knowledge_forgotten','knowledge_unclear'];cautions.append('基础识别与原检查表现不一致，不能仅凭一次选择认定遗忘。')
            if cue:
                basis.append('最小线索后的新应用检查：'+('通过' if cue['passed'] is True else '仍未通过' if cue['passed'] is False else '说不清'))
                if cue['passed'] is True and not f.get('assisted') and not blocked.get('assisted') and a['kind'] in ('model','formula') and recall and recall['reason']=='not_recalled':category='knowledge_retrieval';cautions.append('线索帮助了应用；“当时没想到”仍是你的回顾，并非系统已经确认的历史事实。')
                elif cue['passed'] is not True:alternatives=['formula_application','knowledge_unclear'];cautions.append('线索未能支持应用，当前候选仍需复核。')
        else:
            alternatives=list(dict.fromkeys([CATEGORY[a['kind']],'knowledge_unclear','knowledge_retrieval']))
            cautions.append('尚未完成前置理解与应用追问，暂不强行归因；可继续精细诊断。')
        if f and f.get('assisted'):
            alternatives=list(dict.fromkeys(alternatives+[category]));category='insufficient'
    elif answers:
        basis.append('本次已检查的动作没有发现明确障碍。');cautions.append('现在能选择正确关系，不能证明考试时也能独立完成。')
    if correction:basis.append('你的更正：'+REASONS[correction['reason']][0]);cautions.append('更正单独作为学生自述保存，不覆盖检查证据。')
    if confirmation=='different':category='insufficient';cautions.append('你认为候选不符合，暂不采用它。')
    if s['status']=='ended':cautions.append('本次提前退出，未做的检查保持未知。')
    trace=[]
    for i,a in enumerate(content['actions']):
        e=observed.get('a:'+str(i));trace.append(dict(stage=a['name'],label='尚未检查' if not e else '无提示通过' if e['passed'] is True else '说不清' if e['passed'] is None else '仍需检查'))
    title,advice=CATEGORIES[category]
    return dict(category=category,label=title,location=location,strength='分支证据支持的待验证候选' if category not in ('insufficient','answer_context') else '目前线索',evidence=basis,cautions=cautions,alternatives=[CATEGORIES[x][0] for x in alternatives],next_action=advice,trace=trace,confirmation=confirmation,message='学生回顾、当前检查、提示反应和学生更正分别保存。',classification_version='adaptive-v1')


def focused(content,evs):
    blocked=next((e for e in evs if e['action']=='probe' and e['node'].startswith('a:') and e['passed'] is not True),None)
    return content['actions'][int(blocked['node'][2:])] if blocked else content


def state(c,s):
    content=json.loads(s['effective_card_json'])['adaptive'];evs=events(c,s);node=route(s,content,evs) if s['status']=='active' else None
    summary=summarize(s,content,evs);out=dict(protocol_version=3,session_id=s['id'],mode=s['mode'],status=s['status'],self_report=s['self_report'],cursor=s['cursor'],checked_count=sum(e['action']=='probe' for e in evs),summary=summary,assisted=bool(s['assisted_at']),confirmation=summary['confirmation'])
    if node=='recall':
        codes=('not_reached','ran_out','omitted','unsure') if s['self_report']=='time' else RECALL[focused(content,evs).get('kind',REPORT_KIND.get(s['self_report'],'condition'))]
        out['reflection']=dict(prompt='回想原来做题时，最接近刚才这处困难的情况是？',options=[dict(value=k,label=REASONS[k][0]) for k in codes]);return out
    if node:
        if node.startswith('a:'):p=content['actions'][int(node[2:])];stage=p['name'];hint=''
        else:p=focused(content,evs).get('foundation' if node=='foundation' else 'practice',content['foundation' if node=='foundation' else 'practice']);stage='区分基础理解与应用' if node=='foundation' else '最小线索后的新应用';blocked=next(e for e in evs if e['action']=='probe' and e['node'].startswith('a:') and e['passed'] is not True);hint=content['actions'][int(blocked['node'][2:])]['cue'] if node=='cue' else ''
        out['step']=dict(node=node,stage=stage,prompt=p['prompt'],options=p['options'],hint=hint);return out
    # Completed/ended summary reveals explanations, so any later redo is learning, not independent mastery.
    out['findings']=[dict(stage=content['actions'][int(e['node'][2:])]['name'],explanation=content['actions'][int(e['node'][2:])]['explanation']) for e in evs if e['action']=='probe' and e['node'].startswith('a:')]
    practice=next((e for e in evs if e['action']=='practice' or e['action']=='probe' and e.get('node')=='cue'),None)
    focus=focused(content,evs);exercise=focus.get('practice',content['practice']);verify=focus.get('verify',content['verify'])
    out['exercise']=dict(prompt=exercise['prompt'],options=exercise['options'],answered=bool(practice),feedback=exercise['explanation'] if practice else '',passed=practice.get('passed') if practice else None)
    verification=next((e for e in evs if e['action']=='verify'),None)
    due=datetime.fromisoformat(practice['at'])+timedelta(hours=24) if practice else None
    ready=due is not None and datetime.now(timezone.utc)>=due
    out['verification']=dict(ready=ready,available_at=due.isoformat() if due else '',answered=bool(verification),passed=verification.get('passed') if verification else None,feedback=verify['explanation'] if verification else '',**(dict(prompt=verify['prompt'],options=verify['options']) if ready and not verification else {}))
    return out


def api(repo,user,action,p,g,w,data,card,s):
    c=repo.conn;content=content_for(c,card)
    if action=='diagnosis-state':return dict(available=True,state=state(c,s) if s else None,protocol_version=3,reports=REPORTS)
    if action=='diagnosis-start':
        if s and s['protocol_version']!=3:
            # Route explicit mode selection through the same atomic, idempotent event path.
            return api(repo,user,'diagnosis-event',dict(p,event='select-mode',session_id=s['id'],cursor=s['cursor'],request_key=p.get('request_key') or 'upgrade-'+uuid.uuid4().hex),g,w,data,card,s)
        if s:return dict(available=True,state=state(c,s))
        mode=p.get('mode');report=p.get('self_report','unsure')
        if mode not in ('quick','deep') or report not in REPORTS:raise InvalidRequest('请选择诊断方式与最初情况')
        frozen=dict(json.loads(card['card_json']),adaptive=validate(content))
        c.execute('insert or ignore into diagnostic_sessions(id,school_id,student_id,wrong_id,question_id,fingerprint,card_id,mode,self_report,steps_json,created_at,updated_at,effective_card_json,graph_mapping_json,protocol_version) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('ds-'+uuid.uuid4().hex,user['school_id'],user['id'],g['id'],w['question_id'],fingerprint(data),card['id'],mode,report,'[]',now(),now(),dumps(frozen),'[]',3));c.commit()
        s=c.execute('select * from diagnostic_sessions where student_id=? and wrong_id=? and question_id=? and fingerprint=?',(user['id'],g['id'],w['question_id'],fingerprint(data))).fetchone();return dict(available=True,state=state(c,s))
    if action!='diagnosis-event' or not s or s['id']!=p.get('session_id'):raise InvalidRequest('诊断记录无效')
    event=p.get('event');key=p.get('request_key');value={k:p.get(k) for k in ('event','cursor','answer','reason','confirmation','mode','self_report')}
    if event not in ('probe','locate','finish','deepen','select-mode','revise','practice','verify') or not isinstance(key,str) or not 0<len(key)<=100:raise InvalidRequest('诊断提交无效')
    c.execute('begin immediate')
    try:
        s=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone();old=c.execute('select payload_json from diagnostic_events where session_id=? and request_key=?',(s['id'],key)).fetchone()
        if old:
            if old[0]!=dumps(value):raise StateConflict('重复提交内容不一致')
            c.rollback();return dict(available=True,state=state(c,s))
        if type(p.get('cursor')) is not int or p['cursor']!=s['cursor']:raise StateConflict('诊断已更新，请刷新')
        if s['protocol_version']!=3:
            if event!='select-mode' or s['status']!='active' or s['cursor']!=0 or s['assisted_at'] or c.execute('select 1 from diagnostic_events where session_id=?',(s['id'],)).fetchone():raise StateConflict('已有诊断证据不能改写，请继续原记录')
            if p.get('mode') not in ('quick','deep') or p.get('self_report','unsure') not in REPORTS:raise InvalidRequest('诊断方式无效')
            c.execute('update diagnostic_sessions set protocol_version=3,effective_card_json=?,graph_mapping_json=?,steps_json=? where id=?',(dumps(dict(json.loads(card['card_json']),adaptive=content)),'[]','[]',s['id']))
            s=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone()
        frozen=json.loads(s['effective_card_json'])['adaptive'];evs=events(c,s);current=state(c,s);r={'at':now()}
        if event in ('select-mode','deepen'):
            mode='deep' if event=='deepen' else p.get('mode');report=p.get('self_report',s['self_report'])
            if mode not in ('quick','deep') or report not in REPORTS:raise InvalidRequest('诊断方式无效')
            if evs:report=s['self_report']
            c.execute("update diagnostic_sessions set mode=?,self_report=?,status='active' where id=?",(mode,report,s['id']));r.update(mode=mode)
        elif event=='finish':
            confirmation=p.get('confirmation','')
            if confirmation not in ('','agree','different','unsure'):raise InvalidRequest('确认选项无效')
            r['confirmation']=confirmation;c.execute('update diagnostic_sessions set status=? where id=?',('ended' if s['status']=='active' else s['status'],s['id']))
        elif event=='revise':
            if s['status']=='active' or p.get('reason') not in REASONS:raise InvalidRequest('请在结果页选择更正')
            r.update(reason=p['reason'],source='student-correction')
        elif event=='locate':
            if s['status']!='active' or p.get('reason') not in {o['value'] for o in current.get('reflection',{}).get('options',[])}:raise InvalidRequest('请选择当前回顾选项')
            r.update(reason=p['reason'],source='student-recall')
        else:
            if event=='probe':
                if s['status']!='active' or 'step' not in current:raise StateConflict('请先完成当前回顾')
                node=current['step']['node'];q=frozen['actions'][int(node[2:])] if node.startswith('a:') else focused(frozen,evs).get('foundation' if node=='foundation' else 'practice',frozen['foundation' if node=='foundation' else 'practice']);r.update(node=node,source='current-check',assisted=bool(s['assisted_at']) or node=='cue')
            else:
                if s['status']=='active' or s['self_report']=='time':raise InvalidRequest('请先完成学科诊断')
                if event=='practice' and current['exercise']['answered'] or event=='verify' and (not current['verification']['ready'] or current['verification']['answered']):raise StateConflict('练习已完成或独立再检查尚未到期')
                q=focused(frozen,evs).get('practice' if event=='practice' else 'verify',frozen['practice' if event=='practice' else 'verify']);r.update(source='targeted-practice' if event=='practice' else 'delayed-check',assisted=event=='practice')
            answer=p.get('answer')
            if type(answer) is not int or not -1<=answer<len(q['options']):raise InvalidRequest('请选择一项或说不清')
            r.update(passed=None if answer==-1 else answer==q['correct'])
        c.execute('insert into diagnostic_events values(?,?,?,?,?,?,?,?)',('de-'+uuid.uuid4().hex,s['id'],key,event,-1,dumps(value),dumps(r),now()))
        c.execute('update diagnostic_sessions set cursor=cursor+1,updated_at=? where id=?',(now(),s['id']))
        updated=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone();new_evs=events(c,updated);node=route(updated,frozen,new_evs)
        if updated['status']=='active' and node is None:c.execute("update diagnostic_sessions set status='completed' where id=?",(s['id'],))
        # Mark assistance when a cue, result explanation or exercise is actually served.
        checked=any(e['action']=='probe' for e in new_evs)
        if node=='cue' or checked and (node is None or updated['status']!='active') or event=='practice':c.execute('update diagnostic_sessions set assisted_at=coalesce(assisted_at,?) where id=?',(now(),s['id']))
        if event in ('practice','verify','finish') and checked:
            c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
        updated=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone()
        if updated['status']!='active':
            r['classification']=summarize(updated,frozen,new_evs);c.execute('update diagnostic_events set result_json=? where session_id=? and request_key=?',(dumps(r),s['id'],key))
        c.commit()
    except Exception:c.rollback();raise
    return dict(available=True,state=state(c,c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone()))
