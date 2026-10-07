"""Live difficulty from confirmed first responses in published school exams."""
from html import escape


def classify(correct_count, response_count):
    if not response_count:
        return '未分级'
    # Compare counts directly: rounding a displayed percentage must not change a band.
    for threshold, label in ((30, '挑战'), (50, '拔高'), (70, '巩固'), (90, '基础')):
        if correct_count * 100 < response_count * threshold:
            return label
    return '送分'


def statistics(conn, question_ids):
    ids = list(dict.fromkeys(question_ids))
    result = {qid: dict(label='未分级', correct_count=0, response_count=0, correct_rate=None) for qid in ids}
    if not ids:
        return result
    outcome_mode = bool(conn.execute("select 1 from sqlite_master where name='learning_state'").fetchone())
    outcome = 'r.outcome' if outcome_mode else "case when r.score is null then 'pending' when r.score=r.max_score then 'correct' else 'wrong' end"
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        rows = conn.execute('''select r.question_id,count(*) response_count,
                   sum(case when %s='correct' then 1 else 0 end) correct_count
            from student_responses r
            join questions q on q.id=r.question_id and q.school_id=r.school_id
            join assessment_sessions a on a.id=r.assessment_id and a.school_id=r.school_id
            join question_version_snapshots s on s.id=r.snapshot_id
                and s.assessment_id=a.id and s.question_id=q.id
            join assessment_participants p on p.assessment_id=a.id and p.student_id=r.student_id
            where r.question_id in (%s) and a.grading_status='published'
                and p.status='present' and r.review_status in ('not_required','resolved','confirmed','reviewed')
                and %s in ('correct','wrong','blank')
            group by r.question_id''' % (outcome, ','.join('?' for _ in batch), outcome), batch).fetchall()
        for row in rows:
            correct, total = row['correct_count'], row['response_count']
            result[row['question_id']] = dict(label=classify(correct, total), correct_count=correct,
                                            response_count=total, correct_rate=correct / total)
    return result


def badge(stats, label=''):
    level = stats['label']
    colors = {'挑战': 'challenge', '拔高': 'advanced', '巩固': 'consolidate', '基础': 'basic', '送分': 'easy', '未分级': 'unrated'}
    detail = ('正确率 %.1f%% · %s/%s 次考试作答' %
              (stats['correct_rate'] * 100, stats['correct_count'], stats['response_count'])) if stats['response_count'] else '暂无已确认的考试作答'
    return '<span class="question-difficulty difficulty-%s" title="%s">%s难度：%s <small>%s</small></span>' % (
        colors[level], escape('按本校已发布考试首次作答统计；空白计入，待复核与重做不计入。' + detail),
        escape(label + ' · ' if label else ''), level, detail)


def badges(conn, units):
    stats = statistics(conn, [qid for qid, _ in units])
    return '<div class="question-difficulty-list">' + ''.join(badge(stats[qid], label) for qid, label in units) + '</div>'
