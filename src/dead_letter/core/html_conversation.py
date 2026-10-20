"""DOM-aware HTML conversation segmentation."""

from __future__ import annotations

import re
from html import escape

from selectolax.lexbor import LexborHTMLParser

from dead_letter.core._html_parser import parse_html

from dead_letter.core.attribution import parse_attribution_line
from dead_letter.core.conversation import ConversationResult
from dead_letter.core.forwarding import is_forward_marker_line
from dead_letter.core.sanitize import sanitize_html
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
    """Class tokens of ``node``, plus each ``x_``-prefixed token without the
    prefix (Outlook on the web prefixes the classes of mail it quotes)."""
    class_attr = node.attributes.get("class", "") or ""
    tokens = set(class_attr.split())
    return tokens | {token[2:] for token in tokens if token.startswith("x_")}


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


# A ``gmail_quote`` wrapper with no quote signal stays body content when the
# text outside it is no more than this many characters and under 1/20 of the
# text inside it (talon's rule for Gmail wrappers around the message body).
_GMAIL_WRAPPER_STUB_MAX_CHARS = 40
_GMAIL_WRAPPER_STUB_RATIO = 20


def _gmail_body_wrappers(root) -> frozenset[int]:
    """``mem_id`` of each ``gmail_quote`` element under ``root`` that wraps the
    message body rather than quoted history.

    A wrapper qualifies when it has no quote signal (a blockquote or
    ``gmail_attr`` outside nested ``div.gmail_quote`` blocks, or a first line
    of its own text, nested ``gmail_quote`` text skipped, that is an
    attribution ending in ``:``/``：`` or a forward separator) and the
    text outside it is empty, or short (a greeting) and a small fraction of
    the text inside it. One post-order pass computes text
    counts and signals for every node. Callers classify once on the unchanged
    tree, so extracting forwards cannot reclassify a wrapper.
    """
    counts: dict[int, int] = {}
    signals: dict[int, bool] = {}
    candidates: list = []
    stack = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if not expanded:
            stack.append((node, True))
            child = node.child
            while child is not None:
                stack.append((child, False))
                child = child.next
            continue
        if node.tag == "-text":
            counts[node.mem_id] = len(_WHITESPACE_RE.sub("", node.text_content or ""))
            signals[node.mem_id] = False
            continue
        total = 0
        signal = False
        child = node.child
        while child is not None:
            total += counts[child.mem_id]
            if not signal and child.tag != "-text":
                classes = _class_tokens(child)
                if child.tag == "blockquote" or "gmail_attr" in classes:
                    signal = True
                elif "gmail_quote" not in classes:
                    signal = signals[child.mem_id]
            child = child.next
        counts[node.mem_id] = total
        signals[node.mem_id] = signal
        if node.tag != "blockquote" and "gmail_quote" in _class_tokens(node):
            candidates.append(node)

    wrappers: set[int] = set()
    for node in candidates:
        if signals[node.mem_id]:
            continue
        inside = counts[node.mem_id]
        outside = counts[root.mem_id] - inside
        if outside > _GMAIL_WRAPPER_STUB_MAX_CHARS:
            continue
        if outside and inside <= outside * _GMAIL_WRAPPER_STUB_RATIO:
            continue
        if _is_forward_or_reply_line(_own_first_line(node)):
            continue
        wrappers.add(node.mem_id)
    return frozenset(wrappers)


def _own_first_line(wrapper) -> str:
    """First line of a ``gmail_quote`` wrapper's own text, read like
    ``_FirstLines`` but skipping nested ``gmail_quote`` elements, which are
    checked as blocks of their own. Each node is read only by the scan of its
    nearest enclosing ``gmail_quote``, so the scans together stay linear."""
    parts: list[str] = []
    has_text = False
    stack = [(wrapper.child, False)] if wrapper.child is not None else []
    while stack:
        node, leaving = stack.pop()
        if leaving:
            if has_text:
                break
            continue
        if node.next is not None:
            stack.append((node.next, False))
        tag = node.tag
        if tag == "-text":
            text = node.text_content or ""
            parts.append(text)
            has_text = has_text or bool(text.strip())
            continue
        if has_text and (tag == "br" or tag in _BLOCK_TAGS):
            break
        if tag == "br" or "gmail_quote" in _class_tokens(node):
            continue
        if tag in _BLOCK_TAGS:
            stack.append((node, True))
        if node.child is not None:
            stack.append((node.child, False))
    return _WHITESPACE_RE.sub(" ", "".join(parts).replace("\u00a0", " ")).strip()


