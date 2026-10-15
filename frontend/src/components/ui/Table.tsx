import { MouseEvent, ReactNode } from "react";

export type Column<T> = {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Wrap the cell in the row's link so every row is reachable by keyboard. */
  link?: boolean;
  align?: "start" | "end";
  width?: string;
  /** Hidden below 640px to avoid horizontal scrolling on phones. */
  hideOnMobile?: boolean;
  className?: string;
};

type TableProps<T> = { columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string; rowHref?: (row: T) => string; caption?: string; className?: string };

export function Table<T>({ columns, rows, rowKey, rowHref, caption, className = "" }: TableProps<T>) {
  function onRowClick(event: MouseEvent<HTMLTableRowElement>, row: T) {
    // Links and buttons inside the row keep their own behaviour.
    if (!rowHref || (event.target as HTMLElement).closest("a, button, input, select")) return;
    window.location.hash = rowHref(row).replace(/^#/, "");
  }
  const cellClass = (column: Column<T>) => [column.align === "end" ? "is-end" : "", column.hideOnMobile ? "hide-mobile" : "", column.className ?? ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`table-wrap ${className}`.trim()}>
      <table className="table">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>{columns.map(column => <th key={column.key} scope="col" className={cellClass(column)} style={column.width ? { width: column.width } : undefined}>{column.header}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={rowKey(row)} className={rowHref ? "is-clickable" : undefined} onClick={event => onRowClick(event, row)}>
              {columns.map(column => (
                <td key={column.key} className={cellClass(column)}>
                  {column.link && rowHref ? <a className="table__link" href={rowHref(row)}>{column.render(row)}</a> : column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
