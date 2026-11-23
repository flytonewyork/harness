import type { ProposalDetail } from "../api";
import { Sheet } from "../shared/Sheet";

interface PatientSheetProps {
  open: boolean;
  detail: ProposalDetail | null;
  onClose: () => void;
}

export function PatientSheet({ open, detail, onClose }: PatientSheetProps) {
  return (
    <Sheet open={open} title={`${detail?.patient?.name_en || "Patient"} · Longitudinal record`} onClose={onClose}>
      {detail?.patient && (
        <div>
          <div className="v2-sec">
            <div className="v2-sec-label">Active medications</div>
            <div className="v2-card-flat">
              {(detail.patient.current_meds || []).length === 0
                ? "None recorded"
                : detail.patient.current_meds.map((m, i) => (
                    <div key={i} className="v2-summary-item">
                      {m}
                    </div>
                  ))}
            </div>
          </div>

          <div className="v2-sec">
            <div className="v2-sec-label">Allergies &amp; safety profile</div>
            <div className="v2-card-flat">
              <div>Allergies: {detail.patient.allergies.length ? detail.patient.allergies.join(", ") : "NKDA"}</div>
              <div>G6PD: {detail.patient.g6pd_deficient ? "Deficient" : "Normal"}</div>
              <div>eGFR: {detail.patient.egfr ?? "Unknown (not on file)"}</div>
            </div>
          </div>

          <div className="v2-sec">
            <div className="v2-sec-label">Recent messages ({detail.messages.length})</div>
            <div className="v2-card-flat">
              {detail.messages.map((m) => (
                <div key={m.id} className="v2-summary-item">
                  <div className="voice-quote">{m.sender_role}</div>
                  <div className={m.sender_role === "clinician" ? "voice-doctor" : "voice-assistant"}>{m.body_text}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </Sheet>
  );
}
