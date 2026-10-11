"""Versioned shared goals. A shared name never merges evidence without review."""
import copy
import hashlib
import json
import uuid
from pathlib import Path

from .errors import InvalidRequest

STAGES = {
    'concept': '概念理解', 'model': '对象与条件',
    'equation': '规律与列式', 'execution': '计算与检查',
}
ACTION_STAGES = {
    'knowledge': 'concept', 'condition': 'model', 'model': 'model',
    'plan': 'equation', 'formula': 'equation',
    'calculation': 'execution', 'check': 'execution',
}


def content_hash(content):
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def install(c):
    """Append a publication only for exact, existing reviewed source cards."""
    from .learning_graph import latest, publish
    from .diagnosis_adaptive import content_for
    manifest = json.loads(Path(__file__).with_name('diagnostic_data').joinpath(
        'shared-goals-20261011.json').read_text())
    definitions = {g['id']: g for g in manifest['goals']}
    c.commit()
    c.execute('begin immediate')
    try:
        version = c.execute("select version from app_schema_migrations where feature='shared_learning_goals'").fetchone()
        if version and version[0] != 22:
            raise RuntimeError('Unsupported shared learning goals schema')
        for school in c.execute('select id from schools').fetchall():
            _, graph = latest(c, school[0])
            goals = graph.setdefault('shared_goals', {})
            cards = graph.setdefault('adaptive_bindings', {})
            changed = False
            for item in manifest['cards']:
                for card in c.execute('select * from diagnostic_cards where school_id=? '
                                      'and question_id=? and fingerprint=?',
                                      (school[0], item['question_id'], item['fingerprint'])).fetchall():
                    if card['id'] in cards:
                        continue
                    content = content_for(c, card)
                    if not content or content_hash(content) != item['content_hash']:
                        continue
                    bindings = copy.deepcopy(item['bindings'])
                    for b in bindings:
                        goals.setdefault(b['goal_id'], dict(definitions[b['goal_id']],
                            status='curated', source=manifest['version']))
                        b.update(status='curated', reviewer=None)
                    cards[card['id']] = dict(content_hash=item['content_hash'], bindings=bindings)
                    changed = True
            if changed:
                graph['goal_catalogue_version'] = manifest['version']
                publish(c, school[0], graph, source=manifest['version'])
        c.execute("insert or ignore into app_schema_migrations(feature,version) "
                  "values('shared_learning_goals',22)")
        c.commit()
    except Exception:
        c.rollback()
        raise


def session_mapping(c, card, content):
    """Pin only mappings for this exact branch content, including review status."""
    from .learning_graph import latest
    release, graph = latest(c, card['school_id'])
    entry = graph.get('adaptive_bindings', {}).get(card['id'])
    if not entry or entry['content_hash'] != content_hash(content):
        return [], release['id'] if release else None
    nodes = {r['id'] for r in c.execute('select id from knowledge_nodes where '
        'school_id=? and enabled=1 and deleted_at is null', (card['school_id'],))}
    mappings = []
    for i, b in enumerate(entry['bindings']):
        goal = graph.get('shared_goals', {}).get(b.get('goal_id'))
        if not goal or b['knowledge_id'] not in nodes or b['status'] == 'rejected':
            mappings.append({})
            continue
        mappings.append(dict(b, action_index=i, goal_name=goal['name'],
            goal_definition=goal['definition'], conditions=goal['conditions'],
            goal_status=goal['status'], stage=b.get('stage', ACTION_STAGES[content['actions'][i]['kind']])))
        verification = b.get('verification', {})
        vg = graph.get('shared_goals', {}).get(verification.get('goal_id'))
        if vg and verification.get('knowledge_id') in nodes:
            mappings[-1]['verification'] = dict(verification, goal_name=vg['name'],
                goal_definition=vg['definition'], conditions=vg['conditions'], goal_status=vg['status'])
    return mappings, release['id'] if release else None


