import { Badge, Card } from "../components/ui";
import { TranslationKey, useI18n } from "../i18n";
import { Compliance, Summary } from "../types";

const labels: Record<string, TranslationKey> = { product_name: "extractionProductName", manufacturer: "extractionManufacturer", manufacturer_address: "extractionManufacturerAddress", packer: "extractionPacker", packer_address: "extractionPackerAddress", importer: "extractionImporter", importer_address: "extractionImporterAddress", mrp: "extractionMrp", net_quantity: "extractionNetQuantity", manufacturing_date: "extractionManufacturingDate", packing_date: "extractionPackingDate", best_before: "extractionBestBefore", use_by: "extractionUseBy", expiry_date: "extractionExpiryDate", consumer_care_phone: "extractionConsumerCarePhone", consumer_care_email: "extractionConsumerCareEmail", consumer_care_website: "extractionConsumerCareWebsite", brand_owner: "extractionBrandOwner", brand_owner_address: "extractionBrandOwnerAddress", manufacturing_units: "extractionManufacturingUnits", mrp_declaration: "extractionMrpDeclaration", tax_inclusive: "extractionTaxInclusive", fssai_license: "extractionFssaiLicense", country_of_origin: "extractionCountryOfOrigin", unit_sale_price: "extractionUnitSalePrice", batch_number: "extractionBatchNumber", consumer_care_declaration: "extractionConsumerCareDeclaration", import_declaration: "extractionImportDeclaration", product_category: "extractionProductCategory" };
const fieldValueLabels: Record<string, TranslationKey> = { "mrp_declaration:present_with_value": "mrpDeclarationPresentWithValue", "mrp_declaration:present_without_value": "mrpDeclarationPresentWithoutValue", "mrp_declaration:absent": "mrpDeclarationAbsent", "tax_inclusive:true": "taxInclusiveYes", "consumer_care_declaration:present_with_details": "consumerCareDeclarationPresentWithDetails", "consumer_care_declaration:present_without_details": "consumerCareDeclarationPresentWithoutDetails", "consumer_care_declaration:absent": "consumerCareDeclarationAbsent", "import_declaration:present": "importDeclarationPresent", "product_category:food": "productCategoryFood" };

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
