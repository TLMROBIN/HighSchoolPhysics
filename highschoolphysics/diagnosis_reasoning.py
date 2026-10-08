"""Selection-only reasoning diagnosis. Behaviour, recall and help remain distinct."""
import json
import uuid
from .diagnosis import STAGES,dumps,now
from .errors import InvalidRequest,StateConflict

PROTOCOL=2
REPORTS={'knowledge_gap':'相关知识不记得或一直没弄懂','knowledge_retrieval':'知识记得，但当时没想到用它','condition':'题意或关键条件没理解清楚','model':'不知道对应哪种物理模型','plan':'知道规律，但步骤接不起来','execution':'列式、计算或检查结果出了问题','time':'没做到、时间不够或漏答','unsure':'说不清最先卡在哪里'}
REPORT_STAGE={'knowledge_gap':'model','knowledge_retrieval':'model','condition':'condition','model':'model','plan':'plan','execution':'execution','time':'condition','unsure':'condition'}
CATEGORIES={
 'knowledge_forgotten':('知识遗忘','先用一张概念卡回忆规律、公式及适用条件，再遮住提示试一次。'),
 'knowledge_unclear':('基础知识没有理解清楚','先辨清概念的含义、对象与适用条件，再做一个单一规律的小练习。'),
 'knowledge_retrieval':('知识记得，但没有主动调用','先练“只判断用什么规律、不计算”，把题目特征与已知规律连起来。'),
 'condition_omission':('漏读或忽略关键条件','先逐条圈出条件，并说明每条条件会限制哪个物理量。'),
 'condition_decoding':('读到了条件，但没转成物理含义','把关键条件改写成受力、运动、约束或守恒条件，再列式。'),
 'model_identification':('物理模型识别卡住','先选研究对象，画运动或受力示意图，说明能采用该模型的条件。'),
 'knowledge_organization':('知识知道，但解题链没有接起来','从要求的量倒推需要哪些中间量，再按依赖关系连接各条规律。'),
 'formula_application':('公式适用、对象或列式卡住','先说清公式适用条件和每个符号对应的对象，再代入本题。'),
 'calculation':('计算、符号或单位处理出错','保留符号推导，逐步检查方向、量纲、单位换算和数值。'),
 'result_check':('结果检查没有发现问题','最后核对单位、数量级、方向与题目边界条件。'),
 'answer_context':('时间安排或作答遗漏','先区分没做到、时间不够和漏答；这类情况不能直接归为知识或能力不足。'),
 'insufficient':('暂未定位到明确卡点','可以改为精细诊断继续排查，或先完成一次独立重做。')}
# Codes are categorical records; students never type an explanation.
REASONS={
 'forgot':('这一步的知识当时不记得了','knowledge_forgotten'),
 'never_understood':('这一步的概念一直没理解清楚','knowledge_unclear'),
 'not_recalled':('知识能想起，但当时没有想到用在这里','knowledge_retrieval'),
 'missed':('当时漏看或忽略了这条条件','condition_omission'),
 'untranslated':('看到了条件，但不懂它对应的物理含义','condition_decoding'),
 'term_unclear':('条件中的物理概念本身不清楚','knowledge_unclear'),
 'model_unrecognized':('条件理解了，但不知道该用什么模型','model_identification'),
 'chain_broken':('相关规律都知道，但不知道先后怎样连接','knowledge_organization'),
 'formula_misused':('公式记得，但适用条件、对象或列式不对','formula_application'),
 'arithmetic':('思路和公式清楚，但计算、符号或单位出错','calculation'),
 'unchecked':('算出了结果，但没发现它不符合题目条件','result_check'),
 'not_reached':('当时还没做到这道题','answer_context'),
 'ran_out':('开始做了，但时间不够','answer_context'),
 'omitted':('会做，但漏答或漏写结果','answer_context'),
 'unsure':('还说不清','insufficient')}
REFLECTIONS={
 'condition':('missed','untranslated','term_unclear','unsure'),
 'model':('forgot','never_understood','not_recalled','model_unrecognized','unsure'),
 'plan':('forgot','not_recalled','chain_broken','unsure'),
 'execution':('formula_misused','arithmetic','unchecked','unsure'),
 'context':('not_reached','ran_out','omitted','unsure')}
