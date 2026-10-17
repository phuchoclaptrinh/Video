'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Check, KeyRound, Loader2, LogOut, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { request } from '@/lib/chat-api';
import type { WorkspaceConfig, WorkspaceUser } from '@/types/workspace';
import type { ProviderSelection } from '@/hooks/use-provider-selection';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';
import { WorkspaceDialog } from './WorkspaceDialog';
import { CreditHistory } from './CreditPanel';

export function CredentialEditor({
  label,
  model,
  endpoint,
  onChange,
}: {
  label: string;
  model?: string;
  endpoint: string;
  onChange: () => void;
}) {
  const [key, setKey] = useState('');
  const [configured, setConfigured] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    request<{ configured: boolean }>(endpoint)
      .then((data) => {
        if (alive) setConfigured(data.configured);
      })
      .catch((reason: Error) => {
        if (alive) setError(reason.message);
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [endpoint]);
  async function update(method: 'PUT' | 'DELETE') {
    setBusy(true);
    setError('');
    try {
      const data = await request<{ configured: boolean }>(endpoint, {
        method,
        ...(method === 'PUT' ? { body: JSON.stringify({ api_key: key.trim() }) } : {}),
      });
      setConfigured(data.configured);
      setKey('');
      onChange();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="flow-key-section">
      <div className="flow-section-heading">
        <h3>{label}</h3>
        <span className={configured ? 'flow-connected' : 'flow-muted'}>
          {configured && <Check size={13} />}
          {configured ? 'Đã lưu key' : 'Chưa có key'}
        </span>
      </div>
      <p className="flow-model-caption">{model}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void update('PUT');
        }}
      >
        <label>
          <span className="sr-only">{label} mới</span>
          <input
            aria-label={`${label} mới`}
            type="password"
            autoComplete="new-password"
            placeholder="Nhập API key"
            minLength={8}
            maxLength={1000}
            required
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
        </label>
        <button className="flow-button" disabled={busy || key.trim().length < 8}>
          {busy ? <Loader2 className="spin" size={15} /> : <KeyRound size={15} />}Lưu
        </button>
        <button
          className="flow-icon"
          type="button"
          title={`Xóa ${label}`}
          disabled={busy || !configured}
          onClick={() => void update('DELETE')}
        >
          <Trash2 size={16} />
        </button>
      </form>
      {error && (
        <p className="flow-error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export function WorkspaceSettings({
  config,
  user,
  workspace,
  providers,
  onClose,
  onLogout,
}: {
  config: WorkspaceConfig;
  user: WorkspaceUser;
  workspace: VideoWorkspaceController;
  providers: ProviderSelection;
  onClose: () => void;
  onLogout: () => void;
}) {
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <WorkspaceDialog title="Cài đặt workspace" onClose={onClose}>
      <div className="flow-settings-body">
        <div className="flow-section-heading">
          <span className="flow-account-email">{user.email}</span>
          <span className="flow-tag">{config.mode === 'live' ? 'Live' : 'Demo'}</span>
        </div>
        <CreditHistory />
        {user.is_admin && (
          <section className="flow-key-section flow-admin-note">
            <div className="flow-section-heading">
              <h3>API key và hàng đợi</h3>
              <span className="flow-tag">Admin</span>
            </div>
            <p className="flow-model-caption">
              Key PoYo, Seedvis, OpenAI, hàng đợi video và người dùng được quản lý trong trang
              Admin.
            </p>
            <Link className="flow-button" href="/admin">
              <ShieldCheck size={15} />
              Mở trang Admin
            </Link>
          </section>
        )}
        {user.is_admin && (
          <section className="flow-image-provider-section">
            <h3>Ảnh tham chiếu cho bản nháp mới</h3>
            <div className="flow-provider-selects">
              <label>
                <span>Provider ảnh</span>
                <select
                  aria-label="Provider ảnh"
                  value={config.image_provider}
                  disabled={
                    !providers.catalog ||
                    providers.loading ||
                    workspace.sending ||
                    Boolean(workspace.videoBusyId)
                  }
                  onChange={(event) => {
                    const provider = providers.catalog?.image_providers.find(
                      (item) => item.id === event.target.value,
                    );
                    if (provider)
                      providers.choose({
                        image_provider: provider.id,
                        image_model:
                          (
                            provider.models.find((model) => model.available !== false) ||
                            provider.models[0]
                          )?.id || '',
                      });
                  }}
                >
                  {providers.catalog?.image_providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label}
                      {!provider.configured
                        ? user.is_admin
                          ? ' · chưa có key'
                          : ' · chưa sẵn sàng'
                        : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>Model ảnh</span>
                <select
                  aria-label="Model ảnh"
                  value={config.image_model}
                  disabled={
                    !providers.catalog ||
                    providers.loading ||
                    workspace.sending ||
                    Boolean(workspace.videoBusyId)
                  }
                  onChange={(event) => providers.choose({ image_model: event.target.value })}
                >
                  {providers.image?.models.map((model) => (
                    <option key={model.id} value={model.id} disabled={model.available === false}>
                      {model.label}
                      {model.available === false ? ' · chưa khả dụng' : ''}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </section>
        )}
        <section className="flow-memory-section">
          <div className="flow-section-heading">
            <h3>Bộ nhớ</h3>
            <span className="flow-muted">{workspace.memories.length} ghi nhớ</span>
          </div>
          <form onSubmit={(event) => void workspace.addMemory(event)}>
            <textarea
              aria-label="Ghi nhớ mới"
              placeholder="Phong cách, nhân vật, giọng nói bạn muốn giữ…"
              value={workspace.memoryText}
              onChange={(event) => workspace.setMemoryText(event.target.value)}
              rows={2}
              maxLength={1000}
            />
            <button className="flow-button" disabled={workspace.memoryText.trim().length < 2}>
              <Plus size={15} />
              Thêm ghi nhớ
            </button>
          </form>
          <div className="flow-memory-list">
            {workspace.memories.map((memory) => (
              <div key={memory.id}>
                <p>{memory.content}</p>
                <button
                  className="flow-icon"
                  title="Xóa ghi nhớ"
                  onClick={() => void workspace.removeMemory(memory.id)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        </section>
        {(error || workspace.error) && (
          <p role="alert" className="flow-error-text">
            {error || workspace.error}
          </p>
        )}
        <footer className="flow-settings-footer">
          {user.is_admin ? <small>Key được mã hóa khi lưu trên máy chủ.</small> : <span />}
          <button
            className="flow-button subtle"
            disabled={logoutBusy}
            onClick={async () => {
              setLogoutBusy(true);
              try {
                await request('/auth/session', { method: 'DELETE' });
                onLogout();
              } catch (reason) {
                setError((reason as Error).message);
                setLogoutBusy(false);
              }
            }}
          >
            <LogOut size={15} />
            Đăng xuất
          </button>
        </footer>
      </div>
    </WorkspaceDialog>
  );
}
