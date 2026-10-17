'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import clsx from 'clsx';
import {
  Check,
  ChevronDown,
  Clapperboard,
  FolderOpen,
  Library,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react';
import { request } from '@/lib/chat-api';
import { useVideoWorkspace } from '@/hooks/use-video-workspace';
import { useProviderSelection } from '@/hooks/use-provider-selection';
import { videoEvent } from '@/types/video-event';
import type { LibraryVideo, WorkspaceConfig, WorkspaceUser } from '@/types/workspace';
import { providerLabel, videoModelLabel } from '@/types/workspace';
import { AssetCanvas, terminal } from './AssetCanvas';
import { ProjectHistory } from './ProjectHistory';
import { WorkspaceAuth } from './WorkspaceAuth';
import { WorkspaceComposer } from './WorkspaceComposer';
import { WorkspaceInspector } from './WorkspaceInspector';
import { WorkspaceSettings } from './WorkspaceSettings';
import { WorkspaceDialog } from './WorkspaceDialog';
import { WorkflowStage, workflowSteps, type WorkflowStep } from './WorkflowStage';
import { CampaignPanel } from './CampaignPanel';
import { ProductionBoard } from './ProductionBoard';
import { CreditBadge } from './CreditPanel';
import type { CampaignItem } from '@/types/marketing-campaign';

function Workspace({
  config: defaults,
  user,
  onLogout,
}: {
  config: WorkspaceConfig;
  user: WorkspaceUser;
  onLogout: () => void;
}) {
  const providers = useProviderSelection(defaults, user.id);
  const config = providers.config;
  const w = useVideoWorkspace(config);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [discussionOpen, setDiscussionOpen] = useState(false);
  const [allVideos, setAllVideos] = useState(false);
  const [step, setStep] = useState<WorkflowStep>('brief');
  const [campaignView, setCampaignView] = useState(false);
  const [autoSelectDraft, setAutoSelectDraft] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<LibraryVideo[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState('');
  const refreshJobs = useCallback(async () => {
    setJobsError('');
    setJobsLoading(true);
    try {
      setJobs(await request<LibraryVideo[]>('/generations'));
    } catch (reason) {
      setJobsError((reason as Error).message);
    } finally {
      setJobsLoading(false);
    }
  }, []);
  const entries = useMemo(
    () =>
      w.messages.flatMap((message) => {
        const event = videoEvent(message);
        return event ? [{ message, event }] : [];
      }),
    [w.messages],
  );
  const newestId = entries.at(-1)?.message.id;
  const newestDraft = entries.at(-1)?.event.plan.id;
  useEffect(() => {
    setAllVideos(false);
    setSelectedId(null);
    setStep('brief');
    setCampaignView(false);
    setAutoSelectDraft(true);
  }, [w.activeId]);
  useEffect(() => {
    if (newestId && autoSelectDraft) {
      setSelectedId(newestId);
      setStep('plan');
    }
  }, [newestId, newestDraft, autoSelectDraft]);
  useEffect(() => {
    if (w.planningPhase === 'plan' && w.creationScope === 'single') {
      setAutoSelectDraft(true);
      setCampaignView(false);
    }
  }, [w.planningPhase, w.creationScope]);
  const campaignId = w.marketing.selected?.id;
  useEffect(() => {
    if (campaignId) {
      setCampaignView(true);
      setAutoSelectDraft(false);
      setStep('plan');
    }
  }, [campaignId]);
  const selected = entries.find((entry) => entry.message.id === selectedId) || entries.at(-1);
  const selectedJob = selected?.event.job?.id;
  useEffect(() => {
    // A batch render from the campaign keeps the campaign open; the board tracks every video.
    if (selectedJob && !campaignView) setStep('result');
  }, [selectedJob, campaignView]);
  useEffect(() => {
    if (allVideos) void refreshJobs();
  }, [allVideos, refreshJobs]);
  const pendingJobs = allVideos && jobs.some((job) => !terminal.includes(job.status));
  useEffect(() => {
    if (!pendingJobs) return;
    const interval = window.setInterval(() => void refreshJobs(), 4000);
    return () => window.clearInterval(interval);
  }, [pendingJobs, refreshJobs]);
  const project = w.conversations.find((item) => item.id === w.activeId);
  // The stage shows one video; the board appears once other renders run beside it.
  const renders = entries.filter((entry) => entry.event.job);
  const showBoard =
    renders.length > 1 ||
    (renders.length === 1 && (campaignView || renders[0]!.message.id !== selected?.message.id));
  const busy = w.loading || w.sending || Boolean(w.videoBusyId);
  const stepDone: Record<WorkflowStep, boolean> = {
    brief: entries.length > 0,
    plan: Boolean(selected?.event.job),
    result: selected?.event.job?.status === 'COMPLETED',
  };
  function select(id: string) {
    setCampaignView(false);
    setAutoSelectDraft(false);
    setSelectedId(id);
    setAllVideos(false);
    setStep('plan');
  }
  async function openCampaignItem(item: CampaignItem) {
    if (!item.message_id || !w.activeId) return;
    if (!w.messages.some((message) => message.id === item.message_id))
      await w.openConversation(w.activeId);
    select(item.message_id);
  }
  async function libraryAction(id: string, merge = false) {
    try {
      await request(merge ? `/story-batches/${id}/merge` : `/generations/${id}/cancel`, {
        method: 'POST',
      });
      await refreshJobs();
    } catch (reason) {
      setJobsError((reason as Error).message);
    }
  }
  return (
    <main
      className={clsx(
        'flow-shell workflow-shell',
        `workflow-view-${step}`,
        allVideos && 'workflow-library',
      )}
    >
      <header className="flow-topbar">
        <div className="flow-topbar-start">
          <Link className="flow-brand" href="/" aria-label="VO3 Studio">
            <span className="flow-brand-mark">
              <Clapperboard size={17} />
            </span>
            <span>
              VO3 <b>Studio</b>
            </span>
          </Link>
          <span className="flow-topbar-divider" />
          <button
            className="flow-project-name"
            onClick={() => w.setSidebarOpen(true)}
            title="Mở lịch sử dự án"
          >
            <FolderOpen size={16} />
            <span>{project?.title || 'Dự án mới'}</span>
            <ChevronDown size={14} />
          </button>
        </div>
        <nav className="workflow-steps" aria-label="Quy trình tạo video">
          {workflowSteps.map((item, index) => {
            const active = step === item.id && !allVideos;
            return (
              <button
                key={item.id}
                className={clsx('workflow-step', active && 'active', stepDone[item.id] && 'done')}
                aria-current={active ? 'step' : undefined}
                onClick={() => {
                  setAllVideos(false);
                  setStep(item.id);
                }}
              >
                <span>{stepDone[item.id] && !active ? <Check size={13} /> : index + 1}</span>
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="flow-topbar-actions">
          <CreditBadge onOpen={() => setSettingsOpen(true)} />
          <span
            className="flow-provider-status"
            title={user.is_admin ? 'Provider video đang dùng' : 'Model video đang dùng'}
          >
            <i />
            {user.is_admin ? providerLabel(config.provider) : videoModelLabel(config.model)}
          </span>
          <button
            className="flow-icon"
            title="Trao đổi với AI"
            onClick={() => setDiscussionOpen(true)}
          >
            <MessageSquare size={18} />
          </button>
          <button
            className={clsx('flow-icon', allVideos && 'active')}
            title="Thư viện video"
            aria-pressed={allVideos}
            onClick={() => setAllVideos(!allVideos)}
          >
            <Library size={18} />
          </button>
          {user.is_admin && (
            <Link className="flow-icon" href="/admin" title="Trang quản trị">
              <ShieldCheck size={18} />
            </Link>
          )}
          <span className="flow-topbar-separator" />
          <button
            className="flow-icon"
            title="Dự án mới"
            disabled={busy}
            onClick={() => {
              w.newChat();
              setAllVideos(false);
            }}
          >
            <Plus size={19} />
          </button>
          <button className="flow-icon" title="Cài đặt" onClick={() => setSettingsOpen(true)}>
            <Settings2 size={18} />
          </button>
          <button className="flow-user" title={user.email} onClick={() => setSettingsOpen(true)}>
            {user.email[0]?.toUpperCase()}
          </button>
        </div>
      </header>
      {(providers.error || w.error) && (
        <div className="flow-inline-error workflow-alert" role="alert">
          <span>{w.error || providers.error}</span>
          <button
            className="flow-icon"
            title={w.error ? 'Đóng thông báo' : 'Tải lại danh sách provider'}
            onClick={() => (w.error ? w.setError('') : void providers.refresh())}
          >
            {w.error ? <X size={16} /> : <RefreshCw size={16} />}
          </button>
        </div>
      )}
      <div className="workflow-work-area">
        <aside className="workflow-input-pane" aria-label="Ảnh và yêu cầu">
          <WorkspaceComposer
            providers={providers}
            workspace={w}
            config={config}
            admin={Boolean(user.is_admin)}
            onDiscuss={() => setDiscussionOpen(true)}
          />
        </aside>
        <section className="workflow-canvas" aria-label="Không gian sáng tạo">
          {allVideos ? (
            <>
              <div className="workflow-section-heading">
                <div>
                  <span className="workflow-eyebrow">Mọi dự án</span>
                  <h2>Thư viện video</h2>
                </div>
                <button
                  className="flow-icon"
                  title="Làm mới video"
                  disabled={jobsLoading}
                  onClick={() => void refreshJobs()}
                >
                  <RefreshCw size={17} className={jobsLoading ? 'spin' : ''} />
                </button>
              </div>
              {jobsError && (
                <p className="flow-inline-error" role="alert">
                  {jobsError}
                </p>
              )}
              <AssetCanvas
                entries={entries}
                jobs={jobs}
                allVideos
                selectedId={selectedId}
                loading={jobsLoading}
                onSelect={select}
                onCancel={(id) => libraryAction(id)}
                onMerge={(id) => libraryAction(id, true)}
              />
            </>
          ) : (
            <>
              {w.marketing.campaigns.length > 0 && (
                <div className="workflow-campaign-toolbar">
                  <label>
                    Chiến dịch
                    <select
                      aria-label="Chiến dịch đang xem"
                      value={w.marketing.selectedId || ''}
                      onChange={(event) => {
                        w.marketing.setSelectedId(event.target.value);
                        setCampaignView(true);
                        setStep('plan');
                      }}
                    >
                      {w.marketing.campaigns.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.brief}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="flow-button"
                    onClick={() => {
                      setCampaignView(true);
                      setStep('plan');
                    }}
                  >
                    Kế hoạch marketing
                  </button>
                </div>
              )}
              {entries.length > 1 && (
                <label className="workflow-draft-select">
                  Bản nháp
                  <select
                    aria-label="Bản nháp đang xem"
                    value={selected?.message.id || ''}
                    onChange={(event) => select(event.target.value)}
                  >
                    {entries
                      .slice()
                      .reverse()
                      .map((entry, index) => (
                        <option key={entry.message.id} value={entry.message.id}>
                          {entries.length - index}.{' '}
                          {entry.event.plan.story_plan?.core_value || entry.event.prompt}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {showBoard && (
                <ProductionBoard
                  entries={entries}
                  selectedId={campaignView ? null : selected?.message.id || null}
                  seedvis={config.provider === 'seedvis'}
                  admin={Boolean(user.is_admin)}
                  onSelect={select}
                />
              )}
              {step === 'plan' && campaignView && w.marketing.selected ? (
                <CampaignPanel
                  key={w.marketing.selected.id}
                  campaign={w.marketing.selected}
                  busy={w.marketing.busy || w.loading}
                  entries={entries}
                  renderBusy={w.loading || w.sending || Boolean(w.videoBusyId)}
                  batchRender={w.batchRender}
                  onOpen={(item) => void openCampaignItem(item)}
                  onRetry={(paid) => void w.marketing.retry(w.marketing.selected!.id, paid)}
                  onCancel={() => void w.marketing.cancel(w.marketing.selected!.id)}
                  onRenderAll={(ready, paid) => void w.generateMany(ready, paid)}
                />
              ) : (
                <WorkflowStage step={step} selected={selected} workspace={w} onStep={setStep} />
              )}
            </>
          )}
        </section>
      </div>
      {discussionOpen && (
        <WorkspaceDialog title="Trao đổi trong dự án" wide onClose={() => setDiscussionOpen(false)}>
          <WorkspaceInspector
            workspace={w}
            onSelect={(id) => {
              select(id);
              setDiscussionOpen(false);
            }}
            onClose={() => setDiscussionOpen(false)}
          />
          <form className="workflow-chat-form" onSubmit={(event) => void w.sendMessage(event)}>
            <textarea
              aria-label="Tin nhắn trong dự án"
              rows={3}
              value={w.input}
              disabled={busy}
              onChange={(event) => w.setInput(event.target.value)}
              placeholder="Trao đổi ý tưởng với AI…"
            />
            <button
              className="workflow-primary"
              disabled={busy || config.planner !== 'openai' || !w.input.trim()}
            >
              {w.sending ? <Loader2 className="spin" size={16} /> : <MessageSquare size={16} />}Gửi
            </button>
          </form>
        </WorkspaceDialog>
      )}
      {w.sidebarOpen && <ProjectHistory workspace={w} />}
      {settingsOpen && (
        <WorkspaceSettings
          providers={providers}
          config={config}
          user={user}
          workspace={w}
          onClose={() => setSettingsOpen(false)}
          onLogout={onLogout}
        />
      )}
    </main>
  );
}

export function FlowWorkspace() {
  const [user, setUser] = useState<WorkspaceUser | null>(null);
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const router = useRouter();
  const refreshConfig = useCallback(async () => {
    try {
      setConfig(await request<WorkspaceConfig>('/config'));
      setError('');
    } catch (reason) {
      setError((reason as Error).message);
    }
  }, []);
  useEffect(() => {
    let alive = true;
    Promise.allSettled([request<WorkspaceUser>('/me'), request<WorkspaceConfig>('/config')]).then(
      ([me, settings]) => {
        if (!alive) return;
        if (me.status === 'fulfilled') setUser(me.value);
        if (settings.status === 'fulfilled') setConfig(settings.value);
        else setError((settings.reason as Error).message);
        setLoading(false);
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  if (loading)
    return (
      <main className="flow-loading" aria-busy="true">
        <span className="flow-brand-mark">
          <Clapperboard size={22} />
        </span>
        <span>
          <Loader2 className="spin" size={15} /> Đang mở VO3 Studio…
        </span>
      </main>
    );
  if (!user)
    return (
      <WorkspaceAuth
        onAuthenticated={(next) => {
          // Admins sign in to their own console; the Studio stays reachable from there.
          if (next.is_admin) return router.replace('/admin');
          setUser(next);
          void refreshConfig();
        }}
      />
    );
  if (!config)
    return (
      <main className="flow-loading">
        <p role="alert">{error || 'Chưa đọc được cấu hình máy chủ.'}</p>
        <button className="flow-button" onClick={() => void refreshConfig()}>
          <RefreshCw size={16} />
          Thử lại
        </button>
      </main>
    );
  return <Workspace key={user.id} user={user} config={config} onLogout={() => setUser(null)} />;
}
