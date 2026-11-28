"""Versioned English rules on prepare_eml authored evidence; never calls a provider."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from dead_letter.analysis.eml import prepare_eml

ROOT = Path(__file__).resolve().parent
RULE_VERSION = "authored-rules-v1"
REPLY = "reply_request_present"
ACTION = "non_reply_action_request_present"
COMMITMENT = "sender_commitment_present"
DEADLINE = "action_deadline_present"


def predict(state):
    text = "\n".join(s["text"] for s in state["message"]["authored_segments"]).lower()
    fired = []

    def matches(name, pattern):
        found = bool(re.search(pattern, text, re.MULTILINE))
        if found:
            fired.append(name)
        return found

    reply = matches(
        "reply", r"\?|\b(?:please\s+)?(?:reply|confirm|acknowledge)\b|\blet me know\b"
    )
    action = matches(
        "action",
        r"\b(?:please|kindly|can you|could you)\s+(?:\w+\s+){0,2}(?:review|edit|pay|investigate|submit|send|upload|check|book|replace)\b|^\s*(?:review|edit|pay|investigate|submit|send|upload|check|book|replace)\b",
    )
    commitment = matches(
        "commitment",
        r"\b(?:i(?:'|’)ll|we(?:'|’)ll|i will|we will|i promise to|we commit to)\s+\w+",
    )
    negative_reply = matches(
        "no_reply", r"\bno reply (?:is )?(?:needed|required)|\bdo not reply\b"
    )
    negative_action = matches(
        "no_action",
        r"\bno action (?:is )?(?:needed|required)|\bfor (?:your )?information only\b",
    )
    deadline = matches(
        "date_bound",
        r"\b(?:by|before|no later than)\s+(?:(?:next|this)\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|noon|midnight|end of|\d|january|february|march|april|may|june|july|august|september|october|november|december)\b",
    )
    immediate = matches(
        "immediate", r"\b(?:immediately|asap|right now|as soon as possible)\b"
    )
    prompt = matches("prompt", r"\b(?:promptly|urgent|prioriti[sz]e|soon)\b")
    # A scope heuristic, not an ownership inference: CC-only without an authored
    # alias mention has no positive recipient-request rule. Sender rules survive.
    aliases = (state.get("focus_identity") or {}).get("aliases", [])
    if aliases:
        to = " ".join(state["message"]["to"]).lower()
        named = any(
            a.lower() in text or a.split("@")[0].lower() in text for a in aliases
        )
        if not named and not any(a.lower() in to for a in aliases):
            reply = action = False
            fired = [r for r in fired if r not in {"reply", "action"}]
    reply = reply and not negative_reply
    action = action and not negative_action
    supported = bool(reply or action or negative_reply or negative_action)
    expectation = {
        (False, False): "none",
        (True, False): "reply_only",
        (False, True): "non_reply_action_only",
        (True, True): "both",
    }
    decisions = {
        REPLY: reply if supported else None,
        ACTION: action if supported else None,
        COMMITMENT: True if commitment else None,
        DEADLINE: True if deadline and (reply or action or commitment) else None,
        "expressed_urgency": 2 if immediate else 1 if prompt else None,
        "response_expectation": expectation[reply, action]
        if supported
        else "insufficient_context",
    }
    return {"decisions": decisions, "fired_rules": fired}


def run_baseline(document, source_root):
    predictions = {}
    for case in sorted(document["cases"], key=lambda c: c["case_id"]):
        prepared = prepare_eml(
            source_root / case["file"],
            focus_identity=tuple(case["focus_identity"]),
            model="jev-1.13.0",
        )
        predictions[case["case_id"]] = {
            **predict(prepared.request.payload()["state"]),
            "state_sha256": prepared.request.state_sha256,
            "source_sha256": prepared.snapshot.source_sha256,
        }
    return {
        "schema_version": 1,
        "rule_version": RULE_VERSION,
        "predictions": predictions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=ROOT / "labels.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = run_baseline(json.loads(args.labels.read_text()), args.labels.parent)
    with args.out.open("x") as handle:
        handle.write(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps({"cases": len(result["predictions"]), "rule_version": RULE_VERSION})
    )


if __name__ == "__main__":
    main()
