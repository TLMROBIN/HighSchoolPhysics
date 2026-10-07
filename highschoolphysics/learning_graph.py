"""School curriculum graph, immutable publications and privately scoped observations."""
import hashlib
import json
import uuid
from html import escape as esc
from pathlib import Path
from urllib.parse import quote
from .diagnosis import dumps, now, validate_card
from .errors import InvalidRequest, PermissionDenied, StateConflict

KINDS={'knowledge':'知识点','objective':'学习目标','ability':'能力','literacy':'素养'}
RELATIONS={'contains':'包含','prerequisite':'必要前置','supports':'有助于理解','related':'相关','confusable':'容易混淆','transfer':'方法迁移','uses_ability':'运用能力','supports_literacy':'体现素养'}
STATUS={'draft':'模型目标候选，待教师审核','curated':'系统整理，待教师确认','approved':'教师已确认','rejected':'已排除'}

def migrate(c):
    v=c.execute("select version from app_schema_migrations where feature='connected_learning_graph'").fetchone()
    if v and v[0]>18:raise RuntimeError('Learning graph schema is newer than this application')
    c.executescript('''create table if not exists learning_graph_releases (
      id text primary key, school_id text not null references schools(id), version integer not null,
      graph_json text not null, author_id text references users(id), source text not null, created_at text not null,
      unique(school_id,version));
      create trigger if not exists graph_release_no_update before update on learning_graph_releases
      begin select raise(abort,'Graph publications are immutable'); end;
      insert or ignore into app_schema_migrations(feature,version) values('connected_learning_graph',18);''')
    columns={r[1] for r in c.execute('pragma table_info(diagnostic_sessions)')}
    for column in ('effective_card_json','graph_mapping_json','graph_release_id'):
        if column not in columns:c.execute('alter table diagnostic_sessions add column '+column+' text')
    c.commit()

def catalog(c,school):
    nodes=[]
    for kind,table in (('knowledge','knowledge_nodes'),('ability','ability_tags'),('literacy','literacy_tags')):
        for r in c.execute('select * from '+table+' where school_id=? and enabled=1 and deleted_at is null',(school,)):
            r=dict(r);nodes.append(dict(id=r['id'],kind=kind,name=r['name'],definition=r.get('description') or r['name'],parent=r.get('parent_id'),level=r.get('level',0)))
    return nodes

def latest(c,school):
    row=c.execute('select * from learning_graph_releases where school_id=? order by version desc limit 1',(school,)).fetchone()
    if row:return dict(row),json.loads(row['graph_json'])
    return None,{'nodes':[],'edges':[],'cards':{}}

def publish(c,school,graph,author=None,source='system-curated'):
    version=c.execute('select coalesce(max(version),0)+1 from learning_graph_releases where school_id=?',(school,)).fetchone()[0]
    rid='gr-'+uuid.uuid4().hex
    c.execute('insert into learning_graph_releases values(?,?,?,?,?,?,?)',(rid,school,version,dumps(graph),author,source,now()))
    return rid

def edge(source,target,kind,reason,locator,conditions='',status='curated'):
    key=hashlib.sha256((source+'|'+target+'|'+kind).encode()).hexdigest()[:20]
    return dict(id='ge-'+key,source=source,target=target,kind=kind,reason=reason,locator=locator,conditions=conditions,status=status,reviewer=None)

