'use client';

import { useState } from 'react';
import { Loader2, RefreshCw, X } from 'lucide-react';
import { request } from '@/lib/chat-api';
import { formatTime, useAdminData } from './AdminConsole';
import { errorLabel, statusLabels } from './admin-labels';

type AdminVideo = {
  id: string;
  title: string;
  user: string;
  status: string;
  error: string | null;
  created: number;
  scenes_done: number;
  scenes_total: number;
  scene_status: string | null;
  credential: string | null;
  /** The provider's own message for the failed scene. */
  error_detail: string | null;
  auto_retries: number;
};
type QueueItem = {
  id: string;
  provider: string;
  status: string;
  user: string;
  credential: string | null;
  attempts: number;
  error: string | null;
  waiting_seconds: number;
  cancel_requested: boolean;
};

const filters = [
  { id: 'active', label: 'Đang chạy' },
  { id: 'FAILED', label: 'Lỗi' },
  { id: 'COMPLETED', label: 'Hoàn thành' },
  { id: 'CANCELLED', label: 'Đã hủy' },
  { id: 'all', label: 'Tất cả' },
];

function duration(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} phút`;
  return `${Math.round(seconds / 3600)} giờ`;
}

export function AdminVideos() {
  const [filter, setFilter] = useState('active');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const videos = useAdminData<AdminVideo[]>(
    `/admin/videos?status=${filter}`,
    filter === 'active' ? 5000 : 0,
  );
  const queue = useAdminData<QueueItem[]>('/admin/queue', 5000);

  async function cancel(video: AdminVideo) {
    if (!window.confirm(`Hủy video "${video.title.slice(0, 60)}" của ${video.user}?`)) return;
    setBusyId(video.id);
    setActionError('');
    try {
      await request(`/admin/videos/${video.id}/cancel`, { method: 'POST' });
      videos.reload();
      queue.reload();
    } catch (reason) {
      setActionError((reason as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="admin-panel">
      <div className="admin-heading">
        <div>
          <span className="workflow-eyebrow">Video & hàng đợi</span>
          <h1>Video của mọi người dùng</h1>
        </div>
        <button
          className="flow-icon"
          title="Làm mới"
          disabled={videos.loading}
          onClick={() => {
            videos.reload();
            queue.reload();
          }}
        >
          <RefreshCw size={17} className={videos.loading ? 'spin' : ''} />
        </button>
      </div>
      <div className="workflow-segmented admin-filter" role="group" aria-label="Lọc video">
        {filters.map((item) => (
          <button
            key={item.id}
            aria-pressed={filter === item.id}
            onClick={() => setFilter(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {(videos.error || actionError) && (
        <p className="flow-inline-error">{actionError || videos.error}</p>
      )}
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Video</th>
              <th>Người dùng</th>
              <th>Trạng thái</th>
              <th>Tiến độ</th>
              <th>Tạo lúc</th>
              <th aria-label="Thao tác" />
            </tr>
          </thead>
          <tbody>
            {videos.data?.map((video) => (
              <tr key={video.id}>
                <td className="admin-title" title={video.title}>
                  {video.title}
                  {video.error && <small title={video.error}>{errorLabel(video.error)}</small>}
                  {video.error && video.error !== errorLabel(video.error) && (
                    <small className="admin-error-detail">{video.error}</small>
                  )}
                  {video.error_detail && (
                    <small className="admin-error-detail">Nhà cung cấp: {video.error_detail}</small>
                  )}
                  {video.auto_retries > 0 && (
                    <small className="admin-error-detail">
                      Đã tự thử lại {video.auto_retries} lần
                    </small>
                  )}
                </td>
                <td>{video.user}</td>
                <td>
                  <span className={`admin-status ${video.status.toLowerCase()}`}>
                    {statusLabels[video.status] || video.status}
                  </span>
                  {video.scene_status && (
                    <small className="admin-sub">
                      Cảnh: {statusLabels[video.scene_status] || video.scene_status}
                      {video.credential ? ` · key ${video.credential}` : ''}
                    </small>
                  )}
                </td>
                <td>
                  <span
                    className="admin-progress"
                    title={`${video.scenes_done}/${video.scenes_total} cảnh`}
                  >
                    <i
                      style={{
                        width: `${(video.scenes_done / Math.max(1, video.scenes_total)) * 100}%`,
                      }}
                    />
                  </span>
                  <small className="admin-sub">
                    {video.scenes_done}/{video.scenes_total} cảnh
                  </small>
                </td>
                <td>{formatTime(video.created)}</td>
                <td>
                  {['GENERATING', 'MERGING', 'QUEUED'].includes(video.status) && (
                    <button
                      className="flow-button subtle"
                      disabled={busyId === video.id}
                      onClick={() => void cancel(video)}
                    >
                      {busyId === video.id ? (
                        <Loader2 className="spin" size={14} />
                      ) : (
                        <X size={14} />
                      )}
                      Hủy
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {videos.data && !videos.data.length && <p className="admin-empty">Không có video nào.</p>}
      </div>

      <div className="admin-heading admin-subheading">
        <div>
          <h2>Hàng đợi cảnh</h2>
          <p>Từng cảnh đang chờ hoặc đang tạo ở provider, kèm key Seedvis đang dùng.</p>
        </div>
      </div>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Trạng thái</th>
              <th>Key</th>
              <th>Người dùng</th>
              <th>Đã chờ</th>
              <th>Ghi chú</th>
            </tr>
          </thead>
          <tbody>
            {queue.data?.map((item) => (
              <tr key={item.id}>
                <td>{item.provider}</td>
                <td>
                  <span className={`admin-status ${item.status.toLowerCase()}`}>
                    {statusLabels[item.status] || item.status}
                  </span>
                </td>
                <td>{item.credential || (item.provider === 'seedvis' ? 'chưa gán' : '—')}</td>
                <td>{item.user}</td>
                <td>{duration(item.waiting_seconds)}</td>
                <td>
                  <span className="admin-sub">
                    {item.cancel_requested
                      ? 'Đang hủy'
                      : item.attempts
                        ? `Thử lại lần ${item.attempts}${item.error ? ` · ${errorLabel(item.error)}` : ''}`
                        : item.status === 'QUEUED'
                          ? 'Chờ luồng trống'
                          : ''}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {queue.data && !queue.data.length && <p className="admin-empty">Hàng đợi trống.</p>}
      </div>
    </div>
  );
}
