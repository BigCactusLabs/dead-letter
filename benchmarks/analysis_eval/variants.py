"""Evaluation-only profile variants (#196/#197). Harness-local, never production.

`get_profile`, the TypeSafe provider and `score.load_run` accept only the two
built-in profiles. This module does not register with or relax any of them: a
variant is a harness object with an `eval-` identity, a content digest and an
explicit mapping from each variant question/option back to a canonical
production question/option. Its runs are loaded by `load_variant_run`, which
rejects production-profile trees and digest mismatches, and are scored by
projecting answers onto the canonical questions and reusing the scorer.

No provider is called here. `execute` takes a caller-supplied transport so an
authorized live run can produce sidecars; tests supply fakes.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dead_letter.analysis.contracts import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MAX_REQUEST_BYTES,
    MAX_STATE_AND_QUESTION_BYTES,
    MAX_STATE_BYTES,
    REQUEST_SCHEMA_VERSION,
    AnalysisError,
    PreparedRequest,
    canonical_json,
)
from dead_letter.analysis.eml import PreparedEmail, prepare_eml
from dead_letter.analysis.profiles import _COMMON, Profile, get_profile
from dead_letter.analysis.responses import validate_response
from dead_letter.analysis.service import _now, _result_envelope

from . import score, stability
from .metrics import percentile
from .score import ABSTAIN, EXPECTATION, REPLY, ROOT, _read_json, digest
from .stage import select_cases

PRODUCTION = ("triage-v1", "triage-choice-v1")
CANONICAL = {
    **get_profile("triage-v1").questions(),
    **get_profile("triage-choice-v1").questions(),
}
PINNED_MODEL = "jev-1.13.0"
NAME = re.compile(r"eval-[a-z0-9]+(?:-[a-z0-9]+)*-v[0-9]+\Z")
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


@dataclass(frozen=True, slots=True)
class Variant:
    """Immutable variant. `spec_json` is an ordered list; order is identity.

    Each entry: {id, canonical, type, instructions, criteria?, options?}.
    Score criteria are an ordered list of level texts (index = level). Choice
    criteria are an ordered list of [option, text] pairs and `options` maps
    each variant option to its canonical option.
    """

    name: str
    revision: str
    views: tuple
    purpose: str
    spec_json: str = field(repr=False)

    def __post_init__(self):
        if not NAME.fullmatch(self.name) or self.name in PRODUCTION:
            raise ValueError("variant names must be eval-*-vN, never production")
        try:
            get_profile(self.name)
        except ValueError:
            pass
        else:
            raise ValueError("variant name collides with a production profile")
        if not self.views or any(v not in PRODUCTION for v in self.views):
            raise ValueError("variant views must be production profile names")
        spec = self.spec()
        ids = [q["id"] for q in spec]
        targets = [q["canonical"] for q in spec]
        if len(set(ids)) != len(ids) or len(set(targets)) != len(targets):
            raise ValueError("variant questions and canonical targets must be unique")
        for q in spec:
            canonical = CANONICAL.get(q["canonical"])
            if canonical is None or canonical["type"] != q["type"]:
                raise ValueError(f"invalid canonical mapping for {q['id']}")
            if not isinstance(q.get("instructions"), str) or not q["instructions"]:
                raise ValueError(f"missing instructions for {q['id']}")
            if q["type"] == "noul" and ("criteria" in q or "options" in q):
                raise ValueError("Noul questions take no criteria")
            if q["type"] == "score" and (
                len(q.get("criteria", ())) != len(canonical["criteria"])
                or "options" in q
            ):
                raise ValueError("Score levels must match the canonical level count")
            if q["type"] == "choice":
                keys = [key for key, _ in q["criteria"]]
                options = q["options"]
                if (
                    len(set(keys)) != len(keys)
                    or set(options) != set(keys)
                    or sorted(options.values()) != sorted(canonical["criteria"])
                ):
                    raise ValueError("Choice options must map one-to-one to canonical")
        for view in self.views:
            if not set(get_profile(view).questions()) <= set(targets):
                raise ValueError(f"variant does not cover every {view} question")

    def spec(self) -> list:
        return json.loads(self.spec_json)

    def questions(self) -> dict:
        """Wire questions in declared order, including Choice option order."""
        result = {}
        for q in self.spec():
            item = {"type": q["type"], "instructions": q["instructions"]}
            if q["type"] == "score":
                item["criteria"] = list(q["criteria"])
            elif q["type"] == "choice":
                item["criteria"] = {key: text for key, text in q["criteria"]}
            result[q["id"]] = item
        return result

    def mapping(self) -> dict:
        return {
            q["id"]: {"canonical": q["canonical"], "options": q.get("options")}
            for q in self.spec()
        }

    @property
    def profile(self) -> Profile:
        """Validation/envelope profile. Its sha256 ignores option order; `digest` does not."""
        return Profile(
            name=self.name,
            revision=self.revision,
            questions_json=json.dumps(
                self.questions(), sort_keys=True, separators=(",", ":")
            ),
        )

    @property
    def digest(self) -> str:
        return digest(
            {
                "artifact_type": "analysis_eval_profile_variant",
                "schema_version": 1,
                "name": self.name,
                "revision": self.revision,
                "views": list(self.views),
                # Canonical drift (production text/criteria) invalidates a variant.
                "canonical_profile_sha256": {
                    p: get_profile(p).sha256 for p in PRODUCTION
                },
                "spec": self.spec(),
            }
        )

    def identity(self) -> dict:
        return {
            "name": self.name,
            "revision": self.revision,
            "digest": self.digest,
            "evaluation_only": True,
            "views": list(self.views),
            "purpose": self.purpose,
            "question_order": [q["id"] for q in self.spec()],
            "mapping": self.mapping(),
        }


def _spec_from(profile):
    result = []
    for qid, spec in get_profile(profile).questions().items():
        item = {"id": qid, "canonical": qid, **spec}
        if spec["type"] == "choice":
            item["criteria"] = [[key, text] for key, text in spec["criteria"].items()]
            item["options"] = {key: key for key in spec["criteria"]}
        result.append(item)
    return result


def _make(name, views, purpose, spec):
    return Variant(
        name=name,
        revision="2026-11-10.1",
        views=tuple(views),
        purpose=purpose,
        spec_json=json.dumps(spec, separators=(",", ":")),
    )


_REWORDED_REPLY = (
    "Is the current author asking the scoped recipient(s) to send back an answer, "
    "a confirmation or an acknowledgment, either explicitly or as a clear "
    "expectation? Count requests that the supplied context clearly implies. "
    "'Please confirm receipt' counts; 'No reply needed' does not. An old quoted "
    "request followed by 'Done, thanks' does not create a new reply request. Asking "
    "only for a document review is not a request to communicate a reply, but "
    "'review and reply with approval' does include one."
)
# Production wire order is alphabetical (profiles serialize with sort_keys);
# this is its exact reverse.
_REORDERED = (
    "reply_only",
    "none",
    "non_reply_action_only",
    "insufficient_context",
    "both",
)


def _registry():
    reworded = _spec_from("triage-v1")
    for q in reworded:
        if q["id"] == REPLY:
            if not q["instructions"].startswith(_COMMON):
                raise ValueError("production reply wording drift: review the variant")
            q["instructions"] = _COMMON + _REWORDED_REPLY
    reordered = _spec_from("triage-choice-v1")
    for q in reordered:
        if q["id"] == EXPECTATION:
            texts = dict(q["criteria"])
            q["criteria"] = [[key, texts[key]] for key in _REORDERED]
    union = {q["id"]: q for p in PRODUCTION for q in _spec_from(p)}
    combined = [union[key] for key in sorted(union)]
    variants = (
        _make(
            "eval-reply-reworded-v1",
            ["triage-v1"],
            "#196 wording check: triage-v1 with only the reply Noul reworded.",
            reworded,
        ),
        _make(
            "eval-choice-reordered-v1",
            ["triage-choice-v1"],
            "#196 option-order check: triage-choice-v1 with Choice options reversed.",
            reordered,
        ),
        _make(
            "eval-combined-v1",
            list(PRODUCTION),
            "#197 combined arm: every triage-v1 Noul plus the 5-way Choice in one request.",
            combined,
        ),
    )
    return {v.name: v for v in variants}


# Pinned so that production profile edits or registry edits are noticed, not
# silently carried into new run artifacts. Update only with a reviewed reason.
PINNED_DIGESTS = {
    "eval-reply-reworded-v1": "ca45d6823dba409201957756b95bc2ed3e235d2546640098b08c3d1b80938819",
    "eval-choice-reordered-v1": "be18ece5cf6e1fa07dc3628f2bcc7c9f4c61d3ccb6d7c75058c920882cfe0dae",
    "eval-combined-v1": "8985e2dfb6aedb0cef0e98bc1ab4402b47ce66ec10582d13c5f02e605ff94baf",
}
VARIANTS = _registry()


def get_variant(name):
    variant = VARIANTS.get(name)
    if variant is None:
        raise ValueError(f"unknown evaluation variant: {name}")
    if variant.digest != PINNED_DIGESTS[name]:
        raise ValueError(
            f"variant drift for {name}: digest {variant.digest} differs from the pinned "
            "value; review the production profile or registry change"
        )
    return variant


def to_canonical(variant, answers):
    """Re-key validated variant answers to canonical questions/options."""
    result = {}
    for qid, target in variant.mapping().items():
        answer = copy.deepcopy(answers[qid])
        if answer["type"] == "choice":
            options = target["options"]
            answer["choice"] = options[answer["choice"]]
            answer["probabilities"] = {
                options[key]: value for key, value in answer["probabilities"].items()
            }
            answer["probabilities"] = dict(sorted(answer["probabilities"].items()))
        result[target["canonical"]] = answer
    return result


def from_canonical(variant, answers):
    """Inverse of `to_canonical`, for fakes and round-trip checks."""
    result = {}
    for qid, target in variant.mapping().items():
        answer = copy.deepcopy(answers[target["canonical"]])
        if answer["type"] == "choice":
            inverse = {value: key for key, value in target["options"].items()}
            answer["choice"] = inverse[answer["choice"]]
            answer["probabilities"] = dict(
                sorted(
                    (inverse[key], value)
                    for key, value in answer["probabilities"].items()
                )
            )
        result[qid] = answer
    return result


@dataclass(frozen=True, slots=True)
class VariantRequest:
    """Production-prepared state plus variant questions. No credentials or client."""

    variant: Variant
    model: str
    base_url: str
    state_json: str = field(repr=False)
    snapshot: Any = field(repr=False)  # dead_letter.core.snapshot.EmailSnapshot

    @property
    def state_sha256(self):
        return self.validation_request().state_sha256

    @property
    def fingerprint(self):
        return digest(
            {
                "schema_version": REQUEST_SCHEMA_VERSION,
                "provider": "typesafe",
                "base_url": self.base_url,
                "model": self.model,
                "evaluation_variant_digest": self.variant.digest,
                "state_sha256": self.state_sha256,
            }
        )

    def payload(self):
        return {
            "model": self.model,
            "state": json.loads(self.state_json),
            "questions": self.variant.questions(),
        }

    def validation_request(self):
        return PreparedRequest(
            profile=self.variant.profile,
            model=self.model,
            base_url=self.base_url,
            state_json=self.state_json,
        )

    def preview(self, *, include_state=False):
        payload = self.payload()
        result = {
            "execution_status": "skipped",
            "reason": "offline_preparation_only",
            "remote_enabled": False,
            "evaluation_variant": self.variant.identity(),
            "requested_model": self.model,
            "state_sha256": self.state_sha256,
            "request_fingerprint": self.fingerprint,
            "source_sha256": self.snapshot.source_sha256,
            "wire_question_order": list(payload["questions"]),
            "wire_choice_option_order": {
                qid: list(q["criteria"])
                for qid, q in payload["questions"].items()
                if q["type"] == "choice"
            },
            "limits": {
                "unit": "utf8_bytes_not_provider_tokens",
                "request_bytes": len(canonical_json(payload).encode("utf-8")),
                "max_request_bytes": MAX_REQUEST_BYTES,
            },
            "disclosure": "No network request. Evaluation-only variant; not a shipped profile.",
        }
        if include_state:
            result["state"] = payload["state"]
        return result


def prepare_variant(
    path,
    variant,
    *,
    focus_identity=(),
    max_context_segments=3,
    model=DEFAULT_MODEL,
    base_url=DEFAULT_BASE_URL,
):
    """Request-preparation hook: production state builder, variant questions.

    State does not depend on the profile, so the first view's production
    preparation supplies identical evidence. Production byte limits apply.
    """
    prepared = prepare_eml(
        path,
        profile_name=variant.views[0],
        focus_identity=tuple(focus_identity),
        max_context_segments=max_context_segments,
        model=model,
        base_url=base_url,
    )
    request = VariantRequest(
        variant=variant,
        model=prepared.request.model,
        base_url=prepared.request.base_url,
        state_json=prepared.request.state_json,
        snapshot=prepared.snapshot,
    )
    payload = request.payload()
    state_bytes = len(request.state_json.encode("utf-8"))
    longest = max(
        len(canonical_json(q).encode("utf-8")) for q in payload["questions"].values()
    )
    if state_bytes > MAX_STATE_BYTES:
        raise AnalysisError("state_byte_limit_exceeded")
    if (
        state_bytes + longest > MAX_STATE_AND_QUESTION_BYTES
        or len(canonical_json(payload).encode("utf-8")) > MAX_REQUEST_BYTES
    ):
        raise AnalysisError("request_byte_limit_exceeded")
    return request


def envelope(request):
    """Result envelope with variant identity; never claims a production profile."""
    result = _result_envelope(
        PreparedEmail(snapshot=request.snapshot, request=request.validation_request())
    )
    result.update(
        request_fingerprint=request.fingerprint,
        adapter_version=None,
        evaluation_variant=request.variant.identity(),
    )
    return result


def execute(request, call, *, adapter_version, sdk_version=None):
    """Run one prepared variant through a caller-supplied transport.

    `call(payload)` returns {answers, model, usage, request_id}. The transport
    owns credentials, disclosure and retries; `adapter_version` names it so
    variant artifacts never claim the production adapter.
    """
    if not isinstance(adapter_version, str) or not adapter_version:
        raise ValueError("adapter_version must name the transport")
    result = envelope(request)
    result.update(adapter_version=adapter_version, sdk_version=sdk_version)
    if not result["coverage"]["authored_text_available"]:
        result["reason"] = "no_authored_text"
        return result
    result["billing_status"] = "unknown"
    try:
        raw = call(request.payload())
        checked = validate_response(request.validation_request(), raw)
    except AnalysisError as exc:
        result.update(
            execution_status="failed", assessment_status=None, error_code=exc.code
        )
        return result
    except Exception:
        result.update(
            execution_status="failed",
            assessment_status=None,
            error_code="variant_transport_failed",
        )
        return result
    canonical = to_canonical(request.variant, checked["answers"])
    abstained = canonical.get(EXPECTATION, {}).get("choice") == ABSTAIN
    result.update(
        execution_status="succeeded",
        answers=checked["answers"],
        usage=checked["usage"],
        returned_model=checked["model"],
        request_id=checked["request_id"],
        evaluated_at=_now(),
        assessment_status="insufficient_context" if abstained else "review_suggested",
    )
    return result


def run_staged(stage_dir, variant, out, call, *, adapter_version, sdk_version=None):
    """Write `<out>/<identity group>/<case>.analysis.json` for a staged selection."""
    stage_dir, out = Path(stage_dir), Path(out)
    manifest = _read_json(stage_dir / "manifest.json")
    out.mkdir(parents=True, exist_ok=False)
    written = []
    for case_id, entry in sorted(manifest["cases"].items()):
        group = entry["path"].split("/", 1)[0]
        request = prepare_variant(
            stage_dir / entry["path"],
            variant,
            focus_identity=tuple(manifest["groups"][group]),
            model=PINNED_MODEL,
        )
        result = execute(
            request, call, adapter_version=adapter_version, sdk_version=sdk_version
        )
        target = out / group / f"{case_id}.analysis.json"
        target.parent.mkdir(exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
        written.append(str(target))
    return written


def load_variant_run(directory, variant, cases, source_root):
    """Mirror of `score.load_run` for one variant; answers come back canonical.

    Production-profile sidecars, other variants and digest mismatches raise.
    Invalid successes are malformed; failed/skipped/attempt records never
    replace a success. Inconsistent Choices are malformed (no diagnosis path).
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"run directory missing: {directory}")
    by_id = {c["case_id"]: c for c in cases}
    success, seen, records, statuses, malformed = {}, set(), [], Counter(), []
    item_status = {key: "missing" for key in by_id}
    for path in sorted(directory.rglob("*.json")):
        attempt_parent = next(
            (p for p in path.parents if p.name.endswith(".analysis.json.attempts")),
            None,
        )
        if path.name.endswith(".analysis.json"):
            case_id = path.name.removesuffix(".analysis.json").removesuffix(".eml")
            if case_id in seen:
                raise ValueError(f"duplicate sidecar for {case_id}")
            seen.add(case_id)
        elif attempt_parent is not None:
            case_id = attempt_parent.name.removesuffix(
                ".analysis.json.attempts"
            ).removesuffix(".eml")
        else:
            continue
        if case_id not in by_id:
            raise ValueError(f"unknown sidecar case: {case_id}")

        def bad(reason):
            malformed.append({"path": str(path), "reason": reason})
            if attempt_parent is None:
                item_status[case_id] = "malformed"

        try:
            result = _read_json(path)
            if not isinstance(result, dict):
                raise ValueError("envelope must be an object")
        except (ValueError, OSError) as exc:
            bad(str(exc))
            continue
        profile = result.get("profile")
        name = profile.get("name") if isinstance(profile, dict) else None
        if not name:
            bad("missing profile identity")
            continue
        if name in PRODUCTION:
            raise ValueError(
                f"production profile {name} in {path}; expected evaluation variant {variant.name}"
            )
        if name != variant.name:
            raise ValueError(
                f"profile mismatch in {path}: {name} is not variant {variant.name}"
            )
        recorded = (result.get("evaluation_variant") or {}).get("digest")
        if recorded != variant.digest:
            raise ValueError(
                f"evaluation variant digest mismatch in {path}: artifact {recorded}, registry {variant.digest}"
            )
        status = result.get("execution_status")
        if (
            status not in ("succeeded", "failed", "skipped")
            or result.get("schema_version") != 1
            or result.get("artifact_type") != "message_analysis"
        ):
            bad("invalid envelope schema/status")
            continue
        statuses[status] += 1
        record = {
            "case_id": case_id,
            "path": str(path),
            "result": result,
            "attempt_record": attempt_parent is not None,
        }
        if status != "succeeded" or attempt_parent is not None:
            records.append(record)
            if item_status[case_id] == "missing":
                item_status[case_id] = status if status != "succeeded" else "malformed"
            continue
        try:
            case = by_id[case_id]
            request = prepare_variant(
                Path(source_root) / case["file"],
                variant,
                focus_identity=tuple(case["focus_identity"]),
                model=PINNED_MODEL,
            )
            if result.get("source", {}).get("sha256") != request.snapshot.source_sha256:
                raise ValueError("source hash mismatch")
            if result.get("state_sha256") != request.state_sha256:
                raise ValueError(
                    "state hash mismatch: inspect identity/context configuration"
                )
            if result.get("profile", {}).get("sha256") != variant.profile.sha256:
                raise ValueError("variant profile hash mismatch")
            if result.get("request_fingerprint") != request.fingerprint:
                raise ValueError("variant request fingerprint mismatch")
            if result.get("requested_model") != PINNED_MODEL:
                raise ValueError("requested model is not pinned jev-1.13.0")
            checked = validate_response(
                request.validation_request(),
                {
                    "answers": result["answers"],
                    "model": result.get("returned_model"),
                    "usage": result.get("usage"),
                    "request_id": result.get("request_id"),
                },
            )
            canonical = to_canonical(variant, checked["answers"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            bad(str(exc))
            continue
        records.append(record)
        success[case_id] = {
            **result,
            "native_answers": result["answers"],
            "answers": canonical,
        }
        item_status[case_id] = "succeeded"
    return (
        success,
        {
            "item_status": item_status,
            "envelope_status_counts": dict(statuses),
            "malformed": malformed,
            "argmax_choice_mismatch_ids": [],
        },
        records,
    )


def load_any(identity, directory, cases, source_root):
    """Production profile through `score.load_run`, variant through its loader."""
    if identity in PRODUCTION:
        return score.load_run(Path(directory), identity, cases, Path(source_root))
    return load_variant_run(directory, get_variant(identity), cases, source_root)


def view(predictions, profile):
    """Restrict canonical answers to one production profile's question set."""
    keys = set(get_profile(profile).questions())
    return {
        case_id: {
            **value,
            "answers": {k: v for k, v in value["answers"].items() if k in keys},
        }
        for case_id, value in predictions.items()
    }


def score_view(cases, selected, predictions, profile, *, adjust=None):
    """Existing dev-only threshold fit, normalization and summary for one view."""
    thresholds = score.fit_thresholds(cases, predictions, profile)
    normalized = {
        key: score.normalize(value, profile, thresholds)
        for key, value in predictions.items()
    }
    if adjust is not None:
        normalized = {key: adjust(key, value) for key, value in normalized.items()}
    return thresholds, normalized, score.summarize(selected, normalized)


def usage_summary(records, ids):
    """Response-reported usage only; absent fields stay unknown, never zero."""
    selected = [r for r in records if r["case_id"] in ids]
    result = {"records": len(selected)}
    for key in USAGE_KEYS:
        values = []
        for record in selected:
            usage = record["result"].get("usage")
            value = usage.get(key) if isinstance(usage, dict) else None
            if type(value) is int and value >= 0:
                values.append(value)
        result[key] = {
            "n": len(values),
            "unknown_records": len(selected) - len(values),
            "sum": sum(values) if values else None,
            "mean": sum(values) / len(values) if values else None,
            "p50": percentile(values, 0.5),
            "p90": percentile(values, 0.9),
        }
    result["limits"] = (
        "Response-reported usage, not billed usage; failed retries may be billed "
        "but unreported. Missing usage is unknown, not zero."
    )
    return result


def evaluate_variant(
    document,
    variant,
    directory,
    *,
    source_root=ROOT,
    split="test",
    reviewed_labels=None,
):
    """Score one variant run per production view with the existing scorer."""
    if "label_review" in document:
        raise ValueError("provide original pre-labels and explicit reviewed_labels")
    label_provenance = None
    if reviewed_labels is not None:
        from .reviewed_labels import apply_review

        document, label_provenance = apply_review(
            document, reviewed_labels, source_root, split=split
        )
    cases = document["cases"]
    selected = [c for c in cases if c["split"] == split]
    ids = {c["case_id"] for c in selected}
    raw, diagnostics, records = load_variant_run(directory, variant, cases, source_root)
    against = "reviewed annotations" if label_provenance else "model pre-labels"
    report = {
        "schema_version": 1,
        "status": f"evaluation-only variant {variant.name}: descriptive comparison to {against}; not a held-out quality claim or a shipped profile",
        "split": split,
        "labels_sha256": digest(document),
        "evaluation_variant": variant.identity(),
        "thresholds": {},
        "runs": {},
        "usage": usage_summary(records, ids),
    }
    if label_provenance is not None:
        report["label_provenance"] = label_provenance
    diagnostics["selected_status_counts"] = dict(
        Counter(diagnostics["item_status"][key] for key in sorted(ids))
    )
    normalized = {}
    for profile in variant.views:
        name = f"{variant.name}[{profile}]"
        thresholds, normalized[profile], summary = score_view(
            cases, selected, view(raw, profile), profile
        )
        report["thresholds"][name] = thresholds
        report["runs"][name] = {
            **summary,
            "diagnostics": diagnostics,
            "operations": score.operational(records, ids),
        }
    if set(PRODUCTION) <= set(normalized):
        report["paired_profile_difference"] = score.compare(
            selected, normalized["triage-v1"], normalized["triage-choice-v1"]
        )
    return report


def write_variant_report(report, out):
    score.write_report(report, Path(out))
    identity = report["evaluation_variant"]
    usage = report["usage"]
    lines = [
        "",
        "## Evaluation-only variant",
        "",
        f"`{identity['name']}` (digest `{identity['digest']}`) is not a shipped profile. "
        "Answers were mapped to canonical questions with the recorded mapping.",
        "",
        "| Usage field | Known records | Unknown | Sum | Mean |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key in USAGE_KEYS:
        row = usage[key]
        lines.append(
            f"| {key} | {row['n']} | {row['unknown_records']} | {row['sum']} | {row['mean']} |"
        )
    lines += ["", usage["limits"]]
    with (Path(out) / "report.md").open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def compare_runs(document, runs, *, source_root=ROOT, split=None, arms=(), limit=None):
    """Flips/drift of each run against the first, over shared canonical questions.

    `runs` maps a simple name to (identity, directory); identity is a production
    profile or a variant. Same-identity pairs require equal request fingerprints;
    cross-identity pairs require equal state hashes instead, since the question
    set (and so the fingerprint) differs by design. Labels are never read.
    """
    if len(runs) < 2:
        raise ValueError("provide at least two separately named runs")
    directories = {}
    for name, (identity, path) in runs.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("run names must be simple identifiers")
        path = Path(path).resolve()
        if any(
            path.is_relative_to(other) or other.is_relative_to(path)
            for other in directories.values()
        ):
            raise ValueError("run directories must be separate, non-overlapping trees")
        directories[name] = path
    cases = document["cases"]
    selected = select_cases(document, split, arms, limit)
    if not selected:
        raise ValueError("selection is empty")
    ids = [c["case_id"] for c in selected]
    identities = {name: identity for name, (identity, _) in runs.items()}
    # Per run; each pair compares its own intersection, so adding a run never
    # changes an existing pair's questions or metrics.
    question_sets = {
        name: set(get_profile(i).questions())
        if i in PRODUCTION
        else {t["canonical"] for t in get_variant(i).mapping().values()}
        for name, i in identities.items()
    }
    report = {
        "schema_version": 1,
        "status": "variant/repeat comparison on canonical questions; not correctness or calibration evidence",
        "labels_sha256": digest(document),
        "selected_case_ids": ids,
        "selection": {"split": split, "arms": list(arms), "limit": limit},
        "policy": {
            "reference": next(iter(runs)),
            "decisions": "fixed diagnostic rules shared with the stability comparator",
            "cross_identity_provenance": "state_sha256 replaces request_fingerprint; adapter_version must be present but may differ and is recorded per run",
        },
        "runs": {},
        "comparisons": {},
    }
    loaded = {}
    for name, path in directories.items():
        identity = identities[name]
        raw, diagnostics, records = load_any(identity, path, cases, source_root)
        loaded[name] = raw
        report["runs"][name] = {
            "identity": identity,
            "evaluation_variant": get_variant(identity).identity()
            if identity not in PRODUCTION
            else None,
            "directory": str(path),
            "selected_status_counts": dict(
                Counter(diagnostics["item_status"][key] for key in ids)
            ),
            "usage": usage_summary(records, set(ids)),
            "adapter_versions": dict(
                Counter(
                    str(r["result"].get("adapter_version"))
                    for r in records
                    if r["case_id"] in ids
                )
            ),
        }
    reference = report["policy"]["reference"]
    for name in list(runs)[1:]:
        first, second = loaded[reference], loaded[name]
        cross = identities[name] != identities[reference]
        matched, excluded = [], {}
        for key in ids:
            if key not in first or key not in second:
                excluded[key] = "no_valid_success_in_both_runs"
                continue
            a, b = first[key], second[key]
            if cross:
                # A variant cannot use the production adapter, so the transport
                # may differ (recorded per run); evidence must still be identical.
                a, b = (
                    {
                        **item,
                        "request_fingerprint": item.get("state_sha256"),
                        "adapter_version": "cross-identity"
                        if item.get("adapter_version")
                        else None,
                    }
                    for item in (a, b)
                )
            reason = stability._incomparable(a, b)
            if reason:
                excluded[key] = reason
            else:
                matched.append(key)
        metrics = {}
        for question in sorted(question_sets[reference] & question_sets[name]):
            pairs = [
                (key, first[key]["answers"][question], second[key]["answers"][question])
                for key in matched
            ]
            flips = [
                key
                for key, a, b in pairs
                if stability._decision(a) != stability._decision(b)
            ]
            metrics[question] = {
                "type": CANONICAL[question]["type"],
                "n": len(pairs),
                "label_flips": len(flips),
                "label_flip_rate": len(flips) / len(pairs) if pairs else None,
                "flipped_case_ids": flips,
                "probability_drift": stability._summary(
                    [stability._drift(a, b) for _, a, b in pairs]
                ),
            }
        deltas = {}
        for usage_key in USAGE_KEYS:
            values = []
            for key in matched:
                pair = [
                    (r.get("usage") or {}).get(usage_key)
                    for r in (first[key], second[key])
                ]
                if all(type(v) is int for v in pair):
                    values.append(pair[1] - pair[0])
            deltas[usage_key] = {
                "n": len(values),
                "unknown_pairs": len(matched) - len(values),
                "mean_delta": sum(values) / len(values) if values else None,
                "direction": f"{name} minus {reference}",
            }
        report["comparisons"][name] = {
            "reference": reference,
            "cross_identity": cross,
            "selected": len(ids),
            "matched": len(matched),
            "matched_case_ids": matched,
            "excluded": excluded,
            "questions": metrics,
            "paired_usage_delta": deltas,
        }
    return report


def write_comparison(report, out):
    out = Path(out).resolve()
    if any(
        out.is_relative_to(Path(run["directory"])) for run in report["runs"].values()
    ):
        raise ValueError("report must be outside input run trees")
    out.mkdir(parents=True, exist_ok=False)
    (out / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    lines = [
        "# Variant comparison",
        "",
        report["status"] + ".",
        "",
        "Each run is compared with the first named run on shared canonical questions. "
        "Evaluation-only variants are not shipped profiles; see `metrics.json` for digests and mappings.",
        "",
        "| Run (identity) | Matched / selected | Question | Label flips | Mean probability drift |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, comparison in report["comparisons"].items():
        identity = report["runs"][name]["identity"]
        for question, metrics in comparison["questions"].items():
            drift = metrics["probability_drift"]["mean"]
            lines.append(
                f"| {name} ({identity}) | {comparison['matched']} / {comparison['selected']} | "
                f"{question} | {metrics['label_flips']} / {metrics['n']} | "
                f"{drift if drift is not None else 'not computed'} |"
            )
    lines += [
        "",
        "Paired input-token deltas use only pairs where both runs report usage; "
        "unknown usage is never treated as zero.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print registered variants, digests and mappings")
    preview = commands.add_parser("preview", help="offline request preview for one EML")
    preview.add_argument("eml", type=Path)
    preview.add_argument("--variant", required=True)
    preview.add_argument("--identity", action="append", default=[])
    preview.add_argument("--show-state", action="store_true")
    scored = commands.add_parser("score", help="score one variant run tree")
    scored.add_argument("--variant", required=True)
    scored.add_argument("--run", required=True, type=Path)
    scored.add_argument("--split", choices=("dev", "test"), default="test")
    scored.add_argument("--reviewed-labels", type=Path)
    scored.add_argument("--out", required=True, type=Path)
    compared = commands.add_parser("compare", help="flips against a reference run")
    compared.add_argument(
        "--run", action="append", required=True, metavar="NAME=IDENTITY=DIR"
    )
    compared.add_argument("--split", choices=("dev", "test"))
    compared.add_argument(
        "--arm",
        action="append",
        default=[],
        choices=("clean", "missing_context", "adversarial", "long_thread"),
    )
    compared.add_argument("--limit", type=int)
    compared.add_argument("--out", required=True, type=Path)
    for command in (scored, compared):
        command.add_argument("--labels", type=Path, default=ROOT / "labels.json")
    args = parser.parse_args()
    try:
        if args.command == "list":
            result = {name: get_variant(name).identity() for name in VARIANTS}
        elif args.command == "preview":
            result = prepare_variant(
                args.eml,
                get_variant(args.variant),
                focus_identity=tuple(args.identity),
                model=PINNED_MODEL,
            ).preview(include_state=args.show_state)
        elif args.command == "score":
            report = evaluate_variant(
                _read_json(args.labels),
                get_variant(args.variant),
                args.run,
                source_root=args.labels.parent,
                split=args.split,
                reviewed_labels=_read_json(args.reviewed_labels)
                if args.reviewed_labels
                else None,
            )
            write_variant_report(report, args.out)
            result = {"report": str(args.out / "report.md"), "status": report["status"]}
        else:
            runs = {}
            for value in args.run:
                name, identity, directory = value.split("=", 2)
                if name in runs or not directory:
                    raise ValueError("each run needs a unique name and a directory")
                if identity not in PRODUCTION:
                    get_variant(identity)
                runs[name] = (identity, Path(directory))
            report = compare_runs(
                _read_json(args.labels),
                runs,
                source_root=args.labels.parent,
                split=args.split,
                arms=args.arm,
                limit=args.limit,
            )
            write_comparison(report, args.out)
            result = {"report": str(args.out / "report.md"), "status": report["status"]}
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
