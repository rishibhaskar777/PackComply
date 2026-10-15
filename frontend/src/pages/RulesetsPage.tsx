import { useEffect, useState } from "react";
import { BookOpen, RefreshCw } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Alert, Badge, Button, Card, Column, EmptyState, SkeletonRows, Table } from "../components/ui";
import { fill, useI18n } from "../i18n";
import { ComplianceRule, Ruleset } from "../types";

/** Read-only view of the active ruleset (GET /api/compliance/rules). */
export function RulesetsPage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const [ruleset, setRuleset] = useState<Ruleset | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);

  async function load() {
    setFailure(null);
    try { setRuleset(await getJson<Ruleset>("/compliance/rules", "rules")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
  }
  useEffect(() => { load(); }, []);

  const columns: Column<ComplianceRule>[] = [
    { key: "code", header: t("ruleCode"), hideOnMobile: true, render: rule => <span className="mono">{rule.code}</span> },
    { key: "rule", header: t("rule"), className: "cell-wrap cell-grow", render: rule => <><span className="cell-primary">{rule.title}</span><span className="cell-secondary cell-clamp">{rule.description}</span></> },
    { key: "reference", header: t("legalReference"), className: "cell-wrap", render: rule => <span className="mono reference-text">{rule.legal_reference ?? "—"}</span> },
    { key: "severity", header: t("severity"), hideOnMobile: true, render: rule => <Badge tone="neutral" dot={false}>{rule.severity}</Badge> },
    { key: "applies", header: t("appliesWhen"), hideOnMobile: true, render: rule => <span className="mono muted-text">{rule.applies_when ?? "always"}</span> },
  ];

  return (
    <div className="page">
      <PageHeader title={t("rulesets")} subtitle={ruleset ? `${ruleset.title} · ${fill(t("rulesetVersion"), { version: ruleset.version })}` : t("rulesetsDescription")} />
      {failure ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("rulesetsLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={load}>{t("tryAgain")}</Button>} />
        : <>
          {ruleset?.disclaimer && <Alert variant="info">{ruleset.disclaimer}</Alert>}
          <Card title={ruleset?.label ?? t("rulesets")} description={ruleset ? fill(t("rulesCount"), { count: ruleset.rules.length }) : undefined} flush>
            {ruleset === null ? <SkeletonRows label={t("loading")} />
              : !ruleset.rules.length ? <EmptyState icon={<BookOpen size={16} />} title={t("noRules")} />
              : <Table caption={ruleset.label} columns={columns} rows={ruleset.rules} rowKey={rule => rule.code} />}
          </Card>
        </>}
    </div>
  );
}
