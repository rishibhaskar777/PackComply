import { Compass } from "lucide-react";
import { EmptyState, LinkButton } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { fill, useI18n } from "../i18n";

/** Any unknown #/ address: a friendly dead end with a way back to the role's own home page. */
export function NotFoundPage({ official }: { official: boolean }) {
  const { t } = useI18n();
  return (
    <div className="page">
      <PageHeader title={t("notFoundTitle")} />
      <EmptyState icon={<Compass size={16} />} title={t("notFoundTitle")} description={t("notFoundBody")}
        action={<LinkButton href={official ? "#/overview" : "#/products"}>{fill(t("notFoundHome"), { page: t(official ? "overview" : "myProducts") })}</LinkButton>} />
    </div>
  );
}
