"""Deterministic graph layout helpers for student knowledge maps."""


LAYOUT_VERSION = "deterministic-layered-v1"


def _node_sort_key(node):
    return (
        int(node.get("level") or 0),
        node.get("stable_code") or "",
        node.get("name") or "",
        node.get("id") or "",
    )


def layout_knowledge_graph(nodes, edges, width=720, min_height=320):
    ordered = sorted([dict(node) for node in nodes], key=_node_sort_key)
    levels = {}
    for node in ordered:
        level = int(node.get("level") or 1)
        levels.setdefault(level, []).append(node)
    if not levels:
        return {
            "layout": LAYOUT_VERSION,
            "view_box": {"width": width, "height": min_height},
            "nodes": [],
            "edges": [],
        }

    level_values = sorted(levels)
    max_level_size = max(len(items) for items in levels.values())
    height = max(min_height, 90 + max_level_size * 64)
    column_gap = width / max(1, len(level_values) + 1)
    positioned = []
    position_by_id = {}
    for column, level in enumerate(level_values, start=1):
        items = levels[level]
        row_gap = height / max(1, len(items) + 1)
        for row, node in enumerate(items, start=1):
            x = round(column * column_gap, 1)
            y = round(row * row_gap, 1)
            detail_level = "module" if level <= 1 else "child"
            item = {
                **node,
                "x": x,
                "y": y,
                "detail_level": detail_level,
                "min_label_scale": 0.55 if detail_level == "module" else 1.15,
            }
            positioned.append(item)
            position_by_id[item["id"]] = item

    rendered_edges = []
    for edge in sorted(
        edges,
        key=lambda item: (
            item["source_node_id"],
            item["target_node_id"],
        ),
    ):
        start = position_by_id.get(edge["source_node_id"])
        end = position_by_id.get(edge["target_node_id"])
        if not start or not end:
            continue
        rendered_edges.append({"edge": edge, "source": start, "target": end})

    return {
        "layout": LAYOUT_VERSION,
        "view_box": {"width": width, "height": height},
        "nodes": positioned,
        "edges": rendered_edges,
    }
