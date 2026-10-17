'use client';

import type { ReactNode } from 'react';
import {
  ArrowRight,
  Clapperboard,
  Loader2,
  MessageSquare,
  RefreshCw,
  Settings2,
} from 'lucide-react';
import { StyleReferencePicker } from './StyleReferencePicker';
import { WorkspaceImages } from './WorkspaceImages';
import { ComposerCreditEstimate } from './CreditCost';
import { clipPrice, priceLabel, useCredits } from '@/lib/credits';
import { videoModelLabel } from '@/types/workspace';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';
import type { WorkspaceConfig } from '@/types/workspace';
import type { ProviderSelection } from '@/hooks/use-provider-selection';

function Segmented<T extends string | number>({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: ReactNode }[];
  disabled: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <div className="workflow-segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={value === option.value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function RatioGlyph({ ratio }: { ratio: string }) {
  const [width = 1, height = 1] = ratio.split(':').map(Number);
  const scale = 14 / Math.max(width, height);
  return (
    <i
      className="workflow-ratio-glyph"
      style={{ width: Math.max(6, width * scale), height: Math.max(6, height * scale) }}
      aria-hidden
    />
  );
}

export function WorkspaceComposer({
  workspace: w,
  config,
  providers,
  admin,
  onDiscuss,
}: {
  workspace: VideoWorkspaceController;
  config: WorkspaceConfig;
  providers: ProviderSelection;
  admin: boolean;
  onDiscuss: () => void;
}) {
  const busy = w.loading || providers.loading || w.sending || Boolean(w.videoBusyId);
  const credits = useCredits();
  const price = (provider: string, model: string) =>
    credits ? ` · ${priceLabel(clipPrice(credits.pricing, provider, model))}` : '';
  const video = w.composeMode !== 'chat';
  const campaign = w.creationScope === 'campaign' && w.composeMode === 'video';
  const canPlan =
    config.planner === 'openai' &&
    (config.provider === 'fake' || Boolean(config.native_audio)) &&
    config.story_supported !== false &&
    config.duration_control !== false;
  const phases = {
    upload: 'Đang tải ảnh',
    plan: 'AI đang lập kịch bản',
    reference: 'Đang chuẩn bị ảnh',
  };
  const maxLength = video ? 2000 : 12000;
  // Script planning starts from product facts; direct clips and chat keep a free-form request.
  const product = w.composeMode === 'video';
  const field = product
    ? {
        label: 'Thông tin sản phẩm',
        aria: 'Thông tin sản phẩm',
        placeholder:
          'Ví dụ: Bộ đồ cotton cho bé 1–6 tuổi, vải mỏng thoáng, co giãn 4 chiều, có 5 màu. Giá 159.000đ, freeship đơn từ 2 bộ. Khách hàng: các mẹ có con nhỏ.',
      }
    : video
      ? {
          label: 'Yêu cầu của bạn',
          aria: 'Mô tả video',
          placeholder:
            'Ví dụ: Video review tai nghe 20 giây, dùng nhân vật trong ảnh. Giọng nữ miền Bắc, mở đầu cuốn hút, kết thúc bằng lời mời trải nghiệm.',
        }
      : { label: 'Tin nhắn', aria: 'Tin nhắn', placeholder: 'Trao đổi ý tưởng với AI…' };
  return (
    <form
      className="workflow-composer"
      onSubmit={(event) =>
        void (!video
          ? w.sendMessage(event)
          : campaign
            ? w.createCampaign(event)
            : w.createVideo(event))
      }
      onPaste={(event) => {
        if (!video || busy) return;
        const files = Array.from(event.clipboardData.files).filter((item) =>
          item.type.startsWith('image/'),
        );
        if (files.length) {
          event.preventDefault();
          w.selectInputImages(files);
        }
      }}
    >
      <div className="workflow-brief-heading">
        <span className="workflow-eyebrow">Yêu cầu sáng tạo</span>
        <h1>{campaign ? 'Chiến dịch của bạn' : 'Video của bạn'}</h1>
        <p>
          {!video
            ? 'Trao đổi ý tưởng với AI trong dự án này.'
            : campaign
              ? 'AI lập kế hoạch và nhiều kịch bản cho cùng một sản phẩm.'
              : w.composeMode === 'clip'
                ? 'Tạo một clip trực tiếp từ mô tả và ảnh đầu vào.'
                : 'Thêm ảnh và thông tin sản phẩm, AI sẽ lập kịch bản quảng cáo từng cảnh.'}
        </p>
      </div>
      <WorkspaceImages workspace={w} disabled={busy || !video} />
      {w.composeMode === 'video' && (
        <Segmented
          label="Phạm vi lập kế hoạch"
          value={w.creationScope}
          disabled={busy}
          onChange={w.setCreationScope}
          options={[
            { value: 'single', label: 'Một video' },
            { value: 'campaign', label: 'Chiến dịch' },
          ]}
        />
      )}
      {campaign && (
        <label className="workflow-campaign-count">
          Số kịch bản
          <select
            aria-label="Số kịch bản"
            value={w.scriptCount}
            disabled={busy}
            onChange={(event) => w.setScriptCount(Number(event.target.value))}
          >
            {[3, 5, 10].map((count) => (
              <option key={count} value={count}>
                {count} video
              </option>
            ))}
          </select>
        </label>
      )}
      {w.inputImage && !config.local_image_upload && (
        <p className="flow-inline-error">
          Provider này cần cấu hình URL backend HTTPS để dùng ảnh đầu vào.
        </p>
      )}
      <div className="workflow-field">
        <label className="workflow-label" htmlFor="workflow-request">
          {field.label}
          <small>
            {w.input.length.toLocaleString('vi-VN')}/{maxLength.toLocaleString('vi-VN')}
          </small>
        </label>
        <textarea
          id="workflow-request"
          ref={w.inputRef}
          rows={5}
          value={w.input}
          onChange={(event) => w.setInput(event.target.value)}
          onKeyDown={w.onInputKeyDown}
          aria-label={field.aria}
          aria-describedby={product ? 'workflow-request-hint' : undefined}
          maxLength={maxLength}
          disabled={busy}
          placeholder={field.placeholder}
        />
        {product && (
          <span id="workflow-request-hint" className="workflow-model-caption">
            Tên, chất liệu, kích cỡ, giá, ưu đãi, khách hàng mục tiêu. AI chỉ nói những gì bạn ghi ở
            đây và đọc được từ ảnh.
          </span>
        )}
      </div>
      <div className="workflow-format-fields">
        <div className="workflow-field">
          <span className="workflow-label">Khung hình</span>
          {config.ratio_options.length <= 4 ? (
            <Segmented
              label="Tỷ lệ khung hình"
              value={w.ratio}
              disabled={busy}
              onChange={w.setRatio}
              options={config.ratio_options.map((ratio) => ({
                value: ratio,
                label: (
                  <>
                    <RatioGlyph ratio={ratio} />
                    {ratio}
                  </>
                ),
              }))}
            />
          ) : (
            <select
              aria-label="Tỷ lệ khung hình"
              value={w.ratio}
              disabled={busy}
              onChange={(event) => w.setRatio(event.target.value)}
            >
              {config.ratio_options.map((ratio) => (
                <option key={ratio}>{ratio}</option>
              ))}
            </select>
          )}
        </div>
        <div className="workflow-field">
          <span className="workflow-label">Thời lượng</span>
          {w.composeMode === 'video' ? (
            <Segmented
              label="Tổng thời lượng"
              value={w.targetDuration}
              disabled={busy}
              onChange={w.setTargetDuration}
              options={[0, 20, 30, 40].map((duration) => ({
                value: duration,
                label: duration ? `${duration} giây` : 'Tự động',
              }))}
            />
          ) : config.duration_options.length > 0 && config.duration_options.length <= 4 ? (
            <Segmented
              label="Thời lượng clip"
              value={w.duration}
              disabled={busy}
              onChange={w.setDuration}
              options={config.duration_options.map((duration) => ({
                value: duration,
                label: `${duration} giây`,
              }))}
            />
          ) : (
            <select
              aria-label="Thời lượng clip"
              value={w.duration}
              disabled={busy || !config.duration_options.length}
              onChange={(event) => w.setDuration(Number(event.target.value))}
            >
              {!config.duration_options.length && <option value={w.duration}>Tự động</option>}
              {config.duration_options.map((duration) => (
                <option key={duration} value={duration}>
                  {duration} giây
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
      {w.composeMode === 'video' && w.creationScope === 'single' && (
        <StyleReferencePicker workspace={w} disabled={busy} />
      )}
      {video && config.planner !== 'openai' && (
        <p className="workflow-model-caption" role="note">
          {admin
            ? 'Chưa có key OpenAI nên chỉ tạo được clip trực tiếp, chưa lập được kịch bản. Nhập key trong Admin › API key.'
            : 'Lập kịch bản AI đang tạm ngưng, hiện chỉ tạo được clip trực tiếp.'}
        </p>
      )}
      {video && !canPlan && config.planner === 'openai' && config.provider !== 'fake' && (
        <p className="workflow-model-caption" role="note">
          {config.duration_control === false
            ? `${config.model} không cho chọn thời lượng nên chỉ tạo được clip đơn, chưa lập được kịch bản nhiều cảnh.`
            : `${config.model} chưa có giọng nói đi kèm video nên chưa lập được kịch bản có lời thoại.`}{' '}
          Chọn model có giọng nói và thời lượng cố định (ví dụ Veo 3.1) để có kịch bản, ảnh từng
          cảnh và giọng dẫn.
        </p>
      )}
      <details className="workflow-advanced">
        <summary>
          <Settings2 size={15} />
          Model & thiết lập
        </summary>
        <label>
          Luồng tạo
          <select
            aria-label="Chế độ sáng tạo"
            value={w.composeMode}
            disabled={busy}
            onChange={(event) => {
              const mode = event.target.value as 'video' | 'clip' | 'chat';
              w.setComposeMode(mode);
              if (mode === 'chat') onDiscuss();
            }}
          >
            <option value="video" disabled={!canPlan}>
              Kịch bản video
            </option>
            <option value="clip">Clip trực tiếp</option>
            <option value="chat" disabled={config.planner !== 'openai'}>
              Trao đổi với AI
            </option>
          </select>
        </label>
        {video && providers.catalog && !admin && (
          <div className="workflow-provider-fields">
            <label>
              Model video
              <select
                aria-label="Model video"
                value={`${config.provider}:${config.model}`}
                disabled={busy}
                onChange={(event) => {
                  const [provider, ...model] = event.target.value.split(':');
                  providers.choose({
                    provider: provider as WorkspaceConfig['provider'],
                    video_model: model.join(':'),
                  });
                }}
              >
                {providers.catalog.video_providers
                  .filter((provider) => provider.enabled && provider.id !== 'fake')
                  .flatMap((provider) =>
                    provider.models.map((model) => (
                      <option
                        key={`${provider.id}:${model.id}`}
                        value={`${provider.id}:${model.id}`}
                        disabled={!provider.configured || model.available === false}
                      >
                        {videoModelLabel(model.id, model.label)}
                        {price(provider.id, model.id)}
                        {!provider.configured || model.available === false
                          ? ' · chưa khả dụng'
                          : ''}
                      </option>
                    )),
                  )}
              </select>
            </label>
          </div>
        )}
        {video && providers.catalog && admin && (
          <div className="workflow-provider-fields">
            <label>
              Provider video
              <select
                aria-label="Provider video"
                value={config.provider}
                disabled={busy}
                onChange={(event) => {
                  const provider = providers.catalog?.video_providers.find(
                    (item) => item.id === event.target.value,
                  );
                  if (provider)
                    providers.choose({
                      provider: provider.id as WorkspaceConfig['provider'],
                      video_model:
                        (
                          provider.models.find((model) => model.available !== false) ||
                          provider.models[0]
                        )?.id || '',
                    });
                }}
              >
                {providers.catalog.video_providers.map((provider) => (
                  <option key={provider.id} value={provider.id} disabled={!provider.enabled}>
                    {provider.label}
                    {!provider.configured ? (admin ? ' · chưa có key' : ' · chưa sẵn sàng') : ''}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Model video
              <select
                aria-label="Model video"
                value={config.model}
                disabled={busy}
                onChange={(event) => providers.choose({ video_model: event.target.value })}
              >
                {providers.video?.models.map((model) => (
                  <option key={model.id} value={model.id} disabled={model.available === false}>
                    {videoModelLabel(model.id, model.label)}
                    {price(config.provider, model.id)}
                    {model.available === false ? ' · chưa khả dụng' : ''}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="flow-icon"
              title="Cập nhật model"
              disabled={busy}
              onClick={() => void providers.refresh()}
            >
              <RefreshCw size={16} />
            </button>
          </div>
        )}
        {w.composeMode === 'video' && !w.inputImage && (
          <label className="flow-auto-image">
            <input
              type="checkbox"
              checked={w.createReferenceAutomatically}
              disabled={busy}
              onChange={(event) => w.setCreateReferenceAutomatically(event.target.checked)}
            />
            Tự tạo ảnh tham chiếu{config.mode === 'live' ? ' · có phí API' : ''}
          </label>
        )}
        <span className="workflow-model-caption">
          {videoModelLabel(config.model)}
          {admin ? ` · ${config.image_model}` : ''}
        </span>
      </details>
      <div className="workflow-composer-footer">
        {campaign && (
          <label className="gpt-paid-confirm">
            <input
              type="checkbox"
              checked={w.confirmPlanningCosts}
              disabled={busy}
              onChange={(event) => w.setConfirmPlanningCosts(event.target.checked)}
            />
            Tôi đồng ý phí LLM để lập {w.scriptCount} kịch bản
          </label>
        )}
        {(w.composeMode === 'clip' || config.planner === 'openai') && (
          <ComposerCreditEstimate
            provider={config.provider}
            model={config.model}
            mode={w.composeMode}
            seconds={w.composeMode === 'video' ? w.targetDuration : w.duration}
            sceneSeconds={Math.max(...config.duration_options, 8)}
            scripts={campaign ? w.scriptCount : 1}
            campaign={campaign}
          />
        )}
        <button
          className="workflow-primary workflow-plan-button"
          type="submit"
          disabled={
            busy ||
            (video && config.video_model_available === false) ||
            (campaign && !w.confirmPlanningCosts) ||
            w.input.trim().length < (video ? 5 : 1)
          }
        >
          {busy ? (
            <Loader2 size={18} className="spin" />
          ) : video ? (
            <Clapperboard size={18} />
          ) : (
            <MessageSquare size={18} />
          )}
          {w.planningPhase
            ? phases[w.planningPhase]
            : !video
              ? 'Gửi tin nhắn'
              : w.composeMode === 'clip'
                ? 'Tạo bản nháp clip'
                : campaign
                  ? 'Lập chiến dịch'
                  : 'Lập kịch bản'}
          {!busy && <ArrowRight size={17} />}
        </button>
      </div>
    </form>
  );
}
