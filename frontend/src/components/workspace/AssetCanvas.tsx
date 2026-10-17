'use client';

import { useState } from 'react';
import clsx from 'clsx';
import {
  Clapperboard,
  Download,
  Expand,
  Film,
  Image as ImageIcon,
  Loader2,
  Play,
  RefreshCw,
  X,
} from 'lucide-react';
import type { ChatMessage, VideoEvent } from '@/types/video-event';
import type { LibraryVideo } from '@/types/workspace';
import { WorkspaceDialog } from './WorkspaceDialog';

export const jobLabels: Record<string, string> = {
  QUEUED: 'Đang xếp hàng',
  SUBMITTING: 'Đang gửi',
  SUBMITTED: 'Đã gửi',
  GENERATING: 'Đang tạo',
  MERGING: 'Đang ghép cảnh',
  COMPLETED: 'Hoàn thành',
  FAILED: 'Có lỗi',
  CANCELLED: 'Đã hủy',
  NEEDS_USER_ACTION: 'Cần kiểm tra',
};
export const terminal = ['COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_USER_ACTION'];
export type VideoEntry = { message: ChatMessage; event: VideoEvent };
type Asset = {
  id: string;
  title: string;
  image?: string | null;
  video?: string | null;
  status?: string;
  eventId?: string;
  job?: LibraryVideo;
};

export function AssetCanvas({
  entries,
  jobs,
  allVideos,
  selectedId,
  loading,
  onSelect,
  onCancel,
  onMerge,
}: {
  entries: VideoEntry[];
  jobs: LibraryVideo[];
  allVideos: boolean;
  selectedId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onCancel: (id: string) => Promise<void>;
  onMerge: (id: string) => Promise<void>;
}) {
  const [preview, setPreview] = useState<Asset | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  const assets: Asset[] = allVideos
    ? jobs.map((job) => ({
        id: job.id,
        title: job.prompt,
        video: job.video_url,
        status: job.status,
        job,
      }))
    : entries
        .slice()
        .reverse()
        .map(({ message, event }) => ({
          id: message.id,
          title: event.plan.story_plan?.core_value || event.prompt,
          eventId: message.id,
          image: event.reference_image_url,
          video: event.job?.video_url,
          status: event.job?.status || (event.project_status === 'APPROVED' ? 'APPROVED' : 'DRAFT'),
        }));
  if (!assets.length)
    return (
      <div className="flow-canvas-empty">
        <div className="flow-empty-frame">
          <i />
          <i />
          <i />
          <i />
          <Film size={38} strokeWidth={1} />
          <h1>
            {loading ? 'Đang mở dự án…' : allVideos ? 'Chưa có video' : 'Bắt đầu từ một ý tưởng'}
          </h1>
          <span className="flow-frame-counter">00:00</span>
        </div>
      </div>
    );
  return (
    <>
      <div className={clsx('flow-asset-grid', assets.length === 1 && 'single')}>
        {assets.map((asset) => {
          const pending =
            asset.status &&
            !terminal.includes(asset.status) &&
            !['DRAFT', 'APPROVED'].includes(asset.status);
          return (
            <article
              key={asset.id}
              className={clsx('flow-asset', asset.eventId === selectedId && 'selected')}
            >
              <button
                className="flow-asset-media"
                title={asset.video ? 'Xem video' : 'Xem kịch bản'}
                onClick={() => {
                  if (asset.eventId && !asset.video) onSelect(asset.eventId);
                  if (asset.video) setPreview(asset);
                }}
              >
                {asset.video ? (
                  <video
                    src={`${asset.video}#t=0.1`}
                    poster={asset.image || undefined}
                    muted
                    playsInline
                    preload="metadata"
                  />
                ) : asset.image ? (
                  <img src={asset.image} alt={asset.title} />
                ) : (
                  <div className="flow-media-placeholder">
                    <Clapperboard size={34} strokeWidth={1} />
                    <span>{pending ? 'Đang tạo video' : 'Bản nháp chưa có ảnh'}</span>
                  </div>
                )}
                {asset.video && (
                  <span className="flow-play">
                    <Play size={22} fill="currentColor" />
                  </span>
                )}
                <span className={`flow-asset-status ${asset.status?.toLowerCase()}`}>
                  {pending && <Loader2 className="spin" size={12} />}
                  {asset.status === 'DRAFT'
                    ? 'Bản nháp'
                    : asset.status === 'APPROVED'
                      ? 'Đã duyệt'
                      : jobLabels[asset.status || ''] || asset.status}
                </span>
              </button>
              <div className="flow-asset-caption">
                <span>
                  {asset.video ? <Film size={15} /> : <ImageIcon size={15} />}
                  <span title={asset.title}>{asset.title}</span>
                </span>
                {asset.video && asset.eventId && (
                  <button
                    className="flow-icon"
                    title="Xem kịch bản"
                    onClick={() => onSelect(asset.eventId!)}
                  >
                    <Clapperboard size={16} />
                  </button>
                )}
                {(asset.video || asset.image) && (
                  <button
                    className="flow-icon"
                    title="Mở bản xem lớn"
                    onClick={() => setPreview(asset)}
                  >
                    <Expand size={16} />
                  </button>
                )}
                {asset.video && (
                  <a
                    className="flow-icon"
                    title="Tải video"
                    href={`${asset.video}?download=true`}
                    download
                  >
                    <Download size={16} />
                  </a>
                )}
              </div>
              {asset.job?.error && <p className="flow-asset-error">{asset.job.error}</p>}
              {asset.job && pending && !asset.job.story_batch && (
                <button
                  className="flow-button subtle"
                  disabled={asset.job.cancel_requested || actionId === asset.id}
                  onClick={async () => {
                    setActionId(asset.id);
                    try {
                      await onCancel(asset.id);
                    } finally {
                      setActionId(null);
                    }
                  }}
                >
                  <X size={14} />
                  {asset.job.cancel_requested ? 'Đang hủy' : 'Hủy tác vụ'}
                </button>
              )}
              {asset.job?.story_batch && asset.job.error === 'story_merge_failed' && (
                <button
                  className="flow-button"
                  disabled={actionId === asset.id}
                  onClick={async () => {
                    setActionId(asset.id);
                    try {
                      await onMerge(asset.id);
                    } finally {
                      setActionId(null);
                    }
                  }}
                >
                  <RefreshCw size={14} />
                  Ghép lại video
                </button>
              )}
            </article>
          );
        })}
      </div>
      {preview && (
        <WorkspaceDialog
          title={preview.video ? 'Video' : 'Ảnh tham chiếu'}
          wide
          onClose={() => setPreview(null)}
        >
          <div className="flow-media-viewer">
            {preview.video ? (
              <video src={preview.video} controls playsInline autoPlay={false} preload="metadata" />
            ) : (
              <img src={preview.image || ''} alt={preview.title} />
            )}
            <p>{preview.title}</p>
            {preview.video && (
              <a className="flow-button" href={`${preview.video}?download=true`} download>
                <Download size={16} />
                Tải video
              </a>
            )}
          </div>
        </WorkspaceDialog>
      )}
    </>
  );
}
