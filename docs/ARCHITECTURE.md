# Architecture and behaviour reference

Deep technical detail: OCR, extraction, the rule engine, risk scoring, multi-photo inspections, accounts and i18n. The README has the overview diagrams.

## OCR

PaddleOCR requires the `paddlepaddle`, `paddleocr`, and OpenCV packages declared in `pyproject.toml`. CPU inference is the default; consult PaddlePaddle's installation guidance if your platform needs a platform-specific wheel or GPU support. Model downloads may occur the first time an image is processed.

PackComply runs one recognizer per configured language against the same image. Every detection is tagged with the language that produced it. A secondary-language recognizer also re-reads English text, so a secondary detection is kept only if it contains that language's own script (for example Devanagari for `hi`/`mr`) or its box does not overlap a primary-language box; the number dropped is logged. Configuration is validated against the documented PP-OCRv3 language codes used by this project, so an unsupported code fails clearly at startup.

The configured languages are `en,hi,gom,mai,mr,ne,sa,ta,te,ur,ka`: English, Hindi, Konkani, Maithili, Marathi, Nepali, Sanskrit, Tamil, Telugu, Urdu, and Kannada (`ka` is PaddleOCR's Kannada model code). They are selected with `OCR_LANGUAGES`, and the OCR response reports the codes actually used.

To add another OCR language, first verify a compatible PaddleOCR model and its exact `lang` code for the installed release, then add it to `SUPPORTED_PADDLE_OCR_LANGUAGES` and `OCR_LANGUAGES` and cover it with a unit test.

After uploading an image, OCR can be run directly with `POST /api/inspections/{inspection_id}/images/{image_id}/ocr`; the usual route is `POST /api/inspections/{inspection_id}/process`, which runs every unfinished stage.

## Information extraction

`POST /api/inspections/{inspection_id}/extract` interprets the preserved OCR text into fields such as entities, addresses, MRP, net quantity, dates, and consumer-care details. Confidence values are explainable heuristics, not calibrated probabilities. Missing information is never fabricated and raw OCR evidence is never changed.

Extraction is layout-aware: OCR boxes are grouped into lines, and a value is only taken from text attached to an explicit key (`Net Wt.`, `M.R.P.`, `Marketed by`, `LIC. No.`, Hindi equivalents), either on the key's line, in the box to its right, or directly below it. Each field records the OCR line(s) it came from (`source_text`), how it was found (`matched_pattern`, e.g. `net_quantity:right_of_key`), and the exact boxes (`source_bboxes`). Results stored without boxes fall back to reading the next line. A photo whose text boxes are almost all tall and narrow but carry no recorded rotation is read sideways: the end where the boxes line up (left-aligned text) gives the reading direction.

A second, **value-first** pass (`backend/app/services/extraction/values.py`) links the values the key-first pass cannot reach on phone photos:

1. It finds every value on the label with its position: prices (₹ / Rs / INR / रु, and the shapes OCR gives ₹ such as `T499.00` or `२58/-` when the amount is price-shaped), unit sale prices (`₹0.89/ml`, with `/mn` read as ml and `0.36/9` as per g), net quantities (also glued `550mlnet` and dual `100 ml / 3.38 fl. oz.`, never nutrition rows, dosage strengths, dilutions or `per 100 g`), and dates (also behind a letter or symbol code: `M 12/24`, `¥12/28`).
2. It finds every key **fuzzily** (`fuzzy.py`, standard library only): text and key are folded (M↔VI, l↔1↔I, O↔0, rn↔m, S↔5) and compared with an edit distance of at most 1 for keys of 4–7 characters and 2 for 8 or more, so `VIRP`, `Net Contemt` and `Manufactued by` count. Three-letter keys read with one error (`MAP`, `MFO`) count only when the line confirms them (a price, "incl. of all taxes", another key, a redirect). It also reads combined key blocks (`MRP (incl. of all taxes); USP, MFD (M) & Use Before (U): see below`): the codes they define and the redirect that applies to all their keys.
3. It scores every key/value pair — same line to the right, then a tilted next row, then directly below, then nearest in the same region; a code binds a value to its coded field (conventional `M`/`U`/`E`/`P` codes too); a key/value table on a curved pack pairs row by row; a redirect (`see below`, `see coding area`, `see bottom`, …) allows a stamp-like value elsewhere — and assigns greedily, one value per field and one field per value. Prices offered in a sentence (`also available @ ₹40/-`) are never the MRP. An unkeyed price is taken as the MRP only when the unit sale price and net quantity confirm it (`mrp:usp_consistency`).

Value-first matches record how they were made (`mrp:redirect_coding_area`, `manufacturing_date:letter_code_M`, `net_quantity:glued_key`) and are flagged for review when they come from a redirect, a coding area or the nearest-in-region rule.

Besides the original fields, extraction reports `brand_owner` (manufactured for / marketed by), `manufacturing_units` (every `(CODE)-company` unit of a multi-unit label), `mrp_declaration` (`present_with_value`, `present_without_value` when the price is stamped separately, or `absent`), `tax_inclusive`, `fssai_license`, `country_of_origin`, `unit_sale_price`, `batch_number` and `product_category` (`food`, `drug`, `personal_care`, `household` or `other`; food only with a nutrition panel, FSSAI text, a 14-digit licence, or a best-before date with an ingredients list of food words). Dates also carry an ISO-like `normalized_value` (`2026-03`, `2026-03-12`, or `P9M` for "9 months from manufacture").

Two further fields are evidence for compliance and are not shown as label fields: `declaration_status` records for each mandatory declaration whether it was read with its value (`DECLARED_WITH_VALUE`), only its key or a redirect was found (`KEY_FOUND_VALUE_UNREAD`), or nothing was detected (`NOT_DETECTED`); `ocr_coverage` records how much of the photo OCR read (text lines, mean confidence, share of poorly read lines).

The product name is left empty and flagged for review when no candidate clearly stands out: headings (matched fuzzily), instructions, lists, addresses, company names, foreign-language marketing copy and unreadable text are never candidates, and a name needs either a generic commodity word (shampoo, biscuits, liquid, …) or large type. A wrong name would be reported as a fact; an empty one only asks the inspector to look.

After improving extraction, `POST /api/inspections/{inspection_id}/reanalyse` re-runs extraction and compliance on the stored OCR result without repeating OCR; `python -m backend.scripts.reanalyse_all` does this for every completed inspection.

## Inspection record, corrections and audit trail

An inspection's page (`#/inspections/:id`) shows the photo with numbered evidence markers, the findings, an inspector-review footer and an audit trail.

- **Label identity.** The extracted product name, brand owner and manufacturer are stored on the inspection (`product_name`, `brand_name`, `maker_name`), so history and search work without joining extraction rows. Search matches product, brand, maker and inspection number case-insensitively; nothing extracted shows as "Untitled label". For inspections that predate this, run `python -m backend.scripts.backfill_product_names` once after `alembic upgrade head` (idempotent; it does not re-run OCR, extraction or compliance).
- **Corrections** (OFFICIAL only). `POST /api/inspections/{id}/corrections` with `{field_name, corrected_value, reason?}` validates the value with the extractor's own parsers (MRP `10` / `₹10.00` become `₹10.00`; quantities `70 g`; dates `08/2026`), returns 422 with a message when invalid, then re-runs extraction on the stored OCR, re-applies all corrections (confidence 1.0, source `INSPECTOR`), and re-evaluates compliance and risk. OCR is never repeated. `DELETE /api/inspections/{id}/corrections/{correction_id}` undoes one. Corrections survive "Re-analyse".
- **Verification.** `POST /api/inspections/{id}/verify` with `{remarks}` (OFFICIAL, completed inspections only) records who and when. Any later correction returns the record to `UNREVIEWED`.
- **Audit.** `GET /api/inspections/{id}/audit` returns creation, photo upload (with SHA-256), OCR (with duration), compliance evaluation (with ruleset version), corrections, re-analysis, undo and verification in time order.
- The PDF report gains an "Inspector review" section and marks inspector-corrected values.

Access stays scoped to the inspection's owner; the role checks above additionally reject manufacturers with 403.

## Multi-photo inspections, live progress and the phone field app

An inspection holds up to four photos, one per panel: `BACK`, `FRONT`, `DATE_STAMP`, `OTHER`. Each photo is read once (OCR results are stored per image and never repeated for the same checksum), extracted on its own, and merged field by field: the highest confidence wins, and a tie goes to the panel that owns the field (product and brand names: front; dates and batch: date stamp; everything else: back). Every field keeps the `image_id` it came from, with its boxes in that photo's pixel space. When photos disagree on a field a rule depends on (for example two net quantities), the rule is marked REVIEW with a note instead of one value being dropped. A new photo for a panel replaces the old one for processing; the old file and checksum stay as evidence.

Processing runs in a background thread pool inside the API (no broker; works on Windows). `POST /api/inspections/{id}/process` returns `202` with a progress snapshot; a second start while one is running returns `409`. Follow it with `GET .../progress` (polling, every second) or `GET .../progress/stream` (Server-Sent Events). Percentages are completed steps over total steps. Steps are kept in memory; after a restart the stored inspection status is used.

## Accounts and password reset

Create users with `python -m backend.scripts.create_user` (inspector accounts are only created this way unless `ALLOW_OFFICIAL_SELF_REGISTRATION=true`). Passwords must be at least 8 characters, differ from the username, and not be one of the 20 most common passwords.

A sign-in normally lasts `SESSION_TTL_HOURS` (12). Ticking **Keep me signed in on this device** makes it last `SESSION_REMEMBER_DAYS` (30). After `LOGIN_MAX_ATTEMPTS` (5) failed attempts the account is locked for `LOGIN_LOCKOUT_MINUTES` (15).

**Forgot password** works offline: there is no SMS or paid e-mail service. On the sign-in page, *Forgot password?* asks for a username and then for a one-time 6-digit code. The page never says whether a username exists.

The administrator issues the code on the server and hands it over in person:

```powershell
python -m backend.scripts.reset_password --username inspector
```

This prints `Reset code for inspector: 123456`. The code is valid for 15 minutes and for one use; any earlier code stops working. Five wrong guesses burn it. Alternatively, set a new password directly (typed twice, not echoed):

```powershell
python -m backend.scripts.reset_password --username inspector --set-password
```

Either way, a completed reset signs the user out on every device. A reset through the sign-in page also clears the login lockout. The CLI runs in a separate process and cannot clear that in-memory lock; it expires after `LOGIN_LOCKOUT_MINUTES`.

Optional e-mail delivery uses the Python standard library (`smtplib`) and is off until `SMTP_HOST` is set (see `.env.example`). The code is then e-mailed to the account's address as well. Codes are stored only as scrypt hashes and never written to logs. Reset requests are limited to 3 per username and 3 per IP address per 15 minutes; a further request gets 429.

| Endpoint | Body | Response |
|---|---|---|
| `POST /api/auth/password-reset/request` | `{"username"}` | Always `202` with the same message for known and unknown usernames; `429` when rate-limited |
| `POST /api/auth/password-reset/confirm` | `{"username", "code", "new_password"}` | `200` on success; `400 {"detail": "Invalid or expired code"}` for any code failure; `422` when the new password breaks the policy |

## UI languages

The interface offers 23 fully translated languages: English, Hindi, Assamese, Bengali, Bodo, Dogri, Gujarati, Kannada, Kashmiri, Konkani, Maithili, Malayalam, Manipuri (Meitei), Marathi, Nepali, Odia, Punjabi, Sanskrit, Santali, Sindhi, Tamil, Telugu, and Urdu. The header selector persists the choice under `packcomply-language` and falls back to English when the stored value is invalid.

Translations live in `frontend/src/locales/`. Every locale file must contain every UI key; the app rejects incomplete locale files rather than silently rendering English. The brand name stays "PackComply" in every language.

**Translation status.** The 21 languages other than English and Hindi were translated with machine assistance (no paid service, no network) and **need review by a native speaker before they are relied on**, especially Bodo, Dogri, Kashmiri, Konkani, Maithili, Manipuri (Meitei script), Sanskrit and Santali (Ol Chiki script), where wording and legal terminology are the least certain. Product and legal terms (MRP, FSSAI, LM(PC), Rule 6(1)(e), SHA-256, PDF, OCR…) and `{placeholder}` tokens are kept exactly as in English. `frontend/src/locales.translated.test.ts` fails if a non-English locale keeps more than 10% of its values identical to English (the product name, `MRP`, `PDF` and the language badge are the only allowed exceptions), if a `{placeholder}` goes missing, or if a product or legal term is dropped.

UI language and OCR language are separate. Assamese, Bengali, Bodo, Dogri, Gujarati, Kashmiri, Malayalam, Manipuri (Meitei), Odia, Punjabi, Santali, and Sindhi are UI options but are not enabled for OCR. OCR text is never translated; it remains as printed on the package.

## Compliance ruleset

The checks follow the mandatory declarations of the Legal Metrology (Packaged Commodities) Rules, 2011, as amended (including 2022). Rule metadata — title, inspector-facing description, legal reference, severity, applicability, enable/disable and configuration — lives in a versioned YAML file, `backend/app/services/compliance/rulesets/lm_pc_2011_v2.yaml` (v1 is kept so earlier results stay traceable). The check logic stays in Python, one function per rule code in `rule_engine.py`. The YAML is validated when the API starts and copied into the `compliance_rules` table (rules are updated, never deleted). Every stored result records the ruleset id and version, shown in the API (`ruleset_label`) and the PDF ("Checked against: LM(PC) 2011 ruleset v2"). `GET /api/compliance/rules` lists the active rules.

Each finding is one of:

- **FAIL** — a violation visible on the label (e.g. no net-quantity declaration at all on a well-read photo).
- **REVIEW** — cannot be confirmed from this photo (e.g. the MRP key is printed but the price is stamped elsewhere; the manufacture date is not in the frame). Ask the inspector to check the physical pack.

A tool for regulators must not accuse falsely, so for the mandatory declarations (manufacturer, net quantity, MRP, "inclusive of all taxes", unit sale price, manufacture date, consumer care) a missing value is judged by its `declaration_status`:

- `DECLARED_WITH_VALUE` — the value is checked as usual (PASS or FAIL).
- `KEY_FOUND_VALUE_UNREAD` — the key or a redirect ("see below", "see coding area", "printed on pack", "for MRP and other details see bottom of the pack") is printed but no value was linked: REVIEW, "Declaration present; value could not be read — verify on the pack".
- `NOT_DETECTED` — FAIL only when the photo's OCR coverage is adequate by the ruleset's `evidence` thresholds (`min_text_lines: 8`, `min_mean_confidence: 0.80`, `max_low_confidence_line_share: 0.30`), otherwise REVIEW, "Not detected — photo may not show this panel". The manufacture date is never FAIL.

Results stored before `declaration_status` existed keep their previous outcomes (only the MRP and consumer-care headings were recorded as "key without value", and the photo counts as adequately read).
- **PASS** — the declaration was found and is well formed.
- **NOT_APPLICABLE** — the rule does not apply to this label (e.g. importer rules on a domestic product). Not counted as an issue, in risk scores or in repeat patterns.

The verdict is **NON_COMPLIANT** (any FAIL), **NEEDS_REVIEW** (no FAIL, any REVIEW) or **COMPLIANT**. `overall_status` (FAIL / REVIEW / PASS) is still returned for compatibility.

| Rule code | Checks | Legal reference | Severity |
|---|---|---|---|
| MANUFACTURER_INFORMATION | Manufacturer / packer / brand-owner (or, if imported, importer) name and address | Rule 6(1)(a) | HIGH |
| IMPORTER_INFORMATION | Importer name and address — imported goods only | Rule 6(1)(a) | HIGH |
| COUNTRY_OF_ORIGIN | Country of origin — imported goods only | Rule 6(1)(aa) | HIGH |
| PACKER_INFORMATION | Address of a separately named packer | Rule 6(1)(a) | INFO |
| PRODUCT_IDENTITY | Common or generic name | Rule 6(1)(b) | WARNING |
| NET_QUANTITY_FORMAT | Net quantity in a standard unit | Rule 6(1)(c), Rule 9 | HIGH |
| NET_QUANTITY_UNIT_SUITABILITY | Unit clearly unsuited to the commodity (e.g. a liquid by count) | Rule 9, Fourth Schedule | INFO |
| MRP_FORMAT | MRP in Indian rupees | Rule 6(1)(e) | HIGH |
| MRP_TAX_INCLUSIVE | "Inclusive of all taxes" | Rule 6(1)(e) | WARNING |
| UNIT_SALE_PRICE | Unit sale price present and consistent with MRP ÷ net quantity | Rule 6(11) (2022) | WARNING |
| MANUFACTURE_DATE | Month and year of manufacture / packing (REVIEW, never FAIL, when not visible) | Rule 6(1)(d) | WARNING |
| DATE_CONSISTENCY | Manufacture/packing not after expiry/use-by, not in the future | Rule 6(1)(d) | WARNING |
| CONSUMER_CARE_FORMAT | Consumer-care telephone / e-mail | Rule 6(2) | WARNING |
| FSSAI_LICENSE_PRESENT | 14-digit FSSAI licence on food labels — an FSSAI cross-check, not Legal Metrology | FSS labelling regulations | INFO |

To change a rule's meaning, copy the YAML to a new version (`lm_pc_2011_v3.yaml`), bump `version`, point `DEFAULT_RULESET` in `ruleset.py` at it and re-run `python -m backend.scripts.reanalyse_all`. The unit-sale-price base units and tolerance are configuration and must be verified against the official notification.

## Risk-based targeting

Risk targeting is a transparent, deterministic prioritisation aid for completed inspections. It is not a machine-learning model and does not create legal rules or enforcement decisions. Officials see a queue based only on persisted compliance findings, missing extraction values that need review, and repeated evidence in their own completed-inspection history. Manufacturers cannot access the queue or the numerical score.

Scores are capped at 100: 0–24 LOW, 25–49 MEDIUM, 50–74 HIGH, and 75–100 CRITICAL.

- A FAIL contributes 35 / 25 / 15 / 10 points for HIGH / WARNING / INFO / LOW severity.
- A REVIEW contributes 20 / 12 / 6 / 4 points.
- A NOT_APPLICABLE result contributes nothing.
- A missing field contributes 6 points (maximum 18) only when it is a `required_fields` entry of a rule that FAILed; under a REVIEW the review points already cover it. Optional or informational fields (website, batch number, best before, a packer beside a declared manufacturer) never add points on their own, so a fully compliant label scores 0.
- Repeats count violations only: a rule that FAILs again adds 12, and a missing required field of a failing rule that recurs adds 8. Repeated REVIEW findings still appear on the patterns page but add no risk points.
- Repeats are counted per distinct photo: inspections whose latest image has the same checksum (the same photo uploaded again) are one occurrence, for both risk and the patterns page.
- Three or more distinct FAIL rules (violations) add 8.

Repeat evidence is calculated only within the authenticated owner's completed inspections. `GET /api/risk-targeting` is official-only, paginated, and excludes OCR text and image data.

`GET /api/dashboard/summary?period=7d|30d|all` (default `all`) feeds the Overview page: inspection count, verdict counts (including `PENDING` for inspections without results), the six highest-risk completed labels, the top three rules flagged FAIL or REVIEW on at least `PATTERN_MIN_OCCURRENCES` distinct photos, the median upload-to-completion time and the active ruleset version. Like the history endpoint it covers only the caller's own inspections; manufacturers receive the queue ordered by verdict, with `risk_score` and `risk_level` set to null.

## Declared quantity verification

Quantity verification reuses the existing OCR, extraction, and `NET_QUANTITY_FORMAT` compliance rule. It verifies only the **quantity declared on the label**; it does not weigh a product or claim that the physical contents were measured. The inspection result and PDF show the declared quantity, a normalized representation, its evidence, and the authoritative compliance result.

Supported formats include common mass and volume units (`mg`, `g`, `kg`, `ml`, `L`, `cm3`/`cc`) and count declarations (`N`, `nos`, `pcs`). Harmless spacing, capitalization, and common OCR character variants are normalized for validation while the declared display value is kept. Missing, unsupported, or ambiguous declarations continue through the existing compliance, risk, and repeat-pattern mechanisms.
