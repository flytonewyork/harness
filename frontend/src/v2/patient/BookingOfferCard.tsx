import { useEffect, useMemo, useState } from "react";
import {
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  MapPin,
  MessageSquare,
  Video,
} from "lucide-react";

export type BookingModality = "async_message" | "teleconsult" | "in_clinic";

export interface AvailabilitySlot {
  slot_id: string;
  day_key: "today" | "tomorrow";
  day_label_en: string;
  day_label_zh: string;
  short_day_en: string;
  short_day_zh: string;
  time: string;
  available: boolean;
  state: "open" | "booked" | "booked_by_you";
}

export interface DoctorAvailability {
  doctor_id: string;
  name_en: string;
  name_zh: string;
  initials: string;
  mchk_reg_no: string;
  specialty_en: string;
  specialty_zh: string;
  languages: string;
  room_en: string;
  room_zh: string;
  open_today_count: number;
  open_total_count: number;
  days: {
    today: AvailabilitySlot[];
    tomorrow: AvailabilitySlot[];
  };
}

export interface RecommendedBooking {
  doctor_id: string;
  doctor_name: string;
  doctor_name_zh: string;
  initials: string;
  mchk_reg_no: string;
  specialty_en: string;
  specialty_zh: string;
  languages: string;
  room_en: string;
  room_zh: string;
  slot_id: string;
  day_key: "today" | "tomorrow";
  day_label_en: string;
  day_label_zh: string;
  short_day_en: string;
  short_day_zh: string;
  time: string;
}

export interface BookingOfferPayload {
  offer_id: string;
  status: "offered" | "confirmed";
  booking_ref?: string | null;
  confirmed_at?: string | null;
  checked_doctors_count: number;
  open_today_count: number;
  busy_today_count?: number;
  checked_summary_en: string;
  checked_summary_zh: string;
  visit_type_en: string;
  visit_type_zh: string;
  duration_min: number;
  modality: BookingModality;
  allow_async_message?: boolean;
  allow_teleconsult: boolean;
  location_en: string;
  location_zh: string;
  prep_note_en?: string;
  prep_note_zh?: string;
  selection_reason_en: string;
  selection_reason_zh: string;
  recommended: RecommendedBooking;
  doctors: DoctorAvailability[];
}

interface BookingOfferCardProps {
  offer: BookingOfferPayload;
  isZh: boolean;
  onConfirmBooking: (params: {
    doctorId: string;
    slotId: string;
    modality: BookingModality;
    offerId: string;
  }) => Promise<void>;
}

function normalizeModality(m: string | undefined): BookingModality {
  if (m === "async_message") return "async_message";
  if (m === "teleconsult") return "teleconsult";
  return "in_clinic";
}

