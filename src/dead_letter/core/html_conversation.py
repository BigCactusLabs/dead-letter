"""DOM-aware HTML conversation segmentation."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
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
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dl",
        "dt",
        "dd",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_MAX_FORWARD_BLOCKS = 64
_WHITESPACE_RE = re.compile(r"\s+")


# Real attribution lines are a few hundred characters; the parsers behind
# the colon gate are not linear, so longer lines are never attribution.
_MAX_ATTRIBUTION_CHARS = 1000


def _is_dashed_label(text: str) -> bool:
    return text.startswith("--") and text.endswith("--") and bool(text.strip("- "))


def _format_whitespace(text: str) -> str:
    if text.isascii():
        return text
    return "".join(
        " " if char == "\u00ad" or unicodedata.category(char) == "Cf" else char
        for char in text
    )


def _recognized_attribution(text: str) -> bool:
    if len(text) > _MAX_ATTRIBUTION_CHARS or not text.endswith((":", "：")):
        return False
    return parse_attribution_line(text) is not None or (
        any(reply.headers for reply in parse_email_replies(text))
    )


def _attribution_shaped(text: str) -> bool:
    return text.endswith((":", "：")) or _recognized_attribution(text)


@dataclass(slots=True)
class _Line:
    parts: list[str] = field(default_factory=list)
    nodes: list = field(default_factory=list)
    attr: object | None = None
    container: object | None = None
    text: str = ""
    kind: str = "body"
    classified: bool = False

    def finish(self) -> None:
        self.text = _WHITESPACE_RE.sub(" ", "".join(self.parts)).strip()
        self.parts.clear()

    def classify(self, attr_replies: dict[int, bool], attr_lines: dict[int, list]) -> None:
        if self.classified:
            return
        self.classified = True
        if is_forward_marker_line(self.text) or (
            self.attr is not None and _is_dashed_label(self.text)
        ):
            self.kind = "forward"
        elif (self.attr is not None and _attribution_shaped(self.text)) or (
            _recognized_attribution(self.text)
        ):
            self.kind = "reply"
        elif self.attr is not None:
            # A br can split an attribution before "wrote:". Only join the
            # attr's own first two collected lines, never its whole subtree.
            key = self.attr.mem_id
            if key not in attr_replies:
                text = " ".join(line.text for line in attr_lines.get(key, []))
                attr_replies[key] = _recognized_attribution(text)
            if attr_replies[key]:
                self.kind = "reply"


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


@dataclass(slots=True)
class _Lines:
    first: dict[int, _Line] = field(default_factory=dict)
    last: dict[int, _Line] = field(default_factory=dict)
    attrs: dict[int, list[_Line]] = field(default_factory=dict)
    order: dict[int, int] = field(default_factory=dict)
    end: dict[int, int] = field(default_factory=dict)


def _classify_tree(root) -> tuple[dict[int, _Wrapper], _Lines]:
    """Read the unchanged DOM once, sharing lines but recording their owner.

    The nearest block container identifies direct content. First-line lookup
    can be shared by ancestors for classification; attribution extraction may
    only take a line owned by that wrapper or one non-wrapper direct child.
    Empty subtrees contribute no spans, and traversal is iterative.
    """
    wrappers: dict[int, _Wrapper] = {}
    lines = _Lines()
    first, last = lines.first, lines.last
    line = _Line()
    stack = [(root, False, None, root)]
    while stack:
        node, exiting, attr, container = stack.pop()
        if exiting:
            lines.end[node.mem_id] = len(lines.order)
            if node.tag in _BLOCK_TAGS and line.nodes:
                line.finish()
                line = _Line()
            child_line = first.get(node.mem_id)
            if child_line is not None and node.parent is not None:
                first.setdefault(node.parent.mem_id, child_line)
                last[node.parent.mem_id] = last[node.mem_id]
            continue
        lines.order[node.mem_id] = len(lines.order)
        if node.mem_id != root.mem_id and node.next is not None:
            stack.append((node.next, False, attr, container))
        if (node.tag in _BLOCK_TAGS or node.tag == "br") and line.nodes:
            line.finish()
            line = _Line()
        if node.tag == "-text":
            raw = _format_whitespace(node.text_content or "")
            # Source newlines are whitespace; rendered block/br boundaries
            # end lines. Format-only text must not mask a later attribution.
            if raw.strip():
                if not line.nodes:
                    line.attr, line.container = attr, container
                    if attr is not None:
                        own_lines = lines.attrs.setdefault(attr.mem_id, [])
                        if len(own_lines) < 2:
                            own_lines.append(line)
                line.parts.append(raw)
                line.nodes.append(node)
                first[node.mem_id] = line
                last[node.mem_id] = line
                first.setdefault(node.parent.mem_id, line)
                last[node.parent.mem_id] = line
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
        stack.append((node, True, attr, container))
        if node.child is not None:
            child_attr = node if node.tag == "div" and "gmail_attr" in classes else attr
            child_container = node if node.tag in _BLOCK_TAGS else container
            stack.append((node.child, False, child_attr, child_container))
    line.finish()
    attr_replies: dict[int, bool] = {}
    for wrapper in wrappers.values():
        wrapper.line = first.get(wrapper.node.mem_id)
        if wrapper.line is not None:
            wrapper.line.classify(attr_replies, lines.attrs)
        if wrapper.hint in ("outlook", "front"):
            wrapper.kind = "reply"
        elif wrapper.line is not None and wrapper.line.kind == "forward":
            wrapper.kind = "forward"
        elif wrapper.node.tag == "blockquote" or wrapper.quotes:
            wrapper.kind = "reply"
        elif wrapper.line is not None:
            wrapper.kind = wrapper.line.kind
    return wrappers, lines


def _holds_other_line(node, line: _Line, lines: _Lines) -> bool:
    """True when ``node``'s subtree holds text from any line other than ``line``."""
    return (
        lines.first.get(node.mem_id, line) is not line
        or lines.last.get(node.mem_id, line) is not line
    )


