import hashlib
import json


PROMPT_VERSION = "local-deterministic-v2"
MODEL_VERSION = "rules-only"


def candidate_cache_key(question, ontology_version):
    raw = json.dumps(
        {
            "question_id": question["id"],
            "stem": question["stem"],
            "type": question["question_type"],
            "ontology_version": ontology_version,
            "prompt_version": PROMPT_VERSION,
            "model_version": MODEL_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _combined_question_text(question):
    return " ".join(
        [
            question.get("stem") or "",
            question.get("chapter") or "",
            question.get("analysis") or "",
            question.get("scenario") or "",
        ]
    ).lower()


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def _candidate(item, confidence, rationale):
    return {
        "id": item["id"],
        "name": item["name"],
        "confidence": confidence,
        "rationale": rationale,
    }


def _stable_sort(items):
    return sorted(
        items,
        key=lambda item: (
            -item["confidence"],
            item.get("stable_code", ""),
            item["id"],
        ),
    )


def _match_tag(tags, text, rules):
    matches = []
    for tag in tags:
        haystack = " ".join(
            [
                tag.get("id", ""),
                tag.get("stable_code", ""),
                tag.get("name", ""),
                tag.get("description", ""),
            ]
        ).lower()
        for key, keywords, confidence, rationale in rules:
            if key in haystack and _contains_any(text, keywords):
                matches.append(
                    {
                        **_candidate(tag, confidence, rationale),
                        "stable_code": tag.get("stable_code", ""),
                    }
                )
                break
    return _stable_sort(matches)


def generate_candidate_tags(
    question,
    knowledge_nodes,
    ability_tags,
    literacy_tags,
    ontology_version,
):
    text = _combined_question_text(question)
    chapter = (question.get("chapter") or "").lower()
    knowledge = []

    for node in knowledge_nodes:
        name = node.get("name", "")
        haystack = " ".join(
            [
                name,
                node.get("aliases", ""),
                node.get("description", ""),
                node.get("node_type", ""),
                node.get("stable_code", ""),
                node.get("textbook_scope", ""),
            ]
        ).lower()
        confidence = 0.0
        rationale = ""
        if name and name.lower() in text:
            confidence = 0.9
            rationale = "题干、解析或章节中直接出现该知识点名称。"
        elif chapter and (
            (name and name.lower() in chapter) or chapter in haystack
        ):
            confidence = 0.82
            rationale = "题目章节与知识点教材范围或名称匹配。"
        elif (
            "牛顿" in text
            and ("牛顿" in haystack or "newton" in haystack)
        ) or (
            "功" in text
            and "功" in haystack
        ) or (
            "匀变速" in text
            and "运动" in haystack
        ):
            confidence = 0.78
            rationale = "题干关键词与知识点名称或别名匹配。"
        if confidence:
            knowledge.append(
                {
                    **_candidate(node, confidence, rationale),
                    "stable_code": node.get("stable_code", ""),
                }
            )

    ability_rules = [
        (
            "force",
            ["力", "加速度", "相互作用", "受力"],
            0.84,
            "题目涉及力、加速度或相互作用分析。",
        ),
        (
            "equation",
            ["方程", "求", "关系", "表达式"],
            0.8,
            "题目需要建立物理量关系或方程。",
        ),
        (
            "context_model",
            ["物体", "情境", "模型", "过程"],
            0.78,
            "题目需要从情境抽取对象和变量。",
        ),
        (
            "model",
            ["模型", "建构", "抽象"],
            0.82,
            "题目需要选择或建立物理模型。",
        ),
        (
            "data",
            ["实验", "数据", "图像", "证据", "关系"],
            0.82,
            "题目需要整理、表示或分析实验与数据证据。",
        ),
        (
            "argument",
            ["推理", "论证", "解释", "证据", "关系"],
            0.8,
            "题目需要基于证据和规律形成结论。",
        ),
        (
            "calculation",
            ["计算", "大小", "多少", "求"],
            0.76,
            "题目包含定量计算要求。",
        ),
    ]
    abilities = _match_tag(ability_tags, text, ability_rules)

    literacy_rules = [
        (
            "inquiry.evidence",
            ["证据", "实验", "数据", "观察"],
            0.86,
            "题目强调实验、数据或证据获取。",
        ),
        (
            "thinking.model",
            ["模型", "建构", "抽象"],
            0.84,
            "题目强调模型建构或模型解释。",
        ),
        (
            "thinking.reasoning",
            ["推理", "关系", "规律", "解释"],
            0.8,
            "题目要求基于规律进行科学推理。",
        ),
        (
            "thinking.argument",
            ["论证", "证据", "结论", "评价"],
            0.8,
            "题目要求使用证据和逻辑支持结论。",
        ),
        (
            "concept.energy",
            ["能量", "守恒", "转化"],
            0.78,
            "题目涉及能量观念。",
        ),
        (
            "concept.matter",
            ["物质", "结构", "属性"],
            0.74,
            "题目涉及物质观念。",
        ),
        (
            "attitude.responsibility",
            ["责任", "社会", "环境", "安全"],
            0.72,
            "题目涉及科学态度与社会责任。",
        ),
    ]
    literacies = _match_tag(literacy_tags, text, literacy_rules)

    if not knowledge and knowledge_nodes:
        node = knowledge_nodes[0]
        knowledge.append(
            {
                **_candidate(
                    node,
                    0.52,
                    "未命中强关键词，仅作为低置信候选等待教师判断。",
                ),
                "stable_code": node.get("stable_code", ""),
            }
        )
    if not abilities and ability_tags:
        tag = ability_tags[0]
        abilities.append(
            {
                **_candidate(
                    tag,
                    0.5,
                    "缺少明确能力线索，仅用于进入审核队列。",
                ),
                "stable_code": tag.get("stable_code", ""),
            }
        )
    if not literacies and literacy_tags:
        tag = literacy_tags[0]
        literacies.append(
            {
                **_candidate(
                    tag,
                    0.5,
                    "缺少明确核心素养线索，仅用于进入审核队列。",
                ),
                "stable_code": tag.get("stable_code", ""),
            }
        )

    return {
        "knowledge_tags": _stable_sort(knowledge)[:5],
        "ability_tags": _stable_sort(abilities)[:5],
        "literacy_tags": _stable_sort(literacies)[:5],
        "prompt_version": PROMPT_VERSION,
        "model_version": MODEL_VERSION,
        "cache_key": candidate_cache_key(question, ontology_version),
    }
