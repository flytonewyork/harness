import type { CrisisPayload } from "../api";

interface CrisisScreenProps {
  crisis: CrisisPayload;
  lang: "en" | "zh-HK";
  onAcknowledge: () => void;
}

const DEFAULT_MH_HOTLINES = [
  { name_en: "Samaritan Befrienders Hong Kong (24h)", name_zh: "香港撒瑪利亞防止自殺會（24小時）", tel: "23892222", display: "2389 2222" },
  { name_en: "The Samaritans (24h Multilingual)", name_zh: "撒瑪利亞會（24小時多語種）", tel: "28960000", display: "2896 0000" },
  { name_en: "Suicide Prevention Services (24h)", name_zh: "生命熱線（24小時）", tel: "23820000", display: "2382 0000" },
];

export function CrisisScreen({ crisis, lang, onAcknowledge }: CrisisScreenProps) {
  const isZh = lang === "zh-HK";
  const isMentalHealth = crisis.kind === "mental_health_crisis";
  const hotlines = crisis.hotlines && crisis.hotlines.length > 0 ? crisis.hotlines : DEFAULT_MH_HOTLINES;

  const clinicStatus =
    crisis.pager_acked && crisis.pager_acked_at
      ? isZh
        ? `${crisis.paged_clinician || "當值醫生"} 已於 ${crisis.pager_acked_at} 收到緊急通知。`
        : `${crisis.paged_clinician || "Duty clinician"} was paged at ${crisis.pager_acked_at}.`
      : isZh
        ? "我們亦正通知診所，但請勿等待我們回電。"
        : "We are also telling the clinic. Do not wait for us to call.";

  if (isMentalHealth) {
    return (
      <div className="v2-crisis-screen" role="alertdialog" aria-modal="true" data-testid="crisis-mental-health">
        <div className="v2-crisis-inner">
          <h1 className="v2-decision-title">
            {isZh ? "你不需要獨自承受，現在就有人可以傾聽你。" : "You do not have to carry this alone. Support is available right now."}
          </h1>
          <p className="voice-assistant">
            {isZh
              ? "如你或身邊的人正面臨即時危險，請立即致電 999。"
              : "If you or anyone nearby is in immediate physical danger, call 999 now."}
          </p>

          <a href="tel:999" className="v2-crisis-call">
            {isZh ? "立即致電 999" : "Call 999"}
          </a>

          <div className="v2-sec">
            <div className="v2-sec-label">{isZh ? "香港 24 小時免費支援熱線" : "Hong Kong 24-hour support lines"}</div>
            <div className="v2-card-flat">
              {hotlines.map((h) => (
                <div key={h.tel} className="v2-summary-item v2-order-head">
                  <span>{isZh ? h.name_zh : h.name_en}</span>
                  <a href={`tel:${h.tel}`} className="v2-btn-secondary">
                    {h.display}
                  </a>
                </div>
              ))}
            </div>
          </div>

          <p className="voice-assistant">{clinicStatus}</p>

          <button type="button" className="v2-text-btn" onClick={onAcknowledge} data-testid="crisis-ack-btn">
            {isZh ? "我明白，返回訊息頁" : "I understand, return to timeline"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="v2-crisis-screen" role="alertdialog" aria-modal="true" data-testid="crisis-medical-999">
      <div className="v2-crisis-inner">
        <h1 className="v2-decision-title">
          {isZh ? "這些症狀需要即時緊急醫療評估。" : "These symptoms need immediate emergency care."}
        </h1>
        <a href="tel:999" className="v2-crisis-call">
          {isZh ? "致電 999" : "Call 999"}
        </a>
        <p className="voice-assistant">
          {isZh ? "或立即前往最近的急症室（A&E）。" : "or go to the nearest A&E."}
        </p>

        <div className="v2-card-flat">
          <div className="voice-assistant">{clinicStatus}</div>
        </div>

        <div className="v2-summary-item">
          <button type="button" className="v2-text-btn" onClick={onAcknowledge} data-testid="crisis-ack-btn">
            {isZh ? "我已了解，返回主頁" : "I understand, go back"}
          </button>
        </div>
      </div>
    </div>
  );
}