def _holds_wrapper(node, lines: _Lines, wrapper_orders: list[int]) -> bool:
    """True when ``node``'s subtree contains a classified wrapper."""
    start = lines.order[node.mem_id]
    end = lines.end.get(node.mem_id, start + 1)
    index = bisect_right(wrapper_orders, start)
    return index < len(wrapper_orders) and wrapper_orders[index] < end


def _line_span(line: _Line, lines: _Lines, wrapper_orders: list[int]) -> list:
    """Select the direct-child HTML run that holds only this line.

    Siblings holding any other line, or a planned wrapper, are authored
    content and bound the run; an empty result leaves the line in the body.
    """
    first = line.nodes[0]
    while first.parent.mem_id != line.container.mem_id:
        first = first.parent
    if _holds_other_line(first, line, lines) or _holds_wrapper(first, lines, wrapper_orders):
        return []
    selected = []
    node = first
    while node is not None and node.tag not in _BLOCK_TAGS and node.tag != "br":
        if _holds_other_line(node, line, lines) or _holds_wrapper(node, lines, wrapper_orders):
            break
        selected.append(node)
        node = node.next
    while node is not None and (
        node.tag == "br" or (node.tag == "-text" and not _format_whitespace(node.text_content or "").strip())
    ):
        selected.append(node)
        node = node.next
    return selected


def _preceding_attribution(
    quote, parent, wrappers, lines: _Lines, wrapper_orders: list[int]
) -> tuple[list, _Line | None]:
    """Take at most one adjacent attribution unit from this parent's content."""
    previous = quote.prev
    while previous is not None:
        # A nested wrapper is an ownership boundary, including an empty one.
        if previous.tag == "blockquote" or previous.mem_id in wrappers:
            return [], None
        line = lines.last.get(previous.mem_id)
        if line is not None:
            break
        previous = previous.prev
    if previous is None:
        return [], None
    if previous.tag == "div" and "gmail_attr" in _class_tokens(previous):
        return [previous], None
    container = line.container
    direct = container.mem_id == parent.mem_id
    child_block = (
        container.parent is not None
        and container.parent.mem_id == parent.mem_id
        and container.mem_id not in wrappers
    )
    if not (direct or child_block) or not _recognized_attribution(line.text):
        return [], None
    if child_block and lines.first.get(container.mem_id) is line:
        # A block already holding planned history is never taken whole.
        if _holds_wrapper(container, lines, wrapper_orders):
            return [], None
        return [container], None
    selected = _line_span(line, lines, wrapper_orders)
    if not selected:
        return [], None
    previous = selected[0].prev
    while previous is not None and (
        previous.tag == "br" or (previous.tag == "-text" and not _format_whitespace(previous.text_content or "").strip())
    ):
        previous = previous.prev
    if previous is not None and previous.tag not in _BLOCK_TAGS and previous.mem_id not in wrappers:
        marker = lines.last.get(previous.mem_id)
        if marker is not None and marker.container.mem_id == container.mem_id and _is_dashed_label(marker.text):
            selected = _line_span(marker, lines, wrapper_orders) + selected
    return selected, None


def _reply_parts(
    wrapper: _Wrapper, wrappers, lines: _Lines, wrapper_orders: list[int]
) -> tuple[list, _Line | None]:
    """Select the first direct quote and one adjacent, locally owned attribution."""
    quote = wrapper.node if wrapper.node.tag == "blockquote" else wrapper.quotes[0]
    selected, leading = _preceding_attribution(
        quote, quote.parent, wrappers, lines, wrapper_orders
    )
    return [*selected, quote], leading


