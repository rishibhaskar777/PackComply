import { useEffect, useState } from "react";
import { CircleAlert, CircleCheck, ClipboardList, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../api/client";
import { LivePill } from "../components/LivePill";
import { PageHeader } from "../components/PageHeader";
import { Alert, Badge, Button, Card, Column, EmptyState, LinkButton, RiskScore, Skeleton, SkeletonRows, StatCard, Table, Tabs } from "../components/ui";
import { fill, useI18n } from "../i18n";
import { DashboardPeriod, DashboardSummary, Verdict } from "../types";

type QueueRow = DashboardSummary["top_risk"][number];
const VERDICTS: Array<{ key: Verdict; tone: "violation" | "review" | "pass" }> = [
  { key: "NON_COMPLIANT", tone: "violation" }, { key: "NEEDS_REVIEW", tone: "review" }, { key: "COMPLIANT", tone: "pass" },
];

export function OverviewPage({ official, onExpired, showLive = false }: { official: boolean; onExpired: () => void; showLive?: boolean }) {
  const { t } = useI18n();
  const [period, setPeriod] = useState<DashboardPeriod>("all");
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);
  const [loading, setLoading] = useState(true);

  async function load(selected = period) {
    setLoading(true); setFailure(null);
    try { setData(await getJson<DashboardSummary>(`/dashboard/summary?period=${selected}`, "dashboard")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(period); }, [period]);

  const verdictLabel = { NON_COMPLIANT: t("verdictNonCompliant"), NEEDS_REVIEW: t("verdictNeedsReview"), COMPLIANT: t("verdictCompliant") };
  const subtitle = data ? fill(t("overviewSubtitle"), { count: data.total_inspections, version: data.ruleset_version }) : t("overviewSubtitleLoading");
  const periodTabs = <Tabs label={t("period")} value={period} onChange={setPeriod} items={[{ value: "7d", label: t("period7Days") }, { value: "30d", label: t("period30Days") }, { value: "all", label: t("periodAllTime") }]} />;
  const newButton = <LinkButton href="#/new" icon={<Plus size={16} aria-hidden="true" />}>{t("startFirstInspectionAction")}</LinkButton>;

  const queueColumns: Column<QueueRow>[] = [
    { key: "product", header: t("productName"), link: true, className: "cell-grow", render: row => <><span className="cell-primary">{row.product_name ?? t("unknown")}</span><span className="cell-secondary mono">{row.inspection_number}</span></> },
    { key: "maker", header: t("makerImporter"), hideOnMobile: true, className: "cell-truncate", render: row => row.maker ?? <span className="muted-text">{t("notDetected")}</span> },
    { key: "verdict", header: t("verdict"), render: row => <Badge variant={row.verdict} /> },
    ...(official ? [{ key: "risk", header: t("risk"), render: (row: QueueRow) => row.risk_score === null ? "—" : <RiskScore score={row.risk_score} /> }] : []),
  ];

  let body;
  if (loading && !data) {
    body = <>
      <div className="stat-grid">{[0, 1, 2, 3].map(index => <div className="stat-card" key={index}><Skeleton width="50%" /><Skeleton width="30%" height={32} style={{ marginTop: 12 }} /><Skeleton width="70%" height={12} style={{ marginTop: 8 }} /></div>)}</div>
      <div className="dashboard-grid"><Card title={<Skeleton width={140} />} flush><SkeletonRows rows={5} label={t("loading")} /></Card><Card title={<Skeleton width={100} />}><Skeleton width="100%" height={10} /></Card></div>
    </>;
  } else if (failure) {
    body = <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("dashboardLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => load()}>{t("tryAgain")}</Button>} />;
  } else if (data && data.total_inspections === 0 && period === "all") {
    body = <Card><EmptyState icon={<ClipboardList size={16} />} title={t("noInspections")} description={t("startFirstInspection")} action={newButton} /></Card>;
  } else if (data) {
    const counts = data.verdict_counts;
    const decided = counts.NON_COMPLIANT + counts.NEEDS_REVIEW + counts.COMPLIANT;
    body = <>
      <div className="stat-grid">
        <StatCard index={0} label={t("inspections")} value={data.total_inspections} icon={<ClipboardList size={16} />} tone="primary"
          caption={data.median_processing_seconds !== null ? fill(t("medianProcessing"), { seconds: Math.round(data.median_processing_seconds) }) : counts.PENDING ? fill(t("pendingCount"), { count: counts.PENDING }) : t("inspectionsCaption")} />
        <StatCard index={1} label={t("verdictNonCompliant")} value={counts.NON_COMPLIANT} icon={<CircleAlert size={16} />} tone="violation" caption={t("nonCompliantCaption")} />
        <StatCard index={2} label={t("verdictNeedsReview")} value={counts.NEEDS_REVIEW} icon={<TriangleAlert size={16} />} tone="review" caption={t("needsReviewCaption")} />
        <StatCard index={3} label={t("verdictCompliant")} value={counts.COMPLIANT} icon={<CircleCheck size={16} />} tone="pass" caption={t("compliantCaption")} />
      </div>
      <div className="dashboard-grid">
        <Card index={4} title={official ? t("riskQueue") : t("needsAttention")} description={official ? t("riskQueueDescription") : t("needsAttentionDescription")} flush
          action={<LinkButton variant="outline" size="sm" href={official ? "#/risk" : "#/inspections"}>{t("viewAll")}</LinkButton>}>
          {data.top_risk.length
            ? <Table caption={official ? t("riskQueue") : t("needsAttention")} columns={queueColumns} rows={data.top_risk} rowKey={row => row.id} rowHref={row => `#/inspections/${row.id}`} />
            : <EmptyState title={t("noCompletedInPeriod")} />}
        </Card>
        <div className="dashboard-side">
          <Card index={5} title={t("verdictMix")} description={fill(t("verdictMixDescription"), { count: decided })}>
            <div className="verdict-bar" aria-hidden="true">
              {decided ? VERDICTS.filter(item => counts[item.key] > 0).map(item => <span key={item.key} className={`verdict-bar__segment verdict-bar__segment--${item.tone}`} style={{ flexGrow: counts[item.key] }} />) : <span className="verdict-bar__segment" style={{ flexGrow: 1 }} />}
            </div>
            <ul className="verdict-legend">
              {VERDICTS.map(item => <li key={item.key}><span className={`legend-dot legend-dot--${item.tone}`} aria-hidden="true" /><span className="verdict-legend__label">{verdictLabel[item.key]}</span><span className="verdict-legend__count mono">{counts[item.key]}</span></li>)}
            </ul>
          </Card>
          <Card index={6} title={t("repeatIssues")} description={t("repeatIssuesDescription")}
            action={<LinkButton variant="ghost" size="sm" href="#/patterns">{t("viewAll")}</LinkButton>}>
            {data.repeat_issues.length ? (
              <ul className="issue-list">
                {data.repeat_issues.map(issue => (
                  <li className="issue-list__item" key={issue.rule_id}>
                    <div className="issue-list__text">
                      <p className="issue-list__title">{issue.title}</p>
                      <p className="issue-list__reference mono">{issue.legal_reference ?? issue.rule_id}</p>
                    </div>
                    <span className="count-chip mono">{fill(t(issue.label_count === 1 ? "labelCountOne" : "labelCountOther"), { count: issue.label_count })}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="muted-text">{t("noRepeatIssues")}</p>}
          </Card>
        </div>
      </div>
    </>;
  }

  return (
    <div className="page">
      <PageHeader title={t("overview")} subtitle={subtitle} actions={periodTabs} status={showLive ? <LivePill /> : undefined} />
      {body}
    </div>
  );
}
