"""School-scoped question library, reusable papers and Agent tagging API."""
import copy
import hashlib
import html
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .errors import InvalidRequest, PermissionDenied, ResourceNotFound, StateConflict
from .repository import loads, dumps
from .document_models import canonical_sha256, validate_question_document
from .question_rendering import render_question, render_markdown


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def staff(user):
    if not user or user['role'] not in ('teacher', 'admin') or user['status'] != 'active':
        raise PermissionDenied('需要有效的教师或管理员身份')
    if user.get('must_change_password'):
        raise PermissionDenied('请先完成密码修改')


def question(repo, user, qid):
    staff(user)
    q = repo.get_question(qid)
    if q is None or q['school_id'] != user['school_id']:
        raise ResourceNotFound('题目不存在')
    return q


def tag_revision(conn, qid):
    row = conn.execute('select revision from question_tag_revisions where question_id=?', (qid,)).fetchone()
    return row[0] if row else 0


def tags_ready(repo, qid, tags):
    # An explicit saved decision may leave ability/literacy empty when unsupported.
    kinds = {t['tag_type'] for t in tags}
    return 'knowledge' in kinds and (tag_revision(repo.conn,qid)>0 or {'knowledge','ability','literacy'}.issubset(kinds))


def content(conn, qid, school_id):
    row = conn.execute('''select b.group_id,b.child_key,g.current_revision_id,r.document_json
        from question_content_bindings b join question_content_groups g on g.id=b.group_id
        join question_content_revisions r on r.id=g.current_revision_id
        where b.question_id=? and g.school_id=?''', (qid, school_id)).fetchone()
    if row:
        return {**dict(row), 'document': loads(row['document_json'], {})}
    return None


def batch_question_ids(conn, batch_id, school_id):
    # Publications preserve original item and small-part ordering; legacy imports use source number.
    rows = conn.execute('''select p.question_ids_json from import_item_publications p
        join parsed_question_items i on i.id=p.parsed_item_id
        where i.import_batch_id=? and i.school_id=? order by i.item_index,i.id''', (batch_id, school_id)).fetchall()
    ids = [qid for row in rows for qid in loads(row[0], [])]
    ids += [r[0] for r in conn.execute('''select id from questions where import_batch_id=? and school_id=?
        order by cast(original_question_number as integer),original_question_number,id''', (batch_id, school_id))]
    return list(dict.fromkeys(ids))


def sync_import_paper(conn, batch_id, actor):
    entry = conn.execute('select * from question_bank_imports where batch_id=?', (batch_id,)).fetchone()
    if not entry or entry['import_mode'] != 'paper':
        return None
    ids = batch_question_ids(conn, batch_id, actor['school_id'])
    if not ids:
        return None
    paper_id = entry['paper_id'] or 'paper-' + uuid.uuid4().hex[:16]
    if not entry['paper_id']:
        conn.execute("insert into papers(id,school_id,title,source,status) values(?,?,?,'导入试卷','reviewed')", (paper_id, actor['school_id'], entry['title']))
        conn.execute('update question_bank_imports set paper_id=? where batch_id=?', (paper_id, batch_id))
    # A later partial publication may extend the reusable paper. Assessment snapshots stay frozen.
    conn.execute('delete from paper_questions where paper_id=?', (paper_id,))
    conn.executemany('insert into paper_questions(paper_id,question_id,position,points) values(?,?,?,0)', [(paper_id, qid, n) for n, qid in enumerate(ids, 1)])
    return paper_id


def list_papers(repo, user):
    staff(user)
    return [dict(r) for r in repo.conn.execute('''select p.*,count(pq.question_id) question_count from papers p
        join paper_questions pq on pq.paper_id=p.id where p.school_id=?
        group by p.id order by p.created_at desc,p.id''', (user['school_id'],))]