def _plan_extraction(root, wrappers: dict[int, _Wrapper], lines: _Lines):
    forwards, replies, body_wrappers = [], [], []
    skipped: set[int] = set()
    wrapper_orders = sorted(lines.order[mem_id] for mem_id in wrappers)
    outlook = None
    stack = [(root, None)]
    while stack:
        node, forward_line = stack.pop()
        if node.mem_id != root.mem_id and node.next is not None:
            stack.append((node.next, forward_line))
        if node.mem_id in skipped:
            continue
        wrapper = wrappers.get(node.mem_id)
        if wrapper is not None:
            if wrapper.kind == "forward":
                # Transparent containers can share the exact same separator.
                # It introduces one forward, not an empty enclosing section.
                if (
                    wrapper.line is not forward_line
                    and len(forwards) < _MAX_FORWARD_BLOCKS
                ):
                    forwards.append(wrapper)
                    forward_line = wrapper.line
                # The cap limits forward sections, never subsequent history
                # planning. Uncollected forwards retain their own content.
            elif wrapper.kind == "reply":
                if forward_line is not None:
                    continue  # reply history inside a forward stays with it
                if wrapper.hint == "outlook":
                    outlook = wrapper
                    break  # Outlook owns the remainder in document order
                if (
                    node.tag == "blockquote" or wrapper.quotes
                ) and wrapper.hint != "front":
                    selected, leading = _reply_parts(wrapper, wrappers, lines, wrapper_orders)
                    skipped.update(child.mem_id for child in selected)
                    replies.append((wrapper, selected, leading))
                    if node.tag == "blockquote":
                        continue
                else:
                    replies.append((wrapper, [node], None))
                    continue
            elif forward_line is None:
                body_wrappers.append(node)
        if node.child is not None:
            stack.append((node.child, forward_line))
    replies.sort(key=lambda reply: lines.order[reply[1][0].mem_id])
    return forwards, replies, body_wrappers, outlook


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


def _split_outlook_body_and_quote(
    tree: LexborHTMLParser, quote_node
) -> tuple[str | None, str | None]:
    root = tree.body or tree.css_first("html")
    if root is None:
        return None, None

    body_html, quoted_html, found = _split_node_html(root, quote_node.mem_id)
    if not found:
        return None, None
    return body_html or None, quoted_html or None


def _extract_quote_html(
    quote_node, *, include_following_siblings: bool = False
) -> str | None:
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


def segment_html_conversation(
    html: str, *, client_hint: str | None = None
) -> ConversationResult:
    """Classify the unchanged tree, then extract only positively identified history."""
    tree = parse_html(sanitize_html(html))
    root = tree.body or tree.css_first("html")
    if root is None:
        return ConversationResult(zones=[], client_hint=client_hint, rules_triggered=[])
    wrappers, lines = _classify_tree(root)
    forwards, replies, body_wrappers, outlook = _plan_extraction(root, wrappers, lines)
    rules = list(dict.fromkeys(f"{item.hint}_forward" for item in forwards))
    rules.extend(dict.fromkeys(item.rule for item, _, _ in replies))
    if outlook is not None:
        rules.append(outlook.rule)
    hint = (
        replies[0][0].hint
        if replies
        else outlook.hint
        if outlook
        else forwards[0].hint
        if forwards
        else client_hint
    )

    # Unwrap planned body containers before removing history. This keeps
    # references valid when a bare quote takes a preceding attribution unit.
    for node in body_wrappers:
        node.unwrap()

    forwarded = []
    for wrapper in reversed(forwards):
        forwarded.append(_extract_quote_html(wrapper.node) or "")
    forwarded.reverse()

    quoted = []
    for wrapper, selected, leading in replies:
        bare_quote = wrapper.node.tag == "blockquote"
        if len(selected) == 1 and selected[0].mem_id == wrapper.node.mem_id:
            quoted.append(_extract_quote_html(wrapper.node) or "")
            continue
        parts = []
        for node in selected:
            parts.append(_extract_quote_html(node) or "")
        fragment = "".join(parts)
        quoted.append(
            fragment if bare_quote else _wrap_node_html(wrapper.node, fragment)
        )
    body = _body_html(tree)
    if outlook is not None:
        body, quote = _split_outlook_body_and_quote(tree, outlook.node)
        quoted.append(quote or "")
    # Quote-only messages keep their provenance; rendering supplies the
    # existing visibility fallback without creating authored evidence.

    zones = [
        ConversationZone(
            kind=ZoneKind.BODY,
            content=body or "",
            source_kind="html",
            client_hint=hint,
            confidence=0.95 if rules else 0.7,
        )
    ]
    zones.extend(
        ConversationZone(
            kind=ZoneKind.FORWARDED_BODY,
            content=content,
            source_kind="html",
            client_hint=hint,
            confidence=0.95,
        )
        for content in forwarded
        if content
    )
    if any(quoted):
        zones.append(
            ConversationZone(
                kind=ZoneKind.QUOTED,
                content="".join(quoted),
                source_kind="html",
                client_hint=hint,
                confidence=0.95,
            )
        )
    return ConversationResult(zones=zones, client_hint=hint, rules_triggered=rules)
