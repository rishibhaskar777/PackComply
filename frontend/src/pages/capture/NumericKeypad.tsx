import { Delete } from "lucide-react";
import { useI18n } from "../../i18n";

type Props = {
  /** Extra keys beside the digits, for example "." for amounts or "/" for dates. */
  extra: string[];
  onKey: (key: string) => void;
  onBackspace: () => void;
};

/** A large on-screen number pad (44px+ keys) so a value can be entered one-handed without the phone keyboard. */
export function NumericKeypad({ extra, onKey, onBackspace }: Props) {
  const { t } = useI18n();
  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", extra[0] ?? "", "0", extra[1] ?? ""];
  return (
    <div className="keypad" role="group" aria-label={t("resKeypad")}>
      {keys.map((key, index) => key
        ? <button key={`${key}-${index}`} type="button" className="keypad__key" onClick={() => onKey(key)}>{key}</button>
        : <span key={`gap-${index}`} className="keypad__gap" aria-hidden="true" />)}
      <button type="button" className="keypad__key keypad__key--wide" aria-label={t("resKeypadDelete")} onClick={onBackspace}><Delete size={20} aria-hidden="true" /></button>
    </div>
  );
}