DEFAULT_CATEGORY={'condition':'condition_decoding','model':'model_identification','plan':'knowledge_organization','execution':'formula_application'}

def migrate(c):
    row=c.execute("select version from app_schema_migrations where feature='reasoning_diagnosis'").fetchone()
    if row and row[0]>19:raise RuntimeError('Reasoning diagnosis schema is newer than this application')
    if 'protocol_version' not in {r[1] for r in c.execute('pragma table_info(diagnostic_sessions)')}:
        c.execute('alter table diagnostic_sessions add column protocol_version integer not null default 1')
    if not row:
        c.execute("""update diagnostic_sessions set protocol_version=2
            where status='active' and cursor=0 and assisted_at is null
            and not exists(select 1 from diagnostic_events e where e.session_id=diagnostic_sessions.id)""")
    c.execute("insert or ignore into app_schema_migrations(feature,version) values('reasoning_diagnosis',19)");c.commit()

def events(c,s):return [dict(r) for r in c.execute('select * from diagnostic_events where session_id=? order by created_at,id',(s['id'],))]
def result(e):return json.loads(e['result_json'])
def payload(e):return json.loads(e['payload_json'])

def summarize(s,card,evs):
    indices=json.loads(s['steps_json']);maps=json.loads(s['graph_mapping_json'] or '[]');trace=[]
    for i in indices:
        attempts=[e for e in evs if e['step']==i and e['action']=='answer'];hints=[e for e in evs if e['step']==i and e['action']=='hint']
        label='尚未检查';first=None;passed=None
        if attempts:
            first=result(attempts[0])['passed'];passed=result(attempts[-1])['passed']
            label='无提示通过' if first is True else '提示后推进' if passed is True else '选择了说不清' if passed is None else '仍需检查'
        trace.append(dict(index=i,stage=STAGES[card['steps'][i]['stage']],stage_key=card['steps'][i]['stage'],label=label,first_passed=first,passed=passed,hints=len(hints),graph_node_id=maps[i]['node_id'] if i<len(maps) else ''))
    reflections=[e for e in evs if e['action'] in ('locate','revise')];reflection=reflections[-1] if reflections else None
    checked=[t for t in trace if t['label']!='尚未检查'];blocked=[t for t in checked if t['first_passed'] is not True]
    earliest=min(blocked,key=lambda t:('condition','model','plan','execution').index(t['stage_key'])) if blocked else None
    code=payload(reflection).get('reason') if reflection else None
    category=REASONS.get(code,('','insufficient'))[1];basis=[];cautions=[];strength='待排查'
    if code and code!='unsure':
        basis.append('你的回顾选择：'+REASONS[code][0]);strength='回顾线索'
    else:
        category='insufficient'
        if earliest:
            category='insufficient'
            basis.append('首次需要排查的检查：'+card['steps'][earliest['index']]['prompt'])
            cautions.append('目前只定位了环节，还不能区分知识遗忘、知识调用或其他原因。')
    if reflection and code!='unsure':
        observed=next((t for t in checked if t['index']==reflection['step']),None)
        if observed:
            basis.append(STAGES[card['steps'][observed['index']]['stage']]+'：'+observed['label'])
            if observed['first_passed'] is not True and reflection['action']=='locate':strength='回顾与检查相互支持'
            if observed['first_passed'] is True and category in ('knowledge_forgotten','knowledge_unclear'):
                cautions.append('你当前能无提示完成这一步，知识遗忘或不理解的说法仍需核对。');strength='回顾线索，需核对'
            if category=='knowledge_retrieval' and observed['passed'] is not True:
                cautions.append('这次还没有确认你能运用该知识，不能排除基础理解不清。');strength='回顾线索，需核对'
            if observed['hints']:
                basis.append('最先帮助你推进的是提示 '+str(result(next(e for e in evs if e['action']=='answer' and e['step']==observed['index'] and result(e)['passed'] is True))['hint_level']) if observed['passed'] is True else '本次使用了提示，但仍未确认能够推进。')
        if category=='answer_context':cautions.append('这里只记录作答情境，不判定你的学科能力。')
    if earliest and reflection and category not in ('answer_context','insufficient'):
        stage=card['steps'][reflection['step']]['stage'] if reflection['step']>=0 else ''
        if stage and ('condition','model','plan','execution').index(earliest['stage_key'])<('condition','model','plan','execution').index(stage):
            cautions.append('更前面的'+earliest['stage']+'也未通过，后面的困难可能受它影响，建议先回看前一环。')
    if s['status']=='ended':cautions.append('本次提前结束，尚未检查的环节保持未知。')
    confirmation=next((result(e).get('confirmation','') for e in reversed(evs) if e['action']=='finish'),'')
    if reflections and reflections[-1]['action']=='revise' and next((i for i in range(len(evs)-1,-1,-1) if evs[i]['action']=='revise'),-1)>next((i for i in range(len(evs)-1,-1,-1) if evs[i]['action']=='finish'),-1):confirmation=''
    if confirmation=='different':strength='学生认为不符合';cautions.insert(0,'你认为这项定位不符合实际，暂不采用，可点选更符合的情况。')
    title,action=CATEGORIES[category]
    return dict(location=earliest['stage'] if earliest else '',category=category,label=title,strength=strength,evidence=basis,cautions=cautions,next_action=action,trace=trace,confirmation=confirmation,classification_version='reasoning-v2',message='这是根据你的回顾、当前选择和提示反应得到的本题卡点线索。')

