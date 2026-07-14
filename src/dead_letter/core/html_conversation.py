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


def _class_tokens(node) -> set[str]:
    class_attr = node.attributes.get("class", "") or ""
    return set(class_attr.split())


def _iter_nodes_in_document_order(node):
    """Yield node and all descendants in document order.

    Iterative (explicit stack) rather than recursive so that deeply
    nested, possibly adversarial HTML cannot exceed Python's
    recursion limit. See issue #51.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        children = []
        child = current.child
        while child is not None:
            children.append(child)
            child = child.next
        stack.extend(reversed(children))


def _quote_match(node) -> tuple[str, str] | None:
    classes = _class_tokens(node)
    if "gmail_quote" in classes:
        return "gmail", "gmail_quote"
    if node.attributes.get("id") == "divRplyFwdMsg":
        return "outlook", "outlook_divRplyFwdMsg"
    if node.tag == "blockquote" and "front-blockquote" in classes:
        return "front", "front_blockquote"
    return None


def _find_first_quote_boundary(tree: HTMLParser):
    root = tree.body or tree.css_first("html")
    if root is None:
        return None, None, None

    for node in _iter_nodes_in_document_order(root):
        match = _quote_match(node)
        if match is not None:
            hint, rule = match
            return node, hint, rule
    return None, None, None


def _split_node_html(node, quote_mem_id: int) -> tuple[str, str, bool]:
    stack = [(node, node.child, [], [], False)]
    while stack:
        current, child, body_children, quoted_children, found = stack[-1]
        if current.mem_id == quote_mem_id:
            result = "", _node_html(current), True
        elif child is None:
            if not found:
                result = _node_html(current), "", False
            else:
                result = (
                    _wrap_node_html(current, "".join(body_children)),
                    _wrap_node_html(current, "".join(quoted_children)),
                    True,
                )
        elif found:
            child_html = _node_html(child)
            if child_html:
                quoted_children.append(child_html)
            stack[-1] = current, child.next, body_children, quoted_children, found
            continue
        else:
            stack[-1] = current, child.next, body_children, quoted_children, found
            stack.append((child, child.child, [], [], False))
            continue

        stack.pop()
        if not stack:
            return result

        body_html, quoted_html, child_found = result
        parent, parent_child, parent_body, parent_quoted, parent_found = stack[-1]
        if child_found:
            parent_found = True
            if body_html:
                parent_body.append(body_html)
            if quoted_html:
                parent_quoted.append(quoted_html)
        elif body_html:
            parent_body.append(body_html)
        stack[-1] = parent, parent_child, parent_body, parent_quoted, parent_found


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

    quote_node, detected_hint, detected_rule = _find_first_quote_boundary(tree)
    resolved_hint = detected_hint or client_hint
    if detected_rule is not None:
        rules_triggered.append(detected_rule)

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
