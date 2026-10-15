import { forwardRef, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { ChevronDown, Search } from "lucide-react";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className = "", ...props }, ref) {
  return <input ref={ref} className={`input ${className}`.trim()} {...props} />;
});

export function Select({ className = "", children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={`select ${className}`.trim()}>
      <select className="input select__control" {...props}>{children}</select>
      <ChevronDown className="select__icon" size={16} aria-hidden="true" />
    </span>
  );
}

type SearchInputProps = InputHTMLAttributes<HTMLInputElement> & { hint?: ReactNode };

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(function SearchInput({ className = "", hint, ...props }, ref) {
  return (
    <span className={`search-input ${className}`.trim()}>
      <Search className="search-input__icon" size={16} aria-hidden="true" />
      <input ref={ref} type="search" className="input search-input__control" {...props} />
      {hint && <kbd className="search-input__hint" aria-hidden="true">{hint}</kbd>}
    </span>
  );
});

type FieldProps = { label: ReactNode; htmlFor: string; hint?: ReactNode; children: ReactNode };

export function FormField({ label, htmlFor, hint, children }: FieldProps) {
  return (
    <div className="form-field">
      <label className="form-field__label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <p className="form-field__hint">{hint}</p>}
    </div>
  );
}
