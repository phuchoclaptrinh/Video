'use client';

import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { Check, CircleAlert, Layers, Loader2 } from 'lucide-react';
import { request } from '@/lib/chat-api';
import { jobLabels, terminal, type VideoEntry } from './AssetCanvas';

type Capacity = { keys: number; total_slots: number; active: number; waiting: number };

/** Every render of the project at once, so parallel streams are visible side by side. */
export function ProductionBoard({
  entries,
  selectedId,
  seedvis,
  admin,
  onSelect,
}: {
  entries: VideoEntry[];
  selectedId: string | null;
  seedvis: boolean;
  admin: boolean;
  onSelect: (id: string) => void;
}) {
  const rendered = entries.filter((entry) => entry.event.job).reverse();
  const running = rendered.filter((entry) => !terminal.includes(entry.event.job!.status)).length;
  const [capacity, setCapacity] = useState<Capacity | null>(null);
  useEffect(() => {
    if (!seedvis) return;
    let alive = true;
    const load = () =>
      request<Capacity>('/seedvis/capacity')
        .then((value) => alive && setCapacity(value))
        .catch(() => {});
    void load();
    const timer = running ? window.setInterval(load, 5000) : undefined;
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [seedvis, running]);
  if (!rendered.length) return null;
  return (
    <section className="workflow-production-board" aria-label="Các video đang sản xuất">
      <div className="workflow-production-board-head">
        <strong>
          <Layers size={15} />
          Luồng sản xuất
          <span>
            {running > 1
              ? `${running} video đang chạy song song`
              : running
                ? '1 video đang chạy'
                : `${rendered.length} video`}
          </span>
        </strong>
        {seedvis && capacity && capacity.total_slots > 0 && (
          <span
            className="workflow-capacity"
            title={
              admin
                ? `${capacity.keys} key Seedvis · mỗi luồng tạo một cảnh tại một thời điểm`
                : 'Mỗi luồng tạo một cảnh tại một thời điểm; hết luồng thì cảnh tự xếp hàng'
            }
          >
            <i
              style={{ width: `${Math.min(100, (capacity.active / capacity.total_slots) * 100)}%` }}
            />
            {admin ? 'Seedvis ' : ''}
            {capacity.active}/{capacity.total_slots} luồng
            {capacity.waiting > 0 ? ` · ${capacity.waiting} cảnh chờ` : ''}
          </span>
        )}
      </div>
      <div className="workflow-production-list">
        {rendered.map(({ message, event }) => {
          const job = event.job!;
          const progress = job.clip_progress;
          const done = job.status === 'COMPLETED';
          const failed = ['FAILED', 'CANCELLED', 'NEEDS_USER_ACTION'].includes(job.status);
          return (
            <button
              key={message.id}
              type="button"
              className={clsx(
                'workflow-production-item',
                message.id === selectedId && 'selected',
                done && 'done',
                failed && 'failed',
              )}
              onClick={() => onSelect(message.id)}
            >
              <span className="workflow-production-item-title">
                {done ? (
                  <Check size={14} />
                ) : failed ? (
                  <CircleAlert size={14} />
                ) : (
                  <Loader2 size={14} className="spin" />
                )}
                <span title={event.plan.story_plan?.core_value || event.prompt}>
                  {event.plan.story_plan?.core_value || event.prompt}
                </span>
              </span>
              <span className="workflow-production-bar">
                <i
                  style={{
                    width: `${done ? 100 : progress ? (progress.completed / Math.max(1, progress.total)) * 100 : 0}%`,
                  }}
                />
              </span>
              <small>
                {jobLabels[job.status] || job.status}
                {progress ? ` · ${progress.completed}/${progress.total} cảnh` : ''}
              </small>
            </button>
          );
        })}
      </div>
    </section>
  );
}
