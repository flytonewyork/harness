# EC Healthcare: meeting prep, Wed 11 Nov 2026, 4pm

With Dr Julie Chow, CPO. Who else attends, and where, is still **[check]** (#23).

**What it's for.** Learn what EC wants from AI in its GP clinics, and turn an exploratory conversation into one small
next step in writing that needs no patient data. Thomas isn't formally engaged, and EC approving data access now is
very unlikely, so the ask asks for none.

**The line.** "Use any front end you like. We make it safe and make sure every follow-up gets done."

Meta's WhatsApp business agent, the record systems' own agents and reception-agent startups are all making the chat
layer a commodity. What tmrw keeps is underneath it: follow-ups closed with their evidence quoted, across channels no
single system sees; rules for what any agent may say or do, with a doctor deciding anything clinical; every task
proven in LoopBench before it's switched on; and Hong Kong depth (Cantonese, local lab formats, eHealth, Co-care, the
PDPO and the Medical Council's rules).

## Show for 10 minutes, then stop

The demo: `demo/ec-care-line/index.html`.

1. The one queue: Mr Chan's critical potassium, joined to his WhatsApp request for more spironolactone.
2. The front door: the QR code that signs a patient up to the care line after the visit.
3. The rules: what the agents may and may never do.
4. How it would connect to EC's EMR once EC is ready, with nothing leaving EC.

## Open questions for Julie

1. Where is EC's AI rollout in its GP clinics now, and what has been tried?
2. Which burden on doctors and the front desk should go first?
3. How do patients reach EC's clinics today, on WhatsApp or the phone, and who answers?
4. Can the EMR export, or does it have an API? Who in IT owns it?
5. Is the new GP practice going ahead? Building this in from day one avoids any old patient data.
6. How would EC like to work with Thomas?

## The ask, smallest first

1. **Must get:** a follow-up within two weeks with a clinic lead and someone from IT.
2. **Target:** a two-to-three-week discovery at one clinic, with no patient data. Half a day watching the back office
   and 30 minutes with IT. EC gets a map of the workflow, the top three burdens, an outline of how it would connect to
   the EMR, and the demo rebuilt around EC's own workflow with made-up patients.
3. **Later, not asked for on Wednesday:** the safety analysis (the look-back, run by EC's IT inside EC's environment;
   the report is built and waits on the branch `ec-phase0-report`), then a two-week shadow at one clinic, then going
   live one task at a time.

Don't ask for data, exports, access or a shadow. Don't name a fee at the meeting: it goes in the one-page scope
(`NOW.md`, Waiting on you).

## Afterwards

- Within 24 hours: send the one-page scope for the discovery. Julie's yes to it is the next step in writing.
- File the signal in `#signals`, with the stage it suggests.
- Score bets 6 and 7 in `docs/BETS.md` when their dates pass.
