'use client';

import { useCallback, useEffect, useState } from 'react';
import { KeyRound, Loader2, Plus, Trash2 } from 'lucide-react';
import { request } from '@/lib/chat-api';

type PoolKey = {
  id: string;
  label: string;
  slots: number;
  hint: string;
  active: number;
  out_of_credit?: boolean;
};
type Pool = { keys: PoolKey[]; total_slots: number; active: number; waiting: number };

// Several Seedvis accounts: each key runs up to its own number of videos at once.
export function SeedvisKeyPool({ onChange }: { onChange: () => void }) {
  const [pool, setPool] = useState<Pool | null>(null);
  const [key, setKey] = useState('');
  const [label, setLabel] = useState('');
  const [slots, setSlots] = useState(4);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');

  const run = useCallback(
    async (action: () => Promise<Pool>, changed = true) => {
      setBusy(true);
      setError('');
      try {
        setPool(await action());
        if (changed) onChange();
        return true;
      } catch (reason) {
        setError((reason as Error).message);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onChange],
  );

  useEffect(() => {
    void run(() => request<Pool>('/seedvis/keys'), false);
  }, [run]);

  async function add() {
    const added = await run(() =>
      request<Pool>('/seedvis/keys', {
        method: 'POST',
        body: JSON.stringify({ api_key: key.trim(), label: label.trim(), slots }),
      }),
    );
    if (added) {
      setKey('');
      setLabel('');
      setSlots(4);
    }
  }

  function updateSlots(item: PoolKey, value: number) {
    if (!Number.isInteger(value) || value < 1 || value > 20 || value === item.slots) return;
    void run(() =>
      request<Pool>(`/seedvis/keys/${item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ slots: value }),
      }),
    );
  }

  return (
    <section className="flow-key-section">
      <div className="flow-section-heading">
        <h3>Seedvis API keys</h3>
        <span className={pool?.keys.length ? 'flow-connected' : 'flow-muted'}>
          {pool?.keys.length
            ? `${pool.keys.length} key · ${pool.total_slots} luồng`
            : busy
              ? 'Đang tải…'
              : 'Chưa có key'}
        </span>
      </div>
      <p className="flow-model-caption">
        Mỗi key chạy tối đa số luồng của nó; video mới tự vào key còn trống, hết luồng thì xếp hàng.
        {pool && (pool.active > 0 || pool.waiting > 0)
          ? ` Đang chạy ${pool.active}/${pool.total_slots}, đang chờ ${pool.waiting}.`
          : ''}
      </p>
      {pool && pool.keys.length > 0 && (
        <ul className="flow-key-pool">
          {pool.keys.map((item) => (
            <li key={item.id}>
              <span className="flow-key-pool-name">
                <strong>{item.label || 'Key Seedvis'}</strong>
                <small>
                  {item.hint}
                  {item.out_of_credit && (
                    <span
                      className="flow-key-pool-usage empty"
                      title="Key này báo hết credit; video mới đang chạy trên các key khác"
                    >
                      Hết credit
                    </span>
                  )}
                </small>
              </span>
              <span
                className={
                  item.active >= item.slots ? 'flow-key-pool-usage full' : 'flow-key-pool-usage'
                }
                title="Video đang chạy trên key này"
              >
                {item.active}/{item.slots}
              </span>
              <label className="flow-key-pool-slots">
                <span className="sr-only">Số luồng của {item.label || item.hint}</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  defaultValue={item.slots}
                  key={`${item.id}:${item.slots}`}
                  disabled={busy}
                  onBlur={(event) => updateSlots(item, Number(event.target.value))}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') updateSlots(item, Number(event.currentTarget.value));
                  }}
                />
                luồng
              </label>
              <button
                type="button"
                className="flow-icon"
                title={item.active ? 'Key đang chạy video, đợi xong rồi xóa' : 'Xóa key'}
                disabled={busy || item.active > 0}
                onClick={() => {
                  if (window.confirm(`Xóa key ${item.label || item.hint}?`))
                    void run(() => request<Pool>(`/seedvis/keys/${item.id}`, { method: 'DELETE' }));
                }}
              >
                <Trash2 size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flow-key-pool-add"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <input
          aria-label="Seedvis API key mới"
          type="password"
          autoComplete="new-password"
          placeholder="Nhập API key Seedvis"
          minLength={8}
          maxLength={1000}
          required
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <input
          aria-label="Tên gợi nhớ"
          placeholder="Tên (tùy chọn)"
          maxLength={40}
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
        <label className="flow-key-pool-slots">
          <input
            aria-label="Số luồng"
            type="number"
            min={1}
            max={20}
            value={slots}
            onChange={(event) => setSlots(Number(event.target.value))}
          />
          luồng
        </label>
        <button className="flow-button" disabled={busy || key.trim().length < 8}>
          {busy ? <Loader2 className="spin" size={15} /> : <Plus size={15} />}
          Thêm key
        </button>
      </form>
      {error && (
        <p className="flow-error-text" role="alert">
          {error}
        </p>
      )}
      <p className="flow-model-caption">
        <KeyRound size={12} /> Key được mã hóa trên máy chủ, chỉ hiện 4 ký tự cuối.
      </p>
    </section>
  );
}