def library(repo, user, batch_id='', paper_id='', search='', offset=0):
    staff(user)
    c = repo.conn
    batches = [dict(r) for r in c.execute('''select b.id,b.source_file_name,b.created_at,
        coalesce(i.import_mode,'questions') import_mode,i.paper_id,coalesce(i.title,b.source_file_name) title,
        count(q.id) question_count from question_import_batches b
        join questions q on q.import_batch_id=b.id and q.school_id=b.school_id
        left join question_bank_imports i on i.batch_id=b.id
        where b.school_id=? group by b.id order by b.created_at desc,b.id''', (user['school_id'],))]
    rows = c.execute('''select q.id,q.stem,q.question_type,q.import_batch_id,q.original_question_number,q.version,
        b.group_id,b.child_key from questions q left join question_content_bindings b on b.question_id=q.id
        where q.school_id=? order by q.created_at desc,q.id''', (user['school_id'],)).fetchall()
    scoped_ids = None
    if batch_id:
        if batch_id not in {b['id'] for b in batches}:
            raise ResourceNotFound('导入批次不存在或尚未入库')
        scoped_ids = batch_question_ids(c, batch_id, user['school_id'])
    if paper_id:
        p = c.execute('select id from papers where id=? and school_id=?', (paper_id, user['school_id'])).fetchone()
        if not p:
            raise ResourceNotFound('试卷不存在')
        scoped_ids = [r[0] for r in c.execute('select question_id from paper_questions where paper_id=? order by position', (paper_id,))]
    by_id = {r['id']: dict(r) for r in rows}
    ordered = [by_id[qid] for qid in scoped_ids if qid in by_id] if scoped_ids is not None else list(by_id.values())
    groups = {}
    for row in ordered:
        key = row['group_id'] or row['id']
        group = groups.setdefault(key, {'id': key, 'question_ids': [], 'number': row['original_question_number'] or '', 'stem': row['stem'], 'batch_id': row['import_batch_id'], 'types': [], 'tagged_count': 0})
        group['question_ids'].append(row['id'])
        group['types'].append(row['question_type'])
        if repo.tags_for_question(row['id']):
            group['tagged_count'] += 1
    matches = [g for g in groups.values() if not search or search.lower() in (g['stem'] + ' ' + g['number']).lower()]
    offset = max(0, int(offset))
    return {'batches': batches, 'papers': list_papers(repo, user), 'groups': matches[offset:offset+30],
            'total': len(matches), 'offset': offset, 'scope_question_ids': [qid for g in matches for qid in g['question_ids']]}


def taxonomy(repo, user):
    staff(user)
    catalogs = {'knowledge': repo.knowledge_nodes(), 'ability': repo.ability_tags(), 'literacy': repo.literacy_tags()}
    paths = repo.knowledge_node_paths()
    return {kind: [{**t, 'path_text': ' / '.join(paths.get(t['id'], [t['name']])) if kind == 'knowledge' else t['name']}
                   for t in tags if t['school_id'] == user['school_id'] and t.get('enabled', 1) and not t.get('deleted_at')]
            for kind, tags in catalogs.items()}


def detail(repo, user, qid, base_path=''):
    q = question(repo, user, qid)
    binding = content(repo.conn, qid, user['school_id'])
    if binding:
        d = binding['document']
        rendered = render_question(d, asset_url=lambda aid: base_path+'/api/question-bank/assets/'+quote(aid)+'?question_id='+quote(qid), include_solution=True)
        rows = repo.conn.execute('select question_id,child_key from question_content_bindings where group_id=? order by rowid', (binding['group_id'],)).fetchall()
        units = []
        for row in rows:
            child = next((part for part in d['children'] if part['key'] == row['child_key']), None)
            units.append({'id': row['question_id'], 'label': child['label'] if child else '本题', 'tag_revision': tag_revision(repo.conn, row['question_id']), 'question_version': repo.get_question(row['question_id'])['version'], 'tags': repo.tags_for_question(row['question_id'])})
        document = d
    else:
        rendered = render_markdown(q['stem']) + ''.join('<p>%s. %s</p>' % (html.escape(str(k)), html.escape(str(v))) for k,v in q['options'].items())
        rendered += render_markdown('参考答案：'+str(q['answer'])) + render_markdown(q['analysis'])
        units = [{'id': qid, 'label': '本题', 'tag_revision': tag_revision(repo.conn, qid), 'question_version': q['version'], 'tags': repo.tags_for_question(qid)}]
        document = None
    for unit in units:
        job = repo.conn.execute("select result_json,candidate_id from question_tag_jobs where question_id=? and source like 'bank:%' and status='completed' order by created_at desc,rowid desc limit 1", (unit['id'],)).fetchone()
        if job and job['candidate_id']:
            result = loads(job['result_json'],{})
            expected = result.get('expected',{})
            if result.get('status') == 'suggested' and expected.get('question_version') == unit['question_version'] and expected.get('tag_revision') == unit['tag_revision']:
                unit['suggestion'] = {'candidate':repo.get_candidate(job['candidate_id']), 'expected':expected}
    return {'question': q, 'document': document, 'content_revision_id': binding['current_revision_id'] if binding else None, 'html': rendered, 'units': units}


