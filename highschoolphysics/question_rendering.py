"""Safe Markdown rendering shared by teacher and student question views."""

import html
import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt


ASSET_URI_RE = re.compile(r"^asset:([A-Za-z0-9_-]{1,64})$")
ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}


def _math_inline_rule(state, silent):
    source = state.src
    start = state.pos
    if source.startswith("$$", start):
        opening = closing = "$$"
    elif source.startswith("$", start):
        opening = closing = "$"
    elif source.startswith(r"\(", start):
        opening, closing = r"\(", r"\)"
    elif source.startswith(r"\[", start):
        opening, closing = r"\[", r"\]"
    else:
        return False

    cursor = start + len(opening)
    while cursor < state.posMax:
        end = source.find(closing, cursor, state.posMax)
        if end < 0:
            return False
        backslashes = 0
        probe = end - 1
        while probe >= cursor and source[probe] == "\\":
            backslashes += 1
            probe -= 1
        if backslashes % 2 == 0:
            content = source[cursor:end]
            if not content:
                return False
            if not silent:
                token = state.push("math_inline", "", 0)
                token.content = content
                token.markup = opening
                token.info = closing
                state.pos = end + len(closing)
            return True
        cursor = end + len(closing)
    return False


def _render_math_inline(self, tokens, index, _options, _env):
    token = tokens[index]
    return html.escape(token.markup + token.content + token.info, quote=False)


def question_math_asset_tags(asset_version):
    version = html.escape(str(asset_version), quote=True)
    head = (
        '<link rel="stylesheet" href="/assets/vendor/katex/katex.min.css?v=%s">'
        '<link rel="stylesheet" href="/assets/question-rendering.css?v=%s">'
    ) % (version, version)
    scripts = (
        '<script defer src="/assets/vendor/katex/katex.min.js?v=%s"></script>'
        '<script defer src="/assets/vendor/katex/contrib/auto-render.min.js?v=%s"></script>'
        '<script defer src="/assets/question-rendering.js?v=%s"></script>'
    ) % (version, version, version)
    return head, scripts


def _allowed_image_url(value):
    return isinstance(value, str) and value.startswith("/") and not value.startswith("//")


def _make_markdown_parser(asset_url):
    parser = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": False,
            "breaks": False,
        },
    ).enable(("table", "strikethrough"))
    # Protect math before CommonMark emphasis parses LaTeX subscripts such as
    # F_{1}. The bundled KaTeX auto-render pass consumes these intact delimiters.
    parser.inline.ruler.before("escape", "math_inline", _math_inline_rule)
    parser.add_render_rule("math_inline", _render_math_inline)

    def render_image(_renderer, tokens, index, _options, _env):
        token = tokens[index]
        source = token.attrGet("src") or ""
        match = ASSET_URI_RE.fullmatch(source)
        if not match:
            return '<span class="question-image-unavailable">图片引用无效：%s</span>' % html.escape(
                token.content or "未命名图片"
            )
        resolved = asset_url(match.group(1)) if asset_url else None
        if not _allowed_image_url(resolved):
            return '<span class="question-image-unavailable">图片待复核：%s</span>' % html.escape(
                token.content or "未命名图片"
            )
        return (
            '<img class="question-content-image" src="%s" alt="%s" '
            'loading="lazy" decoding="async">'
            % (html.escape(resolved, quote=True), html.escape(token.content or "", quote=True))
        )

    def render_link_open(renderer, tokens, index, options, env):
        token = tokens[index]
        href = token.attrGet("href") or ""
        parsed = urlsplit(href)
        safe = (
            parsed.scheme.lower() in ALLOWED_LINK_SCHEMES
            or (not parsed.scheme and href.startswith("#"))
        )
        if not safe:
            token.attrSet("href", "#blocked-content-link")
            token.attrSet("aria-disabled", "true")
            token.attrSet("class", "blocked-content-link")
            return renderer.renderToken(tokens, index, options, env)
        if parsed.scheme.lower() in ("http", "https"):
            token.attrSet("target", "_blank")
            token.attrSet("rel", "noopener noreferrer")
        return renderer.renderToken(tokens, index, options, env)

    parser.add_render_rule("image", render_image)
    parser.add_render_rule("link_open", render_link_open)
    return parser


def render_markdown(markdown, asset_url=None):
    if not isinstance(markdown, str):
        raise TypeError("Markdown content must be a string")
    parser = _make_markdown_parser(asset_url)
    safe_headings = re.sub(
        r"(?m)^## (题干|选项|小问|答案|解析)\s*$",
        lambda match: "### " + match.group(1),
        markdown,
    )
    return parser.render(safe_headings)


def render_question(document, asset_url=None, include_solution=False, child_key=None, include_options=True):
    """Render one full question or a child with shared parent conditions."""
    title = html.escape(str(document.get("number", "")))
    parts = ['<article class="question-content" data-question-number="%s">' % title]
    parts.append('<div class="question-stem">%s</div>' % render_markdown(document.get("stem_md", ""), asset_url))

    if include_options and document.get("options"):
        parts.append('<ol class="question-options">')
        for option in document["options"]:
            parts.append(
                '<li data-option-key="%s"><span class="option-key">%s.</span>'
                '<div>%s</div></li>'
                % (
                    html.escape(option["key"], quote=True),
                    html.escape(option["key"]),
                    render_markdown(option["markdown"], asset_url),
                )
            )
        parts.append("</ol>")

    children = document.get("children", [])
    selected = None
    if child_key is not None:
        selected = next((item for item in children if item.get("key") == child_key), None)
        if selected is None:
            raise ValueError("Unknown child key")
    display_children = [selected] if selected else children
    if display_children:
        parts.append('<ol class="question-children">')
        for child in display_children:
            parts.append(
                '<li data-child-key="%s"><strong>%s</strong><div>%s</div>'
                % (
                    html.escape(child["key"], quote=True),
                    html.escape(child["label"]),
                    render_markdown(child["stem_md"], asset_url),
                )
            )
            if include_options and child.get("options"):
                parts.append('<ol class="question-options">')
                for option in child["options"]:
                    parts.append(
                        '<li data-option-key="%s"><span class="option-key">%s.</span>%s</li>'
                        % (
                            html.escape(option["key"], quote=True),
                            html.escape(option["key"]),
                            render_markdown(option["markdown"], asset_url),
                        )
                    )
                parts.append("</ol>")
            parts.append("</li>")
        parts.append("</ol>")

    if include_solution:
        answer_parts = []
        if document.get("answer_md"):
            answer_parts.append(render_markdown(document["answer_md"], asset_url))
        if selected:
            if selected.get("answer_md"):
                answer_parts.append(render_markdown(selected["answer_md"], asset_url))
            if selected.get("analysis_md"):
                answer_parts.append(render_markdown(selected["analysis_md"], asset_url))
        elif children:
            for child in children:
                if child.get("answer_md"):
                    answer_parts.append("<h4>%s</h4>" % html.escape(child["label"]))
                    answer_parts.append(render_markdown(child["answer_md"], asset_url))
                if child.get("analysis_md"):
                    answer_parts.append(render_markdown(child["analysis_md"], asset_url))
        if not children and document.get("analysis_md"):
            answer_parts.append(render_markdown(document["analysis_md"], asset_url))
        if answer_parts:
            parts.append('<section class="question-solution"><h3>参考答案与解析</h3>%s</section>' % "".join(answer_parts))
    parts.append("</article>")
    return "".join(parts)
