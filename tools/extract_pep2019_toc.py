#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
from pathlib import Path


ONTOLOGY_LABEL = "pep-2019-physics-v1"
CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
}

BOOKS = [
    {
        "code": "r1",
        "name": "必修第一册",
        "file_name": "（2019版）普通高中教科书物理必修1.pdf",
        "source_key": "pep2019-r1",
    },
    {
        "code": "r2",
        "name": "必修第二册",
        "file_name": "（2019版）普通高中教科书物理必修2.pdf",
        "source_key": "pep2019-r2",
    },
    {
        "code": "r3",
        "name": "必修第三册",
        "file_name": "（2019版）普通高中教科书物理必修3.pdf",
        "source_key": "pep2019-r3",
    },
    {
        "code": "e1",
        "name": "选择性必修第一册",
        "file_name": "（2019版）普通高中教科书物理选择性必修1.pdf",
        "source_key": "pep2019-e1",
    },
    {
        "code": "e2",
        "name": "选择性必修第二册",
        "file_name": "（2019版）普通高中教科书物理选择性必修2.pdf",
        "source_key": "pep2019-e2",
    },
    {
        "code": "e3",
        "name": "选择性必修第三册",
        "file_name": "（2019版）普通高中教科书物理选择性必修3.pdf",
        "source_key": "pep2019-e3",
    },
]

CURRICULUM_TOPICS = [
    ("required-1-1", "CS.REQUIRED.1.1", "机械运动与物理模型", "必修课程", 20),
    ("required-1-2", "CS.REQUIRED.1.2", "相互作用与运动定律", "必修课程", 21),
    ("required-2-1", "CS.REQUIRED.2.1", "机械能及其守恒定律", "必修课程", 23),
    ("required-2-2", "CS.REQUIRED.2.2", "曲线运动与万有引力定律", "必修课程", 24),
    ("required-2-3", "CS.REQUIRED.2.3", "牛顿力学的局限性与相对论初步", "必修课程", 25),
    ("required-3-1", "CS.REQUIRED.3.1", "静电场", "必修课程", 27),
    ("required-3-2", "CS.REQUIRED.3.2", "电路及其应用", "必修课程", 28),
    ("required-3-3", "CS.REQUIRED.3.3", "电磁场与电磁波初步", "必修课程", 29),
    ("required-3-4", "CS.REQUIRED.3.4", "能源与可持续发展", "必修课程", 30),
    ("elective-1-1", "CS.ELECTIVE.1.1", "动量与动量守恒定律", "选择性必修课程", 33),
    ("elective-1-2", "CS.ELECTIVE.1.2", "机械振动与机械波", "选择性必修课程", 34),
    ("elective-1-3", "CS.ELECTIVE.1.3", "光及其应用", "选择性必修课程", 34),
    ("elective-2-1", "CS.ELECTIVE.2.1", "磁场", "选择性必修课程", 37),
    ("elective-2-2", "CS.ELECTIVE.2.2", "电磁感应及其应用", "选择性必修课程", 37),
    ("elective-2-3", "CS.ELECTIVE.2.3", "电磁振荡与电磁波", "选择性必修课程", 38),
    ("elective-2-4", "CS.ELECTIVE.2.4", "传感器", "选择性必修课程", 39),
    ("elective-3-1", "CS.ELECTIVE.3.1", "固体、液体和气体", "选择性必修课程", 41),
    ("elective-3-2", "CS.ELECTIVE.3.2", "热力学定律", "选择性必修课程", 42),
    ("elective-3-3", "CS.ELECTIVE.3.3", "原子与原子核", "选择性必修课程", 43),
    ("elective-3-4", "CS.ELECTIVE.3.4", "波粒二象性", "选择性必修课程", 43),
]

