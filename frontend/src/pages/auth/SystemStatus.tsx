import { StatusState } from "../../app/useSystemStatus";
import { Skeleton } from "../../components/ui";
import { TranslationKey, useI18n } from "../../i18n";

type Tone = "pass" | "review" | "violation" | "neutral";

function pillState(state: StatusState): { tone: Tone; label: TranslationKey } {
  if (state.phase === "loading") return { tone: "neutral", label: "statusChecking" };
  if (state.phase === "offline") return { tone: "violation", label: "statusBackendOffline" };
  if (!state.data.database) return { tone: "review", label: "statusDatabaseUnavailable" };
  if (state.data.ocr === "unavailable") return { tone: "review", label: "statusOcrUnavailable" };
  return { tone: "pass", label: "statusReady" };
}

/** "All systems ready" / "Database unavailable" / "Backend offline", announced politely when it changes. */
export function StatusPill({ state }: { state: StatusState }) {
  const { t } = useI18n();
  const { tone, label } = pillState(state);
  return (
    <p className={`status-pill status-pill--${tone}`} role="status" aria-live="polite" data-state={label}>
      <span className="status-pill__dot" aria-hidden="true" />
      <span className="sr-only">{t("systemStatus")}: </span>{t(label)}
    </p>
  );
}

const OCR_LABELS: Record<string, TranslationKey> = { ready: "ocrReady", warming_up: "ocrWarmingUp", unavailable: "ocrUnavailable" };

/** Three cells: OCR engine, ruleset and languages, from the status endpoint (skeletons while loading). */
export function StatusStrip({ state }: { state: StatusState }) {
  const { t } = useI18n();
  const data = state.phase === "ready" ? state.data : null;
  const cells: { label: TranslationKey; value: string | null; tone?: Tone }[] = [
    { label: "ocrEngine", value: data ? t(OCR_LABELS[data.ocr]) : state.phase === "offline" ? t("ocrUnavailable") : null, tone: data ? (data.ocr === "ready" ? "pass" : data.ocr === "warming_up" ? "review" : "violation") : state.phase === "offline" ? "violation" : undefined },
    { label: "rulesetLabel", value: data ? `LM(PC) v${data.ruleset_version}` : state.phase === "offline" ? "—" : null },
    { label: "languagesLabel", value: data ? data.languages.map(code => code.toUpperCase()).join(" + ") || "—" : state.phase === "offline" ? "—" : null },
  ];
  return (
    <dl className="status-strip" aria-busy={state.phase === "loading" || undefined}>
      {cells.map(cell => (
        <div className="status-strip__cell" key={cell.label}>
          <dt className="status-strip__label">{t(cell.label)}</dt>
          <dd className={`status-strip__value${cell.tone ? ` status-strip__value--${cell.tone}` : ""}`}>
            {cell.value === null ? <Skeleton width={56} height={12} /> : cell.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
