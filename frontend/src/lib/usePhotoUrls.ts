import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/**
 * Object URLs for stored photos. They are fetched with the session cookie (an <img src> to the API would
 * not carry it across origins) and revoked when the photo goes away or the component unmounts.
 */
export function usePhotoUrls(inspectionPath: string, imageIds: string[]): Record<string, string> {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const held = useRef<Record<string, string>>({});
  const key = imageIds.join(",");

  useEffect(() => {
    let cancelled = false;
    for (const id of Object.keys(held.current)) {
      if (!imageIds.includes(id)) { URL.revokeObjectURL(held.current[id]); delete held.current[id]; }
    }
    for (const id of imageIds) {
      if (held.current[id]) continue;
      api(`${inspectionPath}/images/${id}`, "image").then(response => response.blob()).then(blob => {
        if (cancelled) return;
        held.current[id] = URL.createObjectURL(blob);
        setUrls({ ...held.current });
      }).catch(() => undefined);
    }
    setUrls({ ...held.current });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inspectionPath, key]);

  useEffect(() => () => { Object.values(held.current).forEach(url => URL.revokeObjectURL(url)); held.current = {}; }, []);
  return urls;
}
