'use client';

import { FolderOpen, Plus, Search, Trash2 } from 'lucide-react';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';
import { WorkspaceDialog } from './WorkspaceDialog';

export function ProjectHistory({ workspace: w }: { workspace: VideoWorkspaceController }) {
  const visible = w.conversations.filter((item) =>
    item.title.toLocaleLowerCase('vi').includes(w.filter.toLocaleLowerCase('vi')),
  );
  const busy = w.loading || w.sending || Boolean(w.videoBusyId);
  return (
    <WorkspaceDialog title="Dự án của bạn" onClose={() => w.setSidebarOpen(false)}>
      <div className="flow-history-tools">
        <label className="flow-search">
          <Search size={17} />
          <input
            aria-label="Tìm dự án"
            placeholder="Tìm dự án…"
            value={w.filter}
            onChange={(event) => w.setFilter(event.target.value)}
          />
        </label>
        <button className="flow-button" disabled={busy} onClick={w.newChat}>
          <Plus size={16} />
          Dự án mới
        </button>
      </div>
      {w.error && (
        <p className="flow-inline-error" role="alert">
          {w.error}
        </p>
      )}
      <div className="flow-history-list">
        {visible.map((item) => (
          <div className={w.activeId === item.id ? 'selected' : ''} key={item.id}>
            <button disabled={busy} onClick={() => void w.openConversation(item.id)}>
              <FolderOpen size={18} />
              <span>
                <strong>{item.title || 'Dự án mới'}</strong>
                <small>{new Date(item.updated * 1000).toLocaleDateString('vi-VN')}</small>
              </span>
            </button>
            <button
              className="flow-icon"
              title={`Xóa lịch sử ${item.title}`}
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    'Xóa lịch sử cuộc trò chuyện này? Video đã tạo vẫn được giữ trong thư viện.',
                  )
                )
                  void w.removeConversation(item.id);
              }}
            >
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        {!visible.length && (
          <p className="flow-empty-note">
            {w.filter ? 'Không tìm thấy dự án.' : 'Chưa có dự án nào.'}
          </p>
        )}
      </div>
    </WorkspaceDialog>
  );
}
