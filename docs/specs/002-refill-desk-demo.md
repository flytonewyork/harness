# 002: The refill desk demo (for Josie)

**Problem.** Josie, a Hong Kong GP, said on 7 Nov she might be interested in a clinic that runs on the agentic model.
Thomas said he would show her something on 8 Nov. On 7 Nov he described HK GP work to Josie and to himself as mostly
one-off consults and patients who want their medicines. Instagram is full of "AI agents for your GP practice"
selling admin automation to practice managers. Josie needs to see, in her own visit mix, (1) real doctor time saved,
(2) that she stays in control and is safer, not less safe, (3) that it fits the Medical Council's telemedicine
guidelines, and (4) that it's real, not slides.

**Options considered.**

| Option | For | Against | Verdict |
|---|---|---|---|
| A. Replay the follow-up list (the product screen) | Real engine, real numbers | Back-office loops; doesn't look like her day | Used as evidence, not as the demo |
| B. Walk through the tmrw care line site | Polished | Marketing; she's a doctor, not a buyer | Updated, not shown first |
| C. Slides that break a consult into steps | Quick | Static; can't touch it | No |
| D. **A clickable refill desk: three WhatsApp refill requests on one night, with the agent's prep and one-tap sign-off** | Her most common visit, in Cantonese, on a phone; one case comes from the engine with checked quotes | Two of the three cases are illustrative | **Chosen** |
| E. A live model she messages as a patient | Biggest wow | Fragile; which model may touch HK patient data is still open | After she agrees to a pilot |
| F. Sit in on her session first | The truest input | Not today | It becomes the ask |

**Change.** `demo/refill/index.html`: one self-contained, phone-first page.
1. **Saturday 9:15 pm, the clinic is shut, three WhatsApps came in.** Mrs Wong wants her usual amlodipine (stable,
   readings in range): prepared, waits for Monday sign-off. Ms Lau asks for refills but says her chest has been
   tight for half an hour with sweating: a fixed reply (call 999) within seconds, and the doctor is alerted. Mr Chan
   asks for his lisinopril and spironolactone 90 minutes after a critical potassium of 6.6 landed on the lab
   portal: the agent joins the two and holds the refill, and pages the doctor.
2. **"What a refill bot does":** a toggle that shows a message-only bot sending Mr Chan his spironolactone.
3. **One-tap sign-off:** sign, edit or see in person. On sign, the patient's reply, the dispensing slip and the
   next watch appear.
4. **Your numbers:** sliders for her refill visits a day, minutes per visit and seconds per sign-off (all marked as
   guesses she should change).
5. **Tested on a simulated clinic year:** LoopBench's committed results for the solo clinic.
6. **The rules:** how each refill meets the Medical Council's telemedicine duties, and what isn't solved yet.
7. **The ask:** what would she need to see to put ten of her own refill patients through it?

`demo/refill/case_mr_chan.json` is built from a LoopBench product feed by `demo/refill/build_case.py` (it reads the
run file, as the product's adapters do; no LoopBench import). `tests/test_demo_refill.py` checks every quote on
the demo's Mr Chan card appears word for word in the record it cites.

**Done when.** The page works at 390 px and 1440 px with no console errors; the quote test passes; the authority
line holds on every card (the agent prepares, holds, alerts or pages; only the doctor signs, prescribes or closes).

**Invariants touched.** None in the engine. The demo reuses the engine's item for Mr Chan unchanged. Synthetic
data only: Mr Chan is the LoopBench demo patient; Mrs Wong and Ms Lau are illustrative and labelled that way.

**Out of scope.** A live model, a real WhatsApp number, refill logic in the engine. **Found while building:**
(1) the engine's context step says "No relevant medicines dispensed at this clinic" for Mr Chan, whose note names
lisinopril and spironolactone, so a refill request is the first structured statement of what he takes (this
strengthens `NOW.md` Next 4, repeat dispensing). (2) LoopBench seed 1 refers a 28-year-old man (FUNG Tai Man) to
gynaecology: a realism bug in the referral generator, for a separate LoopBench PR.
