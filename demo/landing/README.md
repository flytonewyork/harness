# Landing page for family doctors

Saved from the design canvas [tmrw one-page demo](https://claude.ai/artifact/7MzAFXNeTSNMJDLqnvS8Zw) (spec 002).
Each file is a whole design-canvas page (`.dc.html`). It renders on the canvas, not in a browser on its own.

| File | Board on the canvas | What it is |
|---|---|---|
| `v2.dc.html` | Landing v2 · 8 Nov (saved) | The version to go back to: four follow-up types with their Hong Kong value. Frozen: `tests/test_landing.py` pins its hash. |
| `v3.dc.html` | Landing v3 · 9 Nov | The test: the plan after the visit, blood tests and scans, repeat prescriptions and care after hospital; the WhatsApp front-desk menu with who finishes each option; opening hours and escalation; the red-team fixes (spec 003). |
| `v4-clinics.dc.html` | Landing v4 · 9 Nov: for clinics | v3 with a "For clinics / For patients" switch at the top and a link to the patient page. |
| `v4-patients.dc.html` | Landing v4 · 9 Nov: for patients | The page a clinic's patients see: what they can do on the WhatsApp menu, the opening hours and 999, their doctor in charge, their information, how to join, and questions patients ask. |

The two v4 pages link to each other by their canvas file names (`Landing-v4-clinics.dc.html`, `Landing-v4-patients.dc.html`); the links work in the canvas's Play mode.

After any change on the canvas, copy the file back here in the same PR. The canvas is a view; this folder is the
record.
