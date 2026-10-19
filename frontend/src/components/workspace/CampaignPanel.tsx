'use client';

import { useState } from 'react';
import {
  ArrowUpRight,
  CalendarDays,
  Check,
  CircleAlert,
  Clapperboard,
  Download,
  Film,
  LayoutGrid,
  List,
  Loader2,
  Play,
  RefreshCw,
  X,
} from 'lucide-react';
import type { MarketingCampaign, CampaignItem } from '@/types/marketing-campaign';
import { readyToRender } from '@/hooks/use-video-workspace';
import { jobActive, jobLabel, type VideoEntry } from './AssetCanvas';
import { CreditCost } from './CreditCost';

const labels = {
  QUEUED: 'Đang xếp hàng',
  WORKING: 'AI đang lập kế hoạch',
  DRAFTING: 'Đang lập kịch bản',
  COMPLETED: 'Kế hoạch đã sẵn sàng',
  PARTIAL: 'Một số kịch bản cần thử lại',
  FAILED: 'Chưa hoàn tất',
  CANCELLED: 'Đã dừng',
};
const stages = { awareness: 'Nhận diện', consideration: 'Cân nhắc', conversion: 'Chuyển đổi' };
type StageFilter = 'all' | CampaignItem['funnel_stage'];
const assetFile = (id?: string) => (id ? `/api/v1/assets/${id}/file` : null);