def save_tags(repo, user, payload, agent_id=None):
    staff(user)
    entries = payload.get('entries')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 500:
        raise InvalidRequest('请提交 1—500 道题的标签')
    c = repo.conn
    c.execute('begin immediate')
    try:
        seen = set()
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get('question_id'), str) or e['question_id'] in seen:
                raise InvalidRequest('题目 ID 无效或重复')
            seen.add(e['question_id'])
            q = question(repo, user, e['question_id'])
            if type(e.get('expected_revision')) is not int or e['expected_revision'] != tag_revision(c, q['id']):
                raise StateConflict('标签已被其他人修改，请刷新后再保存')
            if type(e.get('question_version')) is not int or e['question_version'] != q['version']:
                raise StateConflict('题目内容已变化，请刷新后重新标注')
            for family in ('knowledge','ability','literacy'):
                ids = e.get(family)
                if not isinstance(ids, list) or any(not isinstance(t, str) for t in ids):
                    raise InvalidRequest('三类标签必须是标签 ID 数组，可以为空')
                repo._validate_tag_limit(family, ids)
                repo._assert_active_tags(user['school_id'], family, ids)
        results = []
        for e in entries:
            tags = repo.confirm_question_tags(user['id'], e['question_id'], knowledge_node_ids=e['knowledge'], ability_tag_ids=e['ability'], literacy_tag_ids=e['literacy'], tag_source='agent' if agent_id else 'teacher_review', commit=False)
            results.append({'question_id': e['question_id'], 'revision': tag_revision(c,e['question_id']), 'tags': tags})
        repo.audit(user['id'], 'question_bank_tags_saved', 'question_bank', agent_id or user['id'], {'agent_token_id': agent_id, 'question_ids': sorted(seen)})
        c.commit()
        return {'items': results, 'message': '标签已保存；已发布考试保留原标签快照'}
    except Exception:
        c.rollback()
        raise


def queue_tags(repo, user, payload):
    staff(user)
    ids = payload.get('question_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(qid,str) for qid in ids) or len(set(ids)) != len(ids):
        raise InvalidRequest('请选择 1—500 道题，题目不能重复')
    if not repo.conn.execute("select 1 from provider_configs where school_id=? and provider_kind='llm' and enabled=1", (user['school_id'],)).fetchone():
        raise InvalidRequest('请管理员先配置并启用大模型 API')
    key = payload.get('request_key')
    if not isinstance(key,str) or not 8 <= len(key) <= 96 or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in key):
        raise InvalidRequest('需要 8—96 位稳定 request_key')
    c = repo.conn
    c.execute('begin immediate')
    try:
        questions = [question(repo,user,qid) for qid in ids]
        request_hash = canonical_sha256({'ids': ids, 'only_missing': payload.get('only_missing',True)})
        cached = c.execute("select request_hash,result_json from content_operation_keys where school_id=? and actor_id=? and operation='bank_tag_queue' and request_key=?", (user['school_id'],user['id'],key)).fetchone()
        if cached:
            if cached['request_hash'] != request_hash:
                raise StateConflict('request_key 已用于不同请求')
            c.rollback()
            return loads(cached['result_json'],{})
        job_ids = []
        skipped = []
        for q in questions:
            if payload.get('only_missing',True) and repo.tags_for_question(q['id']):
                skipped.append(q['id']); continue
            source = 'bank:'+uuid.uuid4().hex
            job_id = 'tagjob-'+uuid.uuid4().hex
            expected = {'tag_revision': tag_revision(c,q['id']), 'question_version': q['version']}
            c.execute('''insert into question_tag_jobs(id,school_id,question_id,requested_by,question_version,source,status,available_at,result_json)
                values(?,?,?,?,?,?,'queued',?,?)''', (job_id,user['school_id'],q['id'],user['id'],q['version'],source,now(),dumps({'expected':expected})))
            job_ids.append(job_id)
        result = {'job_ids':job_ids,'skipped':skipped}
        c.execute("insert into content_operation_keys(school_id,actor_id,operation,request_key,request_hash,result_json) values(?,?,'bank_tag_queue',?,?,?)", (user['school_id'],user['id'],key,request_hash,dumps(result)))
        repo.audit(user['id'],'question_bank_ai_requested','question_bank',key,{'question_ids':ids,'job_ids':job_ids})
        c.commit()
        return result
    except Exception:
        c.rollback(); raise


