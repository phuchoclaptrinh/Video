'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import clsx from 'clsx';
import {
  BarChart3,
  Clapperboard,
  Coins,
  ExternalLink,
  KeyRound,
  Loader2,
  LogOut,
  Users,
  Video,
} from 'lucide-react';
import { request } from '@/lib/chat-api';
import type { WorkspaceUser } from '@/types/workspace';
import { WorkspaceAuth } from '@/components/workspace/WorkspaceAuth';
import { AdminOverview } from './AdminOverview';
import { AdminVideos } from './AdminVideos';
import { AdminKeys } from './AdminKeys';
import { AdminUsers } from './AdminUsers';
import { AdminCredits } from './AdminCredits';

const tabs = [
  { id: 'overview', label: 'Tổng quan', icon: BarChart3 },
  { id: 'videos', label: 'Video & hàng đợi', icon: Video },
  { id: 'keys', label: 'API key', icon: KeyRound },
  { id: 'users', label: 'Người dùng', icon: Users },
  { id: 'credits', label: 'Credit', icon: Coins },
] as const;
type Tab = (typeof tabs)[number]['id'];

export function AdminConsole() {
  const [user, setUser] = useState<WorkspaceUser | null | undefined>(undefined);
  const [tab, setTab] = useState<Tab>('overview');
  const router = useRouter();
  useEffect(() => {
    request<WorkspaceUser>('/me')
      .then(setUser)
      .catch(() => setUser(null));
  }, []);
  // The admin console is a separate interface: regular accounts belong in the Studio.
  const regularUser = Boolean(user && !user.is_admin);
  useEffect(() => {
    if (regularUser) router.replace('/');
  }, [regularUser, router]);

  if (user === undefined || regularUser)
    return (
      <main className="flow-loading" aria-busy="true">
        <Loader2 className="spin" size={22} />
        <span>Đang mở trang quản trị…</span>
      </main>
    );
  if (!user) return <WorkspaceAuth admin onAuthenticated={setUser} />;

  return (
    <main className="admin-shell">
      <header className="flow-topbar admin-topbar">
        <div className="flow-topbar-start">
          <Link className="flow-brand" href="/admin">
            <span className="flow-brand-mark">
              <Clapperboard size={17} />
            </span>
            <span>
              VO3 <b>Admin</b>
            </span>
          </Link>
        </div>
        <div className="flow-topbar-actions">
          <span className="admin-user">{user.email}</span>
          <Link className="flow-button" href="/" title="Mở giao diện tạo video của người dùng">
            <ExternalLink size={15} />
            Mở Studio
          </Link>
          <button
            className="flow-icon"
            title="Đăng xuất"
            onClick={() =>
              void request('/auth/session', { method: 'DELETE' })
                .catch(() => undefined)
                .then(() => setUser(null))
            }
          >
            <LogOut size={17} />
          </button>
        </div>
      </header>
      <div className="admin-body">
        <nav className="admin-nav" aria-label="Mục quản trị">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={clsx(tab === id && 'active')}
              aria-current={tab === id ? 'page' : undefined}
              onClick={() => setTab(id)}
            >
              <Icon size={17} />
              {label}
            </button>
          ))}
        </nav>
        <section className="admin-content">
          {tab === 'overview' && <AdminOverview onOpenVideos={() => setTab('videos')} />}
          {tab === 'videos' && <AdminVideos />}
          {tab === 'keys' && <AdminKeys />}
          {tab === 'users' && <AdminUsers currentUserId={user.id} />}
          {tab === 'credits' && <AdminCredits />}
        </section>
      </div>
    </main>
  );
}

export function formatTime(seconds: number | null | undefined) {
  if (!seconds) return '—';
  const date = new Date(seconds * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function useAdminData<T>(path: string, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    request<T>(path)
      .then((value) => {
        if (!alive) return;
        setData(value);
        setError('');
      })
      .catch((reason: Error) => alive && setError(reason.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);
  useEffect(() => {
    if (!refreshMs) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), refreshMs);
    return () => window.clearInterval(timer);
  }, [refreshMs]);
  return { data, error, loading, reload: () => setTick((value) => value + 1) };
}
