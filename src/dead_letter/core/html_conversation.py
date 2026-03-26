"""DOM-aware HTML conversation segmentation."""

from __future__ import annotations

from html import escape

from selectolax.parser import HTMLParser

from dead_letter.core.conversation import ConversationResult
from dead_letter.core.sanitize import sanitize_html
from dead_letter.core.types import ConversationZone, ZoneKind


def _body_text(tree: HTMLParser) -> str:
    if tree.body is not None:
        return tree.body.text(separator="\n", strip=True)
    return tree.text(separator="\n", strip=True)


def _body_html(tree: HTMLParser) -> str:
    if tree.body is not None and tree.body.html:
        return tree.body.html
    return tree.html or ""


def _node_html(node) -> str:
    return node.html or node.text(separator="\n", strip=True) or ""


def _wrap_node_html(node, inner_html: str) -> str:
    if not inner_html:
        return ""
    if node.tag == "-text":
        return inner_html

    attrs = "".join(
        f' {name}="{escape(value, quote=True)}"'
        for name, value in node.attributes.items()
        if value is not None
    )
    return f"<{node.tag}{attrs}>{inner_html}</{node.tag}>"


def _split_node_html(node, quote_mem_id: int) -> tuple[str, str, bool]:
    if node.mem_id == quote_mem_id:
        return "", _node_html(node), True

    child = node.child
    if child is None:
        return _node_html(node), "", False

    body_children: list[str] = []
    quoted_children: list[str] = []
    found = False

    while child is not None:
        if found:
            child_html = _node_html(child)
            if child_html:
                quoted_children.append(child_html)
            child = child.next
            continue

        body_html, quoted_html, child_found = _split_node_html(child, quote_mem_id)
        if child_found:
            found = True
            if body_html:
                body_children.append(body_html)
            if quoted_html:
                quoted_children.append(quoted_html)
        elif body_html:
            body_children.append(body_html)

        child = child.next

    if not found:
        return _node_html(node), "", False

    return (
        _wrap_node_html(node, "".join(body_children)),
        _wrap_node_html(node, "".join(quoted_children)),
        True,
    )


def _split_outlook_body_and_quote(tree: HTMLParser, quote_node) -> tuple[str | None, str | None]:
    root = tree.body or tree.css_first("html")
    if root is None:
        return None, None

    body_html, quoted_html, found = _split_node_html(root, quote_node.mem_id)
    if not found:
        return None, None
    return body_html or None, quoted_html or None


def _extract_quote_html(quote_node, *, include_following_siblings: bool = False) -> str | None:
    nodes = [quote_node]
    if include_following_siblings:
        sibling = quote_node.next
        while sibling is not None:
            nodes.append(sibling)
            sibling = sibling.next

    fragments = [node.html or node.text(separator="\n", strip=True) for node in nodes]
    for node in nodes:
        node.decompose()

    quoted_content = "".join(fragment for fragment in fragments if fragment)
    return quoted_content or None


def segment_html_conversation(html: str, *, client_hint: str | None = None) -> ConversationResult:
    """Split HTML into body and quoted zones before markdown conversion."""
    cleaned = sanitize_html(html)
    tree = HTMLParser(cleaned)
    zones: list[ConversationZone] = []
    rules_triggered: list[str] = []

    quote_node = None
    resolved_hint = client_hint

    gmail_quote = tree.css_first(".gmail_quote")
    if gmail_quote is not None:
        quote_node = gmail_quote
        resolved_hint = "gmail"
        rules_triggered.append("gmail_quote")
    else:
        outlook_quote = tree.css_first("#divRplyFwdMsg")
        if outlook_quote is not None:
            quote_node = outlook_quote
            resolved_hint = "outlook"
            rules_triggered.append("outlook_divRplyFwdMsg")

    body_content = _body_html(tree)
    quoted_content = None
    if quote_node is not None:
        if resolved_hint == "outlook":
            split_body, split_quote = _split_outlook_body_and_quote(tree, quote_node)
            if split_quote is not None:
                body_content = split_body or ""
                quoted_content = split_quote
            else:
                quoted_content = _extract_quote_html(quote_node, include_following_siblings=True)
                body_content = _body_html(tree)
        else:
            quoted_content = _extract_quote_html(quote_node)
            body_content = _body_html(tree)

    if body_content:
        zones.append(
            ConversationZone(
                kind=ZoneKind.BODY,
                content=body_content,
                source_kind="html",
                client_hint=resolved_hint,
                confidence=0.95 if quote_node is not None else 0.7,
            )
        )

    if quoted_content:
        zones.append(
            ConversationZone(
                kind=ZoneKind.QUOTED,
                content=quoted_content,
                source_kind="html",
                client_hint=resolved_hint,
                confidence=0.95,
            )
        )

    return ConversationResult(
        zones=zones,
        client_hint=resolved_hint,
        rules_triggered=rules_triggered,
    )