def backfill(repo):
    manifest=json.loads((Path(__file__).with_name('diagnostic_data')/'graph-20261007.json').read_text())
    c=repo.conn;added=0
    for schoolrow in c.execute('select id from schools').fetchall():
        school=schoolrow[0];release,graph=latest(c,school);changed=False
        ids={n['id'] for n in catalog(c,school)}
        for item in manifest:
            card=c.execute('select * from diagnostic_cards where school_id=? and question_id=? and fingerprint=?',(school,item['question_id'],item['fingerprint'])).fetchone()
            if not card or card['id'] in graph['cards']:continue
            content=json.loads(card['card_json'])
            if len(content['steps'])!=len(item['steps']) or any(x['prompt']!=y['source_prompt'] for x,y in zip(content['steps'],item['steps'])):continue
            if any(s[k] not in ids for s in item['steps'] for k in ('knowledge_id','ability_id','literacy_id')):continue
            mappings=[]
            for index,s in enumerate(item['steps']):
                nid='lo-'+item['question_id'][2:]+'-'+str(index)
                graph['nodes'].append(dict(id=nid,kind='objective',name=s['name'],definition=s['definition'],parent=s['knowledge_id'],level=4))
                mappings.append(dict(node_id=nid,objective_name=s['name'],objective_definition=s['definition'],knowledge_id=s['knowledge_id'],ability_id=s['ability_id'],literacy_id=s['literacy_id'],status='curated',reason='根据本检查点的具体任务关联；不代表整个能力或素养已掌握。',locator=f"{item['question_id']} / 内容指纹 {item['fingerprint']} / 检查点 {index+1}"))
                locator=mappings[-1]['locator']
                graph['edges'] += [edge(s['knowledge_id'],nid,'contains','该检查目标属于本节物理内容。',locator),edge(nid,s['ability_id'],'uses_ability','完成该检查需要'+next(n['name'] for n in catalog(c,school) if n['id']==s['ability_id'])+'。',locator),edge(nid,s['literacy_id'],'supports_literacy','本任务提供观察该素养表现的机会；一次诊断不足以评定素养。',locator)]
            graph['cards'][card['id']]={'card':content,'mappings':mappings,'status':'curated','reviewer':None}
            added+=1;changed=True
        if changed:
            # These links are independently authored, not inferred from question co-occurrence.
            links=[
             ('930123136a094370',0,1,'supports','先辨认 α 粒子的质量数与电荷数，再核对核反应方程的守恒量。','α 衰变方程'),
             ('930123136a094370',1,2,'supports','质量数和电荷数确定后，可以计算中子数 A−Z。','已确定核素'),
             ('6a2c547de5284a48',0,3,'confusable','静止合力为零；摆动最低点速度非零且存在径向加速度，不能套用静止平衡。','摆动最低点'),
             ('7053068280d14bda',0,3,'related','匀速只限制加速度，是否机械能守恒还须检查做功条件。','研究对象与过程须一致'),
             ('398e93c66b8f42e1',0,3,'confusable','波形图的横轴为空间位置；质点在平衡位置附近振动，并不随波迁移。','机械波'),
             ('b745fbd3f85c4adf',0,2,'supports','分解运动后，用沿初速度方向的匀速运动确定偏转时间。','匀强电场垂直初速度，忽略重力'),
             ('30d19ce365bb4102',0,1,'supports','识别速度与斜率的不同含义后，再由斜率求加速度。','速度—时间图像'),
             ('30d19ce365bb4102',1,3,'confusable','速度—时间图像斜率表示加速度；有向面积表示位移。','同一速度—时间图像'),
             ('33503264e99d487a',2,3,'related','线路热损耗与电压降均须使用线路电流，分别用 I²R 与 IR 计算。','纯电阻输电线路'),
             ('fcc4eb8eb9bd4377',0,1,'supports','确定导体相对磁场的切割运动后，选取正确的相对速度。','直导体垂直切割匀强磁场'),
             ('f8e2a9af8ca94056',1,3,'supports','由遮光时间得到各时刻速度后，可以建立速度—时间图像。','遮光片足够短且数据成对'),
             ('6be81e4a0c5244fc',1,2,'supports','明确直线斜率与截距所含参数后，才能利用其比值消去公共参数。','题中近似条件成立'),
             ('9f45c714c10a4aba',0,1,'supports','充气系统总气量变化，需取等量气体作状态换算后累加。','理想气体；绝对温度'),
             ('7c70d2e38b96446f',0,2,'supports','磁场不做功，磁场运动可沿用电场加速后得到的速率。','仅受洛伦兹力进入磁场'),
             ('352a61eea75d4154',2,3,'supports','轨迹几何决定圆心角，再用角速度计算磁场中的飞行时间。','匀强磁场内的圆弧运动'),
             ('5a5d64a5424c47f2',0,1,'supports','先检验分离短时间内外力冲量能否忽略，再建立动量守恒方程。','选取完整分离系统'),
             ('7ed58356ca0144c8',0,2,'supports','先求竖直落地时间，再比较风持续时间并划分水平运动过程。','水平与竖直运动独立'),
            ]
            known={n['id'] for n in graph['nodes']};seen={e['id'] for e in graph['edges']}
            for q,a,b,k,reason,cond in links:
                source=f'lo-{q}-{a}';target=f'lo-{q}-{b}'
                e=edge(source,target,k,reason,f'q-{q} / 检查点 {a+1}、{b+1}',cond)
                if source in known and target in known and e['id'] not in seen:graph['edges'].append(e)
            cross_links=[
                ('lo-f9deee276f294f98-3','lo-ac6102975e57454e-3','transfer','功的正负由研究对象所受力与位移的夹角决定，可以从水流做功迁移到下降过程中的重力做功。','惯性参考系；选定同一研究对象的力和位移'),
                ('lo-30d19ce365bb4102-1','lo-f8e2a9af8ca94056-3','transfer','速度—时间图像斜率表示加速度，在运动分析和光电门实验数据分析中含义相同。','横轴确为时间、纵轴确为速度；不能换成位移—时间图像'),
                ('lo-b745fbd3f85c4adf-0','lo-7ed58356ca0144c8-0','transfer','分解为互相垂直方向并共用时间的方法，可用于电场偏转与风作用下的二维运动。','忽略两个方向的耦合；分段时保持共同的时刻边界'),
                ('lo-7c70d2e38b96446f-1','lo-b745fbd3f85c4adf-1','related','电场加速可用功与动能关系求速度，也可通过电场力和牛顿第二定律求加速度；按目标选择规律。','匀强电场；带电粒子质量电荷不变；分别检查其他力是否可忽略'),
                ('lo-3407caa5f90c4b3c-2','lo-6be81e4a0c5244fc-1','supports','先理解改装电表及支路的电流电压关系，有助于建立电源内阻实验的回路方程与线性表达。','相同电路；是否忽略电表支路电流须另行核对'),
                ('lo-5d9a972adb7f49dd-2','lo-ac6102975e57454e-2','confusable','加速上升用推力减重力等于 ma；悬停是 a=0 的特殊情形，不能把推力等于重力用于所有运动。','竖直方向，仅考虑题中推力与重力'),
            ]
            for source,target,kind,reason,conditions in cross_links:
                e=edge(source,target,kind,reason,source.replace('lo-','q-')+' 与 '+target.replace('lo-','q-')+' / 对应检查目标',conditions)
                if source in known and target in known and e['id'] not in seen:graph['edges'].append(e)
            publish(c,school,graph)
    c.commit();return {'mapped_cards':added}

