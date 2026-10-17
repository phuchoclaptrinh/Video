'use client';

import { useState } from 'react';
import {
  Coins,
  KeyRound,
  Lock,
  LockOpen,
  RefreshCw,
  Search,
  ShieldCheck,
  ShieldOff,
  UserPlus,
} from 'lucide-react';
import { request } from '@/lib/chat-api';
import { formatTime, useAdminData } from './AdminConsole';
import { AdminCreditDialog } from './AdminCredits';
import { AdminAccountDialog } from './AdminAccountDialog';

type AdminUser = {
  id: string;
  email: string;
  created: number | null;
  is_admin: boolean;
  admin_from_env: boolean;
  disabled: boolean;
  projects: number;
  videos: number;
  last_seen: number | null;
  credits: number;
};

export function AdminUsers({ currentUserId }: { currentUserId: string }) {
  const users = useAdminData<AdminUser[]>('/admin/users');
  const [query, setQuery] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [creditUser, setCreditUser] = useState<AdminUser | null>(null);
  // null: closed; 'new': create an account; a user: reset that user's password.
  const [accountTarget, setAccountTarget] = useState<AdminUser | 'new' | null>(null);
  const visible = (users.data || []).filter((user) =>
    user.email.toLowerCase().includes(query.trim().toLowerCase()),
  );

  async function change(
    user: AdminUser,
    values: { is_admin?: boolean; disabled?: boolean },
    question: string,
  ) {
    if (!window.confirm(question)) return;
    setBusyId(user.id);
    setError('');
    try {
      await request(`/admin/users/${user.id}`, { method: 'PATCH', body: JSON.stringify(values) });
      users.reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="admin-panel">
      <div className="admin-heading">
        <div>
          <span className="workflow-eyebrow">Người dùng</span>
          <h1>Tài khoản</h1>
          <p>
            {users.data ? `${users.data.length} tài khoản` : 'Đang tải…'} · admin cố định khai báo
            trong ADMIN_EMAILS của file .env.
          </p>
        </div>
        <div className="admin-heading-actions">
          <button className="flow-button primary" onClick={() => setAccountTarget('new')}>
            <UserPlus size={15} />
            <span>Tạo tài khoản</span>
          </button>
          <button
            className="flow-icon"
            title="Làm mới"
            onClick={users.reload}
            disabled={users.loading}
          >
            <RefreshCw size={17} className={users.loading ? 'spin' : ''} />
          </button>
        </div>
      </div>
      <label className="flow-search admin-search">
        <Search size={16} />
        <input
          aria-label="Tìm theo email"
          placeholder="Tìm theo email…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {(users.error || error) && <p className="flow-inline-error">{error || users.error}</p>}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Vai trò</th>
              <th>Dự án</th>
              <th>Video xong</th>
              <th>Credit</th>
              <th>Hoạt động gần nhất</th>
              <th aria-label="Thao tác" />
            </tr>
          </thead>
          <tbody>
            {visible.map((user) => {
              const self = user.id === currentUserId;
              return (
                <tr key={user.id} className={user.disabled ? 'admin-row-disabled' : undefined}>
                  <td className="admin-title">
                    {user.email}
                    {self && <small className="admin-you">Bạn</small>}
                  </td>
                  <td>
                    {user.disabled ? (
                      <span className="admin-status failed">Đã khóa</span>
                    ) : user.is_admin ? (
                      <span className="admin-status completed">
                        {user.admin_from_env ? 'Admin (.env)' : 'Admin'}
                      </span>
                    ) : (
                      <span className="admin-status">Thành viên</span>
                    )}
                  </td>
                  <td>{user.projects}</td>
                  <td>{user.videos}</td>
                  <td>
                    {user.is_admin ? (
                      <span className="flow-muted">Không trừ</span>
                    ) : (
                      <button
                        className="admin-credit-cell"
                        title="Cộng / trừ credit"
                        onClick={() => setCreditUser(user)}
                      >
                        <Coins size={14} />
                        {user.credits.toLocaleString('vi-VN')}
                      </button>
                    )}
                  </td>
                  <td>{formatTime(user.last_seen)}</td>
                  <td className="admin-actions">
                    <button
                      className="flow-icon"
                      title="Đặt lại mật khẩu"
                      onClick={() => setAccountTarget(user)}
                    >
                      <KeyRound size={16} />
                    </button>
                    {!user.admin_from_env && !self && (
                      <button
                        className="flow-icon"
                        disabled={busyId === user.id}
                        title={user.is_admin ? 'Bỏ quyền admin' : 'Cấp quyền admin'}
                        onClick={() =>
                          void change(
                            user,
                            { is_admin: !user.is_admin },
                            user.is_admin
                              ? `Bỏ quyền admin của ${user.email}?`
                              : `Cấp quyền admin cho ${user.email}?`,
                          )
                        }
                      >
                        {user.is_admin ? <ShieldOff size={16} /> : <ShieldCheck size={16} />}
                      </button>
                    )}
                    {!self && (
                      <button
                        className="flow-icon"
                        disabled={busyId === user.id}
                        title={user.disabled ? 'Mở khóa tài khoản' : 'Khóa tài khoản'}
                        onClick={() =>
                          void change(
                            user,
                            { disabled: !user.disabled },
                            user.disabled
                              ? `Mở khóa ${user.email}?`
                              : `Khóa ${user.email}? Người này bị đăng xuất và không đăng nhập được nữa.`,
                          )
                        }
                      >
                        {user.disabled ? <LockOpen size={16} /> : <Lock size={16} />}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {users.data && !visible.length && <p className="admin-empty">Không tìm thấy tài khoản.</p>}
      </div>
      {accountTarget && (
        <AdminAccountDialog
          user={accountTarget === 'new' ? undefined : accountTarget}
          onClose={() => setAccountTarget(null)}
          onSaved={users.reload}
        />
      )}
      {creditUser && (
        <AdminCreditDialog
          user={creditUser}
          onClose={() => setCreditUser(null)}
          onChanged={users.reload}
        />
      )}
    </div>
  );
}
