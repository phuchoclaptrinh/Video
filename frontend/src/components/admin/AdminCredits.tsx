'use client';

import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { Loader2, Save } from 'lucide-react';
import { request } from '@/lib/chat-api';
import type { ClipPrice, CreditSummary } from '@/lib/credits';
import type { ProviderCatalog } from '@/types/workspace';
import { WorkspaceDialog } from '@/components/workspace/WorkspaceDialog';
import { formatTime, useAdminData } from './AdminConsole';

type Pricing = {
  signup_bonus: number;
  video_clip: Record<string, ClipPrice>;
  plan: number;
  campaign: number;
  image: number;
  hidden_models: string[];
};

const flatFields = [
  ['signup_bonus', 'Tặng tài khoản mới', 'Cộng một lần khi đăng ký'],
  ['plan', 'Lập kịch bản', 'Mỗi kịch bản, kể cả từng kịch bản trong chiến dịch'],
  ['campaign', 'Chiến lược chiến dịch', 'Mỗi lần AI lập chiến lược chiến dịch'],
  ['image', 'Ảnh tham chiếu', 'Mỗi ảnh anchor hoặc keyframe cảnh'],
] as const;

function amount(value: string) {
  const number = Math.floor(Number(value));
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

/** Prices: flat items plus credits per video second for each model. */
export function AdminCredits() {
  const pricing = useAdminData<Pricing>('/admin/credit-pricing');
  const catalog = useAdminData<ProviderCatalog>('/providers');
  const [draft, setDraft] = useState<Pricing | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (pricing.data) setDraft(pricing.data);
  }, [pricing.data]);
  const models = (catalog.data?.video_providers || [])
    .filter((provider) => provider.id !== 'fake')
    .flatMap((provider) =>
      provider.models.map((model) => ({
        key: `${provider.id}:${model.id}`,
        label: `${provider.label} · ${model.label}`,
        // A fixed-length model is priced per clip; a variable one per shortest clip.
        seconds:
          model.duration_options?.length === 1
            ? (model.duration_options[0] ?? 8)
            : Math.min(...(model.duration_options?.length ? model.duration_options : [8])),
      })),
    );

  function setClip(key: string, price: ClipPrice | null) {
    if (!draft) return;
    const clips = { ...draft.video_clip };
    if (price) clips[key] = price;
    else delete clips[key];
    setDraft({ ...draft, video_clip: clips });
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    setMessage('');
    try {
      const saved = await request<Pricing>('/admin/credit-pricing', {
        method: 'PUT',
        body: JSON.stringify(draft),
      });
      setDraft(saved);
      setMessage('Đã lưu bảng giá.');
    } catch (reason) {
      setMessage((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="admin-panel">
      <div className="admin-heading">
        <div>
          <span className="workflow-eyebrow">Credit</span>
          <h1>Bảng giá credit</h1>
          <p>
            Áp dụng cho tài khoản thành viên; admin không bị trừ credit. Cảnh render lỗi không ra
            video được tự hoàn.
          </p>
        </div>
        <button className="flow-button primary" disabled={!draft || saving} onClick={save}>
          {saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
          Lưu bảng giá
        </button>
      </div>
      {(pricing.error || message) && (
        <p
          className={clsx(
            pricing.error || !message.startsWith('Đã') ? 'flow-inline-error' : 'admin-note',
          )}
        >
          {pricing.error || message}
        </p>
      )}
      {draft && (
        <div className="admin-grid admin-credit-grid">
          <section className="admin-box">
            <h2>Thao tác</h2>
            <div className="admin-price-list">
              {flatFields.map(([key, label, hint]) => (
                <label key={key}>
                  <span>
                    <b>{label}</b>
                    <small>{hint}</small>
                  </span>
                  <input
                    type="number"
                    min={0}
                    aria-label={label}
                    value={draft[key]}
                    onChange={(event) => setDraft({ ...draft, [key]: amount(event.target.value) })}
                  />
                </label>
              ))}
            </div>
          </section>
          <section className="admin-box">
            <h2>Render video (credit cho mỗi clip)</h2>
            <p className="flow-model-caption">
              Nhập số credit cho mỗi clip dài bao nhiêu giây, vd 40 credit / 8 giây. Clip dài hơn
              tính thêm theo từng khối giây (Seedance 20 credit / 5 giây: clip 15 giây = 60 credit).
              Bỏ chọn &quot;Hiện&quot; để ẩn model khỏi người dùng; admin vẫn thấy và dùng được.
            </p>
            <div className="admin-price-list">
              <label>
                <span>
                  <b>Mặc định</b>
                  <small>Model chưa đặt giá riêng</small>
                </span>
                <ClipPriceInput
                  label="Giá mặc định"
                  value={draft.video_clip.default}
                  onChange={(price) => setClip('default', price)}
                />
              </label>
              {models.map((model) => {
                const hidden = draft.hidden_models.includes(model.key);
                return (
                  <div key={model.key} className={clsx(hidden && 'admin-price-hidden')}>
                    <span>
                      <b>{model.label}</b>
                      <small>
                        {[
                          hidden && 'Đang ẩn với người dùng',
                          draft.video_clip[model.key] === undefined && 'Đang dùng giá mặc định',
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </small>
                    </span>
                    <label className="admin-price-visible" title="Hiện model này cho người dùng">
                      <input
                        type="checkbox"
                        aria-label={`Hiện ${model.label} cho người dùng`}
                        checked={!hidden}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            hidden_models: event.target.checked
                              ? draft.hidden_models.filter((key) => key !== model.key)
                              : [...draft.hidden_models, model.key],
                          })
                        }
                      />
                      Hiện
                    </label>
                    <ClipPriceInput
                      label={`Giá ${model.label}`}
                      value={draft.video_clip[model.key]}
                      fallback={{
                        credits: draft.video_clip.default?.credits ?? 0,
                        seconds: model.seconds,
                      }}
                      onChange={(price) => setClip(model.key, price)}
                    />
                  </div>
                );
              })}
              {catalog.error && <small className="flow-muted">{catalog.error}</small>}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

/** "N credit / S giây" pair; clearing the credits falls back to the default price. */
function ClipPriceInput({
  label,
  value,
  fallback,
  onChange,
}: {
  label: string;
  value?: ClipPrice;
  fallback?: ClipPrice;
  onChange: (price: ClipPrice | null) => void;
}) {
  const seconds = value?.seconds ?? fallback?.seconds ?? 8;
  return (
    <span className="admin-clip-price">
      <input
        type="number"
        min={0}
        aria-label={label}
        placeholder={fallback ? String(fallback.credits) : '0'}
        value={value?.credits ?? ''}
        onChange={(event) =>
          onChange(
            event.target.value === '' && fallback
              ? null
              : { credits: amount(event.target.value), seconds },
          )
        }
      />
      <small>credit /</small>
      <input
        type="number"
        min={1}
        max={60}
        aria-label={`${label} - số giây`}
        value={seconds}
        disabled={!value}
        onChange={(event) =>
          value &&
          onChange({ ...value, seconds: Math.min(60, Math.max(1, amount(event.target.value))) })
        }
      />
      <small>giây</small>
    </span>
  );
}

/** Top up or deduct one account, with its recent ledger. */
export function AdminCreditDialog({
  user,
  onClose,
  onChanged,
}: {
  user: { id: string; email: string };
  onClose: () => void;
  onChanged: () => void;
}) {
  const ledger = useAdminData<CreditSummary>(`/admin/users/${user.id}/credits`);
  const [delta, setDelta] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const value = Math.trunc(Number(delta));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!value) return;
    setBusy(true);
    setError('');
    try {
      await request(`/admin/users/${user.id}/credits`, {
        method: 'POST',
        body: JSON.stringify({ delta: value, note }),
      });
      setDelta('');
      setNote('');
      ledger.reload();
      onChanged();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <WorkspaceDialog title={`Credit · ${user.email}`} onClose={onClose}>
      <div className="flow-settings-body">
        <div className="flow-section-heading">
          <h3>Số dư</h3>
          <span className="flow-credit-balance">
            {ledger.data ? ledger.data.balance.toLocaleString('vi-VN') : '…'} <small>credit</small>
          </span>
        </div>
        <form className="admin-credit-form" onSubmit={submit}>
          <input
            type="number"
            aria-label="Số credit cộng hoặc trừ"
            placeholder="Vd 100 hoặc -50"
            value={delta}
            onChange={(event) => setDelta(event.target.value)}
            required
          />
          <input
            aria-label="Ghi chú"
            placeholder="Ghi chú (vd: nạp gói tháng 10)"
            maxLength={200}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <button className="flow-button primary" disabled={busy || !value}>
            {busy && <Loader2 size={15} className="spin" />}
            {value < 0 ? `Trừ ${-value}` : `Cộng ${value || ''}`} credit
          </button>
        </form>
        {(error || ledger.error) && <p className="flow-inline-error">{error || ledger.error}</p>}
        {ledger.data && (
          <ul className="flow-credit-history">
            {ledger.data.history.map((entry) => (
              <li key={entry.id}>
                <span>
                  <b>{entry.label}</b>
                  {entry.note && <small>{entry.note}</small>}
                </span>
                <span className={clsx('flow-credit-delta', entry.delta > 0 ? 'plus' : 'minus')}>
                  {entry.delta > 0 ? '+' : ''}
                  {entry.delta}
                  <small>
                    {entry.pending ? 'đang render' : formatTime(entry.created)} · còn{' '}
                    {entry.balance}
                  </small>
                </span>
              </li>
            ))}
            {!ledger.data.history.length && <li className="flow-muted">Chưa có giao dịch.</li>}
          </ul>
        )}
      </div>
    </WorkspaceDialog>
  );
}