def prepare_candidate(repo,card_id):
    """Future model preparations enter staff review; never publish guessed tag causality."""
    c=repo.conn;card=c.execute('select * from diagnostic_cards where id=?',(card_id,)).fetchone()
    if not card:return
    release,graph=latest(c,card['school_id'])
    if card_id in graph['cards']:return
    nodes=catalog(c,card['school_id']);ids={n['id'] for n in nodes}
    knowledge=next((t['tag_id'] for t in repo.get_question_tags(card['question_id']) if t['tag_type']=='knowledge' and t['tag_id'] in ids),'')
    if not knowledge:knowledge=next((n['id'] for n in nodes if n['kind']=='knowledge' and n['level']==3),'')
    if not knowledge:return
    content=json.loads(card['card_json']);mappings=[]
    for i,st in enumerate(content['steps']):
        ability={'condition':'ab-info-extraction','model':'ab-model-construction','plan':'ab-equation-building','execution':'ab-calculation'}[st['stage']]
        literacy='lit-thinking-model' if st['stage']=='model' else 'lit-thinking-reasoning'
        if ability not in ids or literacy not in ids:return
        nid='lo-'+card['question_id'][2:]+'-'+card['fingerprint'][:8]+'-'+str(i)
        name=('辨明：'+st['prompt'])[:100];definition='能解释本检查点所涉及的物理条件与规律。待教师修订为具体学习目标。'
        graph['nodes'].append(dict(id=nid,kind='objective',name=name,definition=definition,parent=knowledge,level=4,status='draft'))
        mappings.append(dict(node_id=nid,objective_name=name,objective_definition=definition,knowledge_id=knowledge,ability_id=ability,literacy_id=literacy,status='draft',reason='按题目标签和检查阶段准备的候选，尚未确认。',locator=f"{card['question_id']} / 内容指纹 {card['fingerprint']} / 检查点 {i+1}"))
    graph['cards'][card_id]=dict(card=content,mappings=mappings,status='draft',reviewer=None)
    publish(c,card['school_id'],graph,source='model-candidate')