CHAPTER_TOPIC_MAP = {
    ("r1", 1): ["required-1-1"],
    ("r1", 2): ["required-1-1"],
    ("r1", 3): ["required-1-2"],
    ("r1", 4): ["required-1-2"],
    ("r2", 5): ["required-2-2"],
    ("r2", 6): ["required-2-2"],
    ("r2", 7): ["required-2-2", "required-2-3"],
    ("r2", 8): ["required-2-1"],
    ("r3", 9): ["required-3-1"],
    ("r3", 10): ["required-3-1"],
    ("r3", 11): ["required-3-2"],
    ("r3", 12): ["required-3-4"],
    ("r3", 13): ["required-3-3"],
    ("e1", 1): ["elective-1-1"],
    ("e1", 2): ["elective-1-2"],
    ("e1", 3): ["elective-1-2"],
    ("e1", 4): ["elective-1-3"],
    ("e2", 1): ["elective-2-1"],
    ("e2", 2): ["elective-2-2"],
    ("e2", 3): ["elective-2-3"],
    ("e2", 4): ["elective-2-3"],
    ("e2", 5): ["elective-2-4"],
    ("e3", 1): ["elective-3-1"],
    ("e3", 2): ["elective-3-1"],
    ("e3", 3): ["elective-3-2"],
    ("e3", 4): ["elective-3-3", "elective-3-4"],
    ("e3", 5): ["elective-3-3"],
}


def normalize_title(value):
    value = re.sub(r"\s+", " ", value).strip()
    value = value.replace("实验 ：", "实验：")
    value = value.replace("实验:", "实验：")
    return value


def extract_toc(pdf_path):
    text = subprocess.check_output(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        text=True,
    )
    chapters = []
    in_toc = False
    end_page = None
    chapter_pattern = re.compile(
        r"^第([一二三四五六七八九十]+)章\s+(.+?)\s+(\d+)$"
    )
    section_pattern = re.compile(r"^(\d+)\.\s*(.+?)\s+(\d+)$")
    end_pattern = re.compile(r"^课题研究\s+(\d+)$")

    for raw_line in text.splitlines():
        line = normalize_title(raw_line)
        if not in_toc:
            if line.replace(" ", "") == "目录":
                in_toc = True
            continue
        end_match = end_pattern.match(line)
        if end_match:
            end_page = int(end_match.group(1))
            break
        chapter_match = chapter_pattern.match(line)
        if chapter_match:
            chapter_number = CHINESE_NUMBERS[chapter_match.group(1)]
            chapters.append(
                {
                    "number": chapter_number,
                    "name": normalize_title(chapter_match.group(2)),
                    "page": int(chapter_match.group(3)),
                    "sections": [],
                }
            )
            continue
        section_match = section_pattern.match(line)
        if section_match and chapters:
            chapters[-1]["sections"].append(
                {
                    "number": int(section_match.group(1)),
                    "name": normalize_title(section_match.group(2)),
                    "page": int(section_match.group(3)),
                }
            )

    if not chapters or end_page is None:
        raise ValueError("Could not parse complete TOC from %s" % pdf_path)
    return chapters, end_page


def source_ref(source_key, start, end, locator):
    return {
        "source_key": source_key,
        "page_start": start,
        "page_end": end,
        "locator": locator,
        "evidence_summary": "教材目录与正文标题核验",
    }


