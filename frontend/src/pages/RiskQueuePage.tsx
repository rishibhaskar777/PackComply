import { useEffect, useState } from "react";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Pagination } from "../components/Pagination";
import { Alert, Badge, Button, Card, Column, EmptyState, RiskScore, Select, SkeletonRows, Table, Tabs } from "../components/ui";
import { fill, useI18n } from "../i18n";
import { RiskItem, RiskLevel, RiskPageData } from "../types";
import { formatDate } from "./InspectionsPage";

const LEVELS: RiskLevel[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
const VERDICT_OF: Record<string, string> = { FAIL: "NON_COMPLIANT", WARNING: "NEEDS_REVIEW", REVIEW: "NEEDS_REVIEW", PASS: "COMPLIANT" };

export function RiskQueuePage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<RiskPageData | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [level, setLevel] = useState<"" | RiskLevel>("");
  const [compliance, setCompliance] = useState("");
  // Level counts come from the unfiltered-by-level summary of the last response.
  const [summary, setSummary] = useState<Record<RiskLevel, number> | null>(null);

  async function load() {
    setLoading(true); setFailure(null);
    try {
      const query = new URLSearchParams({ page: String(page), page_size: "20" });
      if (level) query.set("risk_level", level);
      if (compliance) query.set("compliance_status", compliance);
      const result = await getJson<RiskPageData>(`/risk-targeting?${query}`, "risk targeting");
      setData(result);
      if (!level) setSummary(result.summary);
    } catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, [page, level, compliance]);

  const columns: Column<RiskItem>[] = [
    { key: "priority", header: "#", width: "48px", render: item => <span className="mono muted-text">{item.priority}</span> },
    { key: "product", header: t("productName"), link: true, className: "cell-grow", render: item => <span className="cell-primary">{item.product_name ?? t("recUntitled")}</span> },
    { key: "verdict", header: t("verdict"), render: item => <Badge variant={VERDICT_OF[item.compliance_status] ?? item.compliance_status} /> },
    { key: "level", header: t("riskLevel"), hideOnMobile: true, render: item => <Badge variant={item.risk_level} /> },
    { key: "score", header: t("risk"), render: item => <RiskScore score={item.risk_score} /> },
    { key: "reason", header: t("primaryReason"), hideOnMobile: true, className: "cell-wrap", render: item => <span className="cell-clamp">{item.primary_reason}</span> },
    { key: "date", header: t("date"), hideOnMobile: true, render: item => <span className="tabular">{formatDate(item.completed_at)}</span> },
  ];

  return (
    <div className="page">
      <PageHeader title={t("riskQueue")} subtitle={t("riskTargetingDescription")} />
      <div className="toolbar">
        <Tabs label={t("riskLevel")} value={level} onChange={value => { setPage(1); setLevel(value); }}
          items={[{ value: "" as const, label: t("all"), count: summary ? LEVELS.reduce((total, key) => total + summary[key], 0) : undefined }, ...LEVELS.map(key => ({ value: key, label: t(key.toLowerCase() as "critical" | "high" | "medium" | "low"), count: summary?.[key] }))]} />
        <Select aria-label={t("filterCompliance")} value={compliance} onChange={event => { setPage(1); setCompliance(event.target.value); }}>
          <option value="">{t("allComplianceStatuses")}</option>
          <option value="FAIL">{t("verdictNonCompliant")}</option>
          <option value="WARNING">{t("verdictNeedsReview")}</option>
          <option value="PASS">{t("verdictCompliant")}</option>
        </Select>
      </div>
      {failure ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("riskTargetingError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={load}>{t("tryAgain")}</Button>} />
        : <Card title={t("riskQueueTable")} description={data ? fill(t("riskQueueCount"), { count: data.total }) : undefined} flush>
          {loading && !data ? <SkeletonRows label={t("riskTargetingLoading")} />
            : !data?.items.length ? <EmptyState icon={<ShieldAlert size={16} />} title={t("riskTargetingNoResults")} />
            : <>
              <Table caption={t("riskQueue")} columns={columns} rows={data.items} rowKey={item => item.inspection_id} rowHref={item => `#/inspections/${item.inspection_id}`} />
              <Pagination page={data.page} totalPages={data.total_pages} onPage={setPage} />
            </>}
        </Card>}
    </div>
  );
}