def jobs(repo, user, ids):
    staff(user)
    if not ids or len(ids)>500:
        raise InvalidRequest('需要 1—500 个任务 ID')
    result=[]
    for jid in ids:
        row=repo.conn.execute('select * from question_tag_jobs where id=? and school_id=?', (jid,user['school_id'])).fetchone()
        if row is None:
            raise ResourceNotFound('标签任务不存在')
        item={'id':jid,'question_id':row['question_id'],'status':row['status'],'error_code':row['error_code'],'result':loads(row['result_json'],{})}
        if row['candidate_id']:
            candidate=repo.get_candidate(row['candidate_id'])
            item['candidate']=candidate
        result.append(item)
    return {'jobs':result}


def save_content(repo, user, qid, payload):
    q = question(repo,user,qid)
    c=repo.conn
    c.execute('begin immediate')
    try:
        q=question(repo,user,qid)
        if type(payload.get('question_version')) is not int or payload['question_version']!=q['version']:
            raise StateConflict('题目已变化，请刷新后再编辑')
        binding=content(c,qid,user['school_id'])
        if binding:
            if payload.get('expected_content_revision')!=binding['current_revision_id']:
                raise StateConflict('整题内容已变化，请刷新后再编辑')
            old=binding['document']
            d=payload.get('document')
            known={r[0] for r in c.execute('select asset_id from content_asset_refs where revision_id=?',(binding['current_revision_id'],))}
            d=validate_question_document(d,known_asset_ids=known)
            if [(p['key'],p['kind']) for p in d['children']] != [(p['key'],p['kind']) for p in old['children']] or d['kind']!=old['kind']:
                raise InvalidRequest('题库编辑保留小问结构和题型；结构调整请重新导入')
            if not d['stem_md'].strip():
                raise InvalidRequest('题干不能为空')
            for part in [d,*d['children']]:
                if part.get('grading_rule') and part.get('answer_md') != next((x.get('answer_md') for x in [old,*old['children']] if x.get('key','')==part.get('key','')),None):
                    raise InvalidRequest('此题使用自定义答案核对规则，请先同步调整规则')
            from .document_ingestion import _question_rows, _legacy_question_text
            revision_id='revision-'+uuid.uuid4().hex
            n=c.execute('select max(revision_no)+1 from question_content_revisions where group_id=?',(binding['group_id'],)).fetchone()[0]
            c.execute('''insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason)
                values(?,?,?,1,?,?,'verified',?,?,?)''',(revision_id,binding['group_id'],n,dumps(d),canonical_sha256(d),d['answer_state'],user['id'],'教师题库编辑'))
            c.execute('insert into content_asset_refs(revision_id,asset_id,field_path) select ?,asset_id,field_path from content_asset_refs where revision_id=?',(revision_id,binding['current_revision_id']))
            c.execute('update question_content_groups set current_revision_id=? where id=?',(revision_id,binding['group_id']))
            rows={r['child_key']:r for r in _question_rows(d,d['stem_md'])}
            for b in c.execute('select question_id,child_key from question_content_bindings where group_id=?',(binding['group_id'],)).fetchall():
                r=rows[b['child_key']]
                c.execute('''update questions set stem=?,options_json=?,answer_json=?,analysis=?,original_question_number=?,version=version+1 where id=?''',(_legacy_question_text(r['stem']),dumps({o['key']:_legacy_question_text(o['markdown']) for o in r['options']}),dumps(r['answer_md']),_legacy_question_text(r['analysis_md']),d['number'],b['question_id']))
        else:
            fields=payload.get('fields',{})
            if not isinstance(fields,dict) or not isinstance(fields.get('stem'),str) or not fields['stem'].strip() or not isinstance(fields.get('options'),dict) or not isinstance(fields.get('answer'),str) or not isinstance(fields.get('analysis'),str):
                raise InvalidRequest('请填写有效的题干、选项、答案和解析')
            answer = fields['answer']
            if isinstance(q['answer'],dict) and 'answer' in q['answer']:
                # Preserve numeric/unit/alias matching rules when updating the reference value.
                answer = copy.deepcopy(q['answer'])
                if isinstance(answer['answer'],list):
                    answer['answer'] = [x.strip() for x in fields['answer'].replace('，',',').split(',') if x.strip()]
                else:
                    answer['answer'] = fields['answer']
            elif isinstance(q['answer'],list):
                answer = [x.strip() for x in fields['answer'].replace('，',',').split(',') if x.strip()]
            c.execute('update questions set stem=?,options_json=?,answer_json=?,analysis=?,version=version+1 where id=?',(fields['stem'],dumps(fields['options']),dumps(answer),fields['analysis'],qid))
        repo.audit(user['id'],'question_bank_content_edited','question',qid,{'previous_version':q['version'],'previous_revision':binding['current_revision_id'] if binding else None})
        c.commit()
        return detail(repo,user,qid)
    except Exception:
        c.rollback(); raise