def state(c,s):
    from .learning_graph import session_card
    card=session_card(c,s);evs=events(c,s);indices=json.loads(s['steps_json']);cursor=s['cursor'];summary=summarize(s,card,evs)
    if s['status']!='active':
        saved=next((result(e).get('classification') for e in reversed(evs) if result(e).get('classification')),None)
        if saved:summary=saved
    findings=[dict(stage=t['stage'],passed=t['passed'] is True,assisted=t['hints']>0,explanation=card['steps'][t['index']]['explanation'] if s['status']!='active' or t['passed'] is True and not (s['cursor']<len(indices) and indices[s['cursor']]==t['index'] and not any(e['action']=='locate' for e in evs) and s['self_report']!='unsure') else '',graph_node_id=t['graph_node_id']) for t in summary['trace'] if t['label']!='尚未检查']
    out=dict(protocol_version=PROTOCOL,session_id=s['id'],mode=s['mode'],status=s['status'],self_report=s['self_report'],cursor=cursor,total=len(indices),checked_count=sum(t['label']!='尚未检查' for t in summary['trace']),findings=findings,summary=summary,assisted=bool(s['assisted_at']),message=summary['message'],confirmation=summary['confirmation'])
    selection=next((e for e in reversed(evs) if e['action'] in ('select-mode','deepen')),None)
    out['prior_checks']=result(selection).get('preserved_checks',0) if selection else 0
    if s['status']!='active' or cursor>=len(indices):return out
    if s['self_report']=='time' and not any(e['action']=='locate' for e in evs):
        out['reflection']=dict(prompt='当时没有完成这道题，更接近哪种情况？',options=[dict(value=k,label=REASONS[k][0]) for k in REFLECTIONS['context']]);return out
    index=indices[cursor];st=card['steps'][index];attempts=[e for e in evs if e['step']==index and e['action']=='answer'];hints=[e for e in evs if e['step']==index and e['action']=='hint']
    answered=bool(attempts);passed=result(attempts[-1])['passed'] if answered else None
    # One targeted recall question; do it before hints or explanatory feedback.
    need_recall=answered and not any(e['action']=='locate' for e in evs) and (passed is not True or s['self_report']!='unsure')
    if need_recall:
        out['reflection']=dict(prompt='回想最初做这题时，关于刚才这一步，更接近你的情况是？',options=[dict(value=k,label=REASONS[k][0]) for k in REFLECTIONS[st['stage']]])
    hint_total=sum(e['action']=='hint' for e in evs)
    ready_retry=answered and passed is not True and hints and result(attempts[-1]).get('hint_level',0)<len(hints)
    out['step']=dict(stage=STAGES[st['stage']],prompt=st['prompt'],options=st['options'],answered=answered,passed=passed,hint_level=len(hints),hint=st['hints'][len(hints)-1] if hints else '',can_hint=answered and passed is not True and not need_recall and hint_total<2 and len(hints)<min(2,len(st['hints'])),can_answer=not answered or bool(ready_retry),can_next=answered and not need_recall)
    if answered:
        out['feedback']=dict(passed=passed,assisted=bool(hints),explanation=st['explanation'] if passed is True and not need_recall else '',message='这一步目前还没定位清楚。' if passed is None else '这一步已通过。' if passed else '这一步未通过。先辨清卡点，再选择是否需要提醒。')
    return out

