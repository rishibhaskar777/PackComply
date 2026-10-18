import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Camera, FileSearch, Gauge, LayoutTemplate, RefreshCw, Scale, ShieldCheck, UserCheck, WifiOff } from "lucide-react";
import { getJson, isExpired, isUnavailable } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Alert, Badge, Button, Card, Column, Skeleton, Table } from "../components/ui";
import { fill, TranslationKey, useI18n } from "../i18n";
import { ComplianceRule, EvalGroup, EvalReport, EvalRun, Ruleset } from "../types";
import { FIELD_LABELS } from "./record/Findings";

const STEPS: { icon: typeof Camera; title: TranslationKey; caption: TranslationKey }[] = [
  { icon: Camera, title: "aboutStepPhoto", caption: "aboutStepPhotoCaption" },
  { icon: FileSearch, title: "aboutStepOcr", caption: "aboutStepOcrCaption" },
  { icon: LayoutTemplate, title: "aboutStepExtract", caption: "aboutStepExtractCaption" },
  { icon: Scale, title: "aboutStepRules", caption: "aboutStepRulesCaption" },
  { icon: Gauge, title: "aboutStepRisk", caption: "aboutStepRiskCaption" },
  { icon: UserCheck, title: "aboutStepReview", caption: "aboutStepReviewCaption" },
];
const VERDICTS = ["COMPLIANT", "NEEDS_REVIEW", "NON_COMPLIANT"] as const;
const VERDICT_LABEL: Record<string, TranslationKey> = { COMPLIANT: "verdictCompliant", NON_COMPLIANT: "verdictNonCompliant", NEEDS_REVIEW: "verdictNeedsReview" };
const GROUP_LABEL: Record<string, TranslationKey> = { DEV: "aboutGroupDev", "HOLD-OUT": "aboutGroupUnseen", "REAL PHOTOS": "aboutGroupPhone", ALL: "aboutGroupAll" };

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

function useLoaded<T>(path: string, operation: string, onExpired: () => void) {
  const [data, setData] = useState<T | null>(null);
  const [failure, setFailure] = useState<"unavailable" | "error" | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setFailure(null);
    getJson<T>(path, operation).then(value => { if (current) setData(value); }).catch(error => {
      if (!current) return;
      if (isExpired(error)) onExpired(); else setFailure(isUnavailable(error) ? "unavailable" : "error");
    });
    return () => { current = false; };
  }, [path, attempt]);
  return { data, failure, retry: () => setAttempt(value => value + 1) };
}

