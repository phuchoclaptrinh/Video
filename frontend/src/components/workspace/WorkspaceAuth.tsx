'use client';

import { useState } from 'react';
import { ArrowRight, Clapperboard, Film, ImagePlus, Loader2, Sparkles } from 'lucide-react';
import { request } from '@/lib/chat-api';
import type { WorkspaceUser } from '@/types/workspace';

function Brand({ admin }: { admin?: boolean }) {
  return (
    <div className="flow-auth-brand">
      <span className="flow-brand-mark">
        <Clapperboard size={17} />
      </span>
      <span>
        VO3 <b>{admin ? 'Admin' : 'Studio'}</b>
      </span>
    </div>
  );
}

export function WorkspaceAuth({
  onAuthenticated,
  admin = false,
}: {
  onAuthenticated: (user: WorkspaceUser) => void;
  /** Admin console sign-in: login only, no account creation. */
  admin?: boolean;
}) {
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <main className="flow-auth">
      <section className="flow-auth-hero">
        <Brand admin={admin} />
        <div className="flow-auth-pitch">
          <h2>Biến ảnh sản phẩm thành video quảng cáo có kịch bản.</h2>
          <ul>
            <li>
              <ImagePlus size={20} />
              <span>
                <b>Bắt đầu từ ảnh sản phẩm</b>
                Tải tối đa 6 ảnh, AI đọc chi tiết và giữ đúng sản phẩm trong từng cảnh.
              </span>
            </li>
            <li>
              <Sparkles size={20} />
              <span>
                <b>Kịch bản bắt trend</b>
                Hook mở đầu cuốn hút, lời thoại tiếng Việt theo nhịp video ngắn.
              </span>
            </li>
            <li>
              <Film size={20} />
              <span>
                <b>Video hoàn chỉnh</b>
                Tạo từng cảnh, kiểm tra lời nói rồi tự ghép thành video để tải về.
              </span>
            </li>
          </ul>
        </div>
        <small>Hỗ trợ Veo 3.1, Omni Flash, Seedance và nhiều model video AI.</small>
      </section>
      <section className="flow-auth-panel">
        <Brand admin={admin} />
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            try {
              const user = await request<WorkspaceUser>(
                `/auth/${register ? 'register' : 'login'}`,
                {
                  method: 'POST',
                  body: JSON.stringify({ email, password }),
                },
              );
              setPassword('');
              onAuthenticated(user);
            } catch (reason) {
              setError((reason as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="flow-auth-heading">
            <h1>
              {register ? 'Tạo tài khoản quản trị' : admin ? 'Đăng nhập quản trị' : 'Đăng nhập'}
            </h1>
            <p>
              {register
                ? 'Chỉ dùng cho email quản trị khai báo trong ADMIN_EMAILS.'
                : admin
                  ? 'Dành cho quản trị viên VO3 Studio.'
                  : 'Chào mừng trở lại. Tiếp tục với dự án của bạn.'}
            </p>
          </div>
          <label>
            Email
            <input
              type="email"
              autoComplete="email"
              placeholder="ban@congty.vn"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label>
            Mật khẩu
            <input
              type="password"
              autoComplete={register ? 'new-password' : 'current-password'}
              placeholder={register ? 'Tối thiểu 8 ký tự' : '••••••••'}
              minLength={8}
              maxLength={128}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && (
            <p className="flow-error-text" role="alert">
              {error}
            </p>
          )}
          <button className="flow-button primary" disabled={busy}>
            {busy ? <Loader2 className="spin" size={17} /> : <ArrowRight size={17} />}
            <span>{register ? 'Tạo tài khoản' : 'Đăng nhập'}</span>
          </button>
          {admin ? (
            // A new install has no accounts yet: the admin listed in ADMIN_EMAILS signs up here once.
            <button
              type="button"
              className="flow-auth-switch"
              onClick={() => {
                setRegister(!register);
                setError('');
              }}
            >
              {register ? 'Đã có tài khoản? ' : 'Lần đầu cài đặt? '}
              <b>{register ? 'Đăng nhập' : 'Tạo tài khoản admin'}</b>
            </button>
          ) : (
            <p className="flow-auth-note">
              Tài khoản do quản trị viên cấp. Chưa có tài khoản? Hãy liên hệ admin.
            </p>
          )}
        </form>
      </section>
    </main>
  );
}
