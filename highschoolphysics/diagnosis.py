"""Versioned, optional thinking checks; never grades an original response."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib import request
from .errors import InvalidRequest, PermissionDenied, StateConflict

STAGES = {'condition':'条件理解', 'model':'模型与规律', 'plan':'解题步骤', 'execution':'列式与检验'}
PROMPT_VERSION = 'wrong-diagnosis-v3'

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def now():
    return datetime.now(timezone.utc).isoformat()

def migrate(c):
    version=c.execute("select version from app_schema_migrations where feature='first_wrong_diagnosis'").fetchone()
    if version and version[0]>17:raise RuntimeError('Diagnosis schema is newer than this application')
    c.executescript('''
    create table if not exists diagnostic_cards (
      id text primary key, school_id text not null references schools(id), question_id text not null references questions(id),
      fingerprint text not null, input_json text not null, card_json text not null, source text not null,
      created_at text not null, unique(school_id,question_id,fingerprint));
    create table if not exists diagnostic_jobs (
      id text primary key, school_id text not null references schools(id), question_id text not null references questions(id),
      fingerprint text not null, input_json text not null, status text not null,
      attempts integer not null default 0, lease_until text, lease_token text, error_code text not null default '',
      updated_at text not null, unique(school_id,question_id,fingerprint));
    create table if not exists diagnostic_sessions (
      id text primary key, school_id text not null references schools(id), student_id text not null references users(id),
      wrong_id text not null, question_id text not null references questions(id), fingerprint text not null,
      card_id text not null references diagnostic_cards(id), mode text not null, self_report text not null,
      steps_json text not null, cursor integer not null default 0, status text not null default 'active',
      assisted_at text, created_at text not null, updated_at text not null,
      unique(student_id,wrong_id,question_id,fingerprint));
    create table if not exists diagnostic_events (
      id text primary key, session_id text not null references diagnostic_sessions(id), request_key text not null,
      action text not null, step integer not null, payload_json text not null, result_json text not null, created_at text not null,
      unique(session_id,request_key));
    create index if not exists diagnostic_job_status on diagnostic_jobs(status,updated_at);
    create index if not exists diagnostic_student on diagnostic_sessions(student_id,question_id);
    insert or ignore into app_schema_migrations(feature,version) values('first_wrong_diagnosis',17);
    ''')

def compact_document(doc, child_key=''):
    # Exclude provenance and other small questions, but retain the common stem.
    keys=('stem_md','options','answer_md','analysis_md','answer_state','kind','label')
    result={k:doc.get(k) for k in keys if k in doc}
    if child_key:
        part=next((p for p in doc.get('children',[]) if p.get('key')==child_key), None)
        if not part: raise InvalidRequest('题目内容版本不完整')
        result['part']={k:part.get(k) for k in keys if k in part}
    return result

def question_input(repo, qid, snapshot=None, trial=None):
    q=repo.get_question(qid)
    if not q: raise InvalidRequest('题目不存在')
    from .question_bank import content
    from .question_content import snapshot_content
    if snapshot:
        binding=snapshot_content(repo.conn,snapshot['id'],q['school_id'])
    elif trial:
        binding={'document':json.loads(trial['document_json']), 'child_key':trial.get('child_key','')}
    else: binding=content(repo.conn,qid,q['school_id'])
    if binding:
        data={'content':compact_document(binding['document'],binding.get('child_key',''))}
    elif snapshot:
        data={k:snapshot.get(k) for k in ('stem','options_json','answer_json','analysis','grading_rule_json')}
    else:
        data={k:q.get(k) for k in ('stem','options','answer','analysis','question_type')}
    return data

def fingerprint(data):
    return hashlib.sha256(dumps(data).encode()).hexdigest()

def validate_card(card):
    if not isinstance(card,dict) or not isinstance(card.get('steps'),list) or not 3<=len(card['steps'])<=6:
        raise InvalidRequest('诊断卡必须包含 3—6 个检查点')
    clean={'title':str(card.get('title','思维检查'))[:100], 'steps':[]}
    for s in card['steps']:
        if not isinstance(s,dict) or s.get('stage') not in STAGES: raise InvalidRequest('诊断阶段无效')
        options=s.get('options')
        if not isinstance(options,list) or not 2<=len(options)<=4 or any(not isinstance(x,str) or not x.strip() or len(x)>300 for x in options):
            raise InvalidRequest('诊断选项无效')
        correct=s.get('correct')
        if isinstance(correct,bool) or not isinstance(correct,int) or not 0<=correct<len(options): raise InvalidRequest('诊断答案无效')
        for field,limit in (('prompt',500),('explanation',800)):
            if not isinstance(s.get(field),str) or not s[field].strip() or len(s[field])>limit: raise InvalidRequest('诊断文本无效')
        hints=s.get('hints')
        if not isinstance(hints,list) or not 2<=len(hints)<=3 or any(not isinstance(h,str) or not h.strip() or len(h)>500 for h in hints): raise InvalidRequest('提示必须有 2—3 级')
        clean['steps'].append({k:s[k] for k in ('stage','prompt','options','correct','explanation','hints')})
    if 'adaptive' in card:
        from .diagnosis_adaptive import validate
        clean['adaptive']=validate(card['adaptive'])
    return clean

def store_card(repo,qid,data,card,source):
    c=repo.conn;q=repo.get_question(qid);fp=fingerprint(data);card=validate_card(card)
    c.execute('insert or ignore into diagnostic_cards values(?,?,?,?,?,?,?,?)',
              ('dc-'+uuid.uuid4().hex,q['school_id'],qid,fp,dumps(data),dumps(card),source,now()))
    c.execute("update diagnostic_jobs set status='completed',error_code='',updated_at=? where school_id=? and question_id=? and fingerprint=?",(now(),q['school_id'],qid,fp))
    card_id=c.execute('select id from diagnostic_cards where school_id=? and question_id=? and fingerprint=?',(q['school_id'],qid,fp)).fetchone()[0]
    if source.startswith('model:'):
        from .learning_graph import prepare_candidate
        prepare_candidate(repo,card_id)
    return card_id

def enqueue(repo,qid,data):
    q=repo.get_question(qid);fp=fingerprint(data);c=repo.conn
    if c.execute('select 1 from diagnostic_cards where school_id=? and question_id=? and fingerprint=?',(q['school_id'],qid,fp)).fetchone(): return
    configured=c.execute("select 1 from provider_configs where school_id=? and provider_kind='diagnosis' and enabled=1",(q['school_id'],)).fetchone()
    c.execute('insert or ignore into diagnostic_jobs(id,school_id,question_id,fingerprint,input_json,status,updated_at) values(?,?,?,?,?,?,?)',
              ('dj-'+uuid.uuid4().hex,q['school_id'],qid,fp,dumps(data),'queued' if configured else 'waiting_config',now()))


def reconcile(repo):
    for q in repo.conn.execute('select id from questions').fetchall(): enqueue(repo,q['id'],question_input(repo,q['id']))
    # Historical versions remain valid learning targets after a question is edited.
    for s in repo.conn.execute('select * from question_version_snapshots').fetchall():
        enqueue(repo,s['question_id'],question_input(repo,s['question_id'],snapshot=dict(s)))
    if repo.conn.execute("select 1 from sqlite_master where name='student_bank_trials'").fetchone():
        for t in repo.conn.execute('select distinct question_id,document_json,child_key from student_bank_trials').fetchall():
            enqueue(repo,t['question_id'],question_input(repo,t['question_id'],trial=dict(t)))
    repo.conn.commit()


def owned_target(repo,user,p):
    from .student_learning import require_student
    require_student(user)
    from .student_learning import group_for
    g=group_for(repo,user,str(p.get('wrong_id','')))
    qid=str(p.get('question_id') or g['anchor']['question_id'])
    w=next((w for w in g['members'] if w['question_id']==qid),None)
    if not w: raise PermissionDenied('不能诊断其他学生或其他题目')
    if w.get('personal'):
        data=question_input(repo,qid,trial=w)
    else: data=question_input(repo,qid,snapshot=w['snapshot'])
    return g,w,data

def state(c,s):
    if s['protocol_version']==3:
        from .diagnosis_adaptive import state as adaptive_state
        return adaptive_state(c,s)
    if s['protocol_version']==2:
        from .diagnosis_reasoning import state as reasoning_state
        return reasoning_state(c,s)
    from .learning_graph import session_card
    card=session_card(c,s)
    indices=json.loads(s['steps_json']);cursor=s['cursor'];events=c.execute('select action,step,payload_json,result_json from diagnostic_events where session_id=? order by created_at,id',(s['id'],)).fetchall()
    context=next((json.loads(e['payload_json']).get('note','') for e in events if e['action']=='context'),'')
    confirmation=next((json.loads(e['result_json']).get('confirmation','') for e in reversed(events) if e['action']=='finish'),'')
    findings=[]
    for e in events:
        if e['action']=='answer':
            r=json.loads(e['result_json']);st=card['steps'][e['step']]
            maps=json.loads(s['graph_mapping_json'] or '[]')
            target=maps[e['step']]['node_id'] if e['step']<len(maps) else ''
            findings.append({'stage':STAGES[st['stage']], 'passed':r['passed'], 'assisted':r['assisted'], 'explanation':st['explanation'], 'graph_node_id':target})
    result={'session_id':s['id'],'mode':s['mode'],'status':s['status'],'self_report':s['self_report'], 'note':context,'confirmation':confirmation, 'cursor':cursor,'total':len(indices),'findings':findings,'assisted':bool(s['assisted_at']),
            'message':'这是本题思维检查的线索，不能代表整个知识点的掌握程度。'}
    if s['status']=='active' and cursor<len(indices):
        index=indices[cursor];step=card['steps'][index]
        attempts=[e for e in events if e['step']==index and e['action']=='answer']
        hints=[e for e in events if e['step']==index and e['action']=='hint']
        result['step']={'stage':STAGES[step['stage']],'prompt':step['prompt'],'options':step['options'],
                        'hint_level':len(hints),'hint':step['hints'][len(hints)-1] if hints else '',
                        'can_hint':len(hints)<len(step['hints']),'answered':bool(attempts)}
        if attempts:
            result['feedback']={**json.loads(attempts[-1]['result_json']), 'explanation':step['explanation']}
    return result

def api(repo,user,action,p):
    if not isinstance(p,dict): raise InvalidRequest('请求格式无效')
    if action=='diagnosis-start' and p.get('note'):raise InvalidRequest('诊断只需点选，不接收手写说明')
    c=repo.conn;g,w,data=owned_target(repo,user,p);fp=fingerprint(data)
    card=c.execute('select * from diagnostic_cards where school_id=? and question_id=? and fingerprint=?',(user['school_id'],w['question_id'],fp)).fetchone()
    if not card:
        enqueue(repo,w['question_id'],data);c.commit()
        return {'available':False,'message':'本题的诊断正在准备，可以先正常重做或查看解析。'}
    s=c.execute('select * from diagnostic_sessions where student_id=? and wrong_id=? and question_id=? and fingerprint=?',(user['id'],g['id'],w['question_id'],fp)).fetchone()
    from .diagnosis_adaptive import content_for, api as adaptive_api
    adaptive=content_for(c,card)
    requested=p.get('protocol_version',3)
    # Upgrade only after an explicit choice; the adaptive API holds the write lock.
    upgrade=s and s['protocol_version']!=3 and requested==3 and adaptive and action in ('diagnosis-start','diagnosis-event') and (action=='diagnosis-start' or p.get('event')=='select-mode') and s['status']=='active' and s['cursor']==0 and not s['assisted_at'] and not c.execute('select 1 from diagnostic_events where session_id=?',(s['id'],)).fetchone()
    if (s and s['protocol_version']==3) or (not s and requested==3 and adaptive) or upgrade:
        return adaptive_api(repo,user,action,p,g,w,data,card,s)
    if (s and s['protocol_version']==2) or (not s and p.get('protocol_version',2) in (2,3)):
        from .diagnosis_reasoning import api as reasoning_api
        return reasoning_api(repo,user,action,p,g,w,data,card,s)
    if action=='diagnosis-state': return {'available':True,'state':state(c,s) if s else None}
    if action=='diagnosis-start':
        mode=p.get('mode');report=p.get('self_report','unsure')
        if mode not in ('quick','deep') or report not in (*STAGES,'unsure'): raise InvalidRequest('请选择诊断方式与卡点')
        if s: return {'available':True,'state':state(c,s)}
        note=p.get('note','')
        if not isinstance(note,str) or len(note)>300:raise InvalidRequest('思路记录请控制在 300 字内')
        from .learning_graph import effective
        effective_card,mappings,release_id=effective(c,card)
        mappings=[m for m in mappings if m.get('status')!='draft']
        steps=effective_card['steps'];indices=list(range(len(steps)))
        if mode=='quick':
            start=next((i for i,x in enumerate(steps) if x['stage']==report),0)
            indices=indices[start:start+2] or [start]
        try:
            c.execute('insert into diagnostic_sessions(id,school_id,student_id,wrong_id,question_id,fingerprint,card_id,mode,self_report,steps_json,created_at,updated_at,effective_card_json,graph_mapping_json,graph_release_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('ds-'+uuid.uuid4().hex,user['school_id'],user['id'],g['id'],w['question_id'],fp,card['id'],mode,report,dumps(indices),now(),now(),dumps(effective_card),dumps(mappings),release_id))
            c.commit()
        except Exception:
            c.rollback();raise
        s=c.execute('select * from diagnostic_sessions where student_id=? and wrong_id=? and question_id=? and fingerprint=?',(user['id'],g['id'],w['question_id'],fp)).fetchone()
        if note:
            c.execute('insert or ignore into diagnostic_events values(?,?,?,?,?,?,?,?)',('de-'+uuid.uuid4().hex,s['id'],'context','context',-1,dumps({'note':note}),dumps({}),now()));c.commit()
        return {'available':True,'state':state(c,s)}
    if not s or s['id']!=p.get('session_id'): raise PermissionDenied('诊断记录不属于当前学生与题目')
    key=str(p.get('request_key',''))
    if not key or len(key)>100: raise InvalidRequest('缺少提交标识')
    event=str(p.get('event',''));allowed=('answer','hint','next','finish','deepen')
    if action!='diagnosis-event' or event not in allowed: raise InvalidRequest('诊断操作无效')
    value={'event':event,'cursor':p.get('cursor'),'answer':p.get('answer'),'confirmation':p.get('confirmation')}
    c.execute('begin immediate')
    try:
        s=c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone()
        old=c.execute('select * from diagnostic_events where session_id=? and request_key=?',(s['id'],key)).fetchone()
        if old:
            if old['payload_json']!=dumps(value): raise StateConflict('重复请求内容不一致')
            c.rollback();return {'available':True,'state':state(c,s)}
        if s['cursor']!=p.get('cursor'): raise StateConflict('检查步骤已更新，请刷新诊断')
        from .learning_graph import session_card
        steps=session_card(c,s)['steps'];indices=json.loads(s['steps_json']);index=indices[min(s['cursor'],len(indices)-1)]
        result={}
        answered=c.execute("select 1 from diagnostic_events where session_id=? and step=? and action='answer'",(s['id'],index)).fetchone()
        if event=='deepen':
            if s['mode']!='quick': raise InvalidRequest('已是深入诊断')
            # Keep completed quick checks and append only the untested checks.
            used=[e[0] for e in c.execute("select step from diagnostic_events where session_id=? and action='answer'",(s['id'],))]
            remaining=[i for i in range(len(steps)) if i not in used]
            indices=used+remaining
            c.execute("update diagnostic_sessions set mode='deep',steps_json=?,cursor=?,status=? where id=?",(dumps(indices),len(used),'active' if remaining else 'completed',s['id']))
        elif event=='finish':
            confirmation=p.get('confirmation','')
            if confirmation not in ('agree','different','unsure',''):raise InvalidRequest('确认选项无效')
            result={'confirmation':confirmation}
            c.execute("update diagnostic_sessions set status=? where id=?",('completed' if s['cursor']>=len(indices)-1 and answered else 'ended',s['id']))
        else:
            if s['status']!='active': raise StateConflict('诊断已结束')
            if event=='hint':
                if answered: raise InvalidRequest('已完成当前检查')
                count=c.execute("select count(*) from diagnostic_events where session_id=? and step=? and action='hint'",(s['id'],index)).fetchone()[0]
                if count>=len(steps[index]['hints']): raise InvalidRequest('提示已全部展开')
                result={'level':count+1}
                c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
            elif event=='answer':
                answer=p.get('answer')
                if answered:raise StateConflict('当前检查已提交')
                if isinstance(answer,bool) or not isinstance(answer,int) or not 0<=answer<len(steps[index]['options']): raise InvalidRequest('请选择一个检查选项')
                hinted=c.execute("select 1 from diagnostic_events where session_id=? and step=? and action='hint'",(s['id'],index)).fetchone()
                result={'passed':answer==steps[index]['correct'],'assisted':bool(hinted)}
                # Check feedback itself supplies assistance to the subsequent redo.
                c.execute('update diagnostic_sessions set assisted_at=? where id=?',(now(),s['id']))
            elif event=='next':
                if not answered:raise InvalidRequest('请先完成当前检查，或结束诊断')
                cursor=s['cursor']+1
                c.execute('update diagnostic_sessions set cursor=?,status=? where id=?',(cursor,'completed' if cursor>=len(indices) else 'active',s['id']))
        c.execute('insert into diagnostic_events values(?,?,?,?,?,?,?,?)',('de-'+uuid.uuid4().hex,s['id'],key,event,index,dumps(value),dumps(result),now()))
        c.execute('update diagnostic_sessions set updated_at=? where id=?',(now(),s['id']));c.commit()
    except Exception:
        c.rollback();raise
    return {'available':True,'state':state(c,c.execute('select * from diagnostic_sessions where id=?',(s['id'],)).fetchone())}


def assisted_today(c,student,qid):
    from .learning import day,TZ
    rows=c.execute('select assisted_at from diagnostic_sessions where student_id=? and question_id=? and assisted_at is not null',(student,qid))
    return any(day(r[0])==datetime.now(TZ).date() for r in rows)

def panel(wrong_id,members):
    options=''.join('<option value="%s">%s</option>'%(escape(w['question_id'],quote=True),escape(w.get('part_label') or '本题')) for w in members)
    return '<details class="diagnosis-panel" data-diagnosis-wrong="%s"><summary>选择进行错题诊断（可选）</summary><p>粗略诊断：少量检查，先找线索。精细诊断：在受阻处追问前置理解与应用。全程点选，随时可以收起。</p><label>诊断范围<select data-diagnosis-target>%s</select></label><div data-diagnosis-body aria-live="polite"></div></details>'%(escape(wrong_id,quote=True),options)

def provider(c,school):
    return c.execute("select * from provider_configs where school_id=? and provider_kind='diagnosis' and enabled=1 order by updated_at desc limit 1",(school,)).fetchone()

def save_config(repo,user,p):
    repo._require_admin_actor(user['id'])
    from .llm import _chat_completions_endpoint
    endpoint=str(p.get('baseurl','')).strip();model=str(p.get('model','')).strip();secret=str(p.get('apikey','')).strip()
    _chat_completions_endpoint(endpoint)
    from urllib.parse import urlsplit
    parts=urlsplit(endpoint)
    if parts.username or parts.password or parts.query or parts.fragment or len(endpoint)>500 or not model or len(model)>200: raise InvalidRequest('请填写有效的 Base URL 和模型名称')
    old=provider(repo.conn,user['school_id'])
    if not secret and old:secret=repo._provider_secret_store().decrypt(old['secret_ciphertext'])
    if not secret:raise InvalidRequest('首次配置需要填写 API Key')
    cfg=repo.save_provider_config(user['id'],'diagnosis','错题诊断',model,secret,endpoint,True)
    repo.conn.execute("update provider_configs set enabled=0 where school_id=? and provider_kind='diagnosis' and id<>?",(user['school_id'],cfg['id']))
    repo.conn.execute("update diagnostic_jobs set status='queued',attempts=0,error_code='' where school_id=? and status in ('waiting_config','failed')",(user['school_id'],));repo.conn.commit()
    reconcile(repo)
    return {'message':'诊断模型已保存，后台将自动处理缺少诊断卡的题目。'}

def admin_panel(repo,user):
    cfg=provider(repo.conn,user['school_id']);counts={r[0]:r[1] for r in repo.conn.execute('select status,count(*) from diagnostic_jobs where school_id=? group by status',(user['school_id'],))}
    total=repo.conn.execute('select count(distinct question_id) from diagnostic_cards where school_id=?',(user['school_id'],)).fetchone()[0]
    esc=lambda x:escape(str(x or ''),quote=True)
    return '''<section class="provider-config-section" id="diagnosis-config"><h3>错题思维诊断模型</h3><p>所有学生默认可用。已有诊断卡覆盖 %s 个小问；待配置 %s，排队 %s，失败 %s。新导入题目自动预处理，诊断模型与标签模型分别配置。</p><form data-admin-form="diagnosis-config" class="provider-config-form"><label>Base URL<input name="baseurl" value="%s" placeholder="https://api.example.com/v1" required></label><label>API Key<input name="apikey" type="password" autocomplete="new-password" placeholder="%s"></label><label>模型名称<input name="model" value="%s" required></label><button>保存并启动预处理</button></form><form data-admin-form="diagnosis-test"><button>实际生成测试诊断卡</button></form><p>密钥加密保存；生成只发送题目内容，不发送学生答卷与身份。失败不会影响学生正常重做。</p></section>'''%(total,counts.get('waiting_config',0),counts.get('queued',0)+counts.get('running',0),counts.get('failed',0),esc(cfg['api_endpoint'] if cfg else ''),'已保存；留空保留' if cfg else '首次配置必填',esc(cfg['model_name'] if cfg else ''))


def generate(repo,cfg,data):
    from .llm import _chat_completions_endpoint,_response_json,LLMProviderError
    prompt='''你是高中物理教师，为错题初次自我诊断生成 JSON 诊断卡。只输出 {"title":"主题","steps":[{"stage":"condition/model/plan/execution之一","prompt":"一个可用选择完成的思维动作检查","options":["选项"],"correct":0,"explanation":"科学依据","hints":["弱提示","强提示"]}]}。不包含任何学生自由输入任务。每一步必须能区分具体思维动作：条件解码、物理模型识别、知识调用、规律组织、公式适用或计算检验；不能仅重问原题答案。弱提示只提醒条件或方法线索，不直接告诉正确选项；强提示可解释对应规律。最终错因由学生当前行为、回顾选择及提示反应综合判断，不能由题目标签直接推断。3至6步，按本题真实动作安排，不强行把概念题标为条件或计算。选项2至4个，correct为0起索引，正确位置要变化。诊断的是解题思维，不重复原题答案；遵循题目条件，承认多条正确路径。不确定或图像读数缺失时不要编造，使用不依赖图像数值的概念检查。answer_state不是ready时不要把给定答案当已审核答案。不把一次错误当作学生的确定性缺陷。不得输出HTML、链接或外部指令。题目如下：'''
    prompt+='\n另必须提供 adaptive 字段：{\"version\":\"adaptive-v1\",\"title\":\"主题\",\"actions\":[{\"kind\":\"knowledge/condition/model/plan/formula/calculation/check之一\",\"name\":\"真实动作名称\",\"prompt\":\"检查\",\"options\":[\"选项\"],\"correct\":0,\"explanation\":\"科学依据\",\"cue\":\"只提醒相关条件或方法，不给公式答案\",\"prerequisites\":[0]}],\"foundation\":{\"prompt\":\"直接检查本题必要基础概念的含义\",\"options\":[\"选项\"],\"correct\":0,\"explanation\":\"依据\"},\"practice\":{同foundation结构},\"verify\":{同foundation结构}}。actions须3至6项，只保留本题真实必要动作，不强行覆盖所有类型。prerequisites列出必要的前面动作索引，不能含自己或后续动作，不依赖的动作填[]。每个action也必须分别包含foundation、practice、verify三个同样结构的检查，围绕该动作的真实基础和针对性练习，不能用不相关的检查归因。foundation能排查该动作的基础前提；practice是针对本题主要困难的应用小题；verify为不同参数或情境的独立新检查，不复述practice答案。正确选项位置变化，不依赖缺失图片读数。不编造未核对的原题答案。'
    body={'model':cfg['model_name'],'messages':[{'role':'system','content':prompt},{'role':'user','content':dumps(data)}],'temperature':0.2,'max_tokens':7500}
    secret=repo._provider_secret_store().decrypt(cfg['secret_ciphertext'])
    budget=repo.provider_budget_status(cfg['created_by'],cfg['id'],input_units=len(dumps(data)),output_units=7500)
    if not budget.get('allowed',True):raise LLMProviderError('budget_exceeded','模型调用预算不足')
    req=request.Request(_chat_completions_endpoint(cfg['api_endpoint']),data=dumps(body).encode(),headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'},method='POST')
    try:
        with request.urlopen(req,timeout=60) as response:
            raw=response.read(512*1024+1)
        if len(raw)>512*1024:raise ValueError('oversize')
        output=json.loads(raw);card=validate_card(_response_json(output['choices'][0]['message']['content']))
        if 'adaptive' not in card or any(any(k not in a for k in ('foundation','practice','verify')) for a in card['adaptive']['actions']):raise InvalidRequest('模型未提供逐动作分支诊断内容')
    except Exception as exc:
        # Use only local messages: upstream bodies may contain credentials or data.
        code='diagnosis_provider_failed'
        message='诊断调用或内容校验失败，请检查服务与模型'
        from urllib.error import HTTPError, URLError
        if isinstance(exc, HTTPError):
            code='diagnosis_http_%s' % exc.code
            messages={401:'API Key 无效或已失效',403:'API Key 无权调用此模型',
                      404:'模型或 API 地址不存在',429:'调用过于频繁或账户额度不足',
                      503:'当前模型不可用，请核对服务商的可用模型 ID 或稍后重试'}
            message='诊断模型服务返回 HTTP %s：%s' % (exc.code,messages.get(exc.code,'服务商拒绝请求或服务异常，请核对地址、模型和账户状态'))
        elif isinstance(exc, (TimeoutError, URLError)):
            code='diagnosis_network_error'
            message='连接诊断模型服务失败或超时，请稍后重试'
        repo.record_provider_usage(cfg['created_by'],cfg['id'],'diagnosis',PROMPT_VERSION,outcome='failed',error_category=code,detail={'message':message})
        raise LLMProviderError(code,message) from exc
    usage=output.get('usage',{})
    repo.record_provider_usage(cfg['created_by'],cfg['id'],'diagnosis',PROMPT_VERSION,outcome='success',input_units=usage.get('prompt_tokens',0),output_units=usage.get('completion_tokens',0),detail={'message':'诊断结构校验通过'})
    return card


def test_config(repo,user):
    from .llm import LLMProviderError
    repo._require_admin_actor(user['id']);cfg=provider(repo.conn,user['school_id'])
    if not cfg:raise InvalidRequest('请先保存诊断模型配置')
    try:
        generate(repo,cfg,{'stem':'质量 m 的物体在水平面上匀速直线运动。讨论合力与各个力的关系。'})
    except LLMProviderError as exc:
        raise InvalidRequest(str(exc)) from exc
    return {'message':'已实际调用模型并获得符合诊断结构的检查卡。'}


def run_once(db_path):
    from .db import connect,initialize_database
    from .repository import PhysicsRepository
    c=connect(db_path)
    try:
        initialize_database(c);repo=PhysicsRepository(c);reconcile(repo)
        c.execute('begin immediate')
        c.execute("update diagnostic_jobs set status=case when attempts>=3 then 'failed' else 'queued' end,error_code='lease_expired',lease_token=null where status='running' and lease_until<?",(now(),))
        c.execute("update diagnostic_jobs set status='queued' where status='waiting_config' and school_id in (select school_id from provider_configs where provider_kind='diagnosis' and enabled=1)")
        job=c.execute("select j.* from diagnostic_jobs j where j.status='queued' and exists(select 1 from provider_configs p where p.school_id=j.school_id and p.provider_kind='diagnosis' and p.enabled=1) order by j.updated_at limit 1").fetchone()
        if not job:c.commit();return None
        token=uuid.uuid4().hex
        c.execute("update diagnostic_jobs set status='running',attempts=attempts+1,lease_until=?,lease_token=?,updated_at=? where id=?",((datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat(),token,now(),job['id']));c.commit()
        try:
            cfg=provider(c,job['school_id']);data=json.loads(job['input_json']);card=generate(repo,cfg,data)
            c.execute('begin immediate')
            claimed=c.execute("select 1 from diagnostic_jobs where id=? and status='running' and lease_token=?",(job['id'],token)).fetchone()
            if claimed:store_card(repo,job['question_id'],data,card,'model:'+cfg['model_name'])
            c.commit()
        except Exception:
            c.rollback();c.execute("update diagnostic_jobs set status=case when attempts>=3 then 'failed' else 'queued' end,error_code='generation_failed',updated_at=? where id=? and lease_token=?",(now(),job['id'],token));c.commit()
        return {'diagnostic_job_id':job['id']}
    finally:c.close()


def backfill(repo,path=None):
    manifest=json.loads(Path(path or Path(__file__).with_name('diagnostic_data')/'reviewed-20261007.json').read_text())
    added=0;skipped=0
    for item in manifest:
        q=repo.get_question(item['question_id'])
        if not q:skipped+=1;continue
        current=question_input(repo,q['id'])
        # The reviewed card is tied to the exact reviewed content, never to ID alone.
        if fingerprint(current)!=item['fingerprint']:skipped+=1;continue
        store_card(repo,q['id'],current,item['card'],'assistant-reviewed');added+=1
    repo.conn.commit();reconcile(repo)
    return {'covered':added,'skipped':skipped}


if __name__=='__main__':
    import argparse
    from .db import connect,initialize_database
    from .repository import PhysicsRepository
    parser=argparse.ArgumentParser();parser.add_argument('--db',required=True);parser.add_argument('--backfill',action='store_true');args=parser.parse_args()
    conn=connect(args.db);initialize_database(conn)
    try:print(dumps(backfill(PhysicsRepository(conn)) if args.backfill else {}))
    finally:conn.close()
