'use client';

import { useCallback, useEffect, useState } from 'react';
import { request } from '@/lib/chat-api';
import type { WorkspaceConfig } from '@/types/workspace';
import { SeedvisKeyPool } from '@/components/workspace/SeedvisKeyPool';
import { CredentialEditor } from '@/components/workspace/WorkspaceSettings';

export function AdminKeys() {
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const refresh = useCallback(() => {
    request<WorkspaceConfig>('/config')
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);
  useEffect(refresh, [refresh]);
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
          <SeedvisKeyPool onChange={refresh} />
        </article>
        <article className="admin-box">
          <CredentialEditor
            label="PoYo API key"
            model="Video Veo 3.1"
            endpoint="/provider-credential?provider=poyo"
            onChange={refresh}
          />
        </article>
        <article className="admin-box">
          <CredentialEditor
            label="OpenAI API key"
            model={config ? `Lập kịch bản · ${config.planner_model}` : 'Lập kịch bản'}
            endpoint="/planner-credential"
            onChange={refresh}
          />
        </article>
      </div>
    </div>
  );
}
