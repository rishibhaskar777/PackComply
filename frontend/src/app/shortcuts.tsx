import { useEffect, useRef } from "react";
import { Dialog } from "../components/ui";
import { useI18n } from "../i18n";
import { navigate } from "./router";

type Options = { maker: boolean; onNew: () => void; onHelp: () => void };

/** True while the keystroke belongs to something else: a field being typed in, or an open dialog. */
function typingOrBusy(event: KeyboardEvent): boolean {
  const target = event.target as HTMLElement | null;
  const tag = target?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return true;
  return Boolean(document.querySelector("[role=dialog], dialog[open]"));
}

/**
 * Single-key shortcuts: "?" opens the list, "N" starts a new inspection/check, "G" then "O" (inspector) or "P"
 * (manufacturer) goes to the home page. Ctrl K (search) is handled by the shell. Never active while typing.
 */
export function useShortcuts({ maker, onNew, onHelp }: Options) {
  const latest = useRef({ maker, onNew, onHelp });
  latest.current = { maker, onNew, onHelp };
  useEffect(() => {
    let goUntil = 0;
    function onKeyDown(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented || typingOrBusy(event)) return;
      const key = event.key.toLowerCase();
      const go = Date.now() < goUntil;
      goUntil = 0;
      if (event.key === "?") { event.preventDefault(); latest.current.onHelp(); }
      else if (key === "n") { event.preventDefault(); latest.current.onNew(); }
      else if (key === "g") goUntil = Date.now() + 1500;
      else if (go && key === (latest.current.maker ? "p" : "o")) { event.preventDefault(); navigate(latest.current.maker ? "/products" : "/overview"); }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}

function Keys({ keys }: { keys: string[] }) {
  const { t } = useI18n();
  return <span className="shortcut__keys">{keys.map((key, index) => <span key={key}>{index > 0 && <span className="shortcut__then"> {t("shortcutThen")} </span>}<kbd>{key}</kbd></span>)}</span>;
}

/** The "?" help: what each shortcut does for the current portal. */
export function ShortcutsDialog({ open, onClose, maker }: { open: boolean; onClose: () => void; maker: boolean }) {
  const { t } = useI18n();
  const rows: { keys: string[]; label: string }[] = [
    ...(maker ? [] : [{ keys: ["Ctrl K"], label: t("searchInspections") }]),
    { keys: ["N"], label: t(maker ? "checkALabel" : "newInspection") },
    maker ? { keys: ["G", "P"], label: t("myProducts") } : { keys: ["G", "O"], label: t("overview") },
    { keys: ["?"], label: t("shortcutsTitle") },
  ];
  return (
    <Dialog open={open} onClose={onClose} title={t("shortcutsTitle")} description={t("shortcutsIntro")} className="shortcuts-dialog">
      <ul className="shortcut-list">
        {rows.map(row => <li key={row.label} className="shortcut"><Keys keys={row.keys} /><span>{row.label}</span></li>)}
      </ul>
    </Dialog>
  );
}
