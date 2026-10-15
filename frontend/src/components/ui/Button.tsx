import { AnchorHTMLAttributes, ButtonHTMLAttributes, forwardRef, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";

export type ButtonVariant = "primary" | "outline" | "ghost" | "destructive-outline";
export type ButtonSize = "sm" | "default" | "lg" | "icon";

export function buttonClass(variant: ButtonVariant = "primary", size: ButtonSize = "default", className = "") {
  return ["btn", `btn--${variant}`, `btn--${size}`, className].filter(Boolean).join(" ");
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize; loading?: boolean; icon?: ReactNode };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({ variant = "primary", size = "default", loading = false, icon, className, children, disabled, type = "button", ...props }, ref) {
  const leading = loading ? <LoaderCircle className="btn__spinner" size={16} aria-hidden="true" /> : icon;
  return (
    <button ref={ref} type={type} className={buttonClass(variant, size, className)} disabled={disabled || loading} aria-busy={loading || undefined} {...props}>
      {/* Keyed so the spinner and the icon cross-fade when loading starts or ends. */}
      {leading && <span className="btn__icon" key={loading ? "loading" : "idle"}>{leading}</span>}
      {children}
    </button>
  );
});

type LinkButtonProps = AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode };

export function LinkButton({ variant = "primary", size = "default", icon, className, children, ...props }: LinkButtonProps) {
  return <a className={buttonClass(variant, size, className)} {...props}>{icon && <span className="btn__icon">{icon}</span>}{children}</a>;
}
