# EC Healthcare: meeting prep, Wed 11 Nov 2026, 4pm

With Dr Julie Chow, CPO. Who else attends, and where, is still **[check]** (#23).

Rewritten on 10 Nov, after the direction was locked (`docs/DECISIONS.md`, 10 Nov; spec 005). It replaces the version
on PR #25, which pitched the care line on EC's EMR and asked for a discovery at one clinic. That was a clinic-group
pitch, and clinic-group work is parked until the 1 Feb review.

**What it's for.** Two things, in this order:
1. Learn which burden EC most wants taken off its doctors, and how Julie wants to work with Thomas (#5: the new GP
   practice in Taikoo, health education for corporate clients, the internal AI rollout).
2. Get one small next step in writing that serves the line: Thomas runs his own care line in his own sessions at an
   EC practice. It needs nothing from EC's systems and no patient data from EC.

**The line.** "Every patient I see leaves with my plan on WhatsApp, in their language, and can reach me afterwards.
I approve every clinical word. Repeats and follow-ups come back to the practice."

Whether Thomas is taking sessions at EC, or starting the Taikoo practice, is the open fact this plan rests on
**[check with Julie first]**. If it isn't on the table, do part 1 only and ask for the follow-up below.

## Show for 5 minutes, then stop

The simulator, on the laptop (`python -m backend.line sim`, then `http://localhost:8080/sim`; the walk-through is
`docs/LINE_RUNBOOK.md` §0). Say first that it's a simulator with made-up patients: nothing is live yet.

1. The patient scans the card, joins in two taps, gives a name and date of birth.
2. You dictate three sentences. The draft comes back with every line tied to what you said. Tap Send: the plan
   arrives in English and Chinese.
3. The patient asks a question. They're told when you'll reply; you get it with a draft; one tap.
4. A repeat: you approve, they pay, they choose when to collect it **at the practice**.
5. One sentence on safety: chest pain gets "call 999" at once and you're alerted; nothing clinical goes out unless
   you approved it; the checks behind it are scored in LoopBench.

If the talk turns to the back office or the EMR, the older demo still exists (`demo/ec-care-line/` on PR #25). Don't
open it unless asked.

## Open questions for Julie

1. Which burden on EC's doctors or front desk would you take away first?
2. Is the Taikoo GP practice going ahead, and when? Who will its first patients be (head-office staff, corporate
   clients, walk-ins)?
3. How would you like to work with me: sessions at the practice, the AI rollout, or both? What did finance need to
   settle (#23)?
4. How do patients reach an EC clinic after a visit today, and who answers?
5. What would EC's compliance team need to see before a doctor uses a tool like this with patients he sees at EC?
6. Who else should I show this to: the CTO, a clinic lead?

## The ask, smallest first

1. **Must get:** which burden goes first (question 1), and a follow-up within two weeks with whoever decides about
   the Taikoo practice.
2. **Target:** EC agrees in writing that Thomas may offer his care line to patients he sees in his own sessions at
   one EC practice, for four weeks from launch (around 24 Nov), with repeats collected there. EC gets the numbers
   each week: how many patients joined, came back, asked for a repeat. This is bet 10 in `docs/BETS.md`, and it is
   the "clinic's written OK" the launch needs (`docs/LINE_RUNBOOK.md` §1).
3. **Later, not asked for on Wednesday:** the line for other EC doctors; anything that reads EC's EMR; the look-back.

## What EC will ask, and the answers

| Question | Answer |
|---|---|
| Whose patients are they? | EC's. The line runs in Thomas's sessions at EC's practice. Follow-up visits and repeats come back to the practice. |
| Where is the data, and who holds it? | Each patient consents in the first message and gets a privacy notice. The data is what the patient and the doctor put into the line: nothing is read from EC's systems. It sits in Google Cloud's Hong Kong region. tmrw processes it for the doctor. EC can have an export of its patients' records. |
| What does the AI do? | It drafts from the doctor's own words. Every item must quote what the doctor said, or it is dropped. It never writes to a patient by itself, and it never reads a report. |
| Is it live? | Not yet. Staging and a rehearsal week come first; launch is planned for around 24 Nov. |
| What does it cost EC? | Nothing in the four weeks. Elsewhere, patients pay for repeats and result reviews by message. Whether that is offered at EC is EC's call: charging can be switched off for one clinic, and then repeats there are free to request and collected as usual. |
| Can it carry EC's name, or run for all our doctors? | Later. First, four weeks in one doctor's sessions. |
| We use Heidi. | Heidi writes the note. This starts where the note ends: the patient's plan and what happens after. A Heidi note can be the input. |

## Don't

- Don't ask for data, exports, EMR access or a shadow.
- Don't name a fee for tmrw's work, and don't agree to exclusivity or to EC owning the line. If it comes up: "let me
  send a one-page scope."
- Don't say "the patients follow the doctor". At EC the offer is continuity for EC's patients, at EC's practice.
- Don't offer the two-to-three-week discovery from the old plan. If Julie raises the AI rollout role, treat it as
  separate, paid consulting work through tmrw consulting, and take it away to think about.

## Afterwards

- Within 24 hours: send the one-page scope for the four weeks (what patients see, the data answer above, what EC
  gets each week). Julie's yes to it is the next step in writing.
- Dictate the signal in `#signals` the same day: what she said in her words, the burden she named, and the stage.
- Score bet 10, and bet 6 on 26 Nov. Bet 7 (the discovery, on PR #25) wasn't asked for: if #25 merges, mark it
  withdrawn, not missed.
