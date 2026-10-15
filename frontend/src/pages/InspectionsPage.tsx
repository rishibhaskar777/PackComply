import { useEffect, useRef, useState } from "react";
import { ClipboardList, Plus, RefreshCw } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Pagination } from "../components/Pagination";
import { Alert, Badge, Button, Card, Column, EmptyState, LinkButton, SearchInput, Select, SkeletonRows, Table, Tabs } from "../components/ui";
import { fill, TranslationKey, useI18n } from "../i18n";
import { HistoryItem, HistoryResponse } from "../types";

type VerdictFilter = "" | "FAIL" | "REVIEW" | "PASS";
const STATUS_OPTIONS: Record<string, TranslationKey> = { CREATED: "created", IMAGE_UPLOADED: "imageUploadedStatus", OCR_PROCESSING: "ocrProcessing", OCR_COMPLETED: "ocrCompleted", EXTRACTION_PROCESSING: "extractionProcessing", EXTRACTION_COMPLETED: "extractionCompleted", COMPLIANCE_PROCESSING: "complianceProcessing", COMPLETED: "completed", FAILED: "failed" };

export function formatDate(value: string, withTime = false) {
  return new Intl.DateTimeFormat(undefined, withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(new Date(value));
}

/** Debounced copy of a value, so typing in a search box does not fire a request per key. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => { const timer = window.setTimeout(() => setDebounced(value), delay); return () => window.clearTimeout(timer); }, [value, delay]);
  return debounced;
}

type InspectionsPageProps = { search: string; onSearchChange: (value: string) => void; onExpired: () => void };

export function InspectionsPage({ search, onSearchChange, onExpired }: InspectionsPageProps) {
  const { t } = useI18n();
  const [data, setData] = useState<HistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [verdict, setVerdict] = useState<VerdictFilter>("");
  const term = useDebounced(search.trim());

  async function load() {
    setLoading(true); setFailure(null);
    try {
      const query = new URLSearchParams({ page: String(page), page_size: "20" });
      if (term) query.set("search", term);
      if (status) query.set("status", status);
      if (verdict) query.set("compliance_status", verdict);
      setData(await getJson<HistoryResponse>(`/inspections?${query}`, "history"));
    } catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
    finally { setLoading(false); }
  }
  const lastTerm = useRef(term);
  useEffect(() => {
    // A new search starts from the first page; that page change triggers the load.
    if (lastTerm.current !== term) { lastTerm.current = term; if (page !== 1) { setPage(1); return; } }
    load();
  }, [page, status, verdict, term]);

  const columns: Column<HistoryItem>[] = [
    { key: "product", header: t("productName"), link: true, className: "cell-grow", render: item => <><span className="cell-primary">{item.product_name ?? item.image_filename ?? t("unknown")}</span><span className="cell-secondary mono">{item.inspection_number}</span></> },
    { key: "date", header: t("date"), hideOnMobile: true, render: item => <span className="tabular">{formatDate(item.created_at, true)}</span> },
    { key: "status", header: t("status"), hideOnMobile: true, render: item => <Badge variant={item.status} dot={false} /> },
    { key: "verdict", header: t("verdict"), render: item => item.verdict ?? item.compliance_status ? <Badge variant={item.verdict ?? item.compliance_status} /> : <Badge variant="PENDING" /> },
  ];
  const filtered = Boolean(term || status || verdict);

  return (
    <div className="page">
      <PageHeader title={t("inspections")} subtitle={data ? fill(t("inspectionsSubtitle"), { count: data.total }) : t("inspectionsDescription")} />
      <div className="toolbar">
        <SearchInput className="toolbar__search" aria-label={t("searchInspections")} placeholder={t("searchPlaceholder")} value={search} onChange={event => onSearchChange(event.target.value)} />
        <Tabs label={t("filterCompliance")} value={verdict} onChange={value => { setPage(1); setVerdict(value); }}
          items={[{ value: "", label: t("all") }, { value: "FAIL", label: t("verdictNonCompliant") }, { value: "REVIEW", label: t("verdictNeedsReview") }, { value: "PASS", label: t("verdictCompliant") }]} />
        <Select aria-label={t("filterStatus")} value={status} onChange={event => { setPage(1); setStatus(event.target.value); }}>
          <option value="">{t("allStatuses")}</option>
          {Object.entries(STATUS_OPTIONS).map(([value, key]) => <option key={value} value={value}>{t(key)}</option>)}
        </Select>
      </div>
      {failure ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("historyLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={load}>{t("tryAgain")}</Button>} />
        : <Card flush>
          {loading && !data ? <SkeletonRows label={t("loadingHistory")} />
            : !data?.items.length ? (filtered
              ? <EmptyState icon={<ClipboardList size={16} />} title={t("noMatchingInspections")} description={t("noMatchingInspectionsDescription")} />
              : <EmptyState icon={<ClipboardList size={16} />} title={t("noInspections")} description={t("startFirstInspection")} action={<LinkButton href="#/new" icon={<Plus size={16} aria-hidden="true" />}>{t("startFirstInspectionAction")}</LinkButton>} />)
            : <>
              <Table caption={t("inspections")} columns={columns} rows={data.items} rowKey={item => item.id} rowHref={item => `#/inspections/${item.id}`} className={loading ? "is-refreshing" : ""} />
              <Pagination page={data.page} totalPages={data.total_pages} onPage={setPage} />
            </>}
        </Card>}
    </div>
  );
}
