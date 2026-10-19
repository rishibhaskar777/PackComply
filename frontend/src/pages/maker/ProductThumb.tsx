import { useEffect, useState } from "react";
import { Package } from "lucide-react";
import { api } from "../../api/client";

/** The product's label photo, fetched with the session cookie; a package icon until it arrives or when there is none. */
export function ProductThumb({ inspectionId, enabled = true }: { inspectionId: string; enabled?: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let held: string | null = null;
    api(`/inspections/${encodeURIComponent(inspectionId)}/image`, "image").then(response => response.blob()).then(blob => {
      if (cancelled) return;
      held = URL.createObjectURL(blob);
      setUrl(held);
    }).catch(() => undefined);
    return () => { cancelled = true; if (held) URL.revokeObjectURL(held); };
  }, [inspectionId, enabled]);
  return (
    <span className="thumb" aria-hidden="true">
      {url ? <img src={url} alt="" /> : <Package size={22} />}
    </span>
  );
}