def effective(c,card):
    release,graph=latest(c,card['school_id']);entry=graph['cards'].get(card['id'])
    return (entry['card'],entry['mappings'],release['id']) if entry else (json.loads(card['card_json']),[],release['id'] if release else None)

def session_card(c,s):
    if s['effective_card_json']:return json.loads(s['effective_card_json'])
    return json.loads(c.execute('select card_json from diagnostic_cards where id=?',(s['card_id'],)).fetchone()[0])

def require_staff(user):
    if user['role'] not in ('teacher','admin'):raise PermissionDenied('仅教师和管理员可审核课程关系与诊断卡')

def validate_edge(e,nodes):
    if e.get('source') not in nodes or e.get('target') not in nodes or e['source']==e['target']:raise InvalidRequest('请选择两个不同的有效节点')
    if e.get('kind') not in RELATIONS:raise InvalidRequest('关联类型无效')
    if any(not isinstance(e.get(k),str) or not e[k].strip() or len(e[k])>1000 for k in ('reason','locator')):raise InvalidRequest('请填写教学依据及可定位的来源')
    if not isinstance(e.get('conditions',''),str) or len(e.get('conditions',''))>1000:raise InvalidRequest('适用条件过长')
    if e['kind']=='prerequisite' and any(nodes[e[k]]['kind']!='objective' for k in ('source','target')):raise InvalidRequest('必要前置只能建立在具体学习目标之间')

def check_cycles(edges):
    adj={}
    for e in edges:
        if e['kind']=='prerequisite' and e['status']=='approved':adj.setdefault(e['source'],[]).append(e['target'])
    visited=set();active=set()
    def visit(n):
        if n in active:raise InvalidRequest('必要前置关系不能形成循环')
        if n in visited:return
        active.add(n)
        for m in adj.get(n,[]):visit(m)
        active.remove(n);visited.add(n)
    for n in adj:visit(n)

