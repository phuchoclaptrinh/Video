'use client';

import { useState } from 'react';
import { Check, Copy, Loader2, RefreshCw } from 'lucide-react';
import { request } from '@/lib/chat-api';
import { WorkspaceDialog } from '@/components/workspace/WorkspaceDialog';

function randomPassword() {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join('');
}

/** Create a member account, or set a new password for one; shows the credentials to hand over. */
export function AdminAccountDialog({
  user,
  onClose,
  onSaved,
}: {
  /** Omitted to create a new account. */
  user?: { id: string; email: string };
  onClose: () => void;
  onSaved: () => void;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState(randomPassword);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState<{ email: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (user)
        await request(`/admin/users/${user.id}/password`, {
          method: 'PUT',
          body: JSON.stringify({ password }),
        });
      else
        await request('/admin/users', {
          method: 'POST',
          body: JSON.stringify({ email: email.trim(), password }),
        });
      setDone({ email: user?.email || email.trim().toLowerCase(), password });
      onSaved();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const credentials = done
    ? `Địa chỉ: ${window.location.origin}\nEmail: ${done.email}\nMật khẩu: ${done.password}`
    : '';

  return (
    <WorkspaceDialog
      title={user ? `Đặt lại mật khẩu · ${user.email}` : 'Tạo tài khoản người dùng'}
      onClose={onClose}
    >
      <div className="flow-settings-body">
        {done ? (
          <>
            <p className="admin-note">
              {user
                ? 'Đã đặt mật khẩu mới. Người dùng bị đăng xuất khỏi mọi thiết bị.'
                : 'Đã tạo tài khoản. Gửi thông tin dưới đây cho người dùng.'}
            </p>
            <pre className="admin-credentials">{credentials}</pre>
            <div className="admin-account-actions">
              <button
                type="button"
                className="flow-button"
                onClick={() =>
                  void navigator.clipboard.writeText(credentials).then(() => setCopied(true))
                }
              >
                {copied ? <Check size={15} /> : <Copy size={15} />}
                <span>{copied ? 'Đã sao chép' : 'Sao chép'}</span>
              </button>
              <button type="button" className="flow-button primary" onClick={onClose}>
                <span>Xong</span>
              </button>
            </div>
          </>
        ) : (
          <form className="admin-account-form" onSubmit={submit}>
            {!user && (
              <label>
                <span>Email</span>
                <input
                  type="email"
                  required
                  autoComplete="off"
                  placeholder="khachhang@congty.vn"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </label>
            )}
            <label>
              <span>Mật khẩu</span>
              <span className="admin-password-row">
                <input
                  required
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                  aria-label="Mật khẩu"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  className="flow-icon"
                  title="Tạo mật khẩu ngẫu nhiên"
                  onClick={() => setPassword(randomPassword())}
                >
                  <RefreshCw size={16} />
                </button>
              </span>
            </label>
            {!user && (
              <p className="flow-model-caption">
                Tài khoản mới nhận số credit tặng theo bảng giá; cộng thêm trong cột Credit.
              </p>
            )}
            {error && <p className="flow-inline-error">{error}</p>}
            <button className="flow-button primary" disabled={busy}>
              {busy && <Loader2 size={15} className="spin" />}
              <span>{user ? 'Đặt mật khẩu mới' : 'Tạo tài khoản'}</span>
            </button>
          </form>
        )}
      </div>
    </WorkspaceDialog>
  );
}