_BLOCK_TAGS = frozenset(
    {"div", "p", "blockquote", "table", "tr", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
)


class _FirstLines:
    """First text line of each wrapper that classification reads
    (``gmail_quote`` elements, Yahoo and Proton ``div`` wrappers), indexed
    by node.

    A wrapper's first line is its text up to the first ``<br>``, block
    element start, or block element end that follows non-blank text, or to
    its end, whitespace-collapsed, as the line renders. One document-order
    pass over the unchanged tree records each line as a span of text parts.
    Wrappers still waiting for text share one span start, since whitespace
    before the first text collapses away, so nested wrappers around the same
    text never rescan it. Lines are joined on first lookup, once per distinct
    span.
    """

    def __init__(self, root) -> None:
        self._parts: list[str] = []
        self._spans: dict[int, tuple[int, int]] = {}
        self._lines: dict[tuple[int, int], str] = {}
        parts = self._parts
        spans = self._spans
        wrappers: set[int] = set()
        # Waiting wrappers, nested outermost first, as groups that started
        # at the same text part. Every group below the top has text.
        starts: list[int] = []
        members: list[list[int]] = []
        top_has_text = False

        def end_lines() -> None:
            # Ends the line of every waiting wrapper that has text.
            nonlocal top_has_text
            done = len(starts) if top_has_text else len(starts) - 1
            end = len(parts)
            for start, group in zip(starts[:done], members[:done]):
                for mem_id in group:
                    spans[mem_id] = (start, end)
            del starts[:done]
            del members[:done]
            top_has_text = False

        stack = [(root, False)] if root is not None else []
        while stack:
            node, leaving = stack.pop()
            if leaving:
                if node.mem_id in wrappers and node.mem_id not in spans:
                    # Its subtree ended before its line did; it is the
                    # innermost waiting wrapper.
                    members[-1].pop()
                    spans[node.mem_id] = (starts[-1], len(parts))
                    if not members[-1]:
                        starts.pop()
                        members.pop()
                        top_has_text = True
                if starts and node.tag in _BLOCK_TAGS:
                    end_lines()
                continue
            if node is not root and node.next is not None:
                stack.append((node.next, False))
            tag = node.tag
            if tag == "-text":
                if starts:
                    text = node.text_content or ""
                    parts.append(text)
                    top_has_text = top_has_text or bool(text.strip())
                continue
            if starts and (tag == "br" or tag in _BLOCK_TAGS):
                end_lines()
            if tag == "br":
                continue
            classes = _class_tokens(node)
            if "gmail_quote" in classes or (
                tag == "div"
                and (
                    "protonmail_quote" in classes
                    or any(_YAHOO_QUOTED_RE.fullmatch(token) for token in classes)
                )
            ):
                wrappers.add(node.mem_id)
                if starts and not top_has_text:
                    members[-1].append(node.mem_id)
                else:
                    starts.append(len(parts))
                    members.append([node.mem_id])
                    top_has_text = False
                stack.append((node, True))
            elif starts and tag in _BLOCK_TAGS:
                # Its end ends a line whose text starts inside it.
                stack.append((node, True))
            if node.child is not None:
                stack.append((node.child, False))

    def __getitem__(self, node) -> str:
        span = self._spans[node.mem_id]
        line = self._lines.get(span)
        if line is None:
            start, end = span
            joined = "".join(self._parts[start:end]).replace("\u00a0", " ")
            line = self._lines[span] = _WHITESPACE_RE.sub(" ", joined).strip()
        return line


# Yahoo's quote wrapper class, bare or behind the ``ydp<hex>`` prefix that
# current Yahoo Mail adds.
_YAHOO_QUOTED_RE = re.compile(r"(?:ydp[0-9a-f]+)?yahoo_quoted")


def _is_forward_or_reply_line(line: str) -> bool:
    """True when ``line`` is a forward separator or a reply attribution
    ending in ``:``/``：``."""
    return is_forward_marker_line(line) or line.endswith((":", "\uff1a"))


def _quote_match(
    node, body_wrappers: frozenset[int], first_lines: _FirstLines
) -> tuple[str, str] | None:
    """Classify ``node`` as a quote wrapper. ``body_wrappers`` and
    ``first_lines`` come from the unchanged tree."""
    classes = _class_tokens(node)
    if "gmail_quote" in classes:
        if node.mem_id in body_wrappers:
            return None
        return "gmail", "gmail_quote"
    # Yahoo and Proton use one wrapper for replies and forwards; only a
    # wrapper that opens with a separator or an attribution is a boundary.
    if node.tag == "div" and any(_YAHOO_QUOTED_RE.fullmatch(token) for token in classes):
        if _is_forward_or_reply_line(first_lines[node]):
            return "yahoo", "yahoo_quoted"
        return None
    if node.tag == "div" and "protonmail_quote" in classes:
        if _is_forward_or_reply_line(first_lines[node]):
            return "proton", "protonmail_quote"
        return None
    if node.attributes.get("id") == "divRplyFwdMsg":
        return "outlook", "outlook_divRplyFwdMsg"
    if node.tag == "blockquote" and "front-blockquote" in classes:
        return "front", "front_blockquote"
    return None


# Upper bound on forward blocks split out of one message. The last split
# block also takes the siblings that follow it in the same parent, and any
# forward left uncollected stays in the body (rendered before the forwards),
# so no content is dropped past the cap; wrapped layouts can be reordered.
_MAX_FORWARD_BLOCKS = 64
_WHITESPACE_RE = re.compile(r"\s+")


def _leading_text_line(node) -> str:
    """First line of ``node``'s own leading text, before any child element
    other than a leading ``<br>``."""
    for child in node.iter(include_text=True):
        if child.tag == "br":
            continue
        if child.tag != "-text":
            return ""
        text = (child.text_content or "").strip()
        if text:
            return text.split("\n", 1)[0]
    return ""


def _attr_first_line(attr) -> str:
    """Text of a ``gmail_attr`` up to its first ``<br>``, whitespace-collapsed."""
    parts: list[str] = []
    has_text = False
    for node in _iter_nodes_in_document_order(attr):
        if node is attr:
            continue
        if node.tag == "br":
            if has_text:
                break
            continue
        if node.tag == "-text":
            text = node.text_content or ""
            parts.append(text)
            has_text = has_text or bool(text.strip())
    return _WHITESPACE_RE.sub(" ", "".join(parts).replace("\u00a0", " ")).strip()


def _is_forward_block(node) -> bool:
    """Classify a Gmail ``div.gmail_quote`` as a forwarded message.

    A direct-child ``blockquote.gmail_quote`` marks a reply. Otherwise, when
    the first element child is ``div.gmail_attr``, its first line decides: a
    forward separator is a forward, a line ending in ``:`` or a fullwidth
    ``：`` ("On ... wrote:", "Le ... a écrit :", "... 写道：") is a reply, and anything else is kept as a forward.
    Legacy Gmail forwards have no ``gmail_attr`` and open with the separator
    as the block's own text.
    """
    if node.tag != "div":
        return False
    children = list(node.iter())
    if any(
        child.tag == "blockquote" and "gmail_quote" in _class_tokens(child)
        for child in children
    ):
        return False
    if children and children[0].tag == "div" and "gmail_attr" in _class_tokens(children[0]):
        line = _attr_first_line(children[0])
        if is_forward_marker_line(line):
            return True
        return not line.endswith((":", "\uff1a"))
    if any("gmail_attr" in _class_tokens(child) for child in children):
        return False
    return is_forward_marker_line(_leading_text_line(node))


def _is_forward_quote(node, hint: str, first_lines: _FirstLines) -> bool:
    """Classify a matched quote wrapper as a forwarded message.

    Gmail uses ``_is_forward_block``. Yahoo and Proton wrappers are forwards
    when their first text line is a forward separator; Proton forwards also
    contain a blockquote, so the blockquote rule does not apply to them.
    """
    if hint == "gmail":
        return _is_forward_block(node)
    if hint in ("yahoo", "proton"):
        return is_forward_marker_line(first_lines[node])
    return False


def _collect_forward_blocks(
    root, body_wrappers: frozenset[int], first_lines: _FirstLines
) -> tuple[list, bool]:
    """Return Gmail, Yahoo and Proton forward blocks in document order,
    outermost first.

    Collection stops at the first quote boundary outside a forward that is not
    itself a forward, so reply history keeps today's single-boundary handling.
    Forwards nested in a forward are collected; any other quote block inside a
    forward stays part of that forward's content. The flag reports whether
    collection stopped at ``_MAX_FORWARD_BLOCKS``.
    """
    forwards: list = []
    stack = [(root, False)]
    while stack:
        node, in_forward = stack.pop()
        if node is not root and node.next is not None:
            stack.append((node.next, in_forward))
        match = _quote_match(node, body_wrappers, first_lines)
        if match is not None:
            if _is_forward_quote(node, match[0], first_lines):
                forwards.append(node)
                if len(forwards) >= _MAX_FORWARD_BLOCKS:
                    return forwards, True
                if node.child is not None:
                    stack.append((node.child, True))
                continue
            if not in_forward:
                break
            continue
        if node.child is not None:
            stack.append((node.child, in_forward))
    return forwards, False


def _extract_forward_blocks(
    tree: LexborHTMLParser, body_wrappers: frozenset[int], first_lines: _FirstLines
) -> tuple[list[str], bool, list[str]]:
    """Return forward fragments, the cap flag, and the client hint of each
    collected forward in document order."""
    root = tree.body or tree.css_first("html")
    if root is None:
        return [], False, []
    forwards, capped = _collect_forward_blocks(root, body_wrappers, first_lines)
    hints = [
        match[0]
        for match in (_quote_match(node, body_wrappers, first_lines) for node in forwards)
        if match is not None
    ]
    fragments: list[str] = []
    # Innermost/last first, so each captured block excludes the nested
    # forwards already removed from it.
    for index, node in enumerate(reversed(forwards)):
        if capped and index == 0:
            # The capped block keeps everything after it at its level, so
            # uncollected forwards there stay in document order.
            fragment = _extract_quote_html(node, include_following_siblings=True) or ""
        else:
            fragment = _node_html(node)
            node.decompose()
        fragments.append(fragment)
    fragments.reverse()
    return [fragment for fragment in fragments if fragment], capped, hints


def _find_first_quote_boundary(
    tree: LexborHTMLParser, body_wrappers: frozenset[int], first_lines: _FirstLines
):
    root = tree.body or tree.css_first("html")
    if root is None:
        return None, None, None

    for node in _iter_nodes_in_document_order(root):
        match = _quote_match(node, body_wrappers, first_lines)
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


def _sibling_reply_wrappers(
    quote_node, hint: str | None, body_wrappers: frozenset[int], first_lines: _FirstLines
) -> list:
    """Reply wrappers that directly follow ``quote_node`` with the same hint.

    Yahoo, Proton, and OWA (``x_``-prefixed) Gmail replies can carry each
    earlier message in its own sibling wrapper. These belong to the quoted
    history, not the current body. A sibling is taken only when its first
    text line is a reply attribution that ``parse_attribution_line``
    recognizes ("On … wrote:", "Am … schrieb …:", …): the class can be
    reused around current text, and a line that merely ends in ``:`` (such
    as "My answer:") is not enough. Whitespace and ``<br>`` between wrappers
    are taken along; collection stops at any other node, which stays in the
    body.
    """
    if hint not in ("yahoo", "proton") and not (
        hint == "gmail" and "x_gmail_quote" in _class_tokens(quote_node)
    ):
        return []
    following: list = []
    pending: list = []
    sibling = quote_node.next
    while sibling is not None:
        if sibling.tag == "br" or (
            sibling.tag == "-text" and not (sibling.text_content or "").strip()
        ):
            pending.append(sibling)
        else:
            match = _quote_match(sibling, body_wrappers, first_lines)
            if (
                match is None
                or match[0] != hint
                or _is_forward_quote(sibling, hint, first_lines)
                or parse_attribution_line(first_lines[sibling]) is None
            ):
                break
            following.extend(pending)
            following.append(sibling)
            pending = []
        sibling = sibling.next
    return following


def _extract_quote_html(
    quote_node, *, include_following_siblings: bool = False, following: list | None = None
) -> str | None:
    nodes = [quote_node, *(following or [])]
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
    tree = parse_html(cleaned)
    zones: list[ConversationZone] = []
    rules_triggered: list[str] = []

    root = tree.body or tree.css_first("html")
    first_lines = _FirstLines(root)
    body_wrappers = _gmail_body_wrappers(root) if root is not None else frozenset()
    forwarded_blocks, capped, forward_hints = _extract_forward_blocks(
        tree, body_wrappers, first_lines
    )
    if forwarded_blocks:
        rules_triggered.extend(dict.fromkeys(f"{hint}_forward" for hint in forward_hints))

    quote_node, detected_hint, detected_rule = _find_first_quote_boundary(
        tree, body_wrappers, first_lines
    )
    if (
        capped
        and quote_node is not None
        and detected_hint is not None
        and _is_forward_quote(quote_node, detected_hint, first_lines)
    ):
        # An uncollected forward past the cap is content, never reply history.
        quote_node, detected_hint, detected_rule = None, None, None
    forward_hint = forward_hints[0] if forward_hints else None
    resolved_hint = detected_hint or (forward_hint if forwarded_blocks else None) or client_hint
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
            quoted_content = _extract_quote_html(
                quote_node,
                following=_sibling_reply_wrappers(
                    quote_node, detected_hint, body_wrappers, first_lines
                ),
            )
            body_content = _body_html(tree)

    if body_content:
        zones.append(
            ConversationZone(
                kind=ZoneKind.BODY,
                content=body_content,
                source_kind="html",
                client_hint=resolved_hint,
                confidence=0.95 if quote_node is not None or forwarded_blocks else 0.7,
            )
        )

    for forwarded in forwarded_blocks:
        zones.append(
            ConversationZone(
                kind=ZoneKind.FORWARDED_BODY,
                content=forwarded,
                source_kind="html",
                client_hint=resolved_hint,
                confidence=0.95,
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