def api(repo,user,action,p):
    require_staff(user)
    if not isinstance(p,dict):raise InvalidRequest('请求格式无效')
    try:requested_version=int(p.get('version',-1))
    except (ValueError,TypeError):raise InvalidRequest('图谱版本无效，请刷新审核页面')
    c=repo.conn;c.execute('begin immediate')
    try:
        release,graph=latest(c,user['school_id'])
        if requested_version!=(release['version'] if release else 0):raise StateConflict('审核页面已过期，请刷新后继续')
        nodes={n['id']:n for n in catalog(c,user['school_id'])+graph['nodes']}
        if action=='graph-edge-save':
            candidate={k:p.get(k,'') for k in ('source','target','kind','reason','locator','conditions')};validate_edge(candidate,nodes)
            e=edge(**candidate,status='approved');e['reviewer']=user['id'];e['reviewed_at']=now()
            previous=next((x for x in graph['edges'] if x['id']==p.get('id')),None)
            if p.get('id') and not previous:raise InvalidRequest('关联不存在')
            if previous:graph['edges'].remove(previous)
            graph['edges']=[x for x in graph['edges'] if x['id']!=e['id']]+[e];check_cycles(graph['edges'])
        elif action=='graph-edge-review':
            ids=p.get('ids');status=p.get('status')
            if not isinstance(ids,list) or not ids or status not in ('approved','rejected'):raise InvalidRequest('请选择要确认或排除的关系')
            if set(ids)-{e['id'] for e in graph['edges']}:raise InvalidRequest('关联不存在')
            for e in graph['edges']:
                if e['id'] in ids:
                    if status=='approved':validate_edge(e,nodes)
                    e.update(status=status,reviewer=user['id'],reviewed_at=now())
            check_cycles(graph['edges'])
        elif action=='graph-card-save':
            card=c.execute('select * from diagnostic_cards where id=? and school_id=?',(p.get('card_id'),user['school_id'])).fetchone()
            if not card:raise PermissionDenied('不能审核其他学校的诊断卡')
            content=validate_card(p.get('card'));mappings=p.get('mappings')
            if not isinstance(mappings,list) or len(mappings)!=len(content['steps']):raise InvalidRequest('每个检查点都需要一个学习目标')
            old=graph['cards'].get(card['id']);oldcount=len(old['card']['steps']) if old else len(json.loads(card['card_json'])['steps'])
            if len(content['steps'])!=oldcount:raise InvalidRequest('本版只能修订现有检查点；新增或删减步骤需准备新的内容版本')
            clean=[]
            for i,m in enumerate(mappings):
                if not isinstance(m,dict):raise InvalidRequest('目标格式无效')
                for key,kind in (('knowledge_id','knowledge'),('ability_id','ability'),('literacy_id','literacy')):
                    if m.get(key) not in nodes or nodes[m[key]]['kind']!=kind:raise InvalidRequest('请选择有效的知识、能力和素养节点')
                name=m.get('name','');definition=m.get('definition','')
                if not isinstance(name,str) or not name.strip() or len(name)>100 or not isinstance(definition,str) or not definition.strip() or len(definition)>800:raise InvalidRequest('请填写具体学习目标与说明')
                nid='lo-'+card['question_id'][2:]+'-'+card['fingerprint'][:8]+'-'+str(i) if not old else old['mappings'][i]['node_id']
                graph['nodes']=[n for n in graph['nodes'] if n['id']!=nid]+[dict(id=nid,kind='objective',name=name,definition=definition,parent=m['knowledge_id'],level=4)]
                locator=f"{card['question_id']} / 内容指纹 {card['fingerprint']} / 检查点 {i+1}"
                clean.append(dict(node_id=nid,objective_name=name,objective_definition=definition,knowledge_id=m['knowledge_id'],ability_id=m['ability_id'],literacy_id=m['literacy_id'],status='approved',reviewer=user['id'],reason='教师核对本检查点的教学任务后确认。',locator=locator))
                graph['edges']=[e for e in graph['edges'] if not (e['target']==nid and e['kind']=='contains' or e['source']==nid and e['kind'] in ('uses_ability','supports_literacy'))]
                for s,t,k in ((m['knowledge_id'],nid,'contains'),(nid,m['ability_id'],'uses_ability'),(nid,m['literacy_id'],'supports_literacy')):
                    e=edge(s,t,k,'教师核对本检查点的教学任务后确认；不能据此推断整个知识点或素养的掌握。',locator,status='approved');e['reviewer']=user['id'];graph['edges'].append(e)
            graph['cards'][card['id']]=dict(card=content,mappings=clean,status='approved',reviewer=user['id'],reviewed_at=now())
        else:raise InvalidRequest('图谱操作无效')
        rid=publish(c,user['school_id'],graph,user['id'],'teacher-reviewed');c.commit()
        return {'release_id':rid,'version':(release['version'] if release else 0)+1}
    except Exception:c.rollback();raise

