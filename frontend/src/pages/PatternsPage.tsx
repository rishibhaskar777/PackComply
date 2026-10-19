import { useEffect, useState } from "react";
import { CircleAlert, Layers, RefreshCw, Repeat2 } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Alert, Badge, Button, Card, Column, EmptyState, SkeletonRows, StatCard, Table } from "../components/ui";
import { useI18n } from "../i18n";
import { Pattern } from "../types";
import { formatDate } from "./InspectionsPage";

const MAX_LINKS = 4;

export function PatternsPage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const [patterns, setPatterns] = useState<Pattern[] | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);

  async function load() {
    setFailure(null);
    try { setPatterns(await getJson<Pattern[]>("/patterns", "patterns")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
  }
  useEffect(() => { load(); }, []);

  const typeBadge = (pattern: Pattern) => pattern.pattern_type === "COMPLIANCE_VIOLATION"
    ? <Badge tone="violation">{t("complianceViolation")}</Badge>
    : pattern.pattern_type === "COMPLIANCE_WARNING" ? <Badge tone="review">{t("complianceWarning")}</Badge> : <Badge tone="neutral">{t("missingField")}</Badge>;
  const items = patterns ?? [];
  const columns: Column<Pattern>[] = [
    { key: "issue", header: t("issue"), className: "cell-wrap cell-grow", render: pattern => <><span className="cell-primary">{pattern.title}</span><span className="cell-secondary mono">{pattern.rule_code ?? pattern.affected_field ?? pattern.pattern_key}</span></> },
    { key: "type", header: t("type"), hideOnMobile: true, render: typeBadge },
    { key: "count", header: t("labels"), align: "end", render: pattern => <span className="mono">{pattern.occurrence_count}</span> },
    { key: "last", header: t("lastSeen"), hideOnMobile: true, render: pattern => <span className="tabular">{formatDate(pattern.last_seen)}</span> },
    { key: "inspections", header: t("affectedInspections"), hideOnMobile: true, render: pattern => (
      <span className="id-links">
        {pattern.inspection_ids.slice(0, MAX_LINKS).map(id => <a key={id} className="id-link mono" href={`#/inspections/${id}`} aria-label={`${t("viewInspection")} ${id.slice(0, 8)}`}>{id.slice(0, 8)}</a>)}
        {pattern.inspection_ids.length > MAX_LINKS && <span className="muted-text">+{pattern.inspection_ids.length - MAX_LINKS}</span>}
      </span>
    ) },
  ];

  return (
    <div className="page">
      <PageHeader title={t("repeatIssues")} subtitle={t("patternsDescription")} />
      {failure ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("patternsLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={load}>{t("tryAgain")}</Button>} />
        : <>
          <div className="stat-grid stat-grid--three">
            <StatCard index={0} label={t("detectedPatterns")} value={patterns ? items.length : "—"} icon={<Layers size={16} />} />
            <StatCard index={1} label={t("occurrences")} value={patterns ? items.reduce((total, item) => total + item.occurrence_count, 0) : "—"} icon={<Repeat2 size={16} />} />
            <StatCard index={2} label={t("highSeverity")} value={patterns ? items.filter(item => item.severity === "HIGH").length : "—"} icon={<CircleAlert size={16} />} tone="violation" />
          </div>
          <Card title={t("repeatedIssues")} description={t("patternsTableDescription")} flush>
            {patterns === null ? <SkeletonRows label={t("loading")} />
              : !items.length ? <EmptyState icon={<Repeat2 size={16} />} title={t("noRepeatedPatterns")} description={t("patternsEmptyDescription")} />
              : <Table caption={t("repeatedIssues")} columns={columns} rows={items} rowKey={pattern => `${pattern.pattern_type}:${pattern.pattern_key}`} />}
          </Card>
        </>}
    </div>
  );
}
