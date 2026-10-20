'use client';

import { useEffect, useState } from 'react';
import { Loader2, Save } from 'lucide-react';
import { request } from '@/lib/chat-api';
import type { ProviderCatalog } from '@/types/workspace';
import { useAdminData } from './AdminConsole';

type ImageChoice = { provider: string; model: string };

/** Which provider and model draw the reference images for every member's new drafts. */
export function AdminImageProvider({ catalogVersion = 0 }: { catalogVersion?: number }) {
  const current = useAdminData<ImageChoice>('/admin/image-provider');
  const catalog = useAdminData<ProviderCatalog>(`/providers?refresh=true&v=${catalogVersion}`);
  const [choice, setChoice] = useState<ImageChoice | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (current.data) setChoice(current.data);
  }, [current.data]);
  const providers = catalog.data?.image_providers || [];
  const provider = providers.find((item) => item.id === choice?.provider);
  const changed =
    choice && current.data
      ? choice.provider !== current.data.provider || choice.model !== current.data.model
      : false;

  async function save() {
    if (!choice) return;
    setSaving(true);
    setMessage('');
    try {
      const saved = await request<ImageChoice>('/admin/image-provider', {
        method: 'PUT',
        body: JSON.stringify(choice),
      });
      setChoice(saved);
      current.reload();
      setMessage('Đã lưu. Bản nháp mới của thành viên sẽ dùng model này.');
    } catch (reason) {
      setMessage((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const error = current.error || catalog.error;
  return (
    <section className="flow-key-section">
      <div className="flow-section-heading">
        <h3>Model tạo ảnh</h3>
        {provider && (
          <span className={provider.configured ? 'flow-connected' : 'flow-muted'}>
            {provider.configured ? 'Đã có key' : 'Chưa có key'}
          </span>
        )}
      </div>
      <p className="flow-model-caption">
        Ảnh anchor và keyframe cảnh cho mọi tài khoản thành viên. Admin vẫn tự chọn được trong Cài
        đặt của Studio.
      </p>
      <div className="flow-provider-selects">
        <label>
          <span>Provider ảnh</span>
          <select
            aria-label="Provider ảnh"
            value={choice?.provider || ''}
            disabled={!choice || !providers.length || saving}
            onChange={(event) => {
              const next = providers.find((item) => item.id === event.target.value);
              if (next)
                setChoice({
                  provider: next.id,
                  model:
                    (next.models.find((model) => model.available !== false) || next.models[0])
                      ?.id || '',
                });
            }}
          >
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
                {!item.configured ? ' · chưa có key' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Model ảnh</span>
          <select
            aria-label="Model ảnh"
            value={choice?.model || ''}
            disabled={!provider || saving}
            onChange={(event) => choice && setChoice({ ...choice, model: event.target.value })}
          >
            {provider?.models.map((model) => (
              <option key={model.id} value={model.id} disabled={model.available === false}>
                {model.label}
                {model.available === false ? ' · chưa khả dụng' : ''}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="admin-image-provider-actions">
        <button className="flow-button primary" disabled={!changed || saving} onClick={save}>
          {saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}
          Lưu model ảnh
        </button>
        {catalog.loading && <Loader2 size={15} className="spin" aria-label="Đang tải model" />}
      </div>
      {(error || message) && (
        <p
          className={error || !message.startsWith('Đã') ? 'flow-error-text' : 'admin-note'}
          role={error ? 'alert' : undefined}
        >
          {error || message}
        </p>
      )}
    </section>
  );
}