def student_data(repo,user):
    from .student_learning import require_student,group_wrongs
    from .diagnosis import question_input,fingerprint
    require_student(user);c=repo.conn;release,graph=latest(c,user['school_id'])
    nodes=catalog(c,user['school_id']);allowed={n['id'] for n in nodes}
    objectives=[n for n in graph['nodes'] if n.get('parent') in allowed and n.get('status')!='draft'];nodes+=objectives;allowed|={n['id'] for n in objectives}
    edges=[e for e in graph['edges'] if e['status'] not in ('rejected','draft') and e['source'] in allowed and e['target'] in allowed]
    for n in nodes:
        if n.get('parent') in allowed and n['kind']!='objective':edges.append(edge(n['parent'],n['id'],'contains','现有教材或素养体系的目录关系，不表示学习前置。','校本标签体系',status='approved'))
    evidence=[]
    for g in group_wrongs(repo,user):
        for w in g['members']:
            data=question_input(repo,w['question_id'],trial=w if w.get('personal') else None,snapshot=None if w.get('personal') else w['snapshot']);fp=fingerprint(data)
            card=c.execute('select * from diagnostic_cards where school_id=? and question_id=? and fingerprint=?',(user['school_id'],w['question_id'],fp)).fetchone()
            if not card:continue
            _,maps,rid=effective(c,card)
            session=c.execute('select * from diagnostic_sessions where student_id=? and school_id=? and wrong_id=? and question_id=? and fingerprint=?',(user['id'],user['school_id'],g['id'],w['question_id'],fp)).fetchone()
            if session:
                if session['graph_mapping_json'] is not None:
                    maps=json.loads(session['graph_mapping_json']);rid=session['graph_release_id']
                else:
                    # Sessions made before graph support still use their immutable base card.
                    maps=[];base=json.loads(card['card_json'])
                    for historical in c.execute('select * from learning_graph_releases where school_id=? order by version',(user['school_id'],)):
                        old=json.loads(historical['graph_json'])['cards'].get(card['id'])
                        if old and old['card']==base:
                            maps=old['mappings'];rid=historical['id'];break
            observations={}
            if session:
                for ev in c.execute("select step,result_json from diagnostic_events where session_id=? and action='answer' order by created_at,id",(session['id'],)):
                    result=json.loads(ev['result_json']);observations[ev['step']]='借助帮助完成' if result['passed'] and result['assisted'] else '本步独立通过' if result['passed'] else '本步待检查'
            for i,m in enumerate(maps):
                if m['node_id'] not in allowed:continue
                evidence.append(dict(node_id=m['node_id'],label=(('第 '+str(g['number'])+' 题 · ') if g.get('number') else '')+w.get('part_label','本题')+f' · 检查点 {i+1}',status=observations.get(i,'尚未观察'),url='app?practice='+quote(g['id']),version=rid,reason=m['reason'],objective_name=m.get('objective_name',''),objective_definition=m.get('objective_definition','')))
    return dict(nodes=nodes,edges=edges,evidence=evidence,version=release['version'] if release else 0,relations=RELATIONS,kinds=KINDS,statuses=STATUS)

def assets():return '<link rel="stylesheet" href="assets/learning-graph.css?v=20261007-v2"><script src="assets/learning-graph.js?v=20261007-v2" defer></script>'

def student_page(repo,user):
    data=student_data(repo,user)
    encoded=dumps(data).replace('<','\\u003c').replace('&','\\u0026')
    return '''<section class="connected-graph" data-learning-graph><p>从一个具体目标展开，看看它与知识、能力和素养怎样相连。诊断只提供本题线索，不直接判断整个知识点的掌握。</p>
    <div class="graph-controls"><label>查找学习内容<input type="search" data-graph-search placeholder="输入名称，例如动量、图像" maxlength="100"></label><label>类型<select data-graph-kind><option value="">全部类型</option><option value="knowledge">知识点</option><option value="objective">学习目标</option><option value="ability">能力</option><option value="literacy">素养</option></select></label><label>选择中心节点<select data-graph-focus></select></label><button type="button" data-graph-mode aria-pressed="false">切换到列表</button></div>
    <p class="graph-legend">方框：知识 · 圆角框：学习目标 · 圆形：能力 · 菱形：素养<br>实线：教师确认或目录关系 · 虚线：系统整理，待教师确认 · 箭头按关系方向阅读（相关、混淆为双向）</p>
    <div class="graph-layout"><div><div data-graph-canvas></div><div data-graph-list hidden></div><p data-graph-count role="status"></p></div><aside data-graph-detail aria-live="polite"></aside></div>
    <noscript><p>启用脚本可交互探索图谱。以下为学习内容目录。</p>'''+''.join('<p>'+esc(n['name'])+'：'+esc(n['definition'])+'</p>' for n in data['nodes'])+'</noscript><script type="application/json" data-graph-data>'+encoded+'</script></section>'+assets()

