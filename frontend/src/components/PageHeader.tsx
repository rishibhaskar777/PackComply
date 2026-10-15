import { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions, status }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; status?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <div className="page-header__title-row">
          <h1 className="page-title">{title}</h1>
          {status}
        </div>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}
