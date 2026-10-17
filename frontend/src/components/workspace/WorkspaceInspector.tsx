'use client';

import { Clapperboard, Loader2, MessageSquare, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { VideoEventCard } from '@/components/chat/VideoEventCard';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';
import { videoEvent } from '@/types/video-event';
import type { VideoEntry } from './AssetCanvas';

export function WorkspaceInspector({
  workspace: w,
  selected,
  onSelect,
  onClose,
}: {
  workspace: VideoWorkspaceController;
  selected?: VideoEntry;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const outdated =
    selected?.event.project_current_draft_id &&
    selected.event.project_current_draft_id !== selected.event.plan.id;
  return (
    <aside className="flow-inspector" aria-label="Kịch bản và trao đổi">
      <header>
        <div>
          <Clapperboard size={16} />
          <h2>Kịch bản & trao đổi</h2>
        </div>
        <button className="flow-icon" title="Thu gọn kịch bản" onClick={onClose}>
          <X size={17} />
        </button>
      </header>
      <div className="flow-inspector-scroll" ref={w.scrollRef}>
        {selected ? (
          <>
            {outdated && (
              <p className="flow-outdated">Phiên bản cũ · chọn bản nháp mới nhất để chỉnh sửa.</p>
            )}
            <VideoEventCard
              key={selected.event.plan.id}
              event={selected.event}
              compact
              busy={Boolean(w.videoBusyId) || w.sending || Boolean(outdated)}
              onGenerate={(paid) => void w.generateVideo(selected.message, selected.event, paid)}
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
          </>
        ) : (
          <div className="flow-inspector-empty">
            <Clapperboard size={25} strokeWidth={1} />
            <p>Chưa có bản nháp</p>
          </div>
        )}
        <details className="flow-discussion" open={w.composeMode === 'chat' || !selected}>
          <summary>
            <MessageSquare size={15} />
            Trao đổi<span>{w.messages.length}</span>
          </summary>
          {w.messages.map((message) => {
            const event = videoEvent(message);
            return (
              <article key={message.id} className={`flow-message ${message.role}`}>
                <small>{message.role === 'user' ? 'Bạn' : 'VO3 AI'}</small>
                {event ? (
                  <button className="flow-message-plan" onClick={() => onSelect(message.id)}>
                    <Clapperboard size={15} />
                    <span>{event.plan.story_plan?.core_value || event.prompt}</span>
                  </button>
                ) : (
                  <div className="flow-prose">
                    <ReactMarkdown>{message.content}</ReactMarkdown>
                  </div>
                )}
              </article>
            );
          })}
          {!w.messages.length && (
            <p className="flow-empty-note">Chưa có trao đổi trong dự án này.</p>
          )}
          {w.sending && (
            <p className="flow-thinking" role="status">
              <Loader2 className="spin" size={15} />
              Đang soạn nội dung…
            </p>
          )}
        </details>
      </div>
    </aside>
  );
}
