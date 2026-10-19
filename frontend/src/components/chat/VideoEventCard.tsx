'use client';

import { useEffect, useState } from 'react';
import {
  CheckCircle2,
  Clapperboard,
  Download,
  ImagePlus,
  Loader2,
  Play,
  RefreshCw,
} from 'lucide-react';
import type { VideoEvent } from '@/types/video-event';
import { videoModelLabel } from '@/types/workspace';
import { CreditCost } from '@/components/workspace/CreditCost';

export function VideoEventCard({
  event,
  busy,
  onGenerate,
  onReplan,
  onReviseFailedScene,
  onPrepareReference,
  onRegenerateReference,
  onApprove,
  compact = false,
  storyboard = false,
  onApproveAndGenerate,
}: {
  event: VideoEvent;
  busy: boolean;
  onGenerate: (confirmPaid: boolean) => void;
  onReplan: (prompt: string) => void;
  onReviseFailedScene: (sceneIndex: number, videoPrompt: string, voiceScript: string) => void;
  onPrepareReference: () => void;
  onRegenerateReference: () => void;
  onApprove: () => void;
  compact?: boolean;
  storyboard?: boolean;
  onApproveAndGenerate?: (paid: boolean) => void;
}) {
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [revision, setRevision] = useState('');
  const clips = event.plan.story_plan?.clips || [];
  const direct = event.plan.creation_mode === 'direct';
  const issueLabels: Record<string, string> = {
    visual_reference_drift: 'Khung cuối cảnh lệch nhiều so với ảnh gốc',
    large_anchor_visual_change: 'Hình ảnh lệch nhiều so với ảnh tham chiếu',
    visual_identity_unverified_planned_transition:
      'Chưa xác minh được khuôn mặt sau khi đổi góc máy',
    native_audio_leading_silence: 'Âm thanh im lặng quá lâu ở đầu cảnh',
    continuous_scene_leading_silence_preserved:
      'Giữ khoảng lặng đầu cảnh để không bỏ tư thế nối cảnh',
    native_audio_trailing_silence: 'Âm thanh im lặng quá lâu ở cuối cảnh',
    native_speech_unintelligible: 'Lời nói không rõ',
    native_speech_mismatch: 'Lời nói không khớp kịch bản',
    native_speech_repetition: 'Lời nói bị lặp',
    native_speech_incomplete: 'Lời thoại chưa được nói đủ',
    native_speech_boundary_repetition: 'Lời thoại lặp lại từ cảnh trước',
    native_audio_missing_speech: 'Cảnh thiếu lời nói',
    native_speech_rate_outlier: 'Tốc độ nói chưa tự nhiên',
    native_speech_rate_change: 'Tốc độ nói thay đổi giữa các cảnh',
    native_audio_volume_change: 'Âm lượng thay đổi giữa các cảnh',
    native_voice_identity_unverified: 'Chưa xác minh được cùng chất giọng giữa các cảnh',
    video_trimmed_to_voice_duration: 'Đã rút ngắn video để bỏ khoảng im lặng',
    native_speech_check_unavailable: 'Chưa kiểm tra được lời nói',
  };
  const warnings = (event.quality_reports || []).filter((report) =>
    ['WARN', 'FAIL'].includes(report.status),
  );
  const warningMessages = [
    ...new Set(
      warnings.flatMap((report) =>
        report.issues.map(
          (issue) =>
            `${report.scene_index ? `Cảnh ${report.scene_index}: ` : ''}${issueLabels[issue] || issue}`,
        ),
      ),
    ),
  ];
  const job = event.job;
  const plannedDuration = clips.length
    ? clips.reduce((total, clip) => total + (clip.duration_seconds ?? 8), 0)
    : event.plan.spec.output.duration_seconds;
  const duration = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 }).format(
    job?.status === 'COMPLETED' && job.duration != null ? job.duration : plannedDuration,
  );
  useEffect(() => {
    setConfirmPaid(false);
  }, [event.plan.id, job?.id, job?.status]);
  const failedClip =
    job?.status === 'FAILED' && job.retryable
      ? clips.find((clip) => clip.index === (job.clip_progress?.completed || 0) + 1)
      : undefined;
  // Codes and provider messages reach admins only; members get a plain notice in `error`.
  const technical = job?.error_code || '';
  const retryPending = Boolean(job?.auto_retry?.pending);
  const likenessRejected = /prominent person|protected likeness|celebrity/i.test(technical);
  const providerRejected =
    likenessRejected ||
    /content does not comply|platform regulations|bộ lọc an toàn|safety filter/i.test(technical);
  const speechRejected =
    technical.startsWith('native_speech_quality_failed') ||
    technical.startsWith('native_audio_missing') ||
    false;
  const visualRejected = technical.startsWith('visual_reference_drift');
  const editableClips =
    (providerRejected || speechRejected || visualRejected) && failedClip
      ? clips.filter((clip) => clip.index >= failedClip.index)
      : [];
  const unresolvedText = /\[[^\]\n]{2,100}\]|\b(?:tên|ten)\s+(?:cuốn\s+)?(?:sách|sach)\b/i;
  const hasPlaceholders = editableClips.some((clip) =>
    unresolvedText.test(`${clip.voice_script} ${clip.video_prompt}`),
  );
  const storyHasPlaceholders = clips.some((clip) =>
    unresolvedText.test(`${clip.voice_script} ${clip.video_prompt}`),
  );
  const complete = job?.status === 'COMPLETED';
  const finished =
    complete ||
    (['FAILED', 'CANCELLED', 'NEEDS_USER_ACTION'].includes(job?.status || '') && !retryPending);
  const retryPreparation = Boolean(
    job?.status === 'FAILED' &&
    job.story_batch &&
    (job.retryable === true ||
      (job.clip_progress?.completed === 0 &&
        (technical === 'clip_preparation_failed' ||
          technical.startsWith('clip_preparation_failed:') ||
          technical.startsWith('voice_timeline_mismatch') ||
          technical.startsWith('voice_track_empty')))) &&
    !retryPending,
  );
  const retryUnstartedClip =
    job?.status === 'FAILED' &&
    !job.story_batch &&
    job.retryable &&
    technical === 'seedvis_not_started' &&
    !retryPending;
  const approved = event.project_status === 'APPROVED' || Boolean(job);
  const labels: Record<string, string> = {
    QUEUED: 'Đang xếp hàng',
    SUBMITTING: 'Đang gửi',
    SUBMITTED: 'Đã gửi',
    GENERATING: 'Đang tạo video',
    MERGING: 'Đang ghép cảnh',
    COMPLETED: 'Video đã sẵn sàng',
    FAILED: 'Tạo video thất bại',
    CANCELLED: 'Đã hủy',
    NEEDS_USER_ACTION: 'Cần kiểm tra',
  };
  return (
    <section className="gpt-video-card">
      <header>
        <Clapperboard size={17} />
        <strong>
          {job
            ? retryPending
              ? 'Đang tự thử lại'
              : labels[job.status] || job.status
            : event.plan.story_plan
              ? 'Bản nháp kịch bản'
              : direct
                ? 'Bản nháp clip'
                : 'Chưa có kịch bản LLM'}
        </strong>
        {job?.demo && <span className="gpt-demo-label">Demo</span>}
      </header>
      <p className="gpt-video-summary">
        {event.plan.story_plan?.core_value || event.plan.spec.intent.goal}
      </p>
      {event.plan.story_plan?.book_title && (
        <p className="gpt-video-summary">Tên trên bìa ảnh: {event.plan.story_plan.book_title}</p>
      )}
      <div className="gpt-video-meta">
        {event.plan.render_target && (
          <span>{videoModelLabel(event.plan.render_target.video_model)}</span>
        )}
        <span>{event.plan.spec.output.aspect_ratio}</span>
        {event.plan.story_plan?.requested_duration_seconds != null && (
          <span>Yêu cầu {event.plan.story_plan.requested_duration_seconds} giây</span>
        )}
        <span>
          {event.plan.automatic_duration && job?.duration == null
            ? 'Thời lượng tự động'
            : clips.length
              ? `${clips.length} cảnh · ${job?.status === 'COMPLETED' ? '' : 'khoảng '}${duration} giây`
              : `${duration} giây`}
        </span>
        {job?.clip_progress && (
          <span>
            {job.clip_progress.completed}/{job.clip_progress.total} cảnh
          </span>
        )}
        {job?.audio_mode === 'native' && <span>Thoại Veo · giọng chưa khóa</span>}
        {job?.requested_voice_mode === 'consistent_voice' &&
          job.voice_mode !== 'consistent_voice' && (
            <span>Master voice chưa khả dụng: thiếu alignment/lip-sync</span>
          )}
      </div>
      {!compact && !storyboard && event.reference_image_url && (
        <figure className="gpt-reference-preview">
          <img src={event.reference_image_url} alt="&#x1EA2;nh tham chi&#x1EBF;u chung cho video" />
          <figcaption>
            &#x1EA2;nh &#x111;&#x1EA7;u v&#xE0;o &#x111;&#x1B0;&#x1EE3;c d&#xF9;ng &#x111;&#x1EC3;
            gi&#x1EEF; nh&#xE2;n v&#x1EAD;t v&#xE0; b&#x1ED1;i c&#x1EA3;nh xuy&#xEA;n su&#x1ED1;t
          </figcaption>
        </figure>
      )}
      {clips.length > 0 && (
        <details
          className={storyboard ? 'gpt-video-scenes workflow-scenes' : 'gpt-video-scenes'}
          open={compact || storyboard}
        >
          <summary>Xem kịch bản {clips.length} cảnh</summary>
          <div className={storyboard ? 'workflow-scene-grid' : 'gpt-scene-list'}>
            {clips.map((clip) => (
              <div key={clip.index}>
                {storyboard && event.reference_image_url && (
                  <figure className="workflow-scene-image">
                    <SceneReferenceImage
                      fallback={event.reference_image_url}
                      projectId={event.project_id}
                      sceneIndex={clip.index}
                    />
                    <span>
                      Cảnh {clip.index} · {clip.duration_seconds ?? 8}s
                    </span>
                  </figure>
                )}
                <b>
                  Cảnh {clip.index} · {clip.beat}
                </b>
                <p>“{clip.voice_script}”</p>
                {storyboard && clip.purpose && <small>{clip.purpose}</small>}
                {storyboard && (
                  <details className="workflow-scene-direction">
                    <summary>Hình ảnh & chuyển động</summary>
                    <p>{clip.video_prompt}</p>
                  </details>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
      {event.plan.story_plan && (
        <p className="gpt-video-cost">
          {event.plan.mode === 'live'
            ? 'Tạo ảnh tham chiếu và từng clip có tiếng nói sẽ trừ credit theo bảng giá của model.'
            : 'Chế độ demo không gọi dịch vụ tạo video có tính phí.'}
        </p>
      )}
      {!job && event.plan.story_plan && !event.reference_image_url && (
        <button className="gpt-reference-action" disabled={busy} onClick={onPrepareReference}>
          <ImagePlus size={15} /> T&#x1EA1;o &#x1EA3;nh tham chi&#x1EBF;u &#x111;&#x1EC3; xem
        </button>
      )}
      {event.reference_image_url &&
        !busy &&
        (!event.project_current_draft_id || event.project_current_draft_id === event.plan.id) &&
        (job ? hasPlaceholders : storyHasPlaceholders) && (
          <button className="gpt-reference-action" disabled={busy} onClick={onPrepareReference}>
            <RefreshCw size={15} /> Thử lại đọc ảnh
          </button>
        )}
      {!job && storyHasPlaceholders && event.reference_image_url && busy && (
        <p className="gpt-video-error">AI đang hoàn thiện kịch bản từ ảnh.</p>
      )}
      {!compact && job?.video_url && (
        <div className="gpt-video-result">
          <video controls playsInline preload="metadata" src={job.video_url} />
          <a href={`${job.video_url}?download=true`} download>
            <Download size={15} /> Tải video
          </a>
        </div>
      )}
      {job?.error && (
        <p className="gpt-video-error">
          {speechRejected && failedClip
            ? `Cảnh ${failedClip.index} nói không rõ hoặc lặp lời. Các cảnh trước vẫn được giữ. Bạn có thể sửa lời thoại ngắn hơn rồi xác nhận thử lại cảnh này; lần thử mới có thể phát sinh phí.`
            : visualRejected && failedClip
              ? `Khung cuối cảnh ${failedClip.index} đã lệch nhiều so với ảnh tham chiếu nên hệ thống dừng trước khi tạo cảnh tiếp theo. Các cảnh trước được giữ lại. Hãy chỉnh hành động của cảnh này cho ổn định hơn rồi xác nhận thử lại; lần thử mới có thể phát sinh phí.`
              : providerRejected && failedClip
                ? likenessRejected
                  ? `Nhà cung cấp nghi ảnh tham chiếu có chân dung người nổi tiếng hoặc giống người được bảo vệ. Cảnh ${failedClip.index} chưa được tạo; hãy dùng ảnh khác trước khi thử lại.`
                  : `Nhà cung cấp từ chối cảnh ${failedClip.index} nhưng không cho biết lỗi nằm ở ảnh, lời thoại hay mô tả. ${job.clip_progress?.completed || 0} cảnh trước đã hoàn thành. Hãy kiểm tra cảnh này và ảnh tham chiếu trước khi thử lại.`
                : job.error}
        </p>
      )}
      {failedClip?.index === 1 &&
        providerRejected &&
        event.reference_image_url &&
        !event.plan.spec.references?.length && (
          <button
            className="gpt-reference-action"
            disabled={busy}
            title="Tạo ảnh mới bằng model đã cấu hình; có thể phát sinh phí API ảnh"
            onClick={onRegenerateReference}
          >
            <ImagePlus size={15} /> Tạo ảnh tham chiếu khác
          </button>
        )}
      {hasPlaceholders && (
        <p className="gpt-video-error">
          {busy
            ? 'AI đang hoàn thiện lời thoại từ ảnh.'
            : 'Lời thoại còn chỗ trống; AI chưa hoàn thiện được từ ảnh.'}
        </p>
      )}
      {editableClips.map((clip) => (
        <details
          key={`${clip.index}:${clip.video_prompt}:${clip.voice_script}`}
          className="gpt-video-revise"
          open={clip.index === failedClip?.index}
        >
          <summary>
            {clip.index === failedClip?.index ? 'Sửa cảnh bị lỗi' : 'Sửa cảnh chưa tạo'} · Cảnh{' '}
            {clip.index}
          </summary>
          <form
            onSubmit={(submit) => {
              submit.preventDefault();
              const data = new FormData(submit.currentTarget);
              onReviseFailedScene(
                clip.index,
                String(data.get('video_prompt') || '').trim(),
                String(data.get('voice_script') || '').trim(),
              );
            }}
          >
            <label htmlFor={`failed-scene-prompt-${clip.index}`}>Mô tả hình ảnh</label>
            <textarea
              id={`failed-scene-prompt-${clip.index}`}
              name="video_prompt"
              defaultValue={clip.video_prompt}
              minLength={30}
              maxLength={3000}
              required
            />
            <label htmlFor={`failed-scene-voice-${clip.index}`}>Lời thoại</label>
            <textarea
              id={`failed-scene-voice-${clip.index}`}
              name="voice_script"
              defaultValue={clip.voice_script}
              minLength={2}
              maxLength={500}
              required
            />
            <button type="submit" disabled={busy}>
              <RefreshCw size={15} /> Lưu cảnh {clip.index}
            </button>
          </form>
        </details>
      ))}
      {warnings.length > 0 && (
        <div className="gpt-quality-warning">{warningMessages.join(' · ')}</div>
      )}
      <details className="gpt-video-revise">
        <summary>Chỉnh sửa kịch bản</summary>
        <form
          onSubmit={(submit) => {
            submit.preventDefault();
            if (revision.trim()) onReplan(revision.trim());
          }}
        >
          <textarea
            value={revision}
            onChange={(change) => setRevision(change.target.value)}
            maxLength={2000}
            placeholder="Mô tả điều bạn muốn thay đổi trong video"
          />
          <button type="submit" disabled={busy || revision.trim().length < 5}>
            {busy ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />} Lập phiên bản
            kịch bản mới
          </button>
        </form>
      </details>
      {!job && !event.plan.story_plan && !direct ? (
        <p className="gpt-video-error">
          Bản nháp này chưa có kịch bản LLM. Hãy lập lại kịch bản trước khi duyệt.
        </p>
      ) : !job && !approved && onApproveAndGenerate ? (
        <div className="workflow-approve">
          <div className="workflow-approve-copy">
            <strong>Sẵn sàng tạo video?</strong>
            <span>
              {clips.length
                ? `Duyệt kịch bản, tạo lần lượt ${clips.length} cảnh rồi tự ghép thành video.`
                : 'Duyệt bản nháp và bắt đầu tạo clip.'}
            </span>
            {event.plan.mode === 'live' && (
              <label className="gpt-paid-confirm">
                <input
                  type="checkbox"
                  checked={confirmPaid}
                  onChange={(change) => setConfirmPaid(change.target.checked)}
                />
                Tôi đồng ý tạo video có thể phát sinh phí
              </label>
            )}
            <CreditCost plans={[event.plan]} />
          </div>
          <button
            className="gpt-video-action"
            disabled={busy || storyHasPlaceholders || (event.plan.mode === 'live' && !confirmPaid)}
            onClick={() => onApproveAndGenerate(confirmPaid)}
          >
            {busy ? <Loader2 className="spin" size={16} /> : <CheckCircle2 size={16} />} Duyệt và
            tạo video
          </button>
        </div>
      ) : !job && !approved ? (
        <button
          className="gpt-video-approve"
          disabled={busy || storyHasPlaceholders}
          onClick={onApprove}
        >
          {busy ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />} Duy&#x1EC7;t
          k&#x1ECB;ch b&#x1EA3;n n&#xE0;y
        </button>
      ) : !job ? (
        <>
          {event.plan.mode === 'live' && (
            <label className="gpt-paid-confirm">
              <input
                type="checkbox"
                checked={confirmPaid}
                onChange={(e) => setConfirmPaid(e.target.checked)}
              />
              Tôi đồng ý tạo video có thể phát sinh phí
            </label>
          )}
          <CreditCost plans={[event.plan]} />
          <button
            className="gpt-video-action"
            disabled={busy || storyHasPlaceholders || (event.plan.mode === 'live' && !confirmPaid)}
            onClick={() => onGenerate(confirmPaid)}
          >
            {busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
            {event.plan.mode === 'live' ? 'Xác nhận và tạo video' : 'Tạo video demo'}
          </button>
        </>
      ) : retryPreparation || retryUnstartedClip ? (
        <>
          {/* The render was already confirmed when it started; a retry needs no new consent. */}
          <button
            className="gpt-video-action"
            disabled={busy || hasPlaceholders}
            onClick={() => onGenerate(true)}
          >
            {busy ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            {retryUnstartedClip
              ? 'Thử tạo lại clip'
              : providerRejected && failedClip
                ? `Thử tạo lại cảnh ${failedClip.index}`
                : 'Thử lại bước chuẩn bị và tạo video'}
          </button>
        </>
      ) : !finished ? (
        <div className="gpt-video-pending">
          <Loader2 className="spin" size={16} /> Video đang được xử lý
        </div>
      ) : complete ? (
        <div className="gpt-video-ready">
          <CheckCircle2 size={16} /> Video hoàn chỉnh đã sẵn sàng
        </div>
      ) : null}
    </section>
  );
}

// Each cut scene has its own generated opening keyframe; others fall back to the shared reference.
function SceneReferenceImage({
  fallback,
  projectId,
  sceneIndex,
}: {
  fallback: string;
  projectId?: string | null;
  sceneIndex: number;
}) {
  const version = fallback.includes('/video-projects/')
    ? new URL(fallback, 'http://local').searchParams.get('v')
    : null;
  const sceneUrl =
    projectId && version
      ? `/api/v1/video-projects/${projectId}/scene-reference/${sceneIndex}?v=${version}`
      : null;
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [sceneUrl]);
  return (
    <img
      src={sceneUrl && !failed ? sceneUrl : fallback}
      alt={`Ảnh mở đầu cho cảnh ${sceneIndex}`}
      onError={() => setFailed(true)}
    />
  );
}
