import { useEffect, useMemo, useState } from "react";
import qrcode from "qrcode-generator";
import { api, isExpired } from "../api/client";
import { Dialog, Skeleton } from "../components/ui";
import { useI18n } from "../i18n";

type NetworkInfo = { lan_ip: string | null; phone_url: string | null };

/** A QR code drawn locally as one SVG path (no network, no image service). */
export function QrCode({ value, label }: { value: string; label: string }) {
  const { path, size } = useMemo(() => {
    const code = qrcode(0, "M");
    code.addData(value);
    code.make();
    const count = code.getModuleCount();
    let d = "";
    for (let row = 0; row < count; row++) {
      for (let column = 0; column < count; column++) if (code.isDark(row, column)) d += `M${column + 4} ${row + 4}h1v1h-1z`;
    }
    return { path: d, size: count + 8 };
  }, [value]);
  // A white quiet zone is part of the code, so it stays white in dark mode too.
  return (
    <svg className="qr" role="img" aria-label={label} viewBox={`0 0 ${size} ${size}`} shapeRendering="crispEdges">
      <rect className="qr__light" width={size} height={size} />
      <path className="qr__dark" d={path} />
    </svg>
  );
}

/** "Open on phone": the address a phone on the same Wi-Fi uses, as a QR code. */
export default function PhoneDialog({ open, onClose, onExpired }: { open: boolean; onClose: () => void; onExpired: () => void }) {
  const { t } = useI18n();
  const [info, setInfo] = useState<NetworkInfo | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setFailed(false);
    api("/system/network", "network").then(response => response.json()).then((value: NetworkInfo) => { if (current) setInfo(value); })
      .catch(error => { if (!current) return; if (isExpired(error)) onExpired(); else setFailed(true); });
    return () => { current = false; };
  }, [open]);

  return (
    <Dialog open={open} onClose={onClose} title={t("phoneTitle")} description={t("phoneScan")} className="phone-dialog">
      <div className="phone-dialog__body">
        {failed ? <p role="alert">{t("phoneLoadError")}</p>
          : info === null ? <Skeleton width={220} height={220} />
          : info.phone_url ? <>
            <QrCode value={info.phone_url} label={t("phoneQrLabel")} />
            <p className="phone-dialog__url mono">{info.phone_url}</p>
            <p className="phone-dialog__hint">{t("phoneCertHint")}</p>
          </> : <p role="alert">{t("phoneNoNetwork")}</p>}
      </div>
    </Dialog>
  );
}
