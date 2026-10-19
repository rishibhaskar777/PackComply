import { useEffect, useState } from "react";
import { FileDown, FileText, RefreshCw } from "lucide-react";
import { downloadSelfCheckReport, getJson, isExpired, isUnavailable } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Pagination } from "../../components/Pagination";
import { Alert, Button, Card, Column, EmptyState, LinkButton, SkeletonRows, Table, useToast } from "../../components/ui";
import { fill, useI18n } from "../../i18n";
import { READINESS_TONE, readinessFromVerdict } from "../../lib/readiness";
import type { HistoryItem, HistoryResponse } from "../../types";
import { formatDate } from "../InspectionsPage";
import { STATUS_LABEL } from "./ProductsPage";

/** Completed self-checks with a download for each pre-market report. */
export function SelfCheckReportsPage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [data, setData] = useState<HistoryResponse | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);
  const [page, setPage] = useState(1);
  const [downloading, setDownloading] = useState<string | null>(null);

  async function load() {
    setFailure(null);
    try { setData(await getJson<HistoryResponse>(`/inspections?page=${page}&page_size=20&status=COMPLETED`, "reports")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
  }
  useEffect(() => { void load(); }, [page]); // eslint-disable-line react-hooks/exhaustive-deps

  async function download(item: HistoryItem) {
    setDownloading(item.id);
    try { await downloadSelfCheckReport(item.id, item.inspection_number); toast(t("toastReportDownloaded")); }
    catch (error) { if (isExpired(error)) onExpired(); else toast(t("reportGenerationError"), "error"); }
    finally { setDownloading(null); }
  }

  const columns: Column<HistoryItem>[] = [
    { key: "product", header: t("productName"), link: true, className: "cell-grow", render: item => <><span className="cell-primary">{item.product_name ?? t("unknown")}</span><span className="cell-secondary mono">{item.inspection_number}</span></> },
    { key: "date", header: t("date"), hideOnMobile: true, render: item => <span className="tabular">{formatDate(item.created_at, true)}</span> },
    { key: "readiness", header: t("readiness"), render: item => {
      const state = readinessFromVerdict(item.verdict);
      return state ? <span className={`status-pill status-pill--${READINESS_TONE[state]}`}>{STATUS_LABEL(t, state, 0).replace(/\s*·\s*0$/, "")}</span> : <span className="muted-text">—</span>;
    } },
    { key: "pdf", header: <span className="sr-only">{t("report")}</span>, align: "end", render: item => (
      <Button variant="outline" size="sm" icon={<FileDown size={16} aria-hidden="true" />} loading={downloading === item.id} onClick={() => void download(item)} aria-label={`${t("downloadSelfCheck")}: ${item.product_name ?? item.inspection_number}`}>{t("pdf")}</Button>
    ) },
  ];

  return (
    <div className="page maker-page">
      <PageHeader title={t("selfCheckReports")} subtitle={t("selfCheckReportsDescription")} />
      {failure ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("historyLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void load()}>{t("tryAgain")}</Button>} />
        : <Card title={t("completedChecks")} description={data ? fill(t("selfChecksSubtitle"), { count: data.total }) : undefined} flush>
          {data === null ? <SkeletonRows label={t("loading")} />
            : !data.items.length ? <EmptyState icon={<FileText size={16} />} title={t("noSelfChecks")} description={t("noSelfChecksBody")} action={<LinkButton href="#/check">{t("checkALabel")}</LinkButton>} />
            : <>
              <Table caption={t("selfCheckReports")} columns={columns} rows={data.items} rowKey={item => item.id} rowHref={item => `#/products/${item.id}`} />
              <Pagination page={data.page} totalPages={data.total_pages} onPage={setPage} />
            </>}
        </Card>}
    </div>
  );
}
