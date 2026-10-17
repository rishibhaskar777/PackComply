import { ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useI18n } from "../../i18n";

type OverlayProps = { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; description?: ReactNode; className?: string };

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Shared behaviour: Escape closes, focus moves in on open and returns on close, Tab stays inside. */
function useOverlay(open: boolean, onClose: () => void) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>("[data-autofocus]") ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") { event.stopPropagation(); close.current(); return; }
      if (event.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const [head, tail] = [items[0], items[items.length - 1]];
      if (event.shiftKey && document.activeElement === head) { event.preventDefault(); tail.focus(); }
      else if (!event.shiftKey && document.activeElement === tail) { event.preventDefault(); head.focus(); }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previous?.focus?.(); };
  }, [open]);
  return panel;
}

function Overlay({ variant, open, title, description, onClose, children, footer, className = "" }: OverlayProps & { variant: "dialog" | "sheet" }) {
  const { t } = useI18n();
  const titleId = useId();
  const panel = useOverlay(open, onClose);
  if (!open) return null;
  return createPortal(
    <div className={`overlay overlay--${variant}`}>
      <div className="overlay__scrim" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className={`overlay__panel ${className}`.trim()} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="overlay__header">
          <div className="overlay__heading">
            <h2 id={titleId} className="overlay__title">{title}</h2>
            {description && <p className="overlay__description">{description}</p>}
          </div>
          <button type="button" className="overlay__close" aria-label={t("dismiss")} onClick={onClose}><X size={16} aria-hidden="true" /></button>
        </header>
        <div className="overlay__body">{children}</div>
        {footer && <footer className="overlay__footer">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

/** Centred modal for confirmations. */
export function Dialog(props: OverlayProps) { return <Overlay variant="dialog" {...props} />; }
/** Side sheet that slides in from the inline end. */
export function Sheet(props: OverlayProps) { return <Overlay variant="sheet" {...props} />; }