def build_book_records(book, chapters, end_page):
    code = book["code"]
    source_key = book["source_key"]
    module_id = "kn-pep2019-%s" % code
    records = [
        {
            "default_key": "pep2019.%s" % code,
            "id": module_id,
            "stable_code": "K.PEP2019.%s" % code.upper(),
            "parent_id": None,
            "name": book["name"],
            "node_type": "textbook_volume",
            "level": 1,
            "aliases": [],
            "description": "人民教育出版社 2019 版普通高中物理教材课程模块",
            "textbook_scope": book["name"],
            "source_refs": [
                source_ref(
                    source_key,
                    chapters[0]["page"],
                    end_page - 1,
                    book["name"],
                )
            ],
        }
    ]

    for chapter_index, chapter in enumerate(chapters):
        chapter_number = chapter["number"]
        chapter_id = "%s-c%02d" % (module_id, chapter_number)
        chapter_end = (
            chapters[chapter_index + 1]["page"] - 1
            if chapter_index + 1 < len(chapters)
            else end_page - 1
        )
        records.append(
            {
                "default_key": "pep2019.%s.c%02d"
                % (code, chapter_number),
                "id": chapter_id,
                "stable_code": "K.PEP2019.%s.C%02d"
                % (code.upper(), chapter_number),
                "parent_id": module_id,
                "name": chapter["name"],
                "node_type": "textbook_chapter",
                "level": 2,
                "aliases": [],
                "description": "",
                "textbook_scope": "%s 第%d章"
                % (book["name"], chapter_number),
                "source_refs": [
                    source_ref(
                        source_key,
                        chapter["page"],
                        chapter_end,
                        "第%d章 %s" % (chapter_number, chapter["name"]),
                    )
                ],
            }
        )
        for section_index, section in enumerate(chapter["sections"]):
            section_end = (
                chapter["sections"][section_index + 1]["page"] - 1
                if section_index + 1 < len(chapter["sections"])
                else chapter_end
            )
            records.append(
                {
                    "default_key": "pep2019.%s.c%02d.s%02d"
                    % (code, chapter_number, section["number"]),
                    "id": "%s-s%02d" % (chapter_id, section["number"]),
                    "stable_code": "K.PEP2019.%s.C%02d.S%02d"
                    % (
                        code.upper(),
                        chapter_number,
                        section["number"],
                    ),
                    "parent_id": chapter_id,
                    "name": section["name"],
                    "node_type": "textbook_section",
                    "level": 3,
                    "aliases": [],
                    "description": "",
                    "textbook_scope": "%s 第%d章"
                    % (book["name"], chapter_number),
                    "source_refs": [
                        source_ref(
                            source_key,
                            section["page"],
                            section_end,
                            "第%d章 第%d节"
                            % (chapter_number, section["number"]),
                        )
                    ],
                }
            )
    return records


def build_curriculum_topics():
    return [
        {
            "id": "ct-%s" % key,
            "stable_code": stable_code,
            "name": name,
            "course_module": course_module,
            "source_refs": [
                {
                    "source_key": "curriculum-standard-2017-2020",
                    "page_start": page,
                    "page_end": page,
                    "locator": "%s %s" % (stable_code, name),
                    "evidence_summary": "课程标准主题标题",
                }
            ],
        }
        for key, stable_code, name, course_module, page in CURRICULUM_TOPICS
    ]


def build_curriculum_mappings():
    mappings = []
    topics_by_key = {
        key: {
            "stable_code": stable_code,
            "name": name,
            "page": page,
        }
        for key, stable_code, name, _course_module, page in CURRICULUM_TOPICS
    }
    for (book_code, chapter_number), topic_keys in CHAPTER_TOPIC_MAP.items():
        knowledge_id = "kn-pep2019-%s-c%02d" % (
            book_code,
            chapter_number,
        )
        for topic_key in topic_keys:
            topic = topics_by_key[topic_key]
            mappings.append(
                {
                    "id": "map-%s-%s"
                    % (
                        knowledge_id.removeprefix("kn-"),
                        topic_key,
                    ),
                    "knowledge_node_id": knowledge_id,
                    "curriculum_topic_id": "ct-%s" % topic_key,
                    "mapping_type": "aligned",
                    "rationale": "教材章主题与课程标准主题对应",
                    "source_refs": [
                        {
                            "source_key": "curriculum-standard-2017-2020",
                            "page_start": topic["page"],
                            "page_end": topic["page"],
                            "locator": "%s %s"
                            % (topic["stable_code"], topic["name"]),
                            "evidence_summary": "课程标准主题与教材章主题对照",
                        }
                    ],
                }
            )
    return mappings


def build_manifest(textbook_dir):
    records = []
    for book in BOOKS:
        pdf_path = textbook_dir / book["file_name"]
        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)
        chapters, end_page = extract_toc(pdf_path)
        records.extend(build_book_records(book, chapters, end_page))

    level_counts = {
        level: sum(record["level"] == level for record in records)
        for level in (1, 2, 3)
    }
    if level_counts != {1: 6, 2: 27, 3: 125}:
        raise ValueError("Unexpected TOC counts: %r" % level_counts)

    return {
        "manifest_version": 1,
        "ontology_label": ONTOLOGY_LABEL,
        "source_keys": [book["source_key"] for book in BOOKS]
        + ["curriculum-standard-2017-2020"],
        "records": records,
        "curriculum_topics": build_curriculum_topics(),
        "curriculum_mappings": build_curriculum_mappings(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--textbook-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.textbook_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
