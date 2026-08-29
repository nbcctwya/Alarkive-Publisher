from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape, unescape
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


class RendererError(RuntimeError):
    """An expected Markdown rendering failure."""


@dataclass(frozen=True)
class RenderedContent:
    """Platform-ready representations of one Markdown source string."""

    text: str
    html: str | None = None


@dataclass
class _Node:
    token: Token
    children: list[_Node]


_MARKDOWN = MarkdownIt("commonmark", {"html": False})
_PLAIN_PLATFORMS = {"xiaohongshu", "wechat"}
_RICH_PLATFORMS = {"baijiahao"}
_INLINE_PAIRS = {
    "strong_open": ("strong_close", "strong"),
    "em_open": ("em_close", "em"),
    "s_open": ("s_close", "s"),
}


def _parse(source: str) -> list[Token]:
    try:
        return _MARKDOWN.parse(source)
    except Exception as exc:  # pragma: no cover - parser failures are uncommon
        raise RendererError(f"Could not parse Markdown: {exc}") from exc


def _build_tree(tokens: list[Token]) -> list[_Node]:
    root: list[_Node] = []
    stack: list[list[_Node]] = [root]
    for token in tokens:
        if token.nesting == 1:
            node = _Node(token, [])
            stack[-1].append(node)
            stack.append(node.children)
        elif token.nesting == -1:
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(_Node(token, []))
    return root


def _raw_html_to_text(value: str) -> str:
    # Raw HTML is disabled in MarkdownIt. This fallback also keeps a malformed
    # or unsupported HTML fragment readable without passing tags downstream.
    return unescape(re.sub(r"<[^>]*>", "", value))


