import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { useI18n } from "../../i18n";
import { DURATION, EASE, useReducedMotion } from "../../lib/motion";

export type ToastVariant = "success" | "error" | "info";
type ToastItem = { id: number; message: string; variant: ToastVariant };
type ShowToast = (message: string, variant?: ToastVariant) => void;

export const TOAST_DURATION_MS = 4000;
const MAX_VISIBLE = 3;
const ICONS = { success: CircleCheck, error: CircleAlert, info: Info };

// Without a provider (isolated component tests) toasts are a no-op.
const ToastContext = createContext<ShowToast>(() => undefined);

export function useToast(): ShowToast {
  return useContext(ToastContext);
}

/** Bottom-right notifications: slide and fade in, auto-dismiss after 4 s, announced politely. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setItems(list => list.filter(item => item.id !== id));
  }, []);

  const show = useCallback<ShowToast>((message, variant = "success") => {
    const id = ++nextId.current;
    setItems(list => [...list.slice(-(MAX_VISIBLE - 1)), { id, message, variant }]);
    timers.current.set(id, window.setTimeout(() => dismiss(id), TOAST_DURATION_MS));
  }, [dismiss]);

  useEffect(() => () => { timers.current.forEach(timer => window.clearTimeout(timer)); }, []);

  const motionProps = reduced
    ? { initial: false as const, exit: { opacity: 0, transition: { duration: 0 } } }
    : { initial: { opacity: 0, x: 24, scale: 0.98 }, animate: { opacity: 1, x: 0, scale: 1 }, exit: { opacity: 0, x: 24, transition: { duration: DURATION.fast } }, transition: { duration: DURATION.base, ease: EASE } };

  return (
    <ToastContext.Provider value={show}>
      {children}
      <section className="toast-region" aria-label={t("notifications")}>
        <ol className="toast-list" aria-live="polite">
          <AnimatePresence initial={false}>
            {items.map(item => {
              const Icon = ICONS[item.variant];
              return (
                <m.li key={item.id} layout={!reduced} className={`toast toast--${item.variant}`} {...motionProps}>
                  <Icon className="toast__icon" size={16} aria-hidden="true" />
                  <p className="toast__message">{item.message}</p>
                  <button type="button" className="toast__close" aria-label={t("dismiss")} onClick={() => dismiss(item.id)}><X size={14} aria-hidden="true" /></button>
                </m.li>
              );
            })}
          </AnimatePresence>
        </ol>
      </section>
    </ToastContext.Provider>
  );
}
