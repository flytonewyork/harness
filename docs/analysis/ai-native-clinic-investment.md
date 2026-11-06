# AI-native clinic in Hong Kong: investment analysis

6 Nov 2026 · Thomas, with Claude · Living version (with charts): [Claude Doc](https://claude.ai/artifact/7aiAFUfFtwQ31g1kQWBcgB)
· Every number comes from [`clinic_model.py`](clinic_model.py) (`python docs/analysis/clinic_model.py`). Inputs are
assumptions unless a source is named. Synthetic and illustrative: no patient data.

## Summary

**Verdict: on every unit measure, an AI-native GP practice beats the Hong Kong alternatives, but only if patients pay
for care between visits.** That's the one thing to prove, and Thomas's own sessions can test it in 90 days for almost
no capital.

- **The market:** private GP visits are worth HK$2.5–3.6bn a year, paid mostly in cash, visit by visit. Listed
  operators keep 2–5%. Doctors, not patients, are the scarce input.
- **What changes:** per full-time doctor, revenue rises from HK$308k to HK$494k a month by adding paid WhatsApp tasks,
  memberships and refills. The doctor earns HK$274k (+78% on a 50% session) and the operator HK$58k, about 3x a group
  clinic. Capital per doctor falls from HK$0.5–0.8m to about HK$0.1m.
- **Business model innovation:** real, but as a combination: paying for the task rather than the visit, membership at
  a mass price, medicines unbundled, and a clinic decompiled into rooms by the session. The most novel piece, doctors
  owning their panel on a platform, earns the least in Hong Kong.
- **Network effects:** weak. Higher pay draws doctors in, but pay is a price a rival can match; only cover rotas and
  referrals are true network effects. The moat is the cost position, measured trust and doctor loyalty.
- **Scale:** the base case passes a HK$100m-a-year run-rate around month 36, then reaches HK$165m of revenue and HK$23m
  of EBITDA in year 5, with a peak cash need of about HK$3m. HK$100m a year is 2–3% of Hong Kong's GP market. The
  HK$1.2bn ceiling isn't reachable from Hong Kong GP care alone.
- **The design choice that matters most:** split by line. Pay doctors the market 50% on visits and 70% of what their
  line earns, so a weak start doesn't sink the practice.

**Against the other ways to invest in this market:**

| | Unit economics | Capital | Main risk |
|---|---|---|---|
| A clinic chain (EC, Human Health) | 2–5% margins | Heavy: leases, fit-outs | Doctor costs, weak consumer spending |
| Clinic software (Apricot's VITA) | Software margins on a small market | Light | Crowded, and nine years in |
| An AI scribe (Heidi) | Software margins | Light | Becoming a feature others give away |
| **An AI-native practice (tmrw)** | **About 12% at the practice, 14% for the group by year 5** | **Light** | **Whether patients pay between visits** |

The best unit economics carry the highest demand risk. The 90-day measures near the end decide it.

## The market today

**Hong Kong GP care is an episodic, walk-in, cash market with thin operator margins.** Doctors are the scarce input,
medicines prop up the clinic's margin, and the work between visits goes unpaid.

**Demand.** 14.2% of people saw a doctor in the 30 days before the Thematic Household Survey (2022–23), and private
doctors give about 68% of outpatient consultations (Primary Healthcare Blueprint). That is roughly 6–9 million private
GP consultations a year, or HK$2.5–3.6bn at the HK$400 median fee ([HKMJ 2026](https://www.hkmj.org/system/files/hkmj2513816.pdf)).
Only about 23% of people have a family doctor, and 2.2 million live with a chronic condition (Blueprint).

**Supply.** About 16,000 doctors: roughly 9,000 private and 7,000 public, or 2 per 1,000 people
([China Daily HK, Jul 2026](https://www.chinadailyhk.com/hk/article/636945)). 909 family doctors had joined the
Co-care Network by 23 Sep 2026 ([Health Bureau](https://www.info.gov.hk/gia/general/202610/07/P2026100700411.htm)).

**What patients pay today:**

| Option | Price | What's included | Source |
|---|---|---|---|
| Public general outpatient clinic | HK$50 a visit | Medicines; limited slots | [SCMP](https://www.scmp.com/news/hong-kong/health-environment/article/3302288/are-hongkongers-paying-too-little-healthcare-breaking-down-costs) |
| Private GP, self-pay | HK$400 median (HK$540 for a family-medicine specialist) | Usually 2–3 days of medicine | [HKMJ 2026](https://www.hkmj.org/system/files/hkmj2513816.pdf) |
| Employer plan, network GP | HK$0–50 co-pay | 2 days of basic medicine | [Bowtie](https://www.bowtie.com.hk/blog/zh/%E7%B6%B2%E7%B5%A1%E9%86%AB%E7%94%9F-%E9%9D%9E%E7%B6%B2%E7%B5%A1%E9%86%AB%E7%94%9F/) |
| Chain video consult | HK$350 by day, HK$500 in the evening | Medicine delivered free | [QHMS](https://www.qhms.com/en/promo/video-consultation) |
| Chronic Disease Co-Care visit | HK$150 co-pay plus a HK$166 subsidy | Up to 6 subsidised visits a year | [Health Bureau](https://www.info.gov.hk/gia/general/202309/25/P2023092500476p.htm) |

**Who keeps what.** Human Health, a listed GP network with 62 centres, reported FY2025 revenue of HK$644m. Doctor fees
took 36% of it, staff 21%, medicines 14%, and profit was 4.1%
([HKEX, Sep 2025](https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0925/2025092502096.pdf)). EC Healthcare's
medical-services revenue fell 4.7% to HK$2.51bn in FY25 ([Tiger/Reuters](https://www.itiger.com/hans/news/2544513604)),
and it warned that profit for the half-year to September 2025 would fall by up to 90%
([filing](https://filingreader.com/news-wire/hongkong/2025-11-24/ec-healthcare-projects-significant-profit-and-revenue-decline)).
Independent clinics pay sessional doctors about half the fees and earn a good part of their margin on medicines, at
about double the pharmacy price (Josie, a locum across six practices, #17).

**What this means for an investor:**

- Margins are thin because rent, the front desk and head office eat what the doctor doesn't take.
- The binding constraint is doctors, not patients.
- The public HK$50 visit anchors what mass-market chronic patients will pay. Paid continuing care has to win on time,
  access and continuity, not on price.
- The layer between visits (results, refills, recalls, questions) is real work that nobody bills for. That's the gap
  the AI-native model turns into revenue.

## Operating models compared, per doctor-month

Six ways to run Hong Kong GP care, each scaled to one full-time doctor (22 days a month), 35 visits a day at HK$400.
The first four are today's market. The fifth is a practice tmrw runs, paying doctors 60%. The sixth is doctor mode: a
sessional doctor who also runs their own line on the platform. HK$k a month unless stated.

| Model | Revenue | To the doctor | Operator margin | Doctor hours a month | Doctor's pay per hour | Capital per doctor | Payback |
|---|---|---|---|---|---|---|---|
| Solo GP (owner-run) | 308 | 177 (includes the clinic's profit) | — | 172 | HK$1,029, with the owner's risk | ~HK$805k (fit-out, deposit, stock) | Years, while the panel builds |
| Group clinic, 50% split | 308 | 154 | 19.7 (6.4%) | 161 | HK$955 | ~HK$500k | ~25 months |
| Listed GP chain (Human Health's FY2025 ratios) | 308 | 112 | 15.2 (4.9% before tax) | 161 | ~HK$693 | ~HK$800k | ~53 months |
| AI-added clinic (scribe, paid reminders) | 326 | 163 | 24.0 (7.4%) | 148 | HK$1,096 | ~HK$500k | ~21 months |
| **AI-native practice (tmrw-run, 60%)** | **494** | **274** | **58.2 (11.8%)** | **167** | **HK$1,640** | **~HK$100k** | **~8 months, with ramp-up** |
| Doctor's own line on the platform | 382 (308 sessions + 74 line) | 221 | Host clinic 19.7, unchanged; tmrw ~2.3 | 183 (161 sessions + 22 on the line) | HK$1,208 overall; **HK$3,076 on the line** | None | Immediate |

**Where the AI-native margin comes from** (HK$k a month, against the group clinic's 19.7):

- **+185.5 new revenue:** paid WhatsApp tasks (88), memberships (60) and medicine refills (37.5).
- **−119.6 to the doctor:** 31 of it from the higher split, 89 from the doctor's 60% of the new lines.
- **+21.5 overhead saved:** one nurse instead of a front desk, rooms by the session, wholesale buying.
- **−25.5 refill medicines** and **−23.5 AI, payments and running costs.**
- **= 58.2 operator margin, 11.8% of revenue.**

The doctor's raise is mostly new revenue, not a bigger slice. That matters: a rival can match a split overnight, but
not the between-visit revenue the agents create.

The sixth model: the doctor keeps sessional pay of HK$154k and adds HK$67k a month from a busy line (10 paid tasks a
day, 100 members), net of a HK$4.7k platform fee and card fees: **HK$221k, +44%**. tmrw keeps about HK$2.3k a doctor
after compute and WhatsApp costs.

**The break-even that matters:** without the new lines, a 60% split leaves the AI-native practice at about zero
(−HK$1.9k a month). It matches a group clinic's margin at 11 paid tasks a day with no members, or 4 a day with 100
members. **The model lives or dies on between-visit revenue.**

## What AI-native changes in the business model

**Three shifts:** the billable unit moves from the visit to the task; the cost base moves from fixed to variable; and
the patient relationship moves from the clinic's front desk to the doctor's line.

| | Conventional clinic | AI-native practice | Why an investor cares |
|---|---|---|---|
| **What's sold** | A visit, with 2–3 days of medicine bundled in | Visits, plus paid tasks between visits, memberships and refills | Revenue per doctor rises about 60%, if patients pay for the new lines |
| **Recurring revenue** | Close to none: patients walk in | Memberships and refills are about 20% of revenue | Recurring revenue is worth more and steadies cash flow |
| **Who does the work between visits** | Nurses and receptionists, unpaid, or nobody | Agents prepare it; the doctor signs in 3–5 minutes | A follow-up touch costs roughly 5–10x less |
| **Cost base** | Lease, front desk, head office: mostly fixed | Rooms by the session, one nurse, compute: mostly variable | Lower break-even, and a new doctor pays back in months rather than years |
| **Capital per doctor** | HK$0.5–0.8m for fit-out, deposit and stock | About HK$0.1m | Growth can be paid for from cash flow |
| **Medicines** | Bundled, at about double the pharmacy price | Priced upfront, refills delivered, bought wholesale | Less margin per item, more items, more trust |
| **Who holds the relationship** | The clinic: its records, its phone number | The doctor's WhatsApp line, with the record behind it | Doctors join for it; and because they can leave, tmrw has to keep earning its place |
| **Doctor's pay** | About half the fees | 60% of a larger pot, about 70% more per hour | A recruiting edge in a market short of doctors |
| **Quality** | Dropped loops are invisible | Every item carries its evidence; misses are measured | A safety record that partners, insurers and regulators can check |

**What doesn't change:** the visit in the room, its fee, and the doctor's judgement and accountability. Visits are
still about 62% of an AI-native practice's revenue. This is a new layer on a conventional base, not a replacement for
it, which is why it can start inside existing clinics.

**The catch:** each shift creates a dependency. Paid tasks and memberships need patients willing to pay for care they
now get free or skip. Rooms by the session need partner clinics. And a doctor-held relationship needs tmrw to stay
useful enough that doctors don't take their line elsewhere.

## Is there business model innovation?

**Yes, but mostly as a combination.** No single piece is new to the world. Put together, they are new to Hong Kong,
and two of them only work because AI makes a contact between visits nearly free.

| Innovation | Precedent | What's new here | Economic effect | Main risk |
|---|---|---|---|---|
| **Pay for the task, not the visit** | US Medicare e-visits and chronic-care management; Doctronic, Counsel Health | A priced, routine unit of self-pay care on WhatsApp, under the Medical Council's 2019 telemedicine guidelines | HK$200 for 3–5 doctor-minutes: HK$2,400–4,000 a doctor-hour, against about HK$955 in a session | Whether patients pay for what they now skip or get free |
| **Membership at a mass price** | US direct primary care: 3,600+ practices, average US$98.64 a month, panels of 400–700 ([Hint, 2026](https://blog.hint.com/state-of-dpc-2026-key-takeaways-from-the-dpc-alliances-physician-survey)); One Medical | Agents do the between-visit work, so HK$300 a month (about US$38) can carry it | HK$60k a month of recurring revenue per doctor at 200 members | Churn, and members expecting more than the price carries. Forward shut in 2024 after spending ahead of revenue |
| **The doctor owns the panel; the platform takes a fee** | Shopify, Substack; Doctolib for scheduling | Healthcare platforms usually own the demand. Here the doctor does, and carries it between clinics | No capital, but only HK$3.6–4.7k a month per doctor | Medical Council limits on sharing fees; doctors leaving with their line |
| **Medicines unbundled and priced upfront** | Cost Plus Drugs (US); Hong Kong's Community Pharmacy Programme from late 2026 | A hidden 2x markup becomes a visible, recurring refill margin | About HK$80 margin per refill basket; the biggest profit line after practices in the base case | Needs a pharmacy licence at scale; conflicts with host clinics' dispensing income |
| **The decompiled clinic** | Sessional room rental; nurse home visits | Fixed lease and front desk become per-session costs | Capital per doctor falls from HK$0.5–0.8m to about HK$0.1m | Room supply and quality; clinic licensing under Cap. 633 |
| **Trust as an asset** | Few in primary care | Every item carries verbatim evidence; miss rates measured in LoopBench before patients | Lets partners, insurers and regulators check safety, not take it on faith | Unproven that anyone pays for it |
| **Payer contracts (later)** | Value-based care in the US; Hong Kong's Co-care incentives | An employer or insurer pays per member per month for the line and memberships | Turns members into contracts | Long sales cycles; insurers' own margins |

**The analyst's read:**

- **The real innovation is economic, not technical.** Near-free contact between visits turns unpaid work into two new
  revenue lines, tasks and memberships. Everything else follows from that.
- **The most novel piece is the weakest in Hong Kong.** Doctors owning their panel on a platform earns little per
  doctor, and the fee-sharing rules cap how it can charge. It's an option on scale and on other markets, not the core.
- **So the investable business is the operator:** practices tmrw runs or co-owns, with the medicines layer, and the
  platform riding on top.

## Network effects and moats

**Short answer: weak network effects, real but copyable economics, and one durable moat if the agents work.**

The doctor flywheel, link by link (diagram in the doc):

1. Higher pay per hour, less admin → more doctors join: **economics** (a price a rival can match).
2. More doctors → a cover rota and referrals between them: **network effect.**
3. Cover and referrals → patients get answers at any hour: **network effect.**
4. Faster answers → more members and paid tasks per doctor: **demand.**
5. More revenue and volume per doctor → funds the pay: **economics and scale.**

**Doctors joining for higher pay is not a network effect. It's a price.** A funded rival can offer 60% tomorrow. What
it can't easily copy is the between-visit revenue that pays for the 60%: without it, the split leaves a practice at
about break-even. So the recruiting edge lasts exactly as long as the agents' work stays better than a rival's.

| Advantage | Type | Strength in Hong Kong | What the pilot can show |
|---|---|---|---|
| Cover rota between doctors' lines | Network effect, same side | Real but local; saturates at about 5–10 doctors a rota | Response times and member churn while a doctor is away |
| Referrals between GPs and specialists on the platform | Network effect, cross side | Weak to medium; referral habits already exist | Share of referrals kept in the network |
| Learning from every signed, edited or rejected draft | Data scale economy | Strong early, then flattens; helps evals more than drafts | Edit rate per draft falling over time |
| Buying medicines at volume | Scale economy | Medium; generics have many suppliers | Wholesale price per basket against independents |
| Patients sharing the line in family chats | Word of mouth | Possibly strong in a WhatsApp city; unproven | New members per member; family members per member |
| The patient's history on the line | Switching cost | Medium: patients follow their doctor, not a brand | Retention when a doctor changes clinic |
| Doctors' panels on the platform | Switching cost | Kept low on purpose, since doctors can export. Loyalty has to come from the agents' work | Doctors still active after 12 months |

**Doctor supply is the real constraint, and pay per hour is the lever:**

- HK$1,640 an hour in an AI-native practice and about HK$3,000 an hour on a line, against about HK$955 in a session.
- **Who moves first:** locums and sessional doctors with no panel of their own, doctors returning to practice,
  part-timers, and newly qualified doctors who want a panel without a lease.
- **How many are needed:** the base case needs about 20 full-time doctors in practices and 600 on the platform by year
  five. That's about 1 in 7 of the 4,173 doctors in the Primary Care Directory: ambitious. The bear case (150) is about
  1 in 28.

**Moat verdict,** strongest first:

1. **Cost position.** No lease and no front desk let tmrw pay doctors more and charge patients less at the same time.
2. **Measured trust.** Evidence on every item, and miss rates measured before patients.
3. **Doctor loyalty,** earned by income they can't get elsewhere.

The network effects help at the margin. They shouldn't be in the pitch.

## Scalability, and the five-year build

The base case builds the HK$100m-a-year business from spec 003: practices tmrw runs or co-owns first, then medicines,
with the platform and consulting on top. Hong Kong only, no venture round. HK$m; medicines at gross sales.

| Base case | Year 1 | Year 2 | Year 3 | Year 4 | Year 5 |
|---|---|---|---|---|---|
| Practices | 7.3 | 23.9 | 47.4 | 72.4 | 94.6 |
| Medicines | 1.0 | 6.1 | 16.5 | 28.4 | 39.3 |
| Platform | 0.5 | 3.2 | 9.4 | 16.5 | 22.9 |
| Consulting | 2.4 | 4.8 | 6.0 | 7.2 | 8.4 |
| **Revenue** | **11.2** | **38.0** | **79.3** | **124.5** | **165.2** |
| **EBITDA** | −1.3 (−12%) | 2.6 (7%) | 8.0 (10%) | 15.6 (13%) | 22.6 (14%) |
| Full-time doctors in practices / doctors on the platform (end of year) | 3 / 20 | 7 / 120 | 12 / 300 | 16 / 450 | 20 / 600 |

**How it gets there:**

- **Run-rate passes HK$100m a year around month 36**, the end of year 3, as spec 003 planned.
- **Peak cash need is about HK$3m**: HK$1.5m of set-up (licences, legal opinions, pharmacy) plus early losses. Each
  new doctor needs about HK$0.1m and pays it back in about 8 months as their line ramps up.
- **Doctors earn HK$57m in year 5** across about 20 full-time practice doctors. Platform doctors keep their line income
  themselves; it isn't in tmrw's revenue.

**What scales, and what doesn't:**

- **Linear:** doctors (the binding input), rooms, nurses, and pharmacists as refills grow.
- **Sublinear:** the AI back office (compute and tools are about 1.4% of a practice's revenue), evals, procurement and
  brand.
- **Operating leverage is modest by design.** EBITDA rises from 10% in year 3 to 14% in year 5 as head-office costs
  spread, but a practice tops out near 12% because doctors get 60%. The margin is a choice: every 5 points of split is
  worth about HK$23k per doctor-month.

**What limits the pace:**

- **Recruiting doctors:** 600 on the platform by year 5 is about 1 in 7 directory doctors.
- **Rules:** clinic licences under Cap. 633 for each premises tmrw runs; a pharmacy licence for the medicines layer;
  Medical Council rules on advertising and fee-sharing.
- **Management:** Thomas moving from builder to operator, and a medical director by year 2.

**The Hong Kong ceiling:** private GP visits are worth HK$2.5–3.6bn a year, and new continuing-care demand perhaps
HK$1.5–1.9bn (spec 003). HK$100m a year is about 2–3% of that: a realistic share. The HK$1.2bn ceiling would be a
quarter or more: not reachable from Hong Kong GP care alone. It needs specialists, insurer contracts, or a second
market such as Melbourne or Malaysia.

## Scenarios and sensitivities

| | Bear | Base | Bull |
|---|---|---|---|
| **Between-visit demand per doctor** | 8 tasks a day at HK$150; 80 members at HK$200 | 20 tasks at HK$200; 200 members at HK$300 | 25 tasks at HK$220; 300 members at HK$300 |
| **Doctors by year 5** | 10 in practices, 150 on the platform | 20 and 600 | 40 and 1,000 (needs a second market) |
| **Revenue, year 3 → year 5** | HK$30m → HK$53m | HK$79m → HK$165m | HK$150m → HK$367m |
| **EBITDA, year 5** | About zero | HK$23m (14%) | HK$64m (17%) |
| **Peak cash need** | HK$10m | HK$3m | HK$3m |
| **HK$100m a year** | Not reached | Year 4 | Year 3 |

**The bear case isn't fatal, but a flat 60% split makes it a cost centre.** Per doctor, weak lines leave HK$19k a
month: no better than a conventional group clinic, for more work. Cutting the split to 52% restores HK$47k per doctor
(and HK$3m of group EBITDA in year 5), while doctors still earn 18% more than in a session.

**Sensitivity of operator EBITDA per AI-native doctor-month** (base HK$58.2k; each input moved on its own; HK$k):

| Input (against / in favour) | Against | In favour |
|---|---|---|
| Doctor share of fees (65% / 55%) | −22.8 | +22.8 |
| Paid tasks a day (10 / 30) | −16.5 | +16.5 |
| Visits a day (30 / 40) | −11.5 | +11.5 |
| Members per doctor (100 / 300) | −11.2 | +11.2 |
| Room rent per session (HK$900 / 400) | −13.2 | +8.8 |
| Price per task (HK$150 / 250) | −8.2 | +8.2 |
| Nurse cost a month (HK$40k / 25k) | −10.5 | +5.2 |
| Membership price (HK$200 / 400) | −7.5 | +7.5 |
| AI cost per active patient a month (HK$15 / 1) | −11.0 | +3.0 |

**What the sensitivities say:**

- **No single input breaks a mature practice.** The combination in the bear case does, at 60%.
- **The split is the biggest lever, and it's set by tmrw.** That makes it the right tool for managing risk.
- **Demand for paid tasks matters more than their price.** Volume is the thing to measure first.
- **AI cost is a small risk.** Even at HK$15 per active patient a month, nearly 4x the base, it costs HK$11k per
  doctor-month.

**Recommendation: split by line, not one number.** Pay the market rate on visits (50%) and 70% of what the doctor's
line earns. In the base case the doctor gets HK$258k (+67% on a session) and tmrw HK$74k. With weak lines, the doctor
still gets HK$184k (+19%) and tmrw HK$45k. It protects the downside, and it tells doctors exactly what they're paid
for: the work between visits.

## Risks, and what the pilot must prove

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Patients won't pay between visits** | The public clinic charges HK$50 with medicines, and employer plans HK$0–50. Without paid tasks and members, the model is a conventional clinic with a higher split | Start with people who already pay for time and access: working professionals with a chronic condition, families managing a parent's care, expats, longevity clients. Safety is never charged for |
| **Rules** | Medical Council rules on fee-sharing, advertising and telemedicine; clinic licences under Cap. 633; a pharmacy licence under Cap. 138; PDPO for WhatsApp data and any model that processes it | Written legal opinions before scaling. The doctor is the merchant; tmrw charges a subscription, not a share of fees. Partner pharmacies before owning one |
| **Host clinics see poaching** | A doctor's line can pull return visits and dispensing away from the clinic that hosts the sessions | Clinic mode: the host keeps return visits and dispensing, and shares in line revenue. Start where tmrw is a partner (Adrian's FPC, EC) |
| **A safety incident** | One missed red flag in an AI-drafted reply could end the business | Agents can open and escalate, never close or reassure; verbatim evidence on every item; evals in LoopBench before patients; the doctor signs everything; a medical director; indemnity cover |
| **Doctors leave with their line** | The doctor holds the relationship by design | The two-part split, a cover rota, and an income they can't get elsewhere. Export stays easy, so loyalty has to be earned |
| **Platform risk** | WhatsApp has banned general-purpose AI chatbots since 15 Jan 2026 and can reprice | Stay business-specific and doctor-signed; keep the record in tmrw, not in WhatsApp; have an SMS or app fallback |
| **Competitors copy it** | EC or a chain adds scribes and WhatsApp; Apricot's VITA adds paid messaging; Heidi adds agents | Speed, and an operating model they can't copy without dropping leases and splits. Evals as proof of quality |
| **Key person** | Thomas is a new doctor, the builder and the operator | A medical director and an operations lead by year 2; Josie and Adrian as early partners |
| **Weak consumer spending** | EC blamed spending in the mainland and weak local sentiment for its profit warning | Chronic and family care is less discretionary than aesthetics. Keep costs variable |

**What the first 90 days must prove** (from Thomas's own sessions and Josie's):

| Measure | Stop | Bear | Base |
|---|---|---|---|
| Patients seen who join the line | Under 10% | 20% | 30%+ |
| Paid tasks per line patient a month | Under 0.05 | 0.1 | 0.3 |
| Price accepted per task | Under HK$100 | HK$150 | HK$200 |
| Chronic patients who take a membership | Under 3% | 5–10% | 15%+ |
| Doctor-minutes per task (median) | Over 10 | 5 | 3–4 |
| Safety | Any missed red flag | None | None, with miss rates measured |
| Doctors running a line | Thomas only | Thomas and Josie | Plus one host clinic signed |

**If the stop column comes true,** the practice plan stops and the fallback is clinic mode: software for groups like
EC and Apricot's clinics, or joining Heidi. If base numbers hold, the first tmrw practice opens with Adrian.

## Assumptions and sources

**Per full-time doctor, per month, mature:**

- 22 working days; 35 visits a day at HK$400, the median non-specialist fee.
- HK$58 of medicine inside each visit: Human Health's medicines cost, 14.4% of revenue.
- **Conventional clinics:** two staff at about HK$19k plus MPF; premises HK$35k, close to Human Health's 11% of revenue
  for depreciation and leases; other costs HK$13.5–15k. Group clinics pay doctors 50% (Josie); the listed chain 36%
  (Human Health, including dentists).
- **AI-added clinic:** 37 visits a day from a scribe and recalls, 9 doctor-minutes a visit, HK$4k of software.
- **AI-native practice:**
  - 20 paid tasks a day at HK$200, 4 doctor-minutes each; 200 members at HK$300
  - 150 refill baskets a month at HK$250 (cost HK$170, delivery HK$25); visit medicines 10% cheaper bought wholesale
  - one nurse at HK$30k plus MPF; two room sessions a day at HK$600
  - compute at HK$4 per active patient a month (1,000 active) plus HK$3k of tools; card fees 2.5% (1% on visits);
    HK$20k for indemnity, medical-director time and marketing
- **Doctor's own line:** 10 tasks a day and 100 members; platform fee HK$2.5k plus HK$10 a task; about 400 active
  patients.

**Five-year build:**

- New doctors' visits ramp from 60% to 100% over 6 months, and their line revenue from 20% to 100% over 12 months.
- HK$0.1m per new doctor; half a month of medicine stock; HK$1.5m of set-up in year 1.
- Head-office costs from HK$0.25m a month in year 1 to HK$0.9m in year 5; consulting at a 60% margin.
- The average platform doctor runs half a busy line: 5 tasks a day, 50 members, 20 refills a month.
- **Not modelled:** tax, a second market, insurer or employer contracts, Co-care payments, and specialist referrals.

**Sources:**

- Human Health Holdings, [FY2025 results](https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0925/2025092502096.pdf) (HKEX, 25 Sep 2025)
- EC Healthcare: [FY25 revenue](https://www.itiger.com/hans/news/2544513604) (Tiger, citing Reuters);
  [profit warning](https://filingreader.com/news-wire/hongkong/2025-11-24/ec-healthcare-projects-significant-profit-and-revenue-decline) (24 Nov 2025)
- Lam DTY, Guo M. [Doctors' fees in private practice](https://www.hkmj.org/system/files/hkmj2513816.pdf). *HKMJ* 2026
- [Doctor numbers](https://www.chinadailyhk.com/hk/article/636945) (China Daily HK, 26 Jul 2026)
- Health Bureau: [Co-care Network](https://www.info.gov.hk/gia/general/202610/07/P2026100700411.htm);
  [CDCC subsidy and co-payment](https://www.info.gov.hk/gia/general/202309/25/P2023092500476p.htm)
- [Public clinic fees](https://www.scmp.com/news/hong-kong/health-environment/article/3302288/are-hongkongers-paying-too-little-healthcare-breaking-down-costs) (SCMP);
  [network doctors](https://www.bowtie.com.hk/blog/zh/%E7%B6%B2%E7%B5%A1%E9%86%AB%E7%94%9F-%E9%9D%9E%E7%B6%B2%E7%B5%A1%E9%86%AB%E7%94%9F/) (Bowtie);
  [video consults](https://www.qhms.com/en/promo/video-consultation) (QHMS)
- [State of direct primary care 2026](https://blog.hint.com/state-of-dpc-2026-key-takeaways-from-the-dpc-alliances-physician-survey) (Hint)
- Thematic Household Survey 2022–23 and the Primary Healthcare Blueprint (Health Bureau)
- Spec 003, the care line; signals #15 (Apricot) and #17 (Josie)
