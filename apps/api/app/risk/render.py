"""A risk report as text for the command line."""

from __future__ import annotations

from app.ingestion.render import printable
from app.risk.model import Coverage, Report, RuleStatus

MAX_FINDINGS_SHOWN = 20
MAX_HASHES_SHOWN = 4


def render_report(report: Report) -> str:
    body = report.to_dict()
    subject = body["subject"]
    lines = [f"{subject['type']} {subject['chain']}  {subject['id']}"]
    if body["risk_score"] is None:
        lines.append("  risk score: none: too little data was held to judge")
    else:
        lines.append(
            f"  risk score {body['risk_score']}/100  {body['risk_level']}   "
            f"confidence {body['confidence']:.2f} (a separate measure: how far the evidence "
            "and data can be trusted)"
        )
    lines.append(f"  data coverage: {body['data_coverage']}   outcome: {body['outcome']}")
    if report.score and report.score.override:
        lines.append(f"  policy override: {report.score.override} sets the score to 100")

    findings = report.findings
    lines.append("")
    if findings:
        lines.append(f"Findings ({len(findings)}):")
        for item in findings[:MAX_FINDINGS_SHOWN]:
            finding = item.finding
            lines.append(
                f"  {finding.rule_id} {finding.severity.value:<8} +{item.contribution:5.1f}  "
                f"{printable(finding.description)}"
            )
            detail = [f"confidence {finding.confidence:.2f}", f"rule v{finding.rule_version}"]
            if finding.hops:
                detail.append(f"{finding.hops} hop(s)")
            if finding.sources:
                detail.append("from " + ", ".join(finding.sources))
            lines.append("      " + "; ".join(detail))
            if finding.transactions:
                shown = finding.transactions[:MAX_HASHES_SHOWN]
                more = finding.transactions_total - len(shown)
                lines.append(
                    "      transactions: "
                    + ", ".join(shown)
                    + (f" (+{more} more)" if more > 0 else "")
                )
        if len(findings) > MAX_FINDINGS_SHOWN:
            lines.append(f"  ... {len(findings) - MAX_FINDINGS_SHOWN} more")
    else:
        lines.append("No findings.")
    if report.suppressed:
        lines.append(
            "  not counted twice: "
            + ", ".join(
                f"{i.finding.rule_id} (same event as {i.suppressed_by})" for i in report.suppressed
            )
        )

    unjudged = [r for r in report.rules if r.status in (RuleStatus.NOT_EVALUATED, RuleStatus.ERROR)]
    if unjudged:
        lines.append("")
        lines.append(f"Rules that could not be judged ({len(unjudged)} of {len(report.rules)}):")
        for rule in unjudged:
            lines.append(f"  {rule.rule_id} {rule.name}: {rule.reason}")
    if report.gaps:
        lines.append("")
        lines.append("Data gaps:")
        lines += [f"  - {gap['message']}" for gap in report.gaps]
    lines += [f"  note: {warning}" for warning in report.warnings]

    lines.append("")
    if report.coverage is not Coverage.COMPLETE or not findings:
        lines.append("This is not a clearance: " + body["disclaimer"].split(". ", 1)[-1])
    else:
        lines.append(body["disclaimer"])
    return "\n".join(lines)
