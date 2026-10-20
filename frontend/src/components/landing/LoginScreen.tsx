'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Clapperboard, Loader2 } from 'lucide-react';
import { request } from '@/lib/chat-api';
import type { WorkspaceUser } from '@/types/workspace';
import { WorkspaceAuth } from '@/components/workspace/WorkspaceAuth';

/** Sign-in page; an account that is already signed in goes straight to its workspace. */
export function LoginScreen() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  useEffect(() => {
    let alive = true;
    request<WorkspaceUser>('/me')
      .then((user) => router.replace(user.is_admin ? '/admin' : '/'))
      .catch(() => alive && setChecking(false));
    return () => {
      alive = false;
    };
  }, [router]);
  if (checking)
    return (
      <main className="flow-loading" aria-busy="true">
        <span className="flow-brand-mark">
          <Clapperboard size={22} />
        </span>
        <span>
          <Loader2 className="spin" size={15} /> Đang mở VO3 Studio…
        </span>
      </main>
    );
  return (
    <WorkspaceAuth onAuthenticated={(user) => router.replace(user.is_admin ? '/admin' : '/')} />
  );
}
