import { Button } from "./ui";
import { useI18n } from "../i18n";

export function Pagination({ page, totalPages, onPage }: { page: number; totalPages: number; onPage: (page: number) => void }) {
  const { t } = useI18n();
  if (totalPages <= 1) return null;
  return (
    <nav className="pagination" aria-label={t("pagination")}>
      <span className="pagination__label tabular">{t("page")} {page} {t("of")} {totalPages}</span>
      <div className="pagination__buttons">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>{t("previous")}</Button>
        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>{t("next")}</Button>
      </div>
    </nav>
  );
}