export function BookingOfferCard({ offer, isZh, onConfirmBooking }: BookingOfferCardProps) {
  const [selectedDoctorId, setSelectedDoctorId] = useState<string>(offer.recommended.doctor_id);
  const [selectedSlotId, setSelectedSlotId] = useState<string>(offer.recommended.slot_id);
  const [selectedDay, setSelectedDay] = useState<"today" | "tomorrow">(offer.recommended.day_key || "today");
  const [modality, setModality] = useState<BookingModality>(normalizeModality(offer.modality));
  const [showAllDoctors, setShowAllDoctors] = useState<boolean>(false);
  const [rescheduling, setRescheduling] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // Sync local selection if backend updates the offer (e.g. after confirmation or poll refresh)
  useEffect(() => {
    setSelectedDoctorId(offer.recommended.doctor_id);
    setSelectedSlotId(offer.recommended.slot_id);
    setSelectedDay(offer.recommended.day_key || "today");
    setModality(normalizeModality(offer.modality));
    if (offer.status === "confirmed") {
      setRescheduling(false);
    }
  }, [offer.recommended.doctor_id, offer.recommended.slot_id, offer.recommended.day_key, offer.modality, offer.status]);

  const doctors = offer.doctors || [];

  const activeSelection = useMemo(() => {
    const doc = doctors.find((d) => d.doctor_id === selectedDoctorId);
    if (doc) {
      const allSlots = [...(doc.days?.today || []), ...(doc.days?.tomorrow || [])];
      const slot = allSlots.find((s) => s.slot_id === selectedSlotId);
      if (slot) {
        return {
          doctorId: doc.doctor_id,
          doctorName: isZh ? doc.name_zh : doc.name_en,
          doctorNameEn: doc.name_en,
          initials: doc.initials,
          mchk: doc.mchk_reg_no,
          specialty: isZh ? doc.specialty_zh : doc.specialty_en,
          languages: doc.languages,
          room: isZh ? doc.room_zh : doc.room_en,
          slot,
          daySlots: doc.days?.[selectedDay] || [],
        };
      }
    }
    const fallbackSlot: AvailabilitySlot = {
      slot_id: offer.recommended.slot_id,
      day_key: offer.recommended.day_key || "today",
      day_label_en: offer.recommended.day_label_en,
      day_label_zh: offer.recommended.day_label_zh,
      short_day_en: offer.recommended.short_day_en,
      short_day_zh: offer.recommended.short_day_zh,
      time: offer.recommended.time,
      available: true,
      state: "open",
    };
    return {
      doctorId: offer.recommended.doctor_id,
      doctorName: isZh ? offer.recommended.doctor_name_zh : offer.recommended.doctor_name,
      doctorNameEn: offer.recommended.doctor_name,
      initials: offer.recommended.initials,
      mchk: offer.recommended.mchk_reg_no,
      specialty: isZh ? offer.recommended.specialty_zh : offer.recommended.specialty_en,
      languages: offer.recommended.languages,
      room: isZh ? offer.recommended.room_zh : offer.recommended.room_en,
      slot: fallbackSlot,
      daySlots: doc?.days?.[selectedDay] || [fallbackSlot],
    };
  }, [doctors, selectedDoctorId, selectedSlotId, selectedDay, isZh, offer.recommended]);

  const isRecommendedPick =
    activeSelection.doctorId === offer.recommended.doctor_id &&
    activeSelection.slot.slot_id === offer.recommended.slot_id;

  const isConfirmedView = offer.status === "confirmed" && !rescheduling;

  const handleSelectDoctorSlot = (docId: string, slot: AvailabilitySlot) => {
    if (!slot.available) return;
    setLocalError(null);
    setSelectedDoctorId(docId);
    setSelectedSlotId(slot.slot_id);
    setSelectedDay(slot.day_key);
  };

  const handleConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    setLocalError(null);
    try {
      await onConfirmBooking({
        doctorId: activeSelection.doctorId,
        slotId: activeSelection.slot.slot_id,
        modality,
        offerId: offer.offer_id,
      });
      setRescheduling(false);
      setShowAllDoctors(false);
    } catch (e: any) {
      setLocalError(e?.message || (isZh ? "未能確認時段，請選擇其他空檔。" : "Could not confirm that slot. Please try another open time."));
    } finally {
      setSubmitting(false);
    }
  };

  const isAsyncMsg = modality === "async_message";

  const locationLabel = isAsyncMsg
    ? isZh
      ? "對話內非同步醫生審閱・即時意見（無需親臨診所）"
      : "In-Chat Async Doctor Review · Instant Opinion"
    : modality === "teleconsult"
      ? isZh
        ? "加密診所視像連結（診症前 10 分鐘發送）"
        : "Secure Clinic Video Link (sent 10 min before)"
      : isZh
        ? `中環及半山家庭醫務中心（${activeSelection.room}）`
        : `Central & Mid-Levels Family Medical (${activeSelection.room})`;

  // Confirmed Appointment / Async Review Card
  if (isConfirmedView) {
    return (
      <div className="pc-book-card is-confirmed" data-testid="chat-booking-confirmed-card">
        <div className="pc-book-confirmed-head">
          <span className="pc-book-confirmed-badge">
            <CheckCircle2 size={14} />
            <span>
              {isAsyncMsg
                ? isZh
                  ? "已確認訊息審閱"
                  : "Async Review Confirmed"
                : isZh
                  ? "預約已確認"
                  : "Appointment Confirmed"}
            </span>
          </span>
          {offer.booking_ref && <span className="pc-book-ref">{offer.booking_ref}</span>}
        </div>

        <div className="pc-book-doc-row">
          <div className="pc-book-avatar is-confirmed" aria-hidden="true">
            {activeSelection.initials}
          </div>
          <div className="pc-book-doc-meta">
            <div className="pc-book-doc-name">{activeSelection.doctorName}</div>
            <div className="pc-book-doc-sub">
              MCHK {activeSelection.mchk} · {isZh ? offer.visit_type_zh : offer.visit_type_en}
            </div>
          </div>
        </div>

        <div className="pc-book-detail-list">
          {isAsyncMsg ? (
            <div className="pc-book-detail-item">
              <Clock size={14} />
              <span>{isZh ? "非同步即時意見 · 一般約 15 分鐘內回覆" : "Async Instant Opinion · Typical reply ~15 min"}</span>
            </div>
          ) : (
            <div className="pc-book-detail-item">
              <Calendar size={14} />
              <span className="pc-book-tabular">
                {isZh ? activeSelection.slot.day_label_zh : activeSelection.slot.day_label_en} ·{" "}
                <strong>{activeSelection.slot.time} HKT</strong> ({offer.duration_min} {isZh ? "分鐘" : "min"})
              </span>
            </div>
          )}
          <div className="pc-book-detail-item">
            {isAsyncMsg ? <MessageSquare size={14} /> : modality === "teleconsult" ? <Video size={14} /> : <MapPin size={14} />}
            <span>{locationLabel}</span>
          </div>
        </div>

        {(isZh ? offer.prep_note_zh : offer.prep_note_en) && (
          <div className="pc-book-prep">
            {isZh ? offer.prep_note_zh : offer.prep_note_en}
          </div>
        )}

        <div className="pc-book-confirmed-foot">
          <span className="pc-book-sync-note">
            {isAsyncMsg
              ? isZh
                ? "已放上醫生工作台審閱"
                : "Queued on clinician desk"
              : isZh
                ? "已同步至診所時間表"
                : "Synced to clinic schedule"}
          </span>
          <button
            type="button"
            className="pc-book-link-btn"
            onClick={() => {
              setRescheduling(true);
              if (!isAsyncMsg) setShowAllDoctors(true);
            }}
            data-testid="booking-change-time-btn"
          >
            {isAsyncMsg
              ? isZh
                ? "改選視像／親臨診所"
                : "Switch to Teleconsult / In-Clinic"
              : isZh
                ? "更改醫生或時間"
                : "Change doctor or time"}
          </button>
        </div>
      </div>
    );
  }

  // Active Agent Booking Offer Card
  const shortDay = isZh ? activeSelection.slot.short_day_zh : activeSelection.slot.short_day_en;
  const ctaLabel = submitting
    ? isZh
      ? "正在確認…"
      : "Confirming…"
    : isAsyncMsg
      ? isZh
        ? `確認訊息審閱（最快） · ${activeSelection.doctorName}`
        : `Confirm Message (Fastest) · ${activeSelection.doctorName}`
      : rescheduling
        ? isZh
          ? `改期至 ${shortDay} ${activeSelection.slot.time} · ${activeSelection.doctorName}`
          : `Update to ${shortDay} ${activeSelection.slot.time} · ${activeSelection.doctorName}`
        : isZh
          ? `確認預約 ${shortDay} ${activeSelection.slot.time} · ${activeSelection.doctorName}`
          : `Book ${shortDay} ${activeSelection.slot.time} · ${activeSelection.doctorName}`;

  const showModalityToggle = Boolean(offer.allow_async_message || offer.allow_teleconsult);

  return (
    <div className="pc-book-card" data-testid="chat-booking-offer-card">
      {/* Top Trace Strip: Shows Sam inspected live clinic availability */}
      <div className="pc-book-trace">
        <div className="pc-book-trace-left">
          <span className="pc-book-trace-dot" aria-hidden="true" />
          <span>{isZh ? offer.checked_summary_zh : offer.checked_summary_en}</span>
        </div>
        {!isAsyncMsg && (
          <button
            type="button"
            className="pc-book-trace-toggle"
            onClick={() => setShowAllDoctors((v) => !v)}
            aria-expanded={showAllDoctors}
            data-testid="booking-toggle-all-doctors-btn"
          >
            <span>
              {showAllDoctors
                ? isZh
                  ? "收起時間表"
                  : "Hide schedule"
                : isZh
                  ? "所有醫生"
                  : "All doctors"}
            </span>
            {showAllDoctors ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        )}
      </div>

      {/* Main Offer Body */}
      <div className="pc-book-body">
        <div className="pc-book-kicker-row">
          <span className="pc-book-kicker">
            {isAsyncMsg
              ? isZh
                ? "Sam 建議方式"
                : "Sam's recommended option"
              : isRecommendedPick
                ? isZh
                  ? "Sam 建議時段"
                  : "Sam's recommended slot"
                : isZh
                  ? "已選診症時段"
                  : "Selected appointment"}
          </span>
          <span className="pc-book-duration">
            {isAsyncMsg
              ? isZh
                ? "即時意見 · 非同步"
                : "Instant Opinion · Async"
              : `${isZh ? offer.visit_type_zh : offer.visit_type_en} · ${offer.duration_min}${isZh ? " 分鐘" : "m"}`}
          </span>
        </div>

        {/* Modality Toggle when Async Message or Teleconsult is clinically permitted */}
        {showModalityToggle && (
          <div className="pc-book-modality" role="group" aria-label="Consultation modality">
            {offer.allow_async_message && (
              <button
                type="button"
                className={`pc-book-mod-btn ${modality === "async_message" ? "is-active" : ""}`}
                onClick={() => {
                  setModality("async_message");
                  setShowAllDoctors(false);
                }}
                data-testid="booking-modality-async-message"
              >
                <MessageSquare size={12} />
                <span>{isZh ? "訊息（最快）" : "Message (Fastest)"}</span>
              </button>
            )}
            {offer.allow_teleconsult && (
              <button
                type="button"
                className={`pc-book-mod-btn ${modality === "teleconsult" ? "is-active" : ""}`}
                onClick={() => setModality("teleconsult")}
                data-testid="booking-modality-teleconsult"
              >
                <Video size={12} />
                <span>{isZh ? "視像診症" : "Teleconsult"}</span>
              </button>
            )}
            <button
              type="button"
              className={`pc-book-mod-btn ${modality === "in_clinic" ? "is-active" : ""}`}
              onClick={() => setModality("in_clinic")}
              data-testid="booking-modality-in-clinic"
            >
              <MapPin size={12} />
              <span>{isZh ? "親臨診所" : "In-Clinic"}</span>
            </button>
          </div>
        )}

        {/* Selected Doctor Identity + Rationale */}
        <div className="pc-book-doc-row">
          <div className="pc-book-avatar" aria-hidden="true">
            {activeSelection.initials}
          </div>
          <div className="pc-book-doc-meta">
            <div className="pc-book-doc-name">{activeSelection.doctorName}</div>
            <div className="pc-book-doc-sub">
              MCHK {activeSelection.mchk} · {activeSelection.languages}
            </div>
            <div className="pc-book-doc-spec">{activeSelection.specialty}</div>
          </div>
        </div>

        {isRecommendedPick && (
          <div className="pc-book-reason">
            {isZh ? offer.selection_reason_zh : offer.selection_reason_en}
          </div>
        )}

        {/* Highlighted Async Status Box OR Selected Time Box */}
        {isAsyncMsg ? (
          <div className="pc-book-slot-summary" data-testid="booking-selected-slot-summary">
            <div className="pc-book-slot-summary-main">
              <MessageSquare size={14} />
              <span>
                {isZh
                  ? "非同步訊息審閱 · 一般約 15 分鐘內回覆"
                  : "Async Instant Opinion · Typical reply ~15 min"}
              </span>
            </div>
            <span className="pc-book-open-pill">{isZh ? "最快" : "Fastest"}</span>
          </div>
        ) : (
          <div className="pc-book-slot-summary" data-testid="booking-selected-slot-summary">
            <div className="pc-book-slot-summary-main">
              <Clock size={14} />
              <span className="pc-book-tabular">
                {isZh ? activeSelection.slot.day_label_zh : activeSelection.slot.day_label_en} ·{" "}
                <strong>{activeSelection.slot.time} HKT</strong>
              </span>
            </div>
            <span className="pc-book-open-pill">{isZh ? "可預約" : "Open"}</span>
          </div>
        )}

        {/* Selected Doctor's Real Schedule Strip (Shown for Teleconsult & In-Clinic) */}
        {!isAsyncMsg && !showAllDoctors && activeSelection.daySlots.length > 0 && (
          <div className="pc-book-quick-wrap">
            <div className="pc-book-quick-label">
              <span>
                {isZh
                  ? `${activeSelection.doctorName}（${shortDay}時間表）`
                  : `${activeSelection.doctorName} · ${shortDay} availability`}
              </span>
              <span className="pc-book-legend">
                <span className="pc-book-legend-busy">{isZh ? "已滿" : "Booked"}</span>
              </span>
            </div>
            <div className="pc-book-slot-grid">
              {activeSelection.daySlots.map((sl) => {
                const isSelected = sl.slot_id === activeSelection.slot.slot_id;
                return (
                  <button
                    key={sl.slot_id}
                    type="button"
                    disabled={!sl.available}
                    className={`pc-slot-pill ${isSelected ? "is-selected" : ""} ${!sl.available ? "is-busy" : ""}`}
                    onClick={() => handleSelectDoctorSlot(activeSelection.doctorId, sl)}
                    title={!sl.available ? (isZh ? "此時段已被預約" : "Already booked") : `${sl.time} HKT`}
                    data-testid={`booking-slot-pill-${sl.slot_id}`}
                  >
                    {isSelected && <Check size={11} />}
                    <span className="pc-book-tabular">{sl.time}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Expanded Live Availability Matrix Across All Clinic Doctors (Today / Tomorrow) */}
        {!isAsyncMsg && showAllDoctors && doctors.length > 0 && (
          <div className="pc-book-matrix" data-testid="booking-all-doctors-matrix">
            <div className="pc-book-day-tabs" role="tablist" aria-label="Select day">
              <button
                type="button"
                role="tab"
                aria-selected={selectedDay === "today"}
                className={`pc-book-day-tab ${selectedDay === "today" ? "is-active" : ""}`}
                onClick={() => setSelectedDay("today")}
                data-testid="booking-day-today"
              >
                {isZh ? "今日 · 11月24日" : "Today · Tue 24 Nov"}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={selectedDay === "tomorrow"}
                className={`pc-book-day-tab ${selectedDay === "tomorrow" ? "is-active" : ""}`}
                onClick={() => setSelectedDay("tomorrow")}
                data-testid="booking-day-tomorrow"
              >
                {isZh ? "明日 · 11月25日" : "Tomorrow · Wed 25 Nov"}
              </button>
            </div>

            <div className="pc-book-doctor-list">
              {doctors.map((doc) => {
                const slots = doc.days?.[selectedDay] || [];
                const openCount = slots.filter((s) => s.available).length;
                const isDocSelected = doc.doctor_id === activeSelection.doctorId;
                return (
                  <div
                    key={doc.doctor_id}
                    className={`pc-book-doc-option ${isDocSelected ? "is-active" : ""}`}
                    data-testid={`booking-doctor-row-${doc.doctor_id}`}
                  >
                    <div className="pc-book-doc-option-head">
                      <div>
                        <div className="pc-book-doc-option-name">
                          {isZh ? doc.name_zh : doc.name_en}
                          {doc.doctor_id === offer.recommended.doctor_id && (
                            <span className="pc-book-rec-tag">{isZh ? "建議" : "Best fit"}</span>
                          )}
                        </div>
                        <div className="pc-book-doc-option-sub">
                          {isZh ? doc.specialty_zh : doc.specialty_en}
                        </div>
                      </div>
                      <span className="pc-book-open-count">
                        {openCount} {isZh ? "空檔" : "open"}
                      </span>
                    </div>

                    <div className="pc-book-slot-grid">
                      {slots.map((sl) => {
                        const isSelected =
                          doc.doctor_id === activeSelection.doctorId && sl.slot_id === activeSelection.slot.slot_id;
                        return (
                          <button
                            key={sl.slot_id}
                            type="button"
                            disabled={!sl.available}
                            className={`pc-slot-pill ${isSelected ? "is-selected" : ""} ${!sl.available ? "is-busy" : ""}`}
                            onClick={() => handleSelectDoctorSlot(doc.doctor_id, sl)}
                            title={!sl.available ? (isZh ? "此時段已被預約" : "Already booked") : `${sl.time} HKT`}
                            data-testid={`booking-matrix-slot-${sl.slot_id}`}
                          >
                            {isSelected && <Check size={11} />}
                            <span className="pc-book-tabular">{sl.time}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {localError && <div className="pc-book-error">{localError}</div>}

        {/* Primary Confirm CTA + Optional Cancel Reschedule */}
        <div className="pc-book-actions">
          <button
            type="button"
            className="pc-book-confirm-btn"
            disabled={submitting || !activeSelection.slot.available}
            onClick={() => void handleConfirm()}
            data-testid="booking-confirm-btn"
          >
            <Check size={15} />
            <span>{ctaLabel}</span>
          </button>
          {rescheduling && (
            <button
              type="button"
              className="pc-book-cancel-btn"
              onClick={() => {
                setRescheduling(false);
                setShowAllDoctors(false);
                setSelectedDoctorId(offer.recommended.doctor_id);
                setSelectedSlotId(offer.recommended.slot_id);
              }}
            >
              {isZh ? "保留原有預約" : "Keep existing appointment"}
            </button>
          )}
        </div>

        {(isZh ? offer.prep_note_zh : offer.prep_note_en) && (
          <div className="pc-book-prep-subtle">
            {isZh ? offer.prep_note_zh : offer.prep_note_en}
          </div>
        )}
      </div>
    </div>
  );
}
