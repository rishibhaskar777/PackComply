import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Boxes, CircleAlert, CircleCheck, FileDown, Plus, RefreshCw, ScanSearch, TrendingUp, TriangleAlert } from "lucide-react";
import { downloadSelfCheckReport, getJson, isExpired, isUnavailable } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Alert, Button, Card, CountUp, EmptyState, LinkButton, Skeleton, Tabs, useToast } from "../../components/ui";
import { fill, useI18n } from "../../i18n";
import { READINESS_TONE } from "../../lib/readiness";
import type { ProductCard, ProductsSummary, Readiness } from "../../types";
import { formatDate } from "../InspectionsPage";
import { ProductThumb } from "./ProductThumb";
import { ReadinessRing } from "./ReadinessRing";

type Filter = "all" | "fix" | "ready";

/** Manufacturer home: how many products are ready to print, and one card per product with what to do next. */
export function ProductsPage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [data, setData] = useState<ProductsSummary | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [downloading, setDownloading] = useState<string | null>(null);

  async function load() {
    setFailure(null);
    try { setData(await getJson<ProductsSummary>("/products", "products")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
  }
  useEffect(() => { void load(); }, []);

  async function report(product: ProductCard) {
    setDownloading(product.inspection_id);
    try { await downloadSelfCheckReport(product.inspection_id, product.inspection_id.slice(0, 8)); toast(t("toastReportDownloaded")); }
    catch (error) { if (isExpired(error)) onExpired(); else toast(t("reportGenerationError"), "error"); }
    finally { setDownloading(null); }
  }

  const shown = useMemo(() => (data?.products ?? []).filter(product => filter === "all" || (filter === "fix" ? product.status === "FIX" : product.status === "READY")), [data, filter]);
  const improved = (data?.products ?? []).filter(product => product.improved && product.previous_fixes !== null);
  const newButton = <LinkButton href="#/check" icon={<Plus size={16} aria-hidden="true" />}>{t("checkALabel")}</LinkButton>;

  let body;
  if (failure) {
    body = <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("productsLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void load()}>{t("tryAgain")}</Button>} />;
  } else if (data === null) {
    body = <ProductsSkeleton label={t("loading")} />;
  } else if (data.total === 0) {
    body = <Card><EmptyState icon={<Boxes size={16} />} title={t("noProductsTitle")} description={t("noProductsBody")} action={newButton} /></Card>;
  } else {
    body = <>
      <section className="hero" aria-labelledby="readiness-headline">
        <ReadinessRing percent={data.readiness_percent} label={fill(t("readinessAria"), { percent: data.readiness_percent })} />
        <div className="hero__text">
          <p className="hero__eyebrow">{t("labelReadiness")}</p>
          <h2 className="hero__headline" id="readiness-headline">{fill(t("readyHeadline"), { ready: data.ready, total: data.total })}</h2>
          <p className="hero__sub">{data.open_fixes > 0 ? fill(t("readySub"), { fixes: data.open_fixes, labels: data.labels_to_fix }) : data.check > 0 ? t("readySubVerify") : t("readySubAll")}</p>
          <div className="hero__actions">{newButton}</div>
        </div>
      </section>

      <Card className="counters" headingLevel={2} title={t("readinessBreakdown")}>
        <ul className="counter-list">
          <li className="counter counter--pass"><CircleCheck size={18} aria-hidden="true" /><span className="counter__value tabular"><CountUp value={data.ready} /></span><span className="counter__label">{t("readyToPrint")}</span></li>
          <li className="counter counter--violation"><CircleAlert size={18} aria-hidden="true" /><span className="counter__value tabular"><CountUp value={data.fix} /></span><span className="counter__label">{t("fixNeeded")}</span></li>
          <li className="counter counter--review"><TriangleAlert size={18} aria-hidden="true" /><span className="counter__value tabular"><CountUp value={data.check} /></span><span className="counter__label">{t("checkOnPack")}</span></li>
        </ul>
        {improved.length > 0 && (
          <ul className="improvement-list" aria-live="polite">
            {improved.slice(0, 2).map(product => (
              <li key={product.product_key}><TrendingUp size={16} aria-hidden="true" />{fill(t("improvedLine"), { product: product.product_name, from: fixesText(t, product.previous_fixes ?? 0), to: product.fixes })}</li>
            ))}
          </ul>
        )}
      </Card>

      <div className="product-toolbar">
        <Tabs<Filter> label={t("productFilter")} value={filter} onChange={setFilter} items={[
          { value: "all", label: t("all"), count: data.total }, { value: "fix", label: t("fixNeeded"), count: data.fix }, { value: "ready", label: t("readyShort"), count: data.ready },
        ]} />
      </div>
      {shown.length === 0
        ? <p className="muted-text product-empty" role="status">{t("noProductsInView")}</p>
        : <ul className="product-grid" aria-label={t("myProducts")}>
          {shown.map(product => <li key={product.product_key}><ProductCardView product={product} busy={downloading === product.inspection_id} onReport={() => void report(product)} /></li>)}
        </ul>}
    </>;
  }

  return (
    <div className="page maker-page">
      <PageHeader title={t("myProducts")} subtitle={data && data.total > 0 ? fill(t("productsSubtitle"), { count: data.total }) : t("productsSubtitleEmpty")} />
      {body}
    </div>
  );
}

export function fixesText(t: ReturnType<typeof useI18n>["t"], count: number) {
  return fill(t(count === 1 ? "fixCountOne" : "fixCountOther"), { count });
}

function ProductCardView({ product, busy, onReport }: { product: ProductCard; busy: boolean; onReport: () => void }) {
  const { t } = useI18n();
  const tone = READINESS_TONE[product.status];
  const label = STATUS_LABEL(t, product.status, product.status === "FIX" ? product.fixes : product.review);
  const checks = fill(t(product.checks === 1 ? "checksCountOne" : "checksCountOther"), { count: product.checks });
  const target = `#/products/${encodeURIComponent(product.inspection_id)}`;
  return (
    <article className={`product-card product-card--${tone}`}>
      <div className="product-card__top">
        <ProductThumb inspectionId={product.inspection_id} enabled={product.has_photo} />
        <div className="product-card__heading">
          <h3 className="product-card__name"><a href={target}>{product.product_name}</a></h3>
          <p className="product-card__qty mono">{product.net_quantity ?? t("quantityNotRead")}</p>
        </div>
      </div>
      <p className="product-card__meta">{fill(t("checkedOn"), { date: formatDate(product.checked_at), checks })}</p>
      <div className="product-card__foot">
        <span className={`status-pill status-pill--${tone}`}>{label}</span>
        {product.status === "FIX" && <LinkButton href={target} size="sm" icon={<ArrowRight size={14} aria-hidden="true" />} aria-label={`${t("viewFixes")}: ${product.product_name}`}>{t("viewFixes")}</LinkButton>}
        {product.status === "READY" && <Button size="sm" variant="outline" icon={<FileDown size={14} aria-hidden="true" />} loading={busy} onClick={onReport} aria-label={`${t("viewReport")}: ${product.product_name}`}>{t("viewReport")}</Button>}
        {product.status === "CHECK" && <LinkButton href={target} size="sm" variant="outline" icon={<ScanSearch size={14} aria-hidden="true" />} aria-label={`${t("reviewAction")}: ${product.product_name}`}>{t("reviewAction")}</LinkButton>}
      </div>
    </article>
  );
}

export function STATUS_LABEL(t: ReturnType<typeof useI18n>["t"], status: Readiness, count: number): string {
  if (status === "READY") return t("readyToPrint");
  return fill(t(status === "FIX" ? "fixNeededCount" : "checkOnPackCount"), { count });
}

function ProductsSkeleton({ label }: { label: string }) {
  return (
    <div className="maker-skeleton" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <Skeleton width="100%" height={168} />
      <Skeleton width="100%" height={96} />
      <div className="product-grid">{[0, 1, 2].map(index => <Skeleton key={index} width="100%" height={172} />)}</div>
    </div>
  );
}
