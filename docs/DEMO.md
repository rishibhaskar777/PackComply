# Demo script and operations notes

The 5-minute walkthrough, fallbacks and operational features. See also [SETUP.md](SETUP.md).

### Two portals, one app

The sign-in role decides which product you see (`<html data-portal>`; the server still scopes every record to its owner):

| | Inspector portal (indigo) | Manufacturer portal (teal) |
|---|---|---|
| Home | `#/overview` — *Today's inspection priorities*: KPI tiles, risk queue, violations by rule, repeat issues | `#/products` — *My products*: readiness ring, counters, one card per product |
| Work | `#/new` / phone **New inspection**, `#/inspections/:id` official record (markers, corrections, verify, audit) | `#/check` / phone **Check my label**, `#/products/:id` self-check result (fix cards with examples, *Show on label*) |
| Other pages | Risk queue, Repeat issues, Rulesets, Reports (official PDF), About | Fix list, Self-check reports (pre-market PDF), LM(PC) rules guide, About |
| Wording | Inspection, Violation, Risk, Verify, Official report | Check, Fix, Ready to print, Self-check report (a self-assessment aid, not an approval) |

Opening the other role's route redirects (a manufacturer's `#/inspections/:id` becomes `#/products/:id`; an inspector's `#/products` becomes `#/overview`). Plain-language fix text comes from `backend/app/services/compliance/rulesets/remediation_v1.yaml`, one entry per rule (tested to cover every rule in the active ruleset).

### PDF artwork upload (manufacturers)

On **Check a label** (`#/check`, also when uploading a corrected label) a manufacturer can choose a **PDF** as well as a PNG or JPEG ("PNG, JPEG or PDF (first page)"). The browser sends the PDF untouched; the backend (`backend/app/services/uploads.py`) then:

- renders **only the first page** to a PNG with `pypdfium2` at the OCR size (`OCR_MAX_IMAGE_DIMENSION`, longest side), runs the normal OCR, extraction and rules on that image, and stores the **rendered PNG as the evidence image**;
- records a `PDF_RENDERED` event in the audit trail with the **original PDF's SHA-256**, its file name, size and page count (the image's own SHA-256 is recorded as for any photo);
- rejects with a plain `422` message: an **encrypted or password-protected** PDF, one with **more than 20 pages**, a file over the upload limit (`MAX_UPLOAD_BYTES`, default 10 MB), or a file that is not a valid PDF.

Inspector uploads stay image-only (inspectors photograph packs): a PDF sent by an inspector is refused with `422` and nothing is stored. Everything runs on the laptop; PDFium is a local library, so no network is used. Tests: `backend/tests/test_pdf_upload.py`.

### Keyboard shortcuts and page titles

Press **?** anywhere (outside a text field) for the list: **Ctrl K** focuses the inspection search (inspector portal), **N** starts a new inspection / label check, **G** then **O** goes to the inspector home (**G** then **P** to *My products* in the manufacturer portal). Every route has a meaningful browser-tab title for the role ("Today's inspection priorities · PackComply", "My products · PackComply", "<inspection number> · Inspection details · PackComply"), and an unknown address shows a friendly *Page not found* with a link to the role's home.

### 5-minute judge demo

Have the laptop on Wi-Fi (or a phone hotspot), the phone on the same network, `start-demo.ps1` finished and `seed_demo` run once. Open the laptop URL; on the phone, accept the certificate warning once beforehand and keep the page open.

1. **Log in** as `demo_inspector`. Say: everything runs on this laptop, nothing leaves it (the "Runs offline" card in the sidebar).
2. **Overview**: verdict mix, the risk queue and the repeat issues are already populated from real photos.
3. **Open the highest-risk record** (top of the risk queue, score 100). The photo shows numbered evidence markers; the findings list shows each failed rule with its legal reference.
4. **Click a finding**: the matching evidence box lights up on the photo, with the exact text OCR read.
5. **Make a correction** on a finding that is a mis-read, not a violation (for example the MRP or the net quantity): enter the value, give a reason, save. The verdict and risk update at once; the audit trail records who changed what.
6. **Show a hidden violation**: a finding that a casual look would miss (for example no consumer-care contact details, or an unit sale price that does not match MRP ÷ net quantity). Point at its legal reference and recommended action.
7. **Verify** the record (inspector review), then **download the PDF**.
8. **Phone capture of a new pack**: on the laptop click **Open on phone** and scan the QR (or open the phone URL), sign in as `demo_inspector` on the phone, tap **New inspection** (the field app), and photograph the back of a real pack; add the front or the date stamp if time allows. Each photo is uploaded and read while you frame the next one.
9. **Live progress**: *Done* shows the real step list (reading text EN + हिन्दी per photo, linking, rules, risk) with true durations, no fake percentages.
10. **Result**: the phone shows the verdict; open the record on the laptop. It has three photo tabs (back, front, date) with their own evidence boxes.
11. **Audit trail**: scroll to it: creation, each photo with its SHA-256, OCR time, ruleset version, correction, verification.
12. **Manufacturer portal** (second login, `demo_maker`): *My products* ("3 of 6 ready to print") → **Check a label** → upload `demo/artwork/gluco-biscuits-draft.png` → live checking → **Fix 3 items before printing** with plain-language problem, rule chip, *How to fix* and an *Example* → **Show on label** → **Upload corrected label** (`gluco-biscuits-corrected.png`) → **Ready to print ✓ · Improved from 3 fixes to 0** → **Download self-check report** → *My products* now shows "Gluco Biscuits improved from 3 fixes to 0".
13. If asked about accuracy, open **About & accuracy** (sidebar → Resources): precision, recall, rule agreement and the verdict confusion matrix measured by the evaluation script, labelled development or unseen, with the date.

Fallbacks:

- **Wi-Fi fails or the phone cannot reach the laptop**: turn on the phone's hotspot, connect the laptop to it, run `scripts\stop-demo.ps1` then `scripts\start-demo.ps1` again (a new LAN address is detected) and re-scan the QR. Or skip the phone: on the laptop use **New inspection** (`#/new`), drop the same photos onto the upload form and follow the same progress and result screens.
- **Phone says "can't connect"**: the firewall rule above is the usual cause; the script prints the command whenever it is missing. Check that both devices are on the same network and that the phone URL begins with `https://`.
- **Camera preview does not open on the phone**: the page must be `https://`; if the browser still blocks it, the shutter opens the phone's own camera app instead.
- **One photo cannot be read** (OCR crash or timeout): the inspection still completes with the other photos. Rules that depend on the unread photo are marked REVIEW with a note ("The date stamp photo could not be read…"), never PASS or FAIL, the progress list shows the failed step, and **Retry photo** (on the record) reads only that photo.
- **A screen crashes**: the global error screen offers **Reload**; saved inspections are unaffected. If a session expires, any request returning 401 sends you back to sign-in with a notice.

