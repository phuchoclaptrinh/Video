'use client';

import { useCallback, useEffect, useState } from 'react';
import { request } from '@/lib/chat-api';
import type { WorkspaceConfig } from '@/types/workspace';
import { SeedvisKeyPool } from '@/components/workspace/SeedvisKeyPool';
import { CredentialEditor } from '@/components/workspace/WorkspaceSettings';
import { AdminImageProvider } from './AdminImageProvider';

export function AdminKeys() {
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [keysVersion, setKeysVersion] = useState(0);
  const refresh = useCallback(() => {
    request<WorkspaceConfig>('/config')
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);
  useEffect(refresh, [refresh]);
  // A new or removed key changes which image models the provider reports as available.
  const keysChanged = useCallback(() => {
    setKeysVersion((value) => value + 1);
    refresh();
  }, [refresh]);
  return (
    <div className="admin-panel">
      <div className="admin-heading">
        <div>
          <span className="workflow-eyebrow">API key</span>
          <h1>Nhà cung cấp</h1>
          <p>Key dùng chung cho mọi người dùng, được mã hóa trên máy chủ.</p>
        </div>
      </div>
      <div className="admin-keys">
        <article className="admin-box">
          <AdminImageProvider catalogVersion={keysVersion} />
        </article>
        <article className="admin-box">
          <SeedvisKeyPool onChange={keysChanged} />
        </article>
        <article className="admin-box">
          <CredentialEditor
            label="PoYo API key"
            model="Video Veo 3.1"
            endpoint="/provider-credential?provider=poyo"
            onChange={keysChanged}
          />
        </article>
        <article className="admin-box">
          <CredentialEditor
            label="OpenAI API key"
            model={config ? `Lập kịch bản · ${config.planner_model}` : 'Lập kịch bản'}
            endpoint="/planner-credential"
            onChange={keysChanged}
          />
        </article>
      </div>
    </div>
  );
}
