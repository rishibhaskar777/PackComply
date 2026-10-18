import { Skeleton } from "./ui";
import { useI18n } from "../i18n";

/** Shown while a route's code loads. It reserves roughly the page's height so content does not jump when it arrives. */
export function PageSkeleton() {
  const { t } = useI18n();
  return (
    <div className="page-skeleton" role="status" aria-live="polite">
      <span className="sr-only">{t("loading")}</span>
      <Skeleton width="34%" height={28} />
      <Skeleton width="52%" height={14} />
      <div className="page-skeleton__cards">
        {[0, 1, 2, 3].map(index => <Skeleton key={index} height={96} className="page-skeleton__card" />)}
      </div>
      <Skeleton height={280} className="page-skeleton__block" />
    </div>
  );
}
