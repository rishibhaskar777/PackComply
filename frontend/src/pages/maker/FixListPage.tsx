import { useEffect, useState } from "react";
import { CircleCheck, RefreshCw } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Alert, Button, Card, EmptyState, LinkButton, SkeletonRows } from "../../components/ui";
import { fill, useI18n } from "../../i18n";
import { ruleChip } from "../../lib/readiness";
import type { FixesResponse } from "../../types";

/** Every open fix across the latest self-check of each product, grouped by rule so one change fixes many labels. */
export function FixListPage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<FixesResponse | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);

  async function load() {
    setFailure(null);
    try { setData(await getJson<FixesResponse>("/fixes", "fixes")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
  }
  useEffect(() => { void load(); }, []);

  return (
    <div className="page maker-page">
      <PageHeader title={t("fixList")} subtitle={data ? fill(t(data.total_items === 1 ? "fixListSubtitleOne" : "fixListSubtitleOther"), { count: data.total_items }) : t("fixListIntro")} />
      {failure
        ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("fixesLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void load()}>{t("tryAgain")}</Button>} />
        : data === null ? <Card><SkeletonRows label={t("loading")} rows={4} /></Card>
        : data.groups.length === 0 ? <Card><EmptyState icon={<CircleCheck size={16} />} title={t("noFixesTitle")} description={t("noFixesBody")} action={<LinkButton href="#/check" variant="outline">{t("checkALabel")}</LinkButton>} /></Card>
        : <ul className="fix-groups">
          {data.groups.map(group => {
            const chip = group.rule_chip ?? ruleChip(group.legal_reference);
            const review = group.status === "REVIEW";
            return (
              <li key={`${group.rule_id}-${group.status}`}>
                <article className={`fix-group fix-group--${review ? "review" : "violation"}`}>
                  <header className="fix-group__head">
                    <h2 className="fix-group__title">{group.title} · <span className="fix-group__count">{fill(t(group.product_count === 1 ? "productCountOne" : "productCountOther"), { count: group.product_count })}</span></h2>
                    <div className="fix-card__chips">
                      {chip && <span className="rule-chip mono">{chip}</span>}
                      {review && <span className="status-pill status-pill--review">{t("checkOnPack")}</span>}
                    </div>
                  </header>
                  {group.fix_steps.length > 0 && <div className="fix-card__how"><p className="fix-card__label">{t("howToFix")}</p><ul>{group.fix_steps.map(step => <li key={step}>{step}</li>)}</ul></div>}
                  {group.example && <div className="example-box"><p className="example-box__label">{t("exampleLabel")}</p><p className="example-box__text mono">{group.example}</p></div>}
                  <ul className="fix-group__products" aria-label={t("affectedProducts")}>
                    {group.products.map(product => (
                      <li key={product.inspection_id}><a className="product-link" href={`#/products/${encodeURIComponent(product.inspection_id)}`}>{product.product_name}<span aria-hidden="true"> →</span></a></li>
                    ))}
                  </ul>
                </article>
              </li>
            );
          })}
        </ul>}
    </div>
  );
}
