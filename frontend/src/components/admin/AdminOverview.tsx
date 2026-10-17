'use client';

import { Loader2, RefreshCw } from 'lucide-react';
import { useAdminData } from './AdminConsole';
import { errorLabel } from './admin-labels';

type Overview = {
  users: number;
  videos_today: number;
  videos_week: number;
  completed_week: number;
  failed_week: number;
  failure_rate_week: number;
  active_videos: number;
  scenes_week: number;
  scenes_by_provider: Record<string, number>;
  running_scenes: number;
  queued_scenes: number;
  seedvis_slots: number;
  seedvis_active: number;
  daily: { date: string; completed: number; failed: number; other: number }[];
  top_errors: { code: string; count: number }[];
};

export function AdminOverview({ onOpenVideos }: { onOpenVideos: () => void }) {
  const { data, error, loading, reload } = useAdminData<Overview>('/admin/overview', 15000);
  if (!data)
    return error ? (
      <p className="flow-inline-error">{error}</p>
    ) : (
      <p className="admin-empty">
        <Loader2 className="spin" size={16} /> Đang tải số liệu…
      </p>
    );
  const peak = Math.max(1, ...data.daily.map((day) => day.completed + day.failed + day.other));
  const cards = [
    { label: 'Người dùng', value: data.users },
    {
      label: 'Video 24 giờ qua',
      value: data.videos_today,
      hint: `${data.videos_week} trong 7 ngày`,
    },
    {
      label: 'Đang chạy',
      value: data.active_videos,
      hint: `${data.running_scenes} cảnh đang tạo · ${data.queued_scenes} chờ`,
      action: onOpenVideos,
    },
    {
      label: 'Tỉ lệ lỗi 7 ngày',
      value: `${Math.round(data.failure_rate_week * 100)}%`,
      hint: `${data.failed_week} lỗi / ${data.completed_week} hoàn thành`,
      tone: data.failure_rate_week > 0.3 ? 'warn' : undefined,
    },
    {
      label: 'Cảnh đã tạo 7 ngày',
      value: data.scenes_week,
      hint:
        Object.entries(data.scenes_by_provider)
          .map(([provider, count]) => `${provider} ${count}`)
          .join(' · ') || 'Mỗi cảnh là một lượt gọi provider',
    },
    {
      label: 'Luồng Seedvis',
      value: `${data.seedvis_active}/${data.seedvis_slots}`,
      hint: data.seedvis_slots ? 'Đang dùng / tổng luồng' : 'Chưa có key Seedvis',
    },
  ];
  return (
    <div className="admin-panel">
      <div className="admin-heading">
        <div>
          <span className="workflow-eyebrow">Tổng quan</span>
          <h1>Hoạt động hệ thống</h1>
        </div>
        <button className="flow-icon" title="Làm mới" onClick={reload} disabled={loading}>
          <RefreshCw size={17} className={loading ? 'spin' : ''} />
        </button>
      </div>
      <div className="admin-cards">
        {cards.map((card) => (
          <article
            key={card.label}
            className={card.tone ? `admin-card ${card.tone}` : 'admin-card'}
            onClick={card.action}
            role={card.action ? 'button' : undefined}
            tabIndex={card.action ? 0 : undefined}
          >
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            {card.hint && <small>{card.hint}</small>}
          </article>
        ))}
      </div>
      <div className="admin-grid">
        <article className="admin-box">
          <h2>Video 14 ngày qua</h2>
          <div className="admin-chart" role="img" aria-label="Số video theo ngày">
            {data.daily.map((day) => (
              <div
                key={day.date}
                title={`${day.date}: ${day.completed} xong, ${day.failed} lỗi, ${day.other} khác`}
              >
                <span className="admin-bar">
                  <i className="other" style={{ height: `${(day.other / peak) * 100}%` }} />
                  <i className="failed" style={{ height: `${(day.failed / peak) * 100}%` }} />
                  <i className="completed" style={{ height: `${(day.completed / peak) * 100}%` }} />
                </span>
                <small>{day.date.slice(0, 2)}</small>
              </div>
            ))}
          </div>
          <p className="admin-legend">
            <i className="completed" /> Hoàn thành <i className="failed" /> Lỗi{' '}
            <i className="other" /> Khác
          </p>
        </article>
        <article className="admin-box">
          <h2>Lỗi hay gặp (30 ngày)</h2>
          {data.top_errors.length ? (
            <ul className="admin-errors">
              {data.top_errors.map((item) => (
                <li key={item.code}>
                  <span title={item.code}>{errorLabel(item.code)}</span>
                  <b>{item.count}</b>
                </li>
              ))}
            </ul>
          ) : (
            <p className="admin-empty">Không có video lỗi trong 30 ngày.</p>
          )}
        </article>
      </div>
    </div>
  );
}