/** What PackComply checks, how it works, and the accuracy the evaluation script measured: nothing here is typed in by hand. */
export function AboutPage({ onExpired }: { onExpired: () => void }) {
  const { t, locale } = useI18n();
  const rules = useLoaded<Ruleset>("/compliance/rules", "rules", onExpired);
  const report = useLoaded<EvalReport>("/system/evaluation", "evaluation", onExpired);
  const [selected, setSelected] = useState(0);
  const run = report.data?.runs[Math.min(selected, Math.max((report.data?.runs.length ?? 1) - 1, 0))] ?? null;

  const date = (iso: string) => { try { return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(iso)); } catch { return iso.slice(0, 10); } };
  const columns: Column<ComplianceRule>[] = [
    { key: "rule", header: t("rule"), className: "cell-wrap cell-grow", render: rule => <span className="cell-primary">{rule.title}</span> },
    { key: "reference", header: t("legalReference"), className: "cell-wrap", render: rule => <span className="mono reference-text">{rule.legal_reference ?? "—"}</span> },
  ];

  return (
    <div className="page about">
      <PageHeader title={t("aboutAccuracy")} subtitle={t("aboutSubtitle")} />

      <Card title={t("aboutChecksTitle")} description={rules.data ? fill(t("aboutChecksBody"), { count: rules.data.rules.length, ruleset: rules.data.label }) : undefined} flush>
        {rules.failure
          ? <Alert title={t("rulesetsLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={rules.retry}>{t("tryAgain")}</Button>} />
          : rules.data === null ? <div className="about__loading"><Skeleton width="60%" /><Skeleton width="80%" /><Skeleton width="50%" /></div>
          : <Table caption={rules.data.label} columns={columns} rows={rules.data.rules} rowKey={rule => rule.code} />}
      </Card>

      <Card title={t("aboutHowTitle")}>
        <ol className="about-flow" aria-label={t("aboutHowTitle")}>
          {STEPS.map(({ icon: Icon, title, caption }, index) => (
            <li key={title} className="about-flow__step">
              <span className="about-flow__node"><Icon size={18} aria-hidden="true" /></span>
              <span className="about-flow__text"><strong>{t(title)}</strong><span>{t(caption)}</span></span>
              {index < STEPS.length - 1 && <ArrowRight size={16} className="about-flow__arrow" aria-hidden="true" />}
            </li>
          ))}
        </ol>
      </Card>

      <Card title={t("aboutPrivacyTitle")} action={<WifiOff size={16} aria-hidden="true" />}>
        <p className="about__text">{t("aboutPrivacyBody")}</p>
        <p className="about__text about__text--muted"><ShieldCheck size={14} aria-hidden="true" /> {t("aboutDisclaimer")}</p>
      </Card>

      <Card title={t("aboutEvalTitle")} description={t("aboutEvalIntro")}>
        {report.failure
          ? <Alert title={t("aboutEvalLoadError")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={report.retry}>{t("tryAgain")}</Button>} />
          : report.data === null ? <div className="about__loading"><Skeleton width="40%" height={24} /><Skeleton height={160} /></div>
          : !run ? <p className="about__text">{t("aboutEvalNone")}</p>
          : <EvaluationRun key={run.label} run={run} runs={report.data.runs} selected={selected} onSelect={setSelected} date={date} />}
      </Card>
    </div>
  );
}

function EvaluationRun({ run, runs, selected, onSelect, date }: { run: EvalRun; runs: EvalRun[]; selected: number; onSelect: (index: number) => void; date: (iso: string) => string }) {
  const { t } = useI18n();
  const groups = useMemo(() => run.groups, [run]);
  const [groupName, setGroupName] = useState(() => groups.find(group => group.name === "ALL")?.name ?? groups[groups.length - 1]?.name ?? "");
  const group = groups.find(item => item.name === groupName) ?? groups[0];
  const groupLabel = (name: string) => (GROUP_LABEL[name] ? t(GROUP_LABEL[name]) : name.replace(/_/g, " ").toLowerCase());
  const sizes = run.dataset_sizes;

  return (
    <div className="about-eval">
      <div className="about-eval__meta">
        <Badge tone={run.kind === "unseen" ? "pass" : "review"} dot={false}>{run.kind === "unseen" ? t("aboutKindUnseen") : t("aboutKindDevelopment")}</Badge>
        <span>{fill(t("aboutEvalRun"), { label: run.label })}</span>
        <span>{fill(t("aboutEvalDate"), { date: date(run.generated_at) })}</span>
        <span className="mono">{run.ruleset.id} v{run.ruleset.version}</span>
        {runs.length > 1 && (
          <label className="about-eval__runs">{t("aboutEvalRuns")}
            <select value={selected} onChange={event => onSelect(Number(event.target.value))}>
              {runs.map((item, index) => <option key={item.label} value={index}>{item.label} · {date(item.generated_at)}</option>)}
            </select>
          </label>
        )}
      </div>
      <p className="about__text about__text--muted">{run.kind === "unseen" ? t("aboutKindUnseenNote") : t("aboutKindDevelopmentNote")}{run.note ? ` ${run.note}` : ""}</p>

      <dl className="about-sizes" aria-label={t("aboutSizes")}>
        <div><dt>{t("aboutSizeDev")}</dt><dd className="tabular">{sizes.dev}</dd></div>
        <div><dt>{t("aboutSizePhone")}</dt><dd className="tabular">{sizes.phone_holdout_retired_to_dev}</dd></div>
        <div><dt>{t("aboutSizeUnseen")}</dt><dd className="tabular">{sizes.unseen}</dd></div>
        <div><dt>{t("aboutMedianTime")}</dt><dd className="tabular">{run.processing_seconds ? `${run.processing_seconds.median.toFixed(1)} s` : "—"}</dd></div>
      </dl>

      {group && <>
        <div className="about-eval__group" role="group" aria-label={t("aboutGroup")}>
          {groups.map(item => (
            <button type="button" key={item.name} className={item.name === group.name ? "about-chip is-active" : "about-chip"} aria-pressed={item.name === group.name} onClick={() => setGroupName(item.name)}>
              {groupLabel(item.name)} <span className="tabular">({item.photos})</span>
            </button>
          ))}
        </div>
        <GroupResults group={group} />
      </>}
    </div>
  );
}

function GroupResults({ group }: { group: EvalGroup }) {
  const { t } = useI18n();
  const fields = Object.entries(group.per_field).filter(([, value]) => value.tp + value.fp + value.fn > 0);
  const agreement = group.rule_agreement.total ? group.rule_agreement.agree / group.rule_agreement.total : 0;
  const stats: [TranslationKey, string][] = [
    ["aboutPrecision", percent(group.precision)], ["aboutRecall", percent(group.recall)], ["aboutRuleAgreement", percent(agreement)],
    ["aboutVerdictsCorrect", `${group.verdicts_correct}/${group.photos}`], ["aboutFalseNc", String(group.false_non_compliant)], ["aboutFalsePass", String(group.false_pass_mandatory)],
  ];
  return (
    <div className="about-results">
      <dl className="about-stats">
        {stats.map(([label, value]) => <div key={label} className="about-stats__item"><dt>{t(label)}</dt><dd className="tabular">{value}</dd></div>)}
      </dl>

      <div className="about-tables">
        <div>
          <h3 className="about__subtitle">{t("aboutPerField")}</h3>
          <div className="about-table-scroll">
            <table className="about-table">
              <caption className="sr-only">{t("aboutPerField")}</caption>
              <thead><tr><th scope="col">{t("aboutField")}</th><th scope="col" className="num">{t("aboutPrecision")}</th><th scope="col" className="num">{t("aboutRecall")}</th><th scope="col" className="num">TP / FP / FN</th></tr></thead>
              <tbody>
                {fields.map(([name, value]) => (
                  <tr key={name}>
                    <th scope="row">{FIELD_LABELS[name] ? t(FIELD_LABELS[name]) : name.replace(/_/g, " ")}</th>
                    <td className="num tabular">{percent(value.precision)}</td><td className="num tabular">{percent(value.recall)}</td>
                    <td className="num tabular mono">{value.tp} / {value.fp} / {value.fn}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3 className="about__subtitle">{t("aboutConfusion")}</h3>
          <div className="about-table-scroll">
            <table className="about-table about-confusion">
              <caption className="sr-only">{t("aboutConfusion")}</caption>
              <thead>
                <tr><th scope="col">{t("aboutTruth")} ↓ / {t("aboutPredicted")} →</th>{VERDICTS.map(name => <th scope="col" key={name} className="num">{t(VERDICT_LABEL[name])}</th>)}</tr>
              </thead>
              <tbody>
                {VERDICTS.map(truth => (
                  <tr key={truth}>
                    <th scope="row">{t(VERDICT_LABEL[truth])}</th>
                    {VERDICTS.map(got => {
                      const count = group.verdict_confusion[truth]?.[got] ?? 0;
                      return <td key={got} className={`num tabular${count && truth === got ? " is-diagonal" : count ? " is-miss" : ""}`}>{count}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="about__text about__text--muted">{t("aboutConfusionNote")}</p>
        </div>
      </div>
    </div>
  );
}
