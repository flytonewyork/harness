import type { UnifiedCheck } from "../api";

interface OrderListProps {
  orders: Array<Record<string, any>>;
  checks: UnifiedCheck[];
  suggestedCert?: { days_off: number; start_date: string } | null;
  isClinician: boolean;
  renalDose: "75" | "30";
  issueCert: boolean;
  overrideReason: string;
  onChangeRenalDose: (dose: "75" | "30") => void;
  onToggleCert: (checked: boolean) => void;
  onChangeOverrideReason: (val: string) => void;
}

export function OrderList({
  orders,
  checks,
  suggestedCert,
  isClinician,
  renalDose,
  issueCert,
  overrideReason,
  onChangeRenalDose,
  onToggleCert,
  onChangeOverrideReason,
}: OrderListProps) {
  const warningsNeedingReason = checks.filter((c) => c.status === "warn" && c.requires_reason);
  const hasRenalChoice = checks.some((c) => c.id === "renal_unknown_elderly");

  const findingsForOrder = (drug: string) =>
    checks.filter((c) => (c.status === "warn" || c.status === "fail") && c.affects === `order:${drug.toLowerCase()}`);

  return (
    <section className="v2-sec">
      <div className="v2-sec-label">What we&apos;ll do · Orders</div>
      <div className="v2-card-flat">
        {orders.map((rx, i) => {
          const drug = String(rx.drug || rx.name || "");
          const inlineChecks = findingsForOrder(drug);
          const label =
            rx.display ||
            `${drug.charAt(0).toUpperCase() + drug.slice(1)} ${rx.dose_mg ? `${rx.dose_mg} mg` : ""} ${rx.frequency || ""} ${rx.days ? `× ${rx.days}d` : ""}`.trim();
          return (
            <div key={i} className="v2-order-row">
              <div className="v2-order-head">
                <span>
                  {drug.toLowerCase() === "oseltamivir" && renalDose === "30"
                    ? "Oseltamivir 30 mg BD × 5 days (renal)"
                    : label}
                </span>
                {rx.frequency === "STOP" && <span className="voice-quote">stop repeat</span>}
              </div>
              {inlineChecks.map((ck) => (
                <div key={ck.id} className={ck.status === "fail" ? "v2-inline-fail" : "v2-inline-warn"}>
                  <div>{ck.label}</div>
                  {ck.quick_choice && isClinician && (
                    <div className="v2-composer-row">
                      <button
                        type="button"
                        className={renalDose === "75" ? "v2-btn-primary" : "v2-btn-secondary"}
                        onClick={() => onChangeRenalDose("75")}
                      >
                        {ck.quick_choice.keep}
                      </button>
                      <button
                        type="button"
                        className={renalDose === "30" ? "v2-btn-primary" : "v2-btn-secondary"}
                        onClick={() => onChangeRenalDose("30")}
                      >
                        {ck.quick_choice.reduce}
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          );
        })}

        {suggestedCert && (
          <div className="v2-order-row v2-order-head">
            <label>
              <input
                type="checkbox"
                checked={issueCert}
                onChange={(e) => onToggleCert(e.target.checked)}
                disabled={!isClinician}
              />{" "}
              Sick-leave certificate: {suggestedCert.days_off} days from {suggestedCert.start_date}
            </label>
            <span className="voice-quote">MCHK PDF</span>
          </div>
        )}

        {isClinician && warningsNeedingReason.length > 0 && !hasRenalChoice && (
          <input
            className="v2-reason-input"
            placeholder="Clinical reason for override (required, never pre-filled)…"
            value={overrideReason}
            onChange={(e) => onChangeOverrideReason(e.target.value)}
            data-testid="override-reason-input"
          />
        )}
      </div>
    </section>
  );
}
