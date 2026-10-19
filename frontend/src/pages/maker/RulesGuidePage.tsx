import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { Alert, Button, Card, SkeletonRows } from "../../components/ui";
import { fill, useI18n } from "../../i18n";
import { ruleChip } from "../../lib/readiness";
import type { Ruleset } from "../../types";

/** The rules every label is checked against, in plain language, each with what must be printed and a compliant example. */
export function RulesGuidePage({ onExpired }: { onExpired: () => void }) {
  const { t } = useI18n();
  const [ruleset, setRuleset] = useState<Ruleset | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);

  async function load() {
    setFailure(null);
    try { setRuleset(await getJson<Ruleset>("/compliance/rules", "rules")); }
    catch (error) { if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error"); }
  }
  useEffect(() => { void load(); }, []);

  return (
    <div className="page maker-page">
      <PageHeader title={t("rulesGuide")} subtitle={ruleset ? fill(t("rulesGuideSubtitle"), { count: ruleset.rules.length }) : t("rulesGuideIntro")} />
      {failure
        ? <Alert title={failure === "unavailable" ? t("serverUnavailable") : t("rulesetsLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void load()}>{t("tryAgain")}</Button>} />
        : ruleset === null ? <Card><SkeletonRows label={t("loading")} rows={6} /></Card>
        : <ol className="guide-list">
          {ruleset.rules.map((rule, index) => {
            const chip = rule.rule_chip ?? ruleChip(rule.legal_reference);
            return (
              <li key={rule.code}>
                <article className="guide-card">
                  <header className="guide-card__head">
                    <span className="guide-card__number tabular" aria-hidden="true">{index + 1}</span>
                    <div className="guide-card__heading">
                      <h2 className="guide-card__title">{rule.title}</h2>
                      <div className="fix-card__chips">
                        {chip && <span className="rule-chip mono">{chip}</span>}
                        <span className="guide-card__reference mono">{rule.legal_reference}</span>
                      </div>
                    </div>
                  </header>
                  <dl className="guide-card__body">
                    {rule.expected && <div><dt>{t("mustPrint")}</dt><dd>{rule.expected}</dd></div>}
                    <div><dt>{t("inPlainWords")}</dt><dd>{rule.description}</dd></div>
                  </dl>
                  {rule.example && <div className="example-box"><p className="example-box__label">{t("exampleLabel")}</p><p className="example-box__text mono">{rule.example}</p></div>}
                </article>
              </li>
            );
          })}
        </ol>}
    </div>
  );
}
