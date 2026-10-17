import { ChangeEvent, DragEvent, useEffect, useState } from "react";
import { ImageUp } from "lucide-react";
import { Button, Dialog } from "../../components/ui";
import { PANEL_LABEL } from "../../components/ProgressPanel";
import { fill, useI18n } from "../../i18n";
import { ALL_PANELS, activePhotos, hasPanel } from "../../lib/panels";
import { MAX_PHOTOS } from "../../lib/photoActions";
import type { Panel, PhotoInfo } from "../../types";

const ACCEPTED = ["image/png", "image/jpeg"];

type Props = {
  open: boolean;
  /** "retake" fixes the panel; "add" lets the inspector choose. */
  mode: "add" | "retake";
  initialPanel: Panel;
  photos: PhotoInfo[];
  limit: number;
  onClose: () => void;
  onSubmit: (panel: Panel, file: File) => void;
};

/** Desktop dialog: pick the panel and the photo. Only the new photo is read; the record is re-checked on all of them. */
export function AddPhotoDialog({ open, mode, initialPanel, photos, limit, onClose, onSubmit }: Props) {
  const { t } = useI18n();
  const [panel, setPanel] = useState<Panel>(initialPanel);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const full = activePhotos(photos).length >= MAX_PHOTOS;

  useEffect(() => { if (open) { setPanel(initialPanel); setFile(null); setProblem(null); } }, [open, initialPanel]);
  useEffect(() => { if (!file) { setPreview(null); return; } const url = URL.createObjectURL(file); setPreview(url); return () => URL.revokeObjectURL(url); }, [file]);

  function choose(candidate: File | undefined | null) {
    if (!candidate) return;
    if (!ACCEPTED.includes(candidate.type)) { setProblem(t("newBadType")); return; }
    if (candidate.size > limit) { setProblem(fill(t("newTooLarge"), { limit: `${Math.round((limit / (1024 * 1024)) * 10) / 10} MB` })); return; }
    setProblem(null); setFile(candidate);
  }
  const replaces = hasPanel(photos, panel) && panel !== "OTHER";
  const blocked = full && !replaces;

  return (
    <Dialog open={open} onClose={onClose} title={mode === "retake" ? fill(t("recRetakeTitle"), { panel: t(PANEL_LABEL[initialPanel]) }) : t("recAddPhotoTitle")}
      description={t("recAddPhotoBody")}
      footer={<><Button variant="ghost" onClick={onClose}>{t("recCancel")}</Button><Button icon={<ImageUp size={16} aria-hidden="true" />} disabled={!file || blocked} onClick={() => file && onSubmit(panel, file)}>{t("recAddPhotoStart")}</Button></>}>
      <div className="add-photo">
        {mode === "add" && (
          <fieldset className="add-photo__panels">
            <legend className="form-field__label">{t("recAddPhotoPanel")}</legend>
            {ALL_PANELS.map(option => (
              <label key={option} className={`panel-choice${panel === option ? " is-active" : ""}`}>
                <input className="sr-only" type="radio" name="panel" value={option} checked={panel === option} onChange={() => setPanel(option)} />
                <span>{t(PANEL_LABEL[option])}</span>
              </label>
            ))}
          </fieldset>
        )}
        {replaces && <p className="muted-text" role="status">{t("recAddPhotoReplaces")}</p>}
        {blocked && <p className="form-error" role="alert">{t("recPhotoMax")}</p>}
        <label className={`dropzone dropzone--slot${dragging ? " is-dragging" : ""}${preview ? " has-preview" : ""}`}
          onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
          onDrop={(event: DragEvent) => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files?.[0]); }}>
          <input className="sr-only" type="file" accept="image/png,image/jpeg" aria-label={t("recAddPhotoChoose")} onChange={(event: ChangeEvent<HTMLInputElement>) => { choose(event.target.files?.[0]); event.target.value = ""; }} />
          {preview
            ? <img className="dropzone__preview" src={preview} alt={t("imagePreview")} />
            : <><span className="dropzone__icon" aria-hidden="true"><ImageUp size={20} /></span><span className="dropzone__label">{t("newDropTitle")}</span><span className="dropzone__hint">{t("newBrowse")}</span></>}
        </label>
        {problem && <p className="form-error" role="alert">{problem}</p>}
      </div>
    </Dialog>
  );
}