def issue_token(repo,user,payload):
    staff(user)
    name=str(payload.get('name','')).strip()[:100]
    days=payload.get('days',30)
    if not name or type(days) is not int or not 1<=days<=90:
        raise InvalidRequest('请填写名称及 1—90 天有效期')
    token=secrets.token_urlsafe(32)
    tid='agent-'+uuid.uuid4().hex
    expiry=(datetime.now(timezone.utc)+timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    repo.conn.execute('insert into question_bank_agent_tokens(id,school_id,actor_id,name,token_hash,expires_at) values(?,?,?,?,?,?)',(tid,user['school_id'],user['id'],name,hashlib.sha256(token.encode()).hexdigest(),expiry))
    repo.audit(user['id'],'question_bank_agent_token_created','agent_token',tid,{'name':name,'expires_at':expiry})
    repo.conn.commit()
    return {'id':tid,'token':token,'expires_at':expiry}


def authenticate_agent(conn, bearer):
    row=conn.execute('''select t.id token_id,u.* from question_bank_agent_tokens t join users u on u.id=t.actor_id
        where t.token_hash=? and t.revoked_at is null and t.expires_at>? and t.school_id=u.school_id''',(hashlib.sha256(bearer.encode()).hexdigest(),now())).fetchone()
    if not row:
        raise PermissionDenied('Agent 凭证无效、过期或已撤销')
    user=dict(row)
    staff(user)
    return user,user.pop('token_id')


def tokens(repo,user):
    staff(user)
    return [dict(r) for r in repo.conn.execute('select id,name,expires_at,revoked_at from question_bank_agent_tokens where actor_id=? and school_id=? order by created_at desc',(user['id'],user['school_id']))]


def revoke_token(repo,user,tid):
    staff(user)
    if not repo.conn.execute('update question_bank_agent_tokens set revoked_at=? where id=? and actor_id=? and school_id=?',(now(),tid,user['id'],user['school_id'])).rowcount:
        raise ResourceNotFound('Agent 凭证不存在')
    repo.audit(user['id'],'question_bank_agent_token_revoked','agent_token',tid,{})
    repo.conn.commit()
    return {'message':'凭证已撤销'}


def get_api(repo,user,path,params,base_path='',agent_id=None):
    value=lambda key: (params.get(key) or [''])[0]
    if path=='/api/question-bank/library':
        return library(repo,user,value('batch_id'),value('paper_id'),value('search'),value('offset') or 0)
    if path=='/api/question-bank/taxonomy':
        return taxonomy(repo,user)
    if path=='/api/question-bank/question':
        return detail(repo,user,value('id'),base_path)
    if path=='/api/question-bank/jobs':
        return jobs(repo,user,params.get('id',[]))
    if path=='/api/question-bank/tokens' and not agent_id:
        return {'tokens':tokens(repo,user)}
    raise ResourceNotFound('接口不存在')


def post_api(repo,user,path,payload,agent_id=None):
    if path=='/api/question-bank/tags':
        return save_tags(repo,user,payload,agent_id)
    if agent_id:
        raise PermissionDenied('Agent 凭证仅允许读取题库与修改标签')
    if path=='/api/question-bank/generate':
        return queue_tags(repo,user,payload)
    if path=='/api/question-bank/content':
        return save_content(repo,user,payload.get('question_id'),payload)
    if path=='/api/question-bank/tokens':
        return issue_token(repo,user,payload)
    if path=='/api/question-bank/tokens/revoke':
        return revoke_token(repo,user,payload.get('id'))
    if path=='/api/question-bank/paper':
        staff(user)
        ids=payload.get('question_ids')
        title=payload.get('title')
        if not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(qid,str) for qid in ids) or len(set(ids))!=len(ids) or not isinstance(title,str) or not title.strip():
            raise InvalidRequest('请填写试卷名称并选择题目')
        for qid in ids:
            question(repo,user,qid)
        # Paper creation does not require tags. Examination creation retains existing checks.
        # Validate full groups separately from tag-readiness validation.
        for qid in ids:
            binding=content(repo.conn,qid,user['school_id'])
            if binding:
                all_ids={r[0] for r in repo.conn.execute('select question_id from question_content_bindings where group_id=?',(binding['group_id'],))}
                if not all_ids.issubset(set(ids)):
                    raise InvalidRequest('保存试卷时请选择完整大题的所有小问')
        return repo.assemble_paper(user['id'],title.strip()[:240],'题库组卷',[{'question_id':qid,'points':0} for qid in ids])
    raise ResourceNotFound('接口不存在')


