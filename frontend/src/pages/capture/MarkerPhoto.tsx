import { useI18n } from "../../i18n";
import { fill } from "../../i18n";
import { FindingItem, GROUP_TONE } from "../../lib/evidence";
import type { PhotoInfo } from "../../types";

type Props = {
  src: string | null;
  image: Pick<PhotoInfo, "id" | "width" | "height">;
  items: FindingItem[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
};

/** The photo with numbered markers placed by percentage, so they stay put at any rendered size. */
export function MarkerPhoto({ src, image, items, selectedId, onSelect }: Props) {
  const { t } = useI18n();
  const width = image.width ?? 0, height = image.height ?? 0;
  const markers = width && height ? items.flatMap(item => {
    if (item.number === null || !item.boxes || item.imageId !== image.id) return [];
    const points = item.boxes.flat();
    const x = Math.min(...points.map(point => point[0])), y = Math.min(...points.map(point => point[1]));
    return [{ item, left: (x / width) * 100, top: (y / height) * 100 }];
  }) : [];
  return (
    <div className="res-photo__frame" style={width && height ? { aspectRatio: `${width} / ${height}` } : undefined}>
      {src ? <img className="res-photo__image" src={src} alt={t("resPhotoAlt")} draggable={false} /> : <div className="res-photo__placeholder">{t("noImage")}</div>}
      {markers.map(({ item, left, top }) => {
        const id = item.finding.rule_id;
        return (
          <button key={id} type="button" className={`evidence-marker evidence-marker--${GROUP_TONE[item.group]}${id === selectedId ? " is-selected" : ""}`}
            style={{ left: `${left}%`, top: `${top}%` }} aria-pressed={id === selectedId}
            aria-label={fill(t("recMarkerLabel"), { number: item.number ?? 0, title: item.finding.rule_name })} onClick={() => onSelect(id === selectedId ? null : id)}>
            <span className="tabular">{item.number}</span>
          </button>
        );
      })}
    </div>
  );
}