export function CampaignPanel({
  campaign,
  busy,
  entries,
  renderBusy,
  batchRender,
  onOpen,
  onRetry,
  onCancel,
  onRenderAll,
}: {
  campaign: MarketingCampaign;
  busy: boolean;
  entries: VideoEntry[];
  renderBusy: boolean;
  batchRender: { done: number; total: number } | null;
  onOpen: (item: CampaignItem) => void;
  onRetry: (confirmed: boolean) => void;
  onCancel: () => void;
  onRenderAll: (entries: VideoEntry[], paid: boolean) => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [confirmRender, setConfirmRender] = useState(false);
  const [filter, setFilter] = useState<StageFilter>('all');
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const active = ['QUEUED', 'WORKING', 'DRAFTING'].includes(campaign.status);
  const entryOf = (item: CampaignItem) =>
    entries.find((entry) => entry.message.id === item.message_id);
  const ready = campaign.items.flatMap((item) => {
    const entry = entryOf(item);
    return entry && readyToRender(entry.event) ? [entry] : [];
  });
  const paid = ready.some((entry) => entry.event.plan.mode === 'live');
  const items = campaign.items
    .filter((item) => filter === 'all' || item.funnel_stage === filter)
    .sort((a, b) => a.publish_day - b.publish_day || a.index - b.index);
  const productImage = (item: CampaignItem) =>
    assetFile(campaign.input_asset_ids[(item.index - 1) % campaign.input_asset_ids.length]);
  const scenes = ready.reduce(
    (total, entry) => total + (entry.event.plan.story_plan?.clips.length || 0),
    0,
  );
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(campaign, null, 2)], { type: 'application/json' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `marketing-${campaign.id}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="workflow-campaign" aria-label="Kế hoạch marketing">
      <div className="workflow-section-heading">
        <div>
          <span className="workflow-eyebrow">Kế hoạch marketing</span>
          <h2>Chiến dịch marketing</h2>
        </div>
        <div className="workflow-campaign-actions">
          {campaign.strategy && (
            <button className="flow-icon" title="Tải kế hoạch JSON" onClick={download}>
              <Download size={18} />
            </button>
          )}
          {active && (
            <button
              className="flow-icon"
              title="Dừng lập kịch bản còn lại"
              disabled={busy || campaign.cancel_requested}
              onClick={onCancel}
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>
      <p className="workflow-campaign-brief">{campaign.brief}</p>
      <div className={`workflow-campaign-progress ${campaign.status.toLowerCase()}`} role="status">
        {active ? (
          <Loader2 size={16} className="spin" />
        ) : campaign.status === 'COMPLETED' ? (
          <Check size={16} />
        ) : (
          <CircleAlert size={16} />
        )}
        <span>
          {campaign.cancel_requested && active
            ? 'Đang dừng sau yêu cầu hiện tại'
            : labels[campaign.status]}{' '}
          · {campaign.completed}/{campaign.script_count} kịch bản
        </span>
      </div>
      <progress
        value={campaign.completed}
        max={campaign.script_count}
        aria-label="Tiến độ kịch bản"
      />
      {campaign.input_asset_ids.length > 0 && (
        <div className="workflow-campaign-images">
          {campaign.input_asset_ids.map((id, index) => (
            <img key={id} src={`/api/v1/assets/${id}/file`} alt={`Ảnh sản phẩm ${index + 1}`} />
          ))}
        </div>
      )}
      {campaign.strategy && (
        <>
          <div className="workflow-campaign-strategy">
            <div>
              <small>Sản phẩm</small>
              <p>{campaign.strategy.product_summary}</p>
            </div>
            <div>
              <small>Khách hàng</small>
              <p>{campaign.strategy.audience}</p>
            </div>
            <div>
              <small>Mục tiêu</small>
              <p>{campaign.strategy.objective}</p>
            </div>
          </div>
          <p className="workflow-campaign-positioning">{campaign.strategy.positioning}</p>
          <div className="workflow-campaign-pillars">
            {campaign.strategy.content_pillars.map((pillar, index) => (
              <span key={index}>{pillar}</span>
            ))}
          </div>
          {campaign.strategy.needs_confirmation.length > 0 && (
            <details className="workflow-campaign-facts">
              <summary>
                Thông tin cần xác nhận · {campaign.strategy.needs_confirmation.length}
              </summary>
              <ul>
                {campaign.strategy.needs_confirmation.map((fact, index) => (
                  <li key={index}>{fact}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
      {(ready.length > 0 || batchRender) && (
        <div className="workflow-campaign-render">
          <div>
            <strong>
              {batchRender
                ? `Đang gửi ${batchRender.done}/${batchRender.total} video…`
                : `Tạo cùng lúc ${ready.length} video`}
            </strong>
            <span>
              {batchRender
                ? 'Các video đã gửi bắt đầu chạy song song, mỗi video trên một luồng riêng.'
                : `Duyệt ${ready.length} kịch bản chưa tạo (${scenes} cảnh). Các video chạy song song; khi hết luồng trống thì cảnh tự xếp hàng.`}
            </span>
            {paid && !batchRender && (
              <label className="gpt-paid-confirm">
                <input
                  type="checkbox"
                  checked={confirmRender}
                  disabled={renderBusy}
                  onChange={(event) => setConfirmRender(event.target.checked)}
                />
                Tôi đồng ý tạo {ready.length} video có thể phát sinh phí
              </label>
            )}
            {!batchRender && <CreditCost plans={ready.map((entry) => entry.event.plan)} />}
          </div>
          <button
            className="workflow-primary"
            disabled={renderBusy || !ready.length || (paid && !confirmRender)}
            onClick={() => {
              onRenderAll(ready, confirmRender);
              setConfirmRender(false);
            }}
          >
            {batchRender ? <Loader2 size={17} className="spin" /> : <Clapperboard size={17} />}
            {batchRender ? 'Đang gửi' : 'Tạo tất cả video'}
          </button>
        </div>
      )}
      <div className="workflow-campaign-items-toolbar">
        <div className="workflow-campaign-filter" aria-label="Lọc theo giai đoạn">
          {(['all', 'awareness', 'consideration', 'conversion'] as const).map((key) => {
            const count =
              key === 'all'
                ? campaign.items.length
                : campaign.items.filter((item) => item.funnel_stage === key).length;
            if (key !== 'all' && !count) return null;
            return (
              <button
                key={key}
                type="button"
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {key === 'all' ? 'Tất cả' : stages[key]}
                <span>{count}</span>
              </button>
            );
          })}
        </div>
        <div className="workflow-campaign-view" aria-label="Kiểu hiển thị">
          <button
            type="button"
            title="Dạng lưới"
            aria-pressed={view === 'grid'}
            onClick={() => setView('grid')}
          >
            <LayoutGrid size={16} />
          </button>
          <button
            type="button"
            title="Dạng danh sách"
            aria-pressed={view === 'list'}
            onClick={() => setView('list')}
          >
            <List size={16} />
          </button>
        </div>
      </div>
      <div className={`workflow-campaign-items ${view}`}>
        {items.map((item) => (
          <CampaignCard
            key={item.index}
            item={item}
            entry={entryOf(item)}
            fallbackImage={productImage(item)}
            busy={busy}
            onOpen={onOpen}
          />
        ))}
      </div>
      {campaign.error && (
        <p className="flow-inline-error" role="alert">
          {campaign.error}
        </p>
      )}
      {['FAILED', 'PARTIAL', 'CANCELLED'].includes(campaign.status) &&
        campaign.completed < campaign.script_count && (
          <div className="workflow-campaign-retry">
            <label className="gpt-paid-confirm">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={busy}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              Tôi đồng ý phí LLM khi thử lại phần còn thiếu
            </label>
            <button
              className="flow-button"
              disabled={busy || !confirmed}
              onClick={() => {
                onRetry(confirmed);
                setConfirmed(false);
              }}
            >
              <RefreshCw size={15} />
              Lập tiếp phần còn thiếu
            </button>
          </div>
        )}
    </section>
  );
}

function itemState(item: CampaignItem, entry?: VideoEntry) {
  const job = entry?.event.job;
  if (job) {
    const done = job.status === 'COMPLETED';
    const pending = jobActive(job);
    const progress =
      job.clip_progress && !done
        ? ` · ${job.clip_progress.completed}/${job.clip_progress.total} cảnh`
        : '';
    return {
      tone: done ? 'success' : pending ? 'accent' : 'danger',
      icon: done ? 'check' : pending ? 'spin' : 'alert',
      label: `${jobLabel(job)}${progress}`,
    };
  }
  if (item.plan) return { tone: 'neutral', icon: null, label: 'Kịch bản sẵn sàng' };
  if (item.status === 'RUNNING')
    return { tone: 'accent', icon: 'spin', label: 'Đang lập kịch bản' };
  if (item.status === 'FAILED')
    return { tone: 'danger', icon: 'alert', label: 'Lập kịch bản thất bại' };
  if (item.status === 'PENDING')
    return { tone: 'neutral', icon: null, label: 'Đang chờ lập kịch bản' };
  return { tone: 'neutral', icon: null, label: 'Đã dừng' };
}

function CampaignCard({
  item,
  entry,
  fallbackImage,
  busy,
  onOpen,
}: {
  item: CampaignItem;
  entry?: VideoEntry;
  fallbackImage: string | null;
  busy: boolean;
  onOpen: (item: CampaignItem) => void;
}) {
  const plan = entry?.event.plan || item.plan;
  const job = entry?.event.job;
  const clips = plan?.story_plan?.clips || [];
  const duration =
    job?.duration ||
    clips.reduce((total, clip) => total + (clip.duration_seconds || 0), 0) ||
    plan?.spec.output.duration_seconds;
  // Best available frame: the finished video, then the shared reference anchor, then product photos.
  const video = job?.status === 'COMPLETED' ? job.video_url : null;
  const anchor = entry?.event.reference_image_url || null;
  const image =
    anchor ||
    assetFile(plan?.spec.references?.[0]?.asset_id || plan?.input_asset_ids?.[0]) ||
    fallbackImage;
  const fit = (video || anchor) && plan?.spec.output.aspect_ratio !== '9:16' ? 'cover' : 'contain';
  const state = itemState(item, entry);
  const drafting = !item.plan && ['PENDING', 'RUNNING'].includes(item.status);
  return (
    <article className="workflow-campaign-card">
      <button
        type="button"
        className={`workflow-campaign-thumb ${fit}`}
        disabled={!item.plan || busy}
        onClick={() => onOpen(item)}
        aria-label={`${job ? 'Xem video' : 'Mở kịch bản'}: ${item.title}`}
      >
        {image && (
          <img className="workflow-campaign-thumb-backdrop" src={image} alt="" aria-hidden />
        )}
        {video ? (
          <video
            src={`${video}#t=0.1`}
            poster={anchor || undefined}
            muted
            loop
            playsInline
            preload="metadata"
            onMouseEnter={(event) => void event.currentTarget.play().catch(() => undefined)}
            onMouseLeave={(event) => event.currentTarget.pause()}
          />
        ) : image ? (
          <img src={image} alt="" loading="lazy" />
        ) : (
          <span className="workflow-campaign-thumb-empty">
            <Film size={28} strokeWidth={1.5} />
          </span>
        )}
        {drafting && <span className="workflow-campaign-thumb-shimmer" />}
        <span className="workflow-campaign-thumb-index">#{item.index}</span>
        <span className={`workflow-campaign-thumb-status ${state.tone}`}>
          {state.icon === 'spin' && <Loader2 size={12} className="spin" />}
          {state.icon === 'check' && <Check size={12} />}
          {state.icon === 'alert' && <CircleAlert size={12} />}
          {state.label}
        </span>
        {clips.length > 0 && (
          <span className="workflow-campaign-thumb-info">
            {clips.length} cảnh{duration ? ` · ${Math.round(duration)}s` : ''}
          </span>
        )}
        {video && (
          <span className="workflow-campaign-thumb-play">
            <Play size={18} fill="currentColor" />
          </span>
        )}
      </button>
      <div className="workflow-campaign-card-body">
        <div className="workflow-campaign-item-meta">
          <span>
            <CalendarDays size={13} />
            Ngày {item.publish_day} · {item.channel}
          </span>
          <span className={`workflow-campaign-stage ${item.funnel_stage}`}>
            {stages[item.funnel_stage]}
          </span>
        </div>
        <h3 title={item.title}>{item.title}</h3>
        <blockquote title={item.hook}>
          <span>{item.hook}</span>
        </blockquote>
        <div className="workflow-campaign-cta">
          <small>CTA</small>
          <span>{item.cta}</span>
        </div>
        <details>
          <summary>Chi tiết kịch bản{clips.length ? ` · ${clips.length} cảnh` : ''}</summary>
          <dl>
            <dt>Góc tiếp cận</dt>
            <dd>{item.angle}</dd>
            <dt>Caption</dt>
            <dd>{item.caption}</dd>
            <dt>Hình ảnh</dt>
            <dd>{item.visual_direction}</dd>
          </dl>
          {clips.length > 0 && (
            <ol className="workflow-campaign-beats">
              {clips.map((clip) => (
                <li key={clip.index}>
                  <b>Cảnh {clip.index}</b>
                  <span>{clip.beat}</span>
                </li>
              ))}
            </ol>
          )}
        </details>
        {item.error && <p className="flow-inline-error">{item.error}</p>}
        {item.plan && (
          <div className="workflow-campaign-item-actions">
            <button className="flow-button" disabled={busy} onClick={() => onOpen(item)}>
              {job ? 'Xem video' : 'Mở kịch bản'}
              <ArrowUpRight size={15} />
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
