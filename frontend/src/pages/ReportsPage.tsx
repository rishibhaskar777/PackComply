import { useEffect, useState } from "react";
import { FileDown, FileText, RefreshCw } from "lucide-react";
import { downloadReport, getJson, isExpired, isUnavailable } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Pagination } from "../components/Pagination";
import { Alert, Badge, Button, Card, Column, EmptyState, SkeletonRows, Table, useToast } from "../components/ui";
import { fill, useI18n } from "../i18n";
import { HistoryItem, HistoryResponse } from "../types";
import { formatDate } from "./InspectionsPage";

/** Completed inspections with a PDF report download. */
export function ReportsPage({ onExpired }: { onExpired: () => void }) {
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
  useEffect(() => { load(); }, [page]);

  async function download(item: HistoryItem) {
    setDownloading(item.id);
    try { await downloadReport(item.id, item.inspection_number); toast(t("toastReportDownloaded")); }
    catch (error) { if (isExpired(error)) onExpired(); else toast(t("reportGenerationError"), "error"); }
    finally { setDownloading(null); }
  }

  const columns: Column<HistoryItem>[] = [
    { key: "product", header: t("productName"), link: true, className: "cell-grow", render: item => <><span className="cell-primary">{item.product_name ?? t("unknown")}</span><span className="cell-secondary mono">{item.inspection_number}</span></> },
    { key: "date", header: t("date"), hideOnMobile: true, render: item => <span className="tabular">{formatDate(item.created_at)}</span> },
    { key: "verdict", header: t("verdict"), hideOnMobile: true, render: item => <Badge variant={item.verdict ?? item.compliance_status ?? "PENDING"} /> },
    { key: "pdf", header: <span className="sr-only">{t("report")}</span>, align: "end", render: item => (
      <Button variant="outline" size="sm" icon={<FileDown size={16} aria-hidden="true" />} loading={downloading === item.id} onClick={() => download(item)} aria-label={`${t("downloadPdfReport")} ${item.inspection_number}`}>{t("pdf")}</Button>
    ) },
  ];

  return (
    <div className="page">
      <PageHeader title={t("reports")} subtitle={t("reportsDescription")} />
      {failure ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("historyLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={load}>{t("tryAgain")}</Button>} />
        : <Card title={t("completedInspections")} description={data ? fill(t("inspectionsSubtitle"), { count: data.total }) : undefined} flush>
          {data === null ? <SkeletonRows label={t("loading")} />
            : !data.items.length ? <EmptyState icon={<FileText size={16} />} title={t("noReports")} description={t("noReportsDescription")} />
            : <>
              <Table caption={t("reports")} columns={columns} rows={data.items} rowKey={item => item.id} rowHref={item => `#/inspections/${item.id}`} />
              <Pagination page={data.page} totalPages={data.total_pages} onPage={setPage} />
            </>}
        </Card>}
    </div>
  );
}