def _safe_href(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme.lower() in {"http", "https", "mailto"}:
        return value
    return None


def _token_attr(token: Token, name: str) -> str:
    value = token.attrGet(name)
    return value if isinstance(value, str) else ""


def _render_inline_tokens(tokens: list[Token], rich: bool) -> str:
    def render_range(start: int, closing: str | None = None) -> tuple[str, int]:
        result: list[str] = []
        index = start
        while index < len(tokens):
            token = tokens[index]
            if closing is not None and token.type == closing:
                return "".join(result), index + 1

            if token.type == "link_open":
                inner, index = render_range(index + 1, "link_close")
                href = _token_attr(token, "href")
                if not href:
                    result.append(inner)
                elif rich and _safe_href(href) is not None:
                    result.append(
                        f'<a href="{escape(_safe_href(href) or "", quote=True)}">'
                        f"{inner}</a>"
                    )
                elif rich:
                    result.append(f"{inner}（{escape(href, quote=False)}）")
                else:
                    result.append(f"{inner}（{href}）")
                continue

            if token.type in _INLINE_PAIRS:
                close_type, tag = _INLINE_PAIRS[token.type]
                inner, index = render_range(index + 1, close_type)
                if rich:
                    result.append(f"<{tag}>{inner}</{tag}>")
                else:
                    result.append(inner)
                continue

            if token.type in {"strong_close", "em_close", "s_close", "link_close"}:
                return "".join(result), index

            if token.type in {"softbreak", "hardbreak"}:
                result.append("<br>\n" if rich and token.type == "hardbreak" else "\n")
            elif token.type == "text":
                result.append(escape(token.content, quote=False) if rich else token.content)
            elif token.type == "code_inline":
                value = escape(token.content, quote=False) if rich else token.content
                result.append(f"<code>{value}</code>" if rich else value)
            elif token.type == "image":
                alt = _token_attr(token, "alt") or token.content or "图片"
                label = f"[图片：{alt}]"
                result.append(escape(label, quote=False) if rich else label)
            elif token.type in {"html_inline", "html_block"}:
                value = _raw_html_to_text(token.content)
                result.append(escape(value, quote=False) if rich else value)
            else:
                # Unsupported inline extensions are rendered as escaped text,
                # never as arbitrary HTML.
                value = token.content or ""
                result.append(escape(value, quote=False) if rich else value)
            index += 1
        return "".join(result), index

    rendered, _ = render_range(0)
    return rendered


def _inline_content(node: _Node, rich: bool) -> str:
    return _render_inline_tokens(node.token.children or [], rich)


def _node_content(nodes: list[_Node], rich: bool) -> str:
    return "".join(
        _inline_content(node, rich)
        for node in nodes
        if node.token.type == "inline"
    )


def _code_content(token: Token) -> str:
    return token.content.rstrip("\r\n")


def _render_list_item_plain(node: _Node, prefix: str) -> str:
    blocks = _render_blocks(node.children, rich=False)
    if not blocks:
        return ""
    lines = blocks[0].splitlines() or [blocks[0]]
    result = [prefix + lines[0]]
    result.extend("  " + line for line in lines[1:])
    for block in blocks[1:]:
        result.extend("  " + line for line in block.splitlines())
    return "\n".join(result)


def _render_list(node: _Node, rich: bool) -> str:
    ordered = node.token.type == "ordered_list_open"
    start = 1
    if ordered:
        try:
            start = int(_token_attr(node.token, "start") or "1")
        except ValueError:
            start = 1

    items = [child for child in node.children if child.token.type == "list_item_open"]
    if not rich:
        rendered_items = []
        for offset, item in enumerate(items):
            prefix = f"{start + offset}. " if ordered else "• "
            rendered = _render_list_item_plain(item, prefix)
            if rendered:
                rendered_items.append(rendered)
        return "\n".join(rendered_items)

    tag = "ol" if ordered else "ul"
    rendered_items = []
    for item in items:
        blocks = _render_blocks(item.children, rich=True)
        if blocks:
            item_content = "\n".join(blocks)
            rendered_items.append(f"<li>{item_content}</li>")
    list_content = "\n".join(rendered_items)
    return f"<{tag}>\n{list_content}\n</{tag}>"


def _render_blocks(nodes: list[_Node], rich: bool) -> list[str]:
    blocks: list[str] = []
    for node in nodes:
        token = node.token
        if token.type in {"paragraph_open", "heading_open"}:
            value = _node_content(node.children, rich)
            if not value:
                continue
            if rich:
                tag = "p" if token.type == "paragraph_open" else token.tag
                if tag not in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
                    tag = "p"
                blocks.append(f"<{tag}>{value}</{tag}>")
            else:
                blocks.append(value)
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            value = _render_list(node, rich)
            if value:
                blocks.append(value)
        elif token.type == "blockquote_open":
            inner = _render_blocks(node.children, rich)
            if not inner:
                continue
            if rich:
                inner_content = "\n".join(inner)
                blocks.append(f"<blockquote>\n{inner_content}\n</blockquote>")
            else:
                blocks.append("「" + "\n".join(inner) + "」")
        elif token.type in {"code_block", "fence"}:
            value = _code_content(token)
            if rich:
                code = escape(value, quote=False).replace("\n", "<br>\n")
                blocks.append(f"<p><code>{code}</code></p>")
            else:
                blocks.append(value)
        elif token.type == "hr":
            blocks.append("<p>——</p>" if rich else "——")
        elif token.type == "html_block":
            value = _raw_html_to_text(token.content)
            if value:
                blocks.append(
                    f"<p>{escape(value, quote=False)}</p>" if rich else value
                )
        elif token.type == "inline":
            value = _render_inline_tokens(token.children or [], rich)
            if value:
                blocks.append(value)
        elif token.type == "list_item_open":
            # This is normally handled by _render_list, but rendering it here
            # keeps malformed or unusual token streams readable.
            value = _render_blocks(node.children, rich)
            if value:
                blocks.append("\n".join(value))
    return blocks


def _finish_plain(blocks: list[str]) -> str:
    return "\n\n".join(blocks).strip("\r\n")


def _finish_html(blocks: list[str]) -> str:
    return "\n".join(blocks).strip()


def render_plain(markdown: str) -> str:
    """Render Markdown as readable text without leaking Markdown markers."""

    if not isinstance(markdown, str):
        raise RendererError("Markdown source must be a string.")
    return _finish_plain(_render_blocks(_build_tree(_parse(markdown)), rich=False))


def render_html(markdown: str) -> str:
    """Render Markdown as a small, safe HTML subset."""

    if not isinstance(markdown, str):
        raise RendererError("Markdown source must be a string.")
    return _finish_html(_render_blocks(_build_tree(_parse(markdown)), rich=True))


def render_for_platform(platform: str, markdown: str) -> RenderedContent:
    """Apply the v0.1.2 renderer policy for one platform."""

    if platform in _PLAIN_PLATFORMS:
        return RenderedContent(text=render_plain(markdown))
    if platform in _RICH_PLATFORMS:
        return RenderedContent(text=render_plain(markdown), html=render_html(markdown))
    raise RendererError(f"Unsupported rendering platform: {platform}")