def select_nodes(nodes,kind,value=''):
    return ''.join('<option value="%s"%s>%s</option>'%(esc(n['id'],quote=True),' selected' if value==n['id'] else '',esc(n['name'])) for n in nodes if n['kind']==kind)

def teacher_page(repo,user,params):
    require_staff(user);c=repo.conn;release,graph=latest(c,user['school_id']);version=release['version'] if release else 0
    nodes=catalog(c,user['school_id'])+graph['nodes'];byid={n['id']:n for n in nodes}
    cards=c.execute('select * from diagnostic_cards where school_id=? order by question_id,created_at desc',(user['school_id'],)).fetchall()
    value=lambda k:(params.get(k) or [''])[0]
    card_id=value('card');selected=next((r for r in cards if r['id']==card_id),cards[0] if cards else None)
    out=[f'<section class="graph-review"><h2>诊断目标与关系审核</h2><p>当前图谱第 {version} 版。系统整理内容仍待教师确认；保存会发布新版本，学生既有诊断保留开始时的卡片和目标映射。</p><p data-graph-review-message role="status"></p><form method="get"><input type="hidden" name="module" value="graph"><label>选择诊断题目<select name="card">']
    for r in cards:
        entry=graph['cards'].get(r['id']);content=entry['card'] if entry else json.loads(r['card_json'])
        out.append('<option value="%s"%s>%s · %s</option>'%(esc(r['id']),' selected' if selected and selected['id']==r['id'] else '',esc(content['title']),STATUS[entry['status']] if entry else '目标待补充'))
    out.append('</select></label><button>查看诊断卡</button></form>')
    if selected:
        content,maps,_=effective(c,selected)
        from .question_rendering import render_question,render_markdown
        source=json.loads(selected['input_json']);document=source.get('content')
        if document:
            document=dict(document)
            if document.get('part'):document['children']=[dict(document.pop('part'),key='reviewed-part')]
            question_html=render_question(document,asset_url=lambda aid:'api/question-bank/assets/'+quote(aid)+'?question_id='+quote(selected['question_id']),include_solution=True)
        else:question_html=render_markdown(source.get('stem',''))
        out.append('<details open><summary>对应题目与答案依据</summary>'+question_html+'</details>')
        out.append(f'<form data-graph-card data-version="{version}" data-card-id="{esc(selected["id"])}"><label>诊断标题<input name="title" maxlength="100" required value="{esc(content["title"],quote=True)}"></label>')
        for i,s in enumerate(content['steps']):
            m=maps[i] if i<len(maps) else {};n=byid.get(m.get('node_id'),{})
            out.append(f'<fieldset data-graph-step><legend>检查点 {i+1}</legend><label>阶段<select name="stage">'+''.join('<option value="%s"%s>%s</option>'%(k,' selected' if k==s['stage'] else '',v) for k,v in __import__('highschoolphysics.diagnosis',fromlist=['STAGES']).STAGES.items())+'</select></label>')
            for field,label,limit in (('prompt','检查问题',500),('explanation','反馈说明',800)):
                out.append(f'<label>{label}<textarea name="{field}" maxlength="{limit}" required>'+esc(s[field])+'</textarea></label>')
            for j,o in enumerate(s['options']):out.append(f'<label>选项 {j+1}<input name="option" maxlength="300" value="{esc(o,quote=True)}" required></label>')
            out.append('<label>正确选项<select name="correct">'+''.join('<option value="%s"%s>选项 %s</option>'%(j,' selected' if s['correct']==j else '',j+1) for j in range(len(s['options'])))+'</select></label>')
            for hint in s['hints']:out.append('<label>分层提示<textarea name="hint" maxlength="500" required>'+esc(hint)+'</textarea></label>')
            out.append('<label>具体学习目标<input name="name" maxlength="100" required value="'+esc(n.get('name',''),quote=True)+'"></label><label>目标说明<textarea name="definition" maxlength="800" required>'+esc(n.get('definition',''))+'</textarea></label>')
            for key,kind in (('knowledge_id','knowledge'),('ability_id','ability'),('literacy_id','literacy')):out.append('<label>'+KINDS[kind]+'<select name="'+key+'" required><option value="">请选择</option>'+select_nodes(nodes,kind,m.get(key,''))+'</select></label>')
            out.append('</fieldset>')
        out.append('<button>确认诊断卡与目标并发布</button></form>')
    out.append('<h2>关联关系</h2><p>必要前置用于真正不可缺少的知识条件；辅助理解、相关和混淆应分别标注，不把共同出现视为因果。</p><form data-graph-edges data-version="%s"><div class="graph-controls"><label>查找相连内容<input type="search" data-graph-edge-search placeholder="输入目标、依据或来源"></label><label>审核状态<select data-graph-edge-status><option value="">全部</option><option value="curated">待教师确认</option><option value="approved">教师已确认</option><option value="rejected">已排除</option></select></label><label><span><input type="checkbox" data-graph-semantic-only checked> 仅看目标之间的语义关系</span></label></div><p data-graph-filter-count></p><div class="graph-review-actions"><button name="status" value="approved">确认所选关系</button><button class="secondary" name="status" value="rejected">排除所选关系</button></div>'%version)
    for e in graph['edges']:
        if e['source'] not in byid or e['target'] not in byid:continue
        out.append('<details data-graph-edge-row data-kind="%s" data-status="%s"><summary><input type="checkbox" name="ids" value="%s" aria-label="选择关系"> %s → %s · %s · %s</summary><p>%s</p><p>适用条件：%s</p><p>来源：%s</p><a href="teacher?module=graph&amp;edge=%s">编辑此关系</a></details>'%(esc(e['kind']),esc(e['status']),esc(e['id']),esc(byid[e['source']]['name']),esc(byid[e['target']]['name']),RELATIONS[e['kind']],STATUS[e['status']],esc(e['reason']),esc(e.get('conditions') or '见来源任务'),esc(e['locator']),quote(e['id'])))
    out.append('</form><h3>新增或修订关系</h3>')
    e=next((e for e in graph['edges'] if e['id']==value('edge')), {})
    out.append(f'<form data-graph-edge data-version="{version}"><input type="hidden" name="id" value="{esc(e.get("id",""))}">')
    for key,label in (('source','起点'),('target','终点')):
        out.append('<label>'+label+'<select name="'+key+'" required>'+''.join('<option value="%s"%s>%s · %s</option>'%(esc(n['id']),' selected' if e.get(key)==n['id'] else '',KINDS[n['kind']],esc(n['name'])) for n in nodes)+'</select></label>')
    out.append('<label>关系类型<select name="kind">'+''.join('<option value="%s"%s>%s</option>'%(k,' selected' if e.get('kind')==k else '',v) for k,v in RELATIONS.items())+'</select></label>')
    for key,label in (('reason','教学依据'),('locator','来源位置'),('conditions','适用条件')):out.append('<label>'+label+'<textarea name="'+key+'" maxlength="1000"'+(' required' if key!='conditions' else '')+'>'+esc(e.get(key,''))+'</textarea></label>')
    return ''.join(out)+'<button>确认关系并发布</button></form></section>'+assets()

if __name__=='__main__':
    import argparse
    from .db import connect,initialize_database
    from .repository import PhysicsRepository
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);args=p.parse_args()
    c=connect(args.db);initialize_database(c);print(dumps(backfill(PhysicsRepository(c))));c.close()
