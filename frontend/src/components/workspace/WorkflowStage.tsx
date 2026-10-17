'use client';

import {
  Check,
  Clapperboard,
  Download,
  Film,
  Image as ImageIcon,
  ImagePlus,
  Loader2,
  RefreshCw,
  Sparkles,
  Timer,
} from 'lucide-react';
import { VideoEventCard } from '@/components/chat/VideoEventCard';
import { useImagePreview } from '@/hooks/use-image-preview';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';
import { jobLabels, terminal, type VideoEntry } from './AssetCanvas';

export type WorkflowStep = 'brief' | 'plan' | 'result';
export const workflowSteps: { id: WorkflowStep; label: string }[] = [
  { id: 'brief', label: 'Yêu cầu' },
  { id: 'plan', label: 'Kịch bản' },
  { id: 'result', label: 'Thành phẩm' },
];

export function WorkflowStage({
  step,
  selected,
  workspace: w,
  onStep,
}: {
  step: WorkflowStep;
  selected?: VideoEntry;
  workspace: VideoWorkspaceController;
  onStep: (step: WorkflowStep) => void;
}) {
  const preview = useImagePreview(w.inputImage);
  const event = selected?.event;
  const job = event?.job;
  const outdated = Boolean(
    event?.project_current_draft_id && event.project_current_draft_id !== event.plan.id,
  );
  const complete = job?.status === 'COMPLETED' && Boolean(job.video_url);
  const pending = job && !terminal.includes(job.status);
  // Every scene finished; only joining them failed, which can be retried without new renders.
  const mergeFailed = Boolean(
    job?.story_batch && job.status === 'FAILED' && job.error?.startsWith('story_merge_failed'),
  );
  if (step === 'plan' && selected)
    return (
      <div className="workflow-storyboard">
        <div className="workflow-section-heading">
          <div>
            <span className="workflow-eyebrow">Bước 2 · Storyboard</span>
            <h2>Kịch bản & hình ảnh</h2>
          </div>
          <span className="workflow-draft-label">
            {event?.project_status === 'APPROVED' ? 'Đã duyệt' : 'Bản nháp'}
          </span>
        </div>
        {outdated && (
          <p className="flow-outdated">Phiên bản cũ · chọn bản nháp mới nhất để tạo video.</p>
        )}
        {(selected.event.plan.input_asset_ids?.length || 0) > 1 && (
          <div className="workflow-campaign-images" aria-label="Ảnh đầu vào của kịch bản">
            {selected.event.plan.input_asset_ids!.map((id, index) => (
              <img key={id} src={`/api/v1/assets/${id}/file`} alt={`Ảnh tham khảo ${index + 1}`} />
            ))}
          </div>
        )}
        <VideoEventCard
          key={selected.event.plan.id}
          event={selected.event}
          compact
          storyboard
          busy={Boolean(w.videoBusyId) || w.sending || outdated}
          onGenerate={(paid) => void w.generateVideo(selected.message, selected.event, paid)}
          onApproveAndGenerate={(paid) =>
            void w.generateVideo(selected.message, selected.event, paid, true)
          }
          onApprove={() => void w.approveVideo(selected.message, selected.event)}
          onReplan={(prompt) => void w.reviseVideo(selected.message, selected.event, prompt)}
          onPrepareReference={() => void w.prepareReference(selected.message, selected.event)}
          onRegenerateReference={() =>
            void w.regenerateFailedReference(selected.message, selected.event)
          }
          onReviseFailedScene={(index, prompt, voice) =>
            void w.reviseFailedScene(selected.message, index, prompt, voice)
          }
        />
        {job && (
          <button className="flow-button" onClick={() => onStep('result')}>
            <Film size={16} />
            {complete ? 'Xem video thành phẩm' : 'Xem tiến độ tạo video'}
          </button>
        )}
      </div>
    );
  if (step === 'result' && event)
    return (
      <div className="workflow-result">
        <div className="workflow-section-heading">
          <div>
            <span className="workflow-eyebrow">Bước 3 · Xuất video</span>
            <h2>{complete ? 'Video thành phẩm' : 'Sản xuất video'}</h2>
          </div>
          {complete && (
            <span className="workflow-ready">
              <Check size={14} />
              Hoàn thành
            </span>
          )}
        </div>
        <div className="workflow-result-media">
          {complete ? (
            <video
              src={job!.video_url!}
              poster={event.reference_image_url || undefined}
              controls
              playsInline
              preload="metadata"
            />
          ) : (
            <>
              {event.reference_image_url && (
                <img src={event.reference_image_url} alt="Ảnh tham chiếu của video đang tạo" />
              )}
              <div className="workflow-production-status">
                {pending || w.renderingId ? (
                  <Loader2 className="spin" size={28} />
                ) : (
                  <Film size={28} />
                )}
                <strong>
                  {w.renderingId
                    ? 'Đang gửi yêu cầu tạo video'
                    : job
                      ? jobLabels[job.status] || job.status
                      : 'Kịch bản chưa được duyệt'}
                </strong>
                {job?.clip_progress && (
                  <span>
                    {job.clip_progress.completed}/{job.clip_progress.total} cảnh đã hoàn thành
                  </span>
                )}
              </div>
            </>
          )}
        </div>
        <div className="workflow-result-caption">
          <p>{event.plan.story_plan?.core_value || event.prompt}</p>
          {complete && (
            <a className="workflow-primary" href={`${job!.video_url}?download=true`} download>
              <Download size={17} />
              Tải video
            </a>
          )}
        </div>
        <ol className="workflow-production-track">
          <li className={job ? 'done' : ''}>
            <Check size={15} />
            <span>Kịch bản</span>
          </li>
          <li className={complete || job?.status === 'MERGING' ? 'done' : pending ? 'active' : ''}>
            <Clapperboard size={15} />
            <span>
              Tạo cảnh
              {job?.clip_progress
                ? ` · ${job.clip_progress.completed}/${job.clip_progress.total}`
                : ''}
            </span>
          </li>
          <li className={complete ? 'done' : job?.status === 'MERGING' ? 'active' : ''}>
            <Film size={15} />
            <span>Ghép video</span>
          </li>
          <li className={complete ? 'done' : ''}>
            <Download size={15} />
            <span>Thành phẩm</span>
          </li>
        </ol>
        {job?.error && (
          <p className="flow-inline-error" role="alert">
            {mergeFailed
              ? 'Các cảnh đã tạo xong nhưng bước ghép thành video bị lỗi. Bấm "Ghép lại video" để thử lại; các cảnh không bị tạo lại nên không trừ thêm credit.'
              : job.error}
          </p>
        )}
        {mergeFailed ? (
          <button
            className="flow-button primary"
            disabled={Boolean(w.videoBusyId)}
            onClick={() => void w.retryMerge(selected!.message, event)}
          >
            {w.videoBusyId ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
            <span>{w.videoBusyId ? 'Đang ghép video' : 'Ghép lại video'}</span>
          </button>
        ) : (
          <button className="flow-button" onClick={() => onStep('plan')}>
            <Clapperboard size={16} />
            <span>{job?.error ? 'Kiểm tra kịch bản & thử lại' : 'Xem kịch bản'}</span>
          </button>
        )}
      </div>
    );
  const image = preview || event?.reference_image_url;
  const empty = {
    brief: ['Bắt đầu một video mới', 'Ba bước để có video quảng cáo hoàn chỉnh từ ảnh sản phẩm.'],
    plan: ['Chưa có kịch bản', 'Nhập thông tin sản phẩm ở khung bên trái rồi bấm Lập kịch bản.'],
    result: ['Chưa có video', 'Duyệt một kịch bản để bắt đầu tạo video.'],
  }[step];
  if (!image)
    return (
      <div className="workflow-reference-stage">
        <div className="workflow-empty">
          <span className="workflow-empty-icon">
            <Clapperboard size={26} strokeWidth={1.6} />
          </span>
          <strong>{empty[0]}</strong>
          <span>{empty[1]}</span>
          <ol className="workflow-empty-steps">
            <li>
              <ImagePlus size={20} />
              <b>Thêm ảnh & thông tin sản phẩm</b>
              <span>Tối đa 6 ảnh kèm tên, chất liệu, giá và ưu đãi của sản phẩm.</span>
            </li>
            <li>
              <Sparkles size={20} />
              <b>AI lập kịch bản</b>
              <span>Hook mở đầu, lời thoại tiếng Việt và ảnh mở đầu cho từng cảnh.</span>
            </li>
            <li>
              <Film size={20} />
              <b>Duyệt và tạo video</b>
              <span>Theo dõi tiến độ từng cảnh, tự ghép và tải video hoàn chỉnh.</span>
            </li>
          </ol>
        </div>
        {event && (
          <button className="flow-button" onClick={() => onStep('plan')}>
            <Clapperboard size={16} />
            Mở kịch bản
          </button>
        )}
      </div>
    );
  return (
    <div className="workflow-reference-stage">
      <div className="workflow-section-heading">
        <div>
          <span className="workflow-eyebrow">Hướng hình ảnh</span>
          <h2>Khung hình tham chiếu</h2>
        </div>
        <ImageIcon size={19} />
      </div>
      <div className="workflow-reference-media">
        <img src={image} alt="Khung hình tham chiếu cho kịch bản" />
      </div>
      <div className="workflow-reference-caption">
        <span>
          <ImageIcon size={15} />
          {preview ? w.inputImage?.name : 'Ảnh tham chiếu chung'}
        </span>
        <span>
          <Timer size={15} />
          {w.targetDuration ? `${w.targetDuration} giây` : 'Thời lượng tự động'}
        </span>
      </div>
      {event && (
        <button className="flow-button" onClick={() => onStep('plan')}>
          <Clapperboard size={16} />
          Mở kịch bản
        </button>
      )}
    </div>
  );
}
