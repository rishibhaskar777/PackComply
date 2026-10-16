import { BrandMark } from "../../app/BrandMark";
import { fill, useI18n } from "../../i18n";
import { LabelCard } from "./LabelCard";

/** Desktop left panel: graph paper with rulers, the brand, the scanned label and three facts. */
export function StoryPanel({ ruleCount, reduced }: { ruleCount: number; reduced: boolean }) {
  const { t } = useI18n();
  const facts = [
    { value: fill(t("factRulesValue"), { count: ruleCount }), caption: t("factRulesCaption") },
    { value: t("factLanguagesValue"), caption: t("factLanguagesCaption") },
    { value: t("factOfflineValue"), caption: t("factOfflineCaption") },
  ];
  return (
    <section className="signin-story" aria-labelledby="signin-story-title">
      <Ruler orientation="horizontal" />
      <Ruler orientation="vertical" />
      <div className="signin-story__content">
        <div className="signin-story__brand">
          <BrandMark size={44} />
          <div>
            <p className="signin-story__name">{t("appName")}</p>
            <p className="signin-story__tagline">{t("brandTagline")}</p>
          </div>
        </div>
        <div className="signin-story__stage"><LabelCard reduced={reduced} /></div>
        <div className="signin-story__footer">
          <h2 id="signin-story-title" className="signin-story__headline">{t("storyHeadline")}</h2>
          <ul className="fact-tiles">
            {facts.map(fact => (
              <li className="fact-tile" key={fact.caption}>
                <span className="fact-tile__value">{fact.value}</span>
                <span className="fact-tile__caption">{fact.caption}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

/** A drafting ruler: minor ticks every 8px, major every 40px numbered 0, 1, 2… (drawn with CSS gradients). */
function Ruler({ orientation }: { orientation: "horizontal" | "vertical" }) {
  return (
    <div className={`ruler ruler--${orientation}`} aria-hidden="true">
      <span className="ruler__numbers">
        {Array.from({ length: 40 }, (_, index) => <span key={index} className="ruler__number mono">{index}</span>)}
      </span>
    </div>
  );
}