def save_binding(c, user, graph, p):
    from .diagnosis_adaptive import content_for
    from .diagnosis import now
    card = c.execute('select * from diagnostic_cards where id=? and school_id=?',
                     (p.get('card_id'), user['school_id'])).fetchone()
    if not card:
        raise InvalidRequest('诊断题目不存在或不属于本校')
    content = content_for(c, card)
    index = p.get('action_index')
    phase = p.get('phase', 'check')
    if phase not in ('check', 'verify'):
        raise InvalidRequest('请选择检查或另题复查')
    if not content or type(index) is not int or not 0 <= index < len(content['actions']):
        raise InvalidRequest('请选择当前分支诊断的有效动作')
    stage = p.get('stage', ACTION_STAGES[content['actions'][index]['kind']])
    if stage not in STAGES:
        raise InvalidRequest('请选择有效的学习环节')
    knowledge_id = p.get('knowledge_id')
    if not c.execute('select 1 from knowledge_nodes where id=? and school_id=? '
                     'and enabled=1 and deleted_at is null',
                     (knowledge_id, user['school_id'])).fetchone():
        raise InvalidRequest('请选择本校有效知识内容')
    goals = graph.setdefault('shared_goals', {})
    goal_id = p.get('goal_id')
    if goal_id:
        if goal_id not in goals:
            raise InvalidRequest('共同学习目标不存在，请刷新后重选')
        goal = goals[goal_id]
    else:
        values = {}
        for field, limit in (('name', 100), ('definition', 800), ('conditions', 800)):
            v = p.get(field)
            if not isinstance(v, str) or not v.strip() or len(v) > limit:
                raise InvalidRequest('请填写具体目标、可观察表现和适用条件')
            values[field] = v.strip()
        goal_id = 'sg-' + uuid.uuid4().hex
        goal = dict(id=goal_id, **values)
        goals[goal_id] = goal
    goal.update(status='approved', reviewer=user['id'], reviewed_at=now())
    cards = graph.setdefault('adaptive_bindings', {})
    entry = cards.get(card['id'])
    if not entry or entry['content_hash'] != content_hash(content):
        entry = dict(content_hash=content_hash(content), bindings=[{} for _ in content['actions']])
        cards[card['id']] = entry
    if phase == 'verify' and not entry['bindings'][index].get('goal_id'):
        raise InvalidRequest('请先确认本动作的首次检查目标，再核对另题复查')
    binding = dict(goal_id=goal_id, knowledge_id=knowledge_id, stage=stage,
        kind=content['actions'][index]['kind'], prompt=content['actions'][index]['verify']['prompt'] if phase == 'verify' else content['actions'][index]['prompt'],
        context=content['title'], status='approved', reviewer=user['id'], reviewed_at=now(),
        source=f"{card['question_id']} / 内容指纹 {card['fingerprint']} / 分支动作 {index+1}",
        node_id=entry['bindings'][index].get('node_id', '') if phase == 'check' else '')
    if phase == 'verify':
        entry['bindings'][index]['verification'] = binding
    else:
        binding['verification'] = entry['bindings'][index].get('verification', {})
        entry['bindings'][index] = binding


def review_html(c, user, card):
    from .learning_graph import latest
    from .diagnosis_adaptive import content_for
    from html import escape as esc
    release, graph = latest(c, user['school_id'])
    content = content_for(c, card)
    if not content:
        return '<p>本题尚无可用的分支诊断；原诊断卡继续按原版本使用。</p>'
    entry = graph.get('adaptive_bindings', {}).get(card['id'], {})
    bindings = entry.get('bindings', []) if entry.get('content_hash') == content_hash(content) else []
    goals = graph.get('shared_goals', {})
    knowledge = c.execute('select id,name from knowledge_nodes where school_id=? '
        'and enabled=1 and deleted_at is null order by stable_code,id', (user['school_id'],)).fetchall()
    out = ['<details class="shared-goal-review"><summary>共同学习目标审核 · %s 个动作</summary>'
           '<p>将不同题目的相同学习动作关联到同一目标。每个对应关系逐项确认；'
           '本次保存仅影响以后开始的诊断，既有会话保留原映射。</p>' % len(content['actions'])]
    for i, action in enumerate(content['actions']):
        base = bindings[i] if i < len(bindings) else {}
        out.append('<details><summary>动作 %s：%s</summary>' % (i+1, esc(action['prompt'])))
        for phase in ('check', 'verify'):
            b = base if phase == 'check' else base.get('verification', {})
            probe = action if phase == 'check' else action['verify']
            goal = goals.get(b.get('goal_id'), {})
            out.append('<form data-shared-goal data-version="%s" data-card-id="%s" data-index="%s">'
                       '<input type="hidden" name="phase" value="%s"><h4>%s</h4><p>%s</p>'
                       '<p>正确选择：%s</p><p>对应状态：%s</p>' % (
                release['version'] if release else 0, esc(card['id']), i, phase,
                '首次检查' if phase == 'check' else '另题复查（须独立核对目标）',
                esc(probe['prompt']), esc(probe['options'][probe['correct']]),
                '教师已确认' if b.get('status') == 'approved' else '待教师确认'))
            out.append('<label>共同目标<select name="goal_id"><option value="">新建具体目标</option>')
            for gid, g in sorted(goals.items(), key=lambda pair: pair[1]['name']):
                from json import dumps
                out.append('<option value="%s" data-definition="%s"%s>%s</option>' % (
                    esc(gid), esc(dumps({k:g[k] for k in ('name','definition','conditions')}, ensure_ascii=False)),
                    ' selected' if gid == b.get('goal_id') else '', esc(g['name'])))
            out.append('</select></label><p data-goal-preview>%s · 适用条件：%s</p>' % (
                esc(goal.get('definition','请选择已有目标或填写新目标')), esc(goal.get('conditions','待填写'))))
            out.append('<label>学习环节<select name="stage">')
            for key, label in STAGES.items():
                out.append('<option value="%s"%s>%s</option>' % (key,
                    ' selected' if key == b.get('stage', ACTION_STAGES[action['kind']]) else '', label))
            out.append('</select></label><label>所属知识内容<select name="knowledge_id">')
            for row in knowledge:
                out.append('<option value="%s"%s>%s</option>' % (esc(row['id']),
                    ' selected' if row['id'] == b.get('knowledge_id') else '', esc(row['name'])))
            out.append('</select></label><details><summary>新目标的定义（选择已有目标时无需填写）</summary>')
            for field, label, limit in (('name', '目标名称', 100), ('definition', '可观察表现', 800),
                                        ('conditions', '适用条件', 800)):
                out.append('<label>%s<textarea name="%s" maxlength="%s">%s</textarea></label>' % (
                    label, field, limit, esc(goal.get(field, ''))))
            out.append('</details><button>确认此检查的目标对应</button></form>')
        out.append('</details>')
    return ''.join(out) + '</details>'