def api(repo,user,action,p,g,w,data,card,s):
    from .diagnosis import fingerprint
    from .learning_graph import effective,session_card
    c=repo.conn;fp=fingerprint(data)
    if action=='diagnosis-state':return {'available':True,'state':state(c,s) if s else None,'protocol_version':PROTOCOL,'reports':REPORTS}
    if action=='diagnosis-start':
        if s:return {'available':True,'state':state(c,s)}
        mode=p.get('mode');report=p.get('self_report','unsure')
        if mode not in ('quick','deep') or report not in REPORTS:raise InvalidRequest('请选择诊断方式与最初的情况')
        if p.get('note'):raise InvalidRequest('诊断只需点选，不接收手写说明')
        content,maps,rid=effective(c,card);maps=[m for m in maps if m.get('status')!='draft'];indices=list(range(len(content['steps'])))
        if mode=='quick':
            start=next((i for i,t in enumerate(content['steps']) if t['stage']==REPORT_STAGE[report]),0);indices=indices[start:start+2] or [start]
        c.execute('insert or ignore into diagnostic_sessions(id,school_id,student_id,wrong_id,question_id,fingerprint,card_id,mode,self_report,steps_json,created_at,updated_at,effective_card_json,graph_mapping_json,graph_release_id,protocol_version) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('ds-'+uuid.uuid4().hex,user['school_id'],user['id'],g['id'],w['question_id'],fp,card['id'],mode,report,dumps(indices),now(),now(),dumps(content),dumps(maps),rid,PROTOCOL));c.commit()
        s=c.execute('select * from diagnostic_sessions where student_id=? and wrong_id=? and question_id=? and fingerprint=?',(user['id'],g['id'],w['question_id'],fp)).fetchone()
        return {'available':True,'state':state(c,s)}
    if action!='diagnosis-event' or not s or s['id']!=p.get('session_id'):raise InvalidRequest('诊断操作或记录无效')
    key=p.get('request_key');event=p.get('event')
    if not isinstance(key,str) or not key or len(key)>100:raise InvalidRequest('缺少提交标识')
    if event not in ('answer','locate','revise','hint','next','finish','deepen','select-mode'):raise InvalidRequest('诊断操作无效')
    value={k:p.get(k) for k in ('event','cursor','answer','reason','confirmation')}
    if event=='select-mode':value.update(mode=p.get('mode'),self_report=p.get('self_report'))
    c.execute('begin immediate')
    try:
        s=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone();old=c.execute('select * from diagnostic_events where session_id=? and request_key=?',(s['id'],key)).fetchone()
        if old:
            if old['payload_json']!=dumps(value):raise StateConflict('重复请求内容不一致')
            c.rollback();return {'available':True,'state':state(c,s)}
        if p.get('cursor')!=s['cursor']:raise StateConflict('诊断步骤已更新，请刷新')
        current=state(c,s);content=session_card(c,s);indices=json.loads(s['steps_json']);index=indices[min(s['cursor'],len(indices)-1)];step=content['steps'][index];evs=events(c,s);r={}
        if event=='select-mode':
            mode=p.get('mode');report=p.get('self_report','unsure')
            if mode not in ('quick','deep') or report not in REPORTS:raise InvalidRequest('请选择诊断方式与最初的情况')
            if current.get('reflection') and current.get('step'):
                raise StateConflict('上次检查还有一个卡点选择未完成，请先继续已有诊断完成该选择。')
            used=list(dict.fromkeys(e['step'] for e in evs if e['action']=='answer'))
            if used:report=s['self_report']
            remaining=[i for i in range(len(content['steps'])) if i not in used]
            if mode=='quick':
                start=next((i for i,t in enumerate(content['steps']) if t['stage']==REPORT_STAGE[report]),0)
                remaining=([i for i in remaining if i>=start]+[i for i in remaining if i<start])[:2]
            r={'mode':mode,'preserved_checks':len(used),'new_checks':len(remaining)}
            c.execute('update diagnostic_sessions set mode=?,self_report=?,steps_json=?,cursor=?,status=? where id=?',(mode,report,dumps(used+remaining),len(used),'active' if remaining else 'completed',s['id']))
        elif event=='finish':
            confirmation=p.get('confirmation','')
            if confirmation not in ('agree','different','unsure',''):raise InvalidRequest('确认选项无效')
            r={'confirmation':confirmation};complete=s['status']=='completed' or current.get('step',{}).get('can_next') and s['cursor']==len(indices)-1
            c.execute('update diagnostic_sessions set status=? where id=?',('completed' if complete else 'ended',s['id']))
            if any(e['action']=='answer' for e in evs):
                c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
        elif event=='deepen':
            if s['mode']!='quick':raise InvalidRequest('已是精细诊断')
            used=list(dict.fromkeys(e['step'] for e in evs if e['action']=='answer'));remaining=[i for i in range(len(content['steps'])) if i not in used]
            r={'preserved_checks':len(used),'new_checks':len(remaining)}
            c.execute('update diagnostic_sessions set mode=?,steps_json=?,cursor=?,status=? where id=?',('deep',dumps(used+remaining),len(used),'active' if remaining else 'completed',s['id']))
        elif event=='revise':
            if s['status']=='active':raise InvalidRequest('请先结束诊断，再更正分类')
            if p.get('reason') not in REASONS:raise InvalidRequest('请选择一种情况')
            r={'reason':p['reason'],'source':'student-correction'}
        else:
            if s['status']!='active':raise StateConflict('诊断已结束')
            if event=='locate':
                allowed={x['value'] for x in current.get('reflection',{}).get('options',[])}
                if p.get('reason') not in allowed:raise InvalidRequest('请选择当前定位选项')
                r={'reason':p['reason'],'source':'student-recall'}
                if current.get('step',{}).get('passed') is True:
                    c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
                if s['self_report']=='time':c.execute("update diagnostic_sessions set status='completed' where id=?",(s['id'],));index=-1
            elif event=='answer':
                if not current.get('step',{}).get('can_answer') or current.get('reflection'):raise StateConflict('请先完成当前定位，或请求新的提示后再试')
                answer=p.get('answer')
                if isinstance(answer,bool) or not isinstance(answer,int) or not -1<=answer<len(step['options']):raise InvalidRequest('请选择一个检查选项或说不清')
                hints=sum(e['action']=='hint' and e['step']==index for e in evs)
                r={'passed':None if answer==-1 else answer==step['correct'],'assisted':bool(hints),'hint_level':hints}
                if r['passed'] is True and (s['self_report']=='unsure' or any(e['action']=='locate' for e in evs)):
                    c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
            elif event=='hint':
                if not current.get('step',{}).get('can_hint'):raise InvalidRequest('请先点选卡点；本次最多两次提示')
                level=current['step']['hint_level']+1;r={'level':level,'kind':'weak_cue' if level==1 else 'explicit_explanation'}
                c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
            elif event=='next':
                if not current.get('step',{}).get('can_next'):raise InvalidRequest('请先完成当前检查与卡点选择')
                cursor=s['cursor']+1;c.execute('update diagnostic_sessions set cursor=?,status=? where id=?',(cursor,'completed' if cursor>=len(indices) else 'active',s['id']))
                # Finishing a failed check reveals its explanation in the eventual evidence summary.
                c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
        c.execute('insert into diagnostic_events values(?,?,?,?,?,?,?,?)',('de-'+uuid.uuid4().hex,s['id'],key,event,index,dumps(value),dumps(r),now()));c.execute('update diagnostic_sessions set updated_at=? where id=?',(now(),s['id']))
        updated=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone()
        if updated['status']!='active':
            if any(e['action']=='answer' for e in events(c,updated)):
                c.execute('update diagnostic_sessions set assisted_at=coalesce(assisted_at,?) where id=?',(now(),s['id']))
            r['classification']=summarize(updated,content,events(c,updated))
            c.execute('update diagnostic_events set result_json=? where session_id=? and request_key=?',(dumps(r),s['id'],key))
        c.commit()
    except Exception:c.rollback();raise
    return {'available':True,'state':state(c,c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone())}
