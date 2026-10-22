"""DOM-aware HTML conversation segmentation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

from selectolax.lexbor import LexborHTMLParser

from dead_letter.core._html_parser import parse_html
from dead_letter.core.attribution import parse_attribution_line
from dead_letter.core.conversation import ConversationResult
from dead_letter.core.forwarding import is_forward_marker_line
from dead_letter.core.sanitize import sanitize_html
from dead_letter.core.text_conversation import parse_email_replies
from dead_letter.core.types import ConversationZone, ZoneKind


def _body_text(tree: LexborHTMLParser) -> str:
    if tree.body is not None:
        return tree.body.text(separator="\n", strip=True)
    return tree.text(separator="\n", strip=True)


def _body_html(tree: LexborHTMLParser) -> str:
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

    # Lexbor reports valueless attributes as None and empty ones as "";
    # both are dropped, as the Modest backend reported both as None.
    attrs = "".join(
        f' {name}="{escape(value, quote=True)}"'
        for name, value in node.attributes.items()
        if value
    )
    return f"<{node.tag}{attrs}>{inner_html}</{node.tag}>"


def _class_tokens(node) -> set[str]:
    class_attr = node.attributes.get("class", "") or ""
    return {re.sub(r"^(?:x_)+", "", token) for token in class_attr.split()}


def _iter_nodes_in_document_order(node):
    """Yield node and all descendants in document order.

    Iterative (explicit stack) rather than recursive so that deeply
    nested, possibly adversarial HTML cannot exceed Python's recursion
    limit. Sibling and child pointers are pushed lazily, so a caller that
    stops early (e.g. the quote-boundary search) never walks past the
    matching node into a wide container's remaining siblings. See issue #51.
    """
    yield node
    child = node.child
    stack = [child] if child is not None else []
    while stack:
        current = stack.pop()
        yield current
        if current.next is not None:
            stack.append(current.next)
        if current.child is not None:
            stack.append(current.child)


# A line ends at a visual block boundary, not at inline formatting.
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "div", "dl", "dt", "dd",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "pre", "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
})
_MAX_FORWARD_BLOCKS = 64
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class _Line:
    parts: list[str] = field(default_factory=list)
    nodes: list = field(default_factory=list)
    in_attr: bool = False
    text: str = ""
    kind: str = "body"
    classified: bool = False

    def finish(self) -> None:
        self.text = _WHITESPACE_RE.sub(" ", "".join(self.parts)).strip()
        self.parts.clear()

    def classify(self) -> None:
        if self.classified:
            return
        self.classified = True
        if is_forward_marker_line(self.text):
            self.kind = "forward"
        elif self.text.endswith((":", "：")):
            if self.in_attr or parse_attribution_line(self.text) is not None:
                self.kind = "reply"
            else:
                # Use the same upstream attribution grammar as text replies;
                # a bare colon heading must not become reply evidence.
                self.kind = "reply" if any(
                    reply.headers for reply in parse_email_replies(self.text)
                ) else "body"


@dataclass(slots=True)
class _Wrapper:
    node: object
    hint: str
    rule: str
    line: _Line | None = None
    kind: str = "body"
    quotes: list = field(default_factory=list)


def _wrapper_match(node, classes: set[str]) -> tuple[str, str] | None:
    if node.attributes.get("id") == "divRplyFwdMsg":
        return "outlook", "outlook_divRplyFwdMsg"
    if node.tag == "blockquote" and "front-blockquote" in classes:
        return "front", "front_blockquote"
    if node.tag not in ("div", "blockquote"):
        return None
    if "gmail_quote" in classes:
        return "gmail", "gmail_quote"
    if "protonmail_quote" in classes:
        return "proton", "protonmail_quote"
    if any("yahoo_quoted" in token for token in classes) or (
        node.attributes.get("id", "") or ""
    ).startswith("yahoo_quoted_"):
        return "yahoo", "yahoo_quoted"
    return None


def _classify_tree(root) -> tuple[dict[int, _Wrapper], dict[int, _Line]]:
    """Read the unchanged DOM once, sharing each line across its ancestors.

    Empty/whitespace subtrees contribute no spans. Each text byte is joined
    at most once; even thousands of empty nested wrappers share one result.
    The explicit enter/exit stack also keeps Python recursion depth bounded.
    """
    wrappers: dict[int, _Wrapper] = {}
    first: dict[int, _Line] = {}
    line = _Line()
    stack = [(root, False, False)]
    while stack:
        node, exiting, in_attr = stack.pop()
        if exiting:
            if node.tag in _BLOCK_TAGS and line.nodes:
                line.finish()
                line = _Line()
            child_line = first.get(node.mem_id)
            if child_line is not None and node.parent is not None:
                first.setdefault(node.parent.mem_id, child_line)
            continue
        if node is not root and node.next is not None:
            stack.append((node.next, False, in_attr))
        if (node.tag in _BLOCK_TAGS or node.tag == "br") and line.nodes:
            line.finish()
            line = _Line()
        if node.tag == "-text":
            raw = node.text_content or ""
            # HTML source newlines are whitespace, just like spaces. Only
            # rendered block boundaries / br end a line.
            if raw.strip():
                line.in_attr = in_attr if not line.nodes else line.in_attr
                line.parts.append(raw)
                line.nodes.append(node)
                first[node.mem_id] = line
                first.setdefault(node.parent.mem_id, line)
            elif line.nodes:
                line.parts.append(" ")
            continue
        if node.tag == "blockquote" and node.parent is not None:
            parent_wrapper = wrappers.get(node.parent.mem_id)
            if parent_wrapper is not None:
                parent_wrapper.quotes.append(node)
        classes = _class_tokens(node)
        match = _wrapper_match(node, classes)
        if match is not None:
            wrappers[node.mem_id] = _Wrapper(node, *match)
        stack.append((node, True, in_attr))
        if node.child is not None:
            stack.append((node.child, False, in_attr or "gmail_attr" in classes))
    line.finish()
    for wrapper in wrappers.values():
        wrapper.line = first.get(wrapper.node.mem_id)
        if wrapper.line is not None:
            wrapper.line.classify()
        if wrapper.hint in ("outlook", "front"):
            wrapper.kind = "reply"
        elif wrapper.line is not None and wrapper.line.kind == "forward":
            wrapper.kind = "forward"
        elif wrapper.quotes:
            wrapper.kind = "reply"
        elif wrapper.line is not None:
            wrapper.kind = wrapper.line.kind
    return wrappers, first


def _reply_parts(wrapper: _Wrapper, first: dict[int, _Line]) -> tuple[list, _Line | None]:
    """Select direct blockquotes plus their attribution, leaving other body.

    gmail_attr is taken as an element. Otherwise only the leading text line
    before the first blockquote is selected, including inline formatting.
    """
    selected = []
    leading = None
    previous = None
    for child in wrapper.node.iter(include_text=True):
        if child.tag == "blockquote":
            if previous is not None and "gmail_attr" in _class_tokens(previous):
                selected.append(previous)
            elif not selected and wrapper.line is not first.get(child.mem_id):
                leading = wrapper.line
            selected.append(child)
        if child.tag != "br" and (child.tag != "-text" or (child.text_content or "").strip()):
            previous = child
    return selected, leading


def _plan_extraction(root, wrappers: dict[int, _Wrapper], first: dict[int, _Line]):
    forwards, replies, body_wrappers = [], [], []
    skipped: set[int] = set()
    outlook = None
    stack = [(root, None)]
    capped = False
    while stack:
        node, forward_line = stack.pop()
        if node is not root and node.next is not None:
            stack.append((node.next, forward_line))
        if node.mem_id in skipped:
            continue
        wrapper = wrappers.get(node.mem_id)
        if wrapper is not None:
            if wrapper.kind == "forward":
                # Transparent containers can share the exact same separator.
                # It introduces one forward, not an empty enclosing section.
                if wrapper.line is not forward_line:
                    forwards.append(wrapper)
                    if len(forwards) == _MAX_FORWARD_BLOCKS:
                        capped = True
                        break
                forward_line = wrapper.line
            elif wrapper.kind == "reply":
                if forward_line is not None:
                    continue  # reply history inside a forward stays with it
                if wrapper.hint == "outlook":
                    outlook = wrapper
                    break  # Outlook owns the remainder in document order
                if wrapper.quotes and wrapper.hint != "front":
                    selected, leading = _reply_parts(wrapper, first)
                    skipped.update(child.mem_id for child in selected)
                    replies.append((wrapper, selected, leading))
                else:
                    replies.append((wrapper, [node], None))
                    continue
            elif forward_line is None:
                body_wrappers.append(node)
        if node.child is not None:
            stack.append((node.child, forward_line))
    return forwards, replies, body_wrappers, outlook, capped


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

    # Unreachable in practice: the loop returns once the root frame is popped.
    # The explicit return keeps the function total for the type checker without
    # introducing an unbounded ``while True`` loop.
    return _node_html(node), "", False


def _split_outlook_body_and_quote(tree: LexborHTMLParser, quote_node) -> tuple[str | None, str | None]:
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
    """Classify the unchanged tree, then extract only positively identified history."""
    tree = parse_html(sanitize_html(html))
    root = tree.body or tree.css_first("html")
    if root is None:
        return ConversationResult(zones=[], client_hint=client_hint, rules_triggered=[])
    wrappers, first = _classify_tree(root)
    forwards, replies, body_wrappers, outlook, capped = _plan_extraction(root, wrappers, first)
    rules = list(dict.fromkeys(f"{item.hint}_forward" for item in forwards))
    rules.extend(dict.fromkeys(item.rule for item, _, _ in replies))
    if outlook is not None:
        rules.append(outlook.rule)
    hint = (replies[0][0].hint if replies else outlook.hint if outlook else
            forwards[0].hint if forwards else client_hint)

    forwarded = []
    for index, wrapper in enumerate(reversed(forwards)):
        forwarded.append(_extract_quote_html(
            wrapper.node, include_following_siblings=capped and index == 0,
        ) or "")
    forwarded.reverse()

    quoted = []
    for wrapper, selected, leading in replies:
        if selected == [wrapper.node]:
            quoted.append(_extract_quote_html(wrapper.node) or "")
            continue
        parts = []
        if leading is not None:
            # The first line can span inline nodes. Remove those text nodes
            # only; all following lines/elements remain in the current body.
            parts.append(escape(leading.text) + "<br>")
            for node in leading.nodes:
                node.decompose()
        for node in selected:
            parts.append(_extract_quote_html(node) or "")
        quoted.append(_wrap_node_html(wrapper.node, "".join(parts)))

    # Unwrap from outside in; nested one-child wrappers need constant work.
    for node in body_wrappers:
        node.unwrap()
    body = _body_html(tree)
    if outlook is not None:
        body, quote = _split_outlook_body_and_quote(tree, outlook.node)
        quoted.append(quote or "")
    # Quote-only messages keep their provenance; rendering supplies the
    # existing visibility fallback without creating authored evidence.

    zones = [ConversationZone(
        kind=ZoneKind.BODY, content=body or "", source_kind="html",
        client_hint=hint, confidence=0.95 if rules else 0.7,
    )]
    zones.extend(ConversationZone(
        kind=ZoneKind.FORWARDED_BODY, content=content, source_kind="html",
        client_hint=hint, confidence=0.95,
    ) for content in forwarded if content)
    if any(quoted):
        zones.append(ConversationZone(
            kind=ZoneKind.QUOTED, content="".join(quoted), source_kind="html",
            client_hint=hint, confidence=0.95,
        ))
    return ConversationResult(zones=zones, client_hint=hint, rules_triggered=rules)
