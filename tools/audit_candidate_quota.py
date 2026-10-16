"""Read-only, aggregate-only candidate quota audit; never a release gate.

python tools/audit_candidate_quota.py --paper PRIVATE/paper.jsonl \
    --slots PRIVATE/quota-slots.json --out NEW/report.json

Exit 0 means the audit completed, NOT that quotas/freeze passed. Exit 2 means
invalid input/output. No credentials, Agent, judge, cache or network access.
Slot sidecar is draft author intent bound to exact paper bytes, not approval.
No changes to derive_tags, scorer, schema or historical artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import validate_samples

DIMENSIONS = ("long_term_retention", "memory_recall", "dynamic_update",
              "similar_discrimination", "boundary_recognition", "task_reuse")
DEEP = {"deepening_time": 3, "deepening_disturbance": 3, "deepening_multistep": 4}
SLOTS = {"core", *DEEP}
FORMAT = "okb-quota-draft-v1"
RELATIVE = re.compile(r"本月|这个月|下个月|上个月|本季度|这个季度|上季度|下季度|上周|本周|下周|"
                      r"\b(?:this|last|next)\s+(?:week|month|quarter)\b", re.I)
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class AuditError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise AuditError("duplicate JSON key")
        out[k] = v
    return out


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditError("nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise AuditError("invalid JSON input") from exc


def read_inputs(paper_path: Path, slots_path: Path):
    raw, side = paper_path.read_bytes(), slots_path.read_bytes()
    try:
        samples = [_json(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    except UnicodeError as exc:
        raise AuditError("paper must be UTF-8") from exc
    if not samples or any(not isinstance(s, dict) for s in samples):
        raise AuditError("paper must contain sample objects")
    # Do not echo lint messages: these can contain private content.
    report = validate_samples(samples)
    if report.errors:
        raise AuditError("paper validation failed (private lint required)")
    ids = [s["id"] for s in samples]
    if len(ids) != len(set(ids)):
        raise AuditError("duplicate sample identity")
    manifest = _json(side)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "paper_sha256", "rows"}:
        raise AuditError("slot manifest shape invalid")
    if manifest["format"] != FORMAT or manifest["paper_sha256"] != sha(raw):
        raise AuditError("slot manifest version or paper fingerprint mismatch")
    rows = manifest["rows"]
    if not isinstance(rows, list):
        raise AuditError("slot rows must be a list")
    slots = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"sample_id", "slot"}:
            raise AuditError("slot row shape invalid")
        sid, slot = row["sample_id"], row["slot"]
        if not isinstance(sid, str) or not isinstance(slot, str) or slot not in SLOTS or sid in slots:
            raise AuditError("duplicate identity or invalid slot")
        slots[sid] = slot
    if set(slots) != set(ids):
        raise AuditError("slot coverage must equal paper identities exactly")
    return samples, slots, {"paper_sha256": sha(raw), "slots_sha256": sha(side)}, len(report.warnings)


def _timestamp(text):
    if not isinstance(text, str) or not ISO.fullmatch(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _artifact_path(value):
    if not isinstance(value, str) or not value or any(c in value for c in ("\\", ":", "\x00")):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        return None
    # Conservative portable alias handling: case variants do not count twice.
    return str(path).casefold()


def structural_features(sample):
    """Observable prerequisites only; explicitly not semantic eligibility tags."""
    script = sample["script"]
    writes = [i for i, x in enumerate(script) if x["phase"] == "write"]
    probes = [i for i, x in enumerate(script) if x["phase"] == "probe"]
    pairs = [(w, p) for w in writes for p in probes if w < p]
    minimum = min((sum(script[i]["phase"] == "disturb" for i in range(w + 1, p))
                   for w, p in pairs), default=0)
    stamps = [_timestamp(x.get("time")) for x in script]
    ordered = bool(stamps) and all(x is not None for x in stamps)
    ordered = ordered and all(a <= b for a, b in zip(stamps, stamps[1:]))
    month_span = 0
    if ordered and pairs:
        # Draft conservative interpretation: first write to final probe >=2 month boundaries.
        a, b = stamps[writes[0]], stamps[probes[-1]]
        month_span = (b.year - a.year) * 12 + b.month - a.month
    relative = any(RELATIVE.search(t["text"]) for p in probes for t in script[p]["turns"])
    historical = False
    for ref in sample.get("expect", {}).get("gold_evidence", []):
        session, turn = ref.split(".")
        for i in writes:
            if script[i]["session"] == session and any(i < p for p in probes):
                historical = True
    artifacts = set()
    for anchor in sample["anchors"]:
        if (anchor["type"] == "file_content" and anchor["severity"] == "hard"
                and anchor["expect"] == "present" and anchor.get("content_regex")
                and not anchor.get("must_equal_fixture")):
            path = _artifact_path(anchor.get("path"))
            if path:
                artifacts.add(path)
    # time has no explicit 'update' phase: two writes are only a structural proxy.
    time = bool(len(writes) >= 2 and probes and ordered and month_span >= 2 and relative)
    return {"deepening_time": time,
            "deepening_disturbance": bool(pairs and minimum >= 3),
            "deepening_multistep": bool(historical and probes and len(artifacts) >= 2)}


def audit(samples, slots, fingerprints, lint_warnings=0):
    core, declared, supported, markers = Counter(), Counter(), Counter(), Counter()
    by_dim = {d: Counter() for d in DIMENSIONS}
    boundary, anchor_counts, absent_candidates = Counter(), Counter(), Counter()
    raw_positive = raw_brake = overlap = 0
    for sample in samples:
        dim, sid, expect = sample["dimension"], sample["id"], sample["expect"]
        slot = slots[sid]
        if slot == "core":
            core[dim] += 1
            if dim == "boundary_recognition":
                boundary[expect["boundary_kind"]] += 1
        else:
            declared[slot] += 1
        features = structural_features(sample)
        for name, present in features.items():
            markers[name] += int(present)
        if slot in DEEP and features[slot]:
            supported[slot] += 1
        withhold = dim == "boundary_recognition" and expect.get("boundary_kind") == "withhold"
        positive = expect["verdict"] == "remembered"
        brake = not positive or withhold
        raw_positive += positive
        raw_brake += brake
        overlap += positive and brake
        # Diagnostic convention ONLY: withhold takes priority to reproduce prior 11:1.
        by_dim[dim]["brake" if brake else "positive"] += 1
        anchor_counts[dim] += sum(a["severity"] == "hard" and a.get("on_violation") == "confused"
                                  for a in sample["anchors"])
        absent_candidates[dim] += any(a["type"] == "file_content" and a["expect"] == "absent"
                                       and a.get("field_path") for a in sample["anchors"])
    p = sum(v["positive"] for v in by_dim.values())
    b = sum(v["brake"] for v in by_dim.values())
    quotas = {"total_exactly_100": len(samples) == 100,
              "core_exactly_15_each": all(core[d] == 15 for d in DIMENSIONS),
              "declared_deepening_exact": all(declared[k] == n for k, n in DEEP.items()),
              "all_declared_deepening_structurally_supported": all(declared[k] == supported[k] for k in DEEP),
              "core_boundary_withhold_10_release_5": boundary["withhold"] >= 10 and boundary["release"] >= 5}
    return {"format": "okb-quota-audit-v1", "status": "draft_audit_completed",
            "release_authorized": False, "formal_quota_accepted": 0,
            "official_symmetry_gate": None, "official_deepening_gate": None,
            "input_sha256": fingerprints, "sample_count": len(samples), "lint_warning_count": lint_warnings,
            "core_counts": {d: core[d] for d in DIMENSIONS},
            "deepening_declared": {k: declared[k] for k in DEEP},
            "deepening_structurally_supported": {k: supported[k] for k in DEEP},
            "structural_feature_counts_not_tags": {k: markers[k] for k in DEEP},
            "quota_diagnostics_not_release_gates": quotas,
            "boundary_core_counts": {k: boundary[k] for k in ("withhold", "release")},
            "literal_symmetry": {"raw_positive": raw_positive, "raw_brake": raw_brake, "overlap": overlap,
                "withhold_priority_diagnostic": {"positive": p, "brake": b,
                    "imbalance_over_total": abs(p-b)/len(samples),
                    "difference_le_10_percent_of_total": abs(p-b)*10 <= len(samples),
                    "brake_at_least_5_each": all(by_dim[d]["brake"] >= 5 for d in DIMENSIONS),
                    "by_dimension": {d: {k: by_dim[d][k] for k in ("positive", "brake")} for d in DIMENSIONS}}},
            "hard_confused_anchor_counts": {d: anchor_counts[d] for d in DIMENSIONS},
            "absent_field_sample_candidates_not_approved": {d: absent_candidates[d] for d in DIMENSIONS},
            "unresolved": ["symmetry partition, denominator and >=90 membership need co-signature",
                "task intent proposal not applied; negative anchors do not establish braking intent",
                "two write sessions do not establish a write/update/probe temporal dependency",
                "time span, relative lexicon and window-anchor linkage need semantic review",
                "three disturbance sessions do not establish three distinct topics",
                "two output files do not establish separate historical applications",
                "absent field predicates do not prove old/neighbor-value confusion semantics",
                "slot declarations and structural markers are not official builder tags",
                "independent review, runtime compatibility and other freeze gates remain external"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", type=Path, required=True)
    parser.add_argument("--slots", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.exists() or args.out.resolve() in (args.paper.resolve(), args.slots.resolve()):
            raise AuditError("output must be new and distinct from inputs")
        samples, slots, fingerprints, warnings = read_inputs(args.paper, args.slots)
        result = audit(samples, slots, fingerprints, warnings)
        # Recheck bytes before output; no result bound to drifted inputs.
        if sha(args.paper.read_bytes()) != fingerprints["paper_sha256"] or sha(args.slots.read_bytes()) != fingerprints["slots_sha256"]:
            raise AuditError("input changed during audit")
        with args.out.open("x", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except (AuditError, OSError, ValueError, TypeError, KeyError, AttributeError):
        # Avoid paths/sample values/exceptions leaking candidate content to public logs.
        print("Audit refused: invalid input or non-exclusive output; inspect privately.", file=sys.stderr)
        return 2
    print("Draft audit completed; formal accepted=0; release_authorized=false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
