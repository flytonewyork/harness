# Landing page for family doctors

Saved from the design canvas [tmrw one-page demo](https://claude.ai/artifact/7MzAFXNeTSNMJDLqnvS8Zw) (spec 002).
Each file is a whole design-canvas page (`.dc.html`). It renders on the canvas, not in a browser on its own.

| File | Board on the canvas | What it is |
|---|---|---|
| `v2.dc.html` | Landing v2 · 8 Nov (saved) | The version to go back to: four follow-up types with their Hong Kong value. Frozen: `tests/test_landing.py` pins its hash. |
| `v3.dc.html` | Landing v3 · 9 Nov | The test: the plan after the visit, blood tests and scans, repeat prescriptions and care after hospital; the WhatsApp front-desk menu with who finishes each option; opening hours and escalation; the red-team fixes (spec 003). |
| `v4-clinics.dc.html` | Landing v4 · for clinics · English | v3 with a "For clinics / For patients" switch at the top and a link to the patient page. |
| `v4-patients.dc.html` | Landing v4 · for patients · English | The page a clinic's patients see: what they can do on the WhatsApp menu, the opening hours and 999, their doctor in charge, their information, how to join, and questions patients ask. |
| `v4-clinics-zh.dc.html`, `v4-patients-zh.dc.html` | Landing v4 · for clinics · 中文, for patients · 中文 | The same pages in Traditional Chinese (Hong Kong). The WhatsApp examples stay in Cantonese. |

Every v4 page has two switches at the top: for clinics or for patients, and English or 中文 (English by default). They
link by canvas file name (`Landing-v4-clinics.dc.html`, `Landing-v4-patients-zh.dc.html` and so on), and work in the
canvas's Play mode.

After any change on the canvas, copy the file back here in the same PR. The canvas is a view; this folder is the
record.