def page(user):
    staff(user)
    return '''<section class="question-bank-page" data-question-bank>
      <div class="bank-heading"><div><p class="eyebrow">备课与考试</p><h1>题库与试卷</h1><p>按导入批次管理题目，生成或调整标签，再选整卷或选题创建考试。</p></div><a href="/teacher">返回教师工作台</a></div>
      <div class="bank-filters"><label>导入批次<select data-bank-batch><option value="">全部批次与手工录题</option></select></label><label>试卷<select data-bank-paper><option value="">全部题目</option></select></label><label>题目搜索<input type="search" data-bank-search placeholder="题干或原题号"></label><button type="button" data-bank-filter>筛选</button></div>
      <div class="bank-actions"><button type="button" data-bank-select-all>选中当前范围全部题目</button><button type="button" data-bank-clear>清空选择</button><span data-bank-selected>已选 0 道小题</span>
      <button type="button" data-bank-ai-missing>AI 生成未标注题</button><button type="button" data-bank-ai>AI 重新生成所选题</button><button type="button" data-bank-save-ai hidden>采用所选 AI 建议</button></div>
      <p data-bank-status role="status" aria-live="polite"></p><div data-bank-results></div>
      <nav class="bank-pagination"><button data-bank-prev type="button">上一页</button><span data-bank-page></span><button data-bank-next type="button">下一页</button></nav>
      <form data-bank-save-paper class="bank-save-paper"><h2>保存为一套试卷</h2><label>试卷名称<input name="title" required maxlength="240"></label><button type="submit">保存所选题目为试卷</button><a href="/teacher#new-exam">新建考试</a></form>
      <details class="bank-agent"><summary>外部 Agent 标签接口</summary><p>凭证绑定当前账号与学校，仅允许读取题目和修改标签，最长有效期 90 天。创建后仅显示一次，请交给需要接入的 Agent。</p>
      <form data-bank-token><label>Agent 名称<input name="name" required maxlength="100"></label><label>有效天数<input name="days" type="number" min="1" max="90" value="30" required></label><button type="submit">创建接入凭证</button></form>
      <div data-bank-token-result></div><div data-bank-tokens></div><a href="/question-bank/agent-guide">查看接口说明</a></details>
    </section>'''.replace('data-question-bank>', 'data-question-bank data-actor-id="'+html.escape(user['id'],quote=True)+'">')


def agent_guide():
    return '''<section class="panel"><h1>Agent 标签接口</h1><a href="/question-bank">返回题库</a>
    <p>以部署地址下的 /api/question-bank/ 为接口根路径（学校代理地址需带 /physics 前缀）。请求使用 Authorization: Bearer &lt;接入凭证&gt;；POST 使用 application/json。</p>
    <ol><li>GET library：列出批次、试卷与题目。可传 batch_id、paper_id、search、offset（每页 30 道大题）。scope_question_ids 提供当前筛选全部小题 ID。</li><li>GET taxonomy：读取启用的 knowledge、ability、literacy 标签 ID 和名称。</li><li>GET question?id=题目ID：读取完整题目 document、答案解析、units 中的小问标签及版本。</li><li>POST tags：原子批量保存三类标签。每次最多 500 道小题；每类最多 3 个标签，可为空。只接受当前学校启用的标签 ID。</li></ol>
    <pre>{"entries":[{"question_id":"题目ID","question_version":1,"expected_revision":0,"knowledge":["知识点ID"],"ability":["能力ID"],"literacy":["素养ID"]}]}</pre>
    <p>question_version 与 expected_revision 必须使用刚读取的值；返回 409 表示题目或标签已变化，请重新读取并判断后保存。保存只更新题库标签，已发布考试保持快照。返回 403 表示凭证无效、失效或账号无权限，404 表示资源不可访问。</p>
    <p>接入凭证不可编辑题目内容、调用内置模型、创建考试或读取学生数据。操作审计保留账号、凭证 ID 和题目范围；凭证可在题库页面撤销。</p></section>'''
