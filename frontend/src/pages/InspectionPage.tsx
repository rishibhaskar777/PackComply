import { ChangeEvent, useEffect, useRef, useState } from "react";
import { FileDown, ImageIcon, Plus, RefreshCw, Upload } from "lucide-react";
import { api, ApiError, downloadReport, isExpired } from "../api/client";
import { navigate } from "../app/router";
import { Alert, Badge, Button, Card, CountUp, useToast } from "../components/ui";
import { TranslationKey, useI18n } from "../i18n";
import { Compliance, Summary } from "../types";

const labels: Record<string, TranslationKey> = { product_name: "extractionProductName", manufacturer: "extractionManufacturer", manufacturer_address: "extractionManufacturerAddress", packer: "extractionPacker", packer_address: "extractionPackerAddress", importer: "extractionImporter", importer_address: "extractionImporterAddress", mrp: "extractionMrp", net_quantity: "extractionNetQuantity", manufacturing_date: "extractionManufacturingDate", packing_date: "extractionPackingDate", best_before: "extractionBestBefore", use_by: "extractionUseBy", expiry_date: "extractionExpiryDate", consumer_care_phone: "extractionConsumerCarePhone", consumer_care_email: "extractionConsumerCareEmail", consumer_care_website: "extractionConsumerCareWebsite", brand_owner: "extractionBrandOwner", brand_owner_address: "extractionBrandOwnerAddress", manufacturing_units: "extractionManufacturingUnits", mrp_declaration: "extractionMrpDeclaration", tax_inclusive: "extractionTaxInclusive", fssai_license: "extractionFssaiLicense", country_of_origin: "extractionCountryOfOrigin", unit_sale_price: "extractionUnitSalePrice", batch_number: "extractionBatchNumber", consumer_care_declaration: "extractionConsumerCareDeclaration", import_declaration: "extractionImportDeclaration", product_category: "extractionProductCategory" };
const fieldValueLabels: Record<string, TranslationKey> = { "mrp_declaration:present_with_value": "mrpDeclarationPresentWithValue", "mrp_declaration:present_without_value": "mrpDeclarationPresentWithoutValue", "mrp_declaration:absent": "mrpDeclarationAbsent", "tax_inclusive:true": "taxInclusiveYes", "consumer_care_declaration:present_with_details": "consumerCareDeclarationPresentWithDetails", "consumer_care_declaration:present_without_details": "consumerCareDeclarationPresentWithoutDetails", "consumer_care_declaration:absent": "consumerCareDeclarationAbsent", "import_declaration:present": "importDeclarationPresent", "product_category:food": "productCategoryFood" };
const stageKeys: Record<string, TranslationKey> = { CREATED: "created", IMAGE_UPLOADED: "imageUploadedStatus", OCR_PROCESSING: "ocrProcessing", OCR_COMPLETED: "ocrCompleted", EXTRACTION_PROCESSING: "extractionProcessing", EXTRACTION_COMPLETED: "extractionCompleted", COMPLIANCE_PROCESSING: "complianceProcessing", COMPLETED: "inspectionComplete", FAILED: "failed" };

// Evidence the compliance rules read (declaration states, OCR coverage), not label text: never listed as fields.
const evidenceFields = new Set(["declaration_status", "ocr_coverage"]);
export function labelFields<T>(fields: Record<string, T>): [string, T][] {
  return Object.entries(fields).filter(([name]) => !evidenceFields.has(name));
}

type ManufacturingUnit = { code: string; name: string; address: string | null; licence: string | null };
export function FieldValue({ name, value }: { name: string; value: string | null }) {
  const { t } = useI18n();
  if (value === null) return <span className="field-value--missing">{t("notDetected")}</span>;
  const label = fieldValueLabels[`${name}:${value}`];
  if (label) return <span>{t(label)}</span>;
  if (name === "manufacturing_units") {
    let units: ManufacturingUnit[] = [];
    try { units = JSON.parse(value); } catch { return <span>{value}</span>; }
    return <ul className="unit-list">{units.map(unit => <li key={unit.code}><strong>({unit.code})</strong> {unit.name}{unit.address ? `, ${unit.address}` : ""}{unit.licence ? ` · ${t("extractionFssaiLicense")}: ${unit.licence}` : ""}</li>)}</ul>;
  }
  return <span>{value}</span>;
}

