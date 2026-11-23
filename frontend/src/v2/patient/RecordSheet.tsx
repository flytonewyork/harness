import { v2Api } from "../api";
import type { CompanionHome } from "../api";
import { Sheet } from "../shared/Sheet";

interface RecordSheetProps {
  open: boolean;
  isZh: boolean;
  home: CompanionHome;
  token: string;
  onClose: () => void;
  onUpdated: () => Promise<void>;
}

export function RecordSheet({ open, isZh, home, token, onClose, onUpdated }: RecordSheetProps) {
  const openCertPdf = async (certId: string) => {
    const res = await v2Api.certDownloadToken(certId, token);
    await v2Api.downloadVaultPdf(res.download_url, token, `HK_Medical_Certificate_${certId}.pdf`);
  };

  return (
    <Sheet open={open} title={isZh ? "你的病歷紀錄" : "Your record"} onClose={onClose}>
      <div className="v2-sec">
        <div className="v2-sec-label">{isZh ? "藥物及過敏史" : "Medicines & allergies"}</div>
        <div className="v2-card-flat">
          <div>
            <strong>{isZh ? "現正服用：" : "Active medicines: "}</strong>
            {home.record.current_meds.length ? home.record.current_meds.join(", ") : isZh ? "無" : "None"}
          </div>
          <div>
            <strong>{isZh ? "藥物過敏：" : "Allergies: "}</strong>
            {home.record.allergies.length ? home.record.allergies.join(", ") : isZh ? "無紀錄" : "None recorded"}
          </div>
          <div>
            <strong>G6PD: </strong>
            {home.record.g6pd_deficient ? (isZh ? "缺乏（蠶豆症）" : "Deficient") : isZh ? "正常" : "Normal"}
          </div>
        </div>
      </div>

      <div className="v2-sec">
        <div className="v2-sec-label">{isZh ? "醫生簽發文件及處方" : "Signed certificates & prescriptions"}</div>
        <div className="v2-card-flat">
          {home.record.certificates.map((c) => (
            <div key={c.id} className="v2-summary-item v2-order-head">
              <span className="voice-doctor">
                Sick leave {c.start_date} – {c.end_date} ({c.days_off}d) · MCHK {c.mchk_reg_no}
              </span>
              <button type="button" className="v2-btn-secondary" onClick={() => openCertPdf(c.id)} data-testid={`cert-pdf-${c.id}`}>
                PDF
              </button>
            </div>
          ))}
          {home.record.prescriptions.map((r) => (
            <div key={r.id} className="v2-summary-item">
              <div className="voice-doctor">
                e-Script ({r.fulfilment_status}) · MCHK {r.mchk_reg_no}
              </div>
              {r.fulfilment_mode === "pending_choice" && (
                <div className="v2-composer-row">
                  <button
                    type="button"
                    className="v2-btn-secondary"
                    data-testid={`rx-pickup-${r.id}`}
                    onClick={async () => {
                      await v2Api.chooseFulfilment(r.id, "pickup", "", token);
                      await onUpdated();
                    }}
                  >
                    Clinic pickup
                  </button>
                  <button
                    type="button"
                    className="v2-btn-secondary"
                    data-testid={`rx-delivery-${r.id}`}
                    onClick={async () => {
                      await v2Api.chooseFulfilment(r.id, "delivery", "Central, Hong Kong", token);
                      await onUpdated();
                    }}
                  >
                    Courier delivery
                  </button>
                </div>
              )}
            </div>
          ))}
          {home.record.certificates.length === 0 && home.record.prescriptions.length === 0 && (
            <div className="voice-assistant">{isZh ? "暫未有簽發文件。" : "No signed documents yet."}</div>
          )}
        </div>
      </div>

      <div className="v2-sec">
        <div className="v2-sec-label">{isZh ? "帳戶及香港個人資料私隱" : "Account & Hong Kong PDPO Privacy"}</div>
        <div className="v2-card-flat voice-assistant">
          <div>
            {home.patient.name_en} · HKID {home.patient.hkid_masked} · {home.patient.phone_masked}
          </div>
          <div>
            {isZh
              ? "資料儲存於香港（asia-east2）。醫療紀錄須按香港法例（第347章）保留。"
              : "Data stored in Hong Kong (asia-east2). Statutory medical record retention applies (Cap. 347)."}
          </div>
        </div>
      </div>
    </Sheet>
  );
}
