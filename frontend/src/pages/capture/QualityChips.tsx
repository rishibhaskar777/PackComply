import { useI18n } from "../../i18n";
import type { QualityReport } from "../../lib/imageQuality";

/** Sharpness, light and glare as chips with a green (ok) or amber (needs attention) dot. */
export function QualityChips({ report }: { report: QualityReport | null }) {
  const { t } = useI18n();
  if (!report) return null;
  const chips = [
    { key: "sharp", ok: report.sharp, text: t(report.sharp ? "capSharp" : "capBlurry") },
    { key: "light", ok: report.lightOk, text: t(report.lightOk ? "capLightGood" : "capLightDark") },
    { key: "glare", ok: !report.glare, text: t(report.glare ? "capGlare" : "capNoGlare") },
  ];
  return (
    <ul className="quality-chips" aria-label={t("capQuality")}>
      {chips.map(chip => (
        <li key={chip.key} className={`quality-chip quality-chip--${chip.ok ? "ok" : "warn"}`} data-check={chip.key} data-ok={chip.ok}>
          <span className="quality-chip__dot" aria-hidden="true" />{chip.text}
        </li>
      ))}
    </ul>
  );
}