// Verdict and per-finding legal reference; NOT_APPLICABLE findings are shown neutrally, never as issues.
export function ComplianceResults({ compliance }: { compliance: Compliance }) {
  const { t } = useI18n();
  const { summary } = compliance;
  return (
    <Card title={<>{t("compliance")}: {compliance.verdict ? <Badge variant={compliance.verdict} /> : compliance.overall_status}</>}
      description={compliance.ruleset_label ? <span className="ruleset-label">{t("checkedAgainst")}: {compliance.ruleset_label}</span> : undefined}
      action={<span className="summary-pill tabular">{t("success")}: {summary.passed} · {t("error")}: {summary.failed} · {t("needsReview")}: {summary.review}{summary.not_applicable ? ` · ${t("notApplicable")}: ${summary.not_applicable}` : ""}</span>}>
      <ul className="finding-list">
        {compliance.findings.map(f => (
          <li className={`finding finding--${f.status.toLowerCase().replace("_", "-")}`} key={f.rule_id}>
            <strong className="finding__title">{f.rule_name} — <Badge variant={f.status} /> ({f.severity})</strong>
            <span className="finding__message">{f.message}</span>
            {f.legal_reference && <span className="finding__reference mono">{t("legalReference")}: {f.legal_reference}</span>}
            <span className="finding__field">{f.field}: {f.value ?? t("notDetected")}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

type InspectionPageProps = { inspectionId?: string; onExpired: () => void; onCompleted: () => void };

/** Single-inspection workflow (upload → OCR → extraction → compliance). Layout is rebuilt in Phase 3B. */
export function InspectionPage({ inspectionId, onExpired, onCompleted }: InspectionPageProps) {
  const { t } = useI18n();
  const toast = useToast();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [stage, setStage] = useState("CREATED");
  const [busy, setBusy] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The inspection this page already holds, so replacing the URL after creation does not refetch it.
  const loadedId = useRef<string | null>(null);
  const stageText = (status: string) => stageKeys[status] ? t(stageKeys[status]) : status;

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);
  useEffect(() => {
    if (!inspectionId) {
      if (loadedId.current) reset();
      return;
    }
    if (loadedId.current === inspectionId) return;
    loadedId.current = inspectionId;
    viewInspection(inspectionId);
  }, [inspectionId]);

  function reset() {
    loadedId.current = null;
    setSummary(null); setStage("CREATED"); setError(null);
    setPreview(null);
  }
  function handleFailure(e: unknown, fallback: string) {
    if (isExpired(e)) return onExpired();
    setStage("FAILED");
    setError((e as ApiError).detail ?? fallback);
  }
  async function viewInspection(id: string) {
    setBusy(true); setError(null); setSummary(null); setPreview(null);
    try {
      const result: Summary = await (await api(`/inspections/${encodeURIComponent(id)}`, "inspection")).json();
      setSummary(result); setStage(result.status);
    } catch (e) { if (isExpired(e)) onExpired(); else setError((e as ApiError).detail ?? t("historyLoadError")); }
    finally { setBusy(false); }
  }
  async function process(id: string) {
    setStage("OCR_PROCESSING");
    const result: Summary = await (await api(`/inspections/${id}/process`, "process", { method: "POST" }, 300_000)).json();
    setSummary(result); setStage(result.status);
    if (result.status === "COMPLETED") { onCompleted(); toast(t("toastInspectionSaved")); }
  }
  async function selectImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    setPreview(URL.createObjectURL(file)); setBusy(true); setError(null); setSummary(null);
    try {
      const inspection = await (await api("/inspections", "inspection", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
      loadedId.current = inspection.id;
      navigate(`/inspections/${inspection.id}`, { replace: true });
      setStage("IMAGE_UPLOADED");
      const form = new FormData(); form.append("image", file);
      const image = await (await api(`/inspections/${inspection.id}/image`, "upload", { method: "POST", body: form })).json();
      setSummary({ ...inspection, status: "IMAGE_UPLOADED", image });
      await process(inspection.id);
    } catch (e) { handleFailure(e, "Unable to complete the inspection."); }
    finally { setBusy(false); }
  }
  async function retry() {
    if (!summary) return;
    setBusy(true); setError(null);
    try { await process(summary.id); } catch (e) { handleFailure(e, "Unable to complete the inspection."); } finally { setBusy(false); }
  }
  async function report() {
    if (!summary || reportBusy) return;
    setReportBusy(true); setError(null);
    try { await downloadReport(summary.id, summary.inspection_number); toast(t("toastReportDownloaded")); }
    catch (e) { if (isExpired(e)) onExpired(); else setError(t("reportGenerationError")); }
    finally { setReportBusy(false); }
  }
  function startNew() { reset(); navigate("/new"); }

  const ocr = summary?.ocr, extraction = summary?.extraction, compliance = summary?.compliance, quantity = summary?.quantity_verification;
  const working = busy && stage !== "FAILED";
  return (
    <div className="page inspection-page">
      <header className="page-header">
        <div className="page-header__text">
          <h1 className="page-title">{summary ? summary.inspection_number : t("newInspection")}</h1>
          <p className="page-subtitle">{t("legalNotice")}</p>
        </div>
        <div className="page-header__actions">
          <span className={`stage-chip stage-chip--${stage === "FAILED" ? "failed" : stage === "COMPLETED" ? "done" : working ? "working" : "idle"}`} role="status">
            <span className="stage-chip__dot" aria-hidden="true" />{stageText(stage)}
          </span>
        </div>
      </header>

      {error && <Alert action={summary ? <Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={retry} disabled={busy}>{t("tryAgain")}</Button> : undefined}>{t("error")}: {error}</Alert>}

      <div className="inspection-grid">
        <Card title={t("uploadImage")} description={t("imageHelp")}>
          <label className={`dropzone ${busy ? "is-busy" : ""}`}>
            <input className="sr-only" type="file" accept="image/png,image/jpeg" onChange={selectImage} disabled={busy} />
            <span className="dropzone__icon" aria-hidden="true"><Upload size={16} /></span>
            <span className="dropzone__label">{busy ? stageText(stage) : t("selectImage")}</span>
          </label>
          {summary?.image && <p className="upload-note">{t("imageUploaded")}: <span className="mono">{summary.image.original_filename}</span></p>}
        </Card>
        <Card title={t("imagePreview")}>
          {preview ? <div className="evidence"><img className="label-preview" src={preview} alt={t("imagePreview")} /></div> : <div className="preview-placeholder"><ImageIcon size={16} aria-hidden="true" /><p>{t("noImage")}</p></div>}
        </Card>
      </div>

      <Card title={t("ocrResults")} action={ocr ? <span className="summary-pill">{t("processingLanguages")}: {ocr.languages.join(", ")}</span> : undefined}>
        {ocr ? <>
          <pre className="raw-text">{ocr.raw_text}</pre>
          {ocr.results.length > 0 && <ul className="detection-list">{ocr.results.map((v, i) => <li className="detection" key={`${v.text}-${i}`}><strong>{v.text}</strong><span className="detection__meta">{t("confidence")}: {v.confidence === null ? "—" : `${Math.round(v.confidence * 100)}%`}</span></li>)}</ul>}
        </> : <p className="muted-text">{busy ? stageText(stage) : t("noResults")}</p>}
      </Card>

      {extraction && <Card title={t("extractionResults")}>
        <dl className="field-grid">
          {labelFields(extraction.fields).map(([name, field]) => (
            <div className="field-grid__item" key={name}>
              <dt>{t(labels[name] ?? "productInformation")}</dt>
              <dd><FieldValue name={name} value={field.value} /></dd>
              <dd className="field-grid__meta">{t("extractionConfidence")}: <span className="mono">{Math.round(field.confidence * 100)}%</span> {field.needs_review ? `(${t("needsReview")})` : ""}</dd>
            </div>
          ))}
        </dl>
      </Card>}

      {quantity && <Card className="quantity-verification" title={t("netQuantity")} description={t("compliance")} action={<Badge variant={quantity.status} />}>
        <dl className="field-grid">
          <div className="field-grid__item"><dt>{t("netQuantity")}</dt><dd>{quantity.declared_quantity ?? t("notDetected")}</dd></div>
          <div className="field-grid__item"><dt>{t("detectedText")}</dt><dd>{quantity.normalized_quantity ?? t("notDetected")}</dd></div>
          <div className="field-grid__item"><dt>{t("status")}</dt><dd>{quantity.reason}</dd></div>
          <div className="field-grid__item"><dt>{t("warnings")}</dt><dd className="mono">{quantity.rule_code} · {quantity.severity}</dd></div>
          {quantity.evidence && <div className="field-grid__item"><dt>{t("ocrResults")}</dt><dd>{quantity.evidence}</dd></div>}
        </dl>
      </Card>}

      {compliance && <ComplianceResults compliance={compliance} />}

      {summary?.risk && <Card title={<>{t("riskScore")}: <span className="mono"><CountUp value={summary.risk.risk_score} />/100</span></>} description={t("riskReasons")} action={<Badge variant={summary.risk.risk_level} />}>
        <ul className="detection-list">
          {summary.risk.reasons.map((reason, index) => <li className="detection" key={`${reason.type}-${index}`}><strong><span className="mono">+{reason.points}</span> — <span className="mono">{reason.rule_code ?? reason.field ?? reason.type}</span></strong><span className="detection__meta">{reason.description}</span></li>)}
        </ul>
      </Card>}

      {(summary || stage === "FAILED") && <div className="page-footer-actions">
        {summary && <Button variant="outline" icon={<FileDown size={16} aria-hidden="true" />} onClick={report} loading={reportBusy}>{reportBusy ? t("generatingPdf") : t("downloadPdfReport")}</Button>}
        {(summary?.status === "COMPLETED" || stage === "FAILED") && <Button icon={<Plus size={16} aria-hidden="true" />} onClick={startNew} disabled={busy}>{t("startNewInspection")}</Button>}
      </div>}
    </div>
  );
}
