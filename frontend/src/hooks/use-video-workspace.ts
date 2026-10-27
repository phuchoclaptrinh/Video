'use client';

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';
import { request } from '@/lib/chat-api';
import { useMarketingCampaigns } from '@/hooks/use-marketing-campaigns';
import { useStyleReferences } from '@/hooks/use-style-references';
import type { MarketingCampaign } from '@/types/marketing-campaign';
import { jobActive, videoEvent } from '@/types/video-event';
import type {
  ChatMessage,
  Conversation,
  Memory,
  QualityReport,
  VideoEvent,
  VideoJob,
  VideoPlan,
} from '@/types/video-event';
import type { WorkspaceConfig } from '@/types/workspace';

// Same placeholder check as VideoEventCard: such scripts still need the image read.
const unresolvedText = /\[[^\]\n]{2,100}\]|\b(?:tên|ten)\s+(?:cuốn\s+)?(?:sách|sach)\b/i;

/** A drafted script that can be approved and rendered without opening it first. */
export function readyToRender(event: VideoEvent) {
  return Boolean(
    !event.job &&
    event.project_id &&
    event.plan.story_plan &&
    (!event.project_current_draft_id || event.project_current_draft_id === event.plan.id) &&
    !event.plan.story_plan.clips.some((clip) =>
      unresolvedText.test(`${clip.voice_script} ${clip.video_prompt}`),
    ),
  );
}

export function useVideoWorkspace(config: WorkspaceConfig) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [inputImages, setInputImages] = useState<File[]>([]);
  const inputImage = inputImages[0] || null;
  const uploadedImages = useRef(new Map<File, string>());
  const campaignRequest = useRef<{ signature: string; id: string } | null>(null);
  const [creationScope, setCreationScope] = useState<'single' | 'campaign'>('single');
  const [scriptCount, setScriptCount] = useState(3);
  const [confirmPlanningCosts, setConfirmPlanningCosts] = useState(false);
  const [createReferenceAutomatically, setCreateReferenceAutomatically] = useState(true);
  const [filter, setFilter] = useState('');
  const [memoryText, setMemoryText] = useState('');
  const [loading, setLoading] = useState(true);
  const [ratio, setRatio] = useState(config.ratio_options[0] || '16:9');
  const [duration, setDuration] = useState(
    config.duration_options.includes(8) ? 8 : config.duration_options[0] || 8,
  );
  const [targetDuration, setTargetDuration] = useState(20);
  const conversationLoad = useRef(0);
  const [sending, setSending] = useState(false);
  const canPlan =
    config.planner === 'openai' &&
    (config.provider === 'fake' || Boolean(config.native_audio)) &&
    config.story_supported !== false &&
    config.duration_control !== false;
  const [composeMode, setComposeMode] = useState<'chat' | 'video' | 'clip'>(
    canPlan ? 'video' : 'clip',
  );
  const [videoBusyId, setVideoBusyId] = useState<string | null>(null);
  const [renderingId, setRenderingId] = useState<string | null>(null);
  const [batchRender, setBatchRender] = useState<{ done: number; total: number } | null>(null);
  const [planningPhase, setPlanningPhase] = useState<'upload' | 'plan' | 'reference' | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [error, setError] = useState('');
  const marketing = useMarketingCampaigns(activeId, setError);
  const styles = useStyleReferences(setError);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const autoGroundAttempted = useRef(new Set<string>());
  const refreshedFailures = useRef(new Set<string>());
  const activeIdRef = useRef<string | null>(null);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  useEffect(() => {
    if (
      (composeMode === 'video' && !canPlan) ||
      (composeMode === 'chat' && config.planner !== 'openai')
    )
      setComposeMode('clip');
    if (!config.ratio_options.includes(ratio)) setRatio(config.ratio_options[0] || '16:9');
    if (config.duration_options.length && !config.duration_options.includes(duration))
      setDuration(config.duration_options[0] ?? 8);
  }, [
    canPlan,
    composeMode,
    config.planner,
    config.ratio_options,
    config.duration_options,
    ratio,
    duration,
  ]);

  const refreshConversations = useCallback(async () => {
    setConversations(await request<Conversation[]>('/chat/conversations'));
  }, []);

  const openConversation = useCallback(async (id: string) => {
    const load = ++conversationLoad.current;
    setLoading(true);
    setError('');
    try {
      const items = await request<ChatMessage[]>(`/chat/conversations/${id}/messages`);
      const videoItems = items
        .slice()
        .reverse()
        .filter((item) => videoEvent(item)?.project_id);
      const projects = new Set([
        ...videoItems.slice(0, 10).map((item) => item.id),
        ...videoItems
          .filter((item) =>
            videoEvent(item)?.plan.story_plan?.clips.some((clip) =>
              /\[[^\]\n]{2,100}\]|\b(?:tên|ten)\s+(?:cuốn\s+)?(?:sách|sach)\b/i.test(
                `${clip.voice_script} ${clip.video_prompt}`,
              ),
            ),
          )
          .map((item) => item.id),
      ]);
      const hydrated = await Promise.all(
        items.map(async (item) => {
          const event = videoEvent(item);
          if (!event?.project_id || !projects.has(item.id)) return item;
          try {
            const project = await request<{
              project: { status: string };
              plan_versions: { draft_id: string; version: number }[];
              quality_reports: QualityReport[];
              reference_image_url: string | null;
            }>(`/video-projects/${event.project_id}`);
            const currentDraftId = project.plan_versions[0]?.draft_id || null;
            return {
              ...item,
              content: JSON.stringify({
                ...event,
                project_status: project.project.status,
                project_current_draft_id: currentDraftId,
                reference_image_url:
                  currentDraftId === event.plan.id
                    ? project.reference_image_url ||
                      (event.plan.input_asset_ids?.[0]
                        ? `/api/v1/assets/${event.plan.input_asset_ids[0]}/file`
                        : null)
                    : null,
                quality_reports: project.quality_reports,
              }),
            };
          } catch {
            return item;
          }
        }),
      );
      if (load !== conversationLoad.current) return;
      setActiveId(id);
      activeIdRef.current = id;
      setMessages(hydrated);
      setSidebarOpen(false);
    } catch (reason) {
      if (load === conversationLoad.current) setError((reason as Error).message);
    } finally {
      if (load === conversationLoad.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([request<Conversation[]>('/chat/conversations'), request<Memory[]>('/memories')])
      .then(async ([items, saved]) => {
        if (!alive) return;
        setConversations(items);
        setMemories(saved);
        const mostRecent = items[0];
        if (mostRecent) await openConversation(mostRecent.id);
        if (alive) setLoading(false);
      })
      .catch((reason: Error) => {
        if (!alive) return;
        setError(reason.message);
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [openConversation]);

  useEffect(() => {
    if (composeMode === 'chat')
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending, composeMode]);

  useEffect(() => {
    if (videoBusyId) return;
    const pending = messages
      .slice()
      .reverse()
      .find((message) => {
        const event = videoEvent(message);
        if (
          !event?.project_id ||
          !event.plan.story_plan ||
          (event.project_current_draft_id && event.project_current_draft_id !== event.plan.id) ||
          (!event.reference_image_url && !createReferenceAutomatically)
        )
          return false;
        const job = event.job;
        const clips = job
          ? job.status === 'FAILED' && job.retryable
            ? event.plan.story_plan.clips.filter(
                (clip) => clip.index > (job.clip_progress?.completed || 0),
              )
            : []
          : event.plan.story_plan.clips;
        return (
          clips.some((clip) =>
            /\[[^\]\n]{2,100}\]|\b(?:tên|ten)\s+(?:cuốn\s+)?(?:sách|sach)\b/i.test(
              `${clip.voice_script} ${clip.video_prompt}`,
            ),
          ) && !autoGroundAttempted.current.has(`${message.id}:${event.plan.id}`)
        );
      });
    if (!pending) return;
    const event = videoEvent(pending)!;
    autoGroundAttempted.current.add(`${pending.id}:${event.plan.id}`);
    setVideoBusyId(pending.id);
    void request<{
      draft_id: string;
      reference_image_url: string;
      story_plan?: VideoPlan['story_plan'];
      grounding_error?: string;
    }>(`/video-projects/${event.project_id}/reference-image`, { method: 'POST' })
      .then((result) => {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== pending.id) return item;
            const latest = videoEvent(item);
            if (!latest || latest.plan.id !== event.plan.id || result.draft_id !== event.plan.id)
              return item;
            return {
              ...item,
              content: JSON.stringify({
                ...latest,
                reference_image_url: result.reference_image_url,
                plan: { ...latest.plan, story_plan: result.story_plan || latest.plan.story_plan },
              }),
            };
          }),
        );
        if (result.grounding_error && activeIdRef.current === pending.conversation_id) {
          setError(result.grounding_error);
        }
      })
      .catch((reason: Error) => {
        if (activeIdRef.current === pending.conversation_id) setError(reason.message);
      })
      .finally(() => {
        setVideoBusyId((current) => (current === pending.id ? null : current));
      });
  }, [messages, videoBusyId, createReferenceAutomatically]);

  useEffect(() => {
    const pending = messages.flatMap((message) => {
      const event = videoEvent(message);
      return event?.job && jobActive(event.job) ? [{ message, event }] : [];
    });
    if (!pending.length) return;
    const timer = window.setInterval(() => {
      void Promise.all(
        pending.map(async ({ message, event }) => {
          try {
            const updated = await request<ChatMessage>(`/chat/video-events/${message.id}`, {
              method: 'PUT',
              body: JSON.stringify({ job_id: event.job!.id }),
            });
            const currentEvent = videoEvent(updated);
            let reports = event.quality_reports;
            if (event.project_id) {
              try {
                const project = await request<{
                  project: { status: string };
                  plan_versions: { draft_id: string; version: number }[];
                  quality_reports: QualityReport[];
                  reference_image_url: string | null;
                }>(`/video-projects/${event.project_id}`);
                reports = project.quality_reports;
                const currentDraftId = project.plan_versions[0]?.draft_id || null;
                setMessages((current) =>
                  current.map((item) =>
                    item.id === message.id
                      ? {
                          ...updated,
                          content: JSON.stringify({
                            ...(currentEvent || event),
                            project_status: project.project.status,
                            project_current_draft_id: currentDraftId,
                            reference_image_url:
                              currentDraftId === event.plan.id ? project.reference_image_url : null,
                            quality_reports: reports,
                          }),
                        }
                      : item,
                  ),
                );
                return;
              } catch {
                /* Status polling remains useful without the report endpoint. */
              }
            }
            setMessages((current) =>
              current.map((item) =>
                item.id === message.id
                  ? {
                      ...updated,
                      content: JSON.stringify({
                        ...(currentEvent || event),
                        quality_reports: reports,
                      }),
                    }
                  : item,
              ),
            );
          } catch {
            /* Keep the last known status; a later poll may succeed. */
          }
        }),
      );
    }, 4000);
    return () => window.clearInterval(timer);
  }, [messages]);

  // A finished job is stored in the chat message and no longer polled, so a failed render keeps
  // the retry options computed when it failed. Ask the server once per session for the current
  // state, so fixes such as an added API key or newly retryable errors show a retry button.
  useEffect(() => {
    const stale = messages.flatMap((message) => {
      const event = videoEvent(message);
      return event?.job &&
        ['FAILED', 'NEEDS_USER_ACTION'].includes(event.job.status) &&
        !jobActive(event.job) &&
        !refreshedFailures.current.has(message.id)
        ? [{ message, event }]
        : [];
    });
    for (const { message, event } of stale) {
      refreshedFailures.current.add(message.id);
      void request<ChatMessage>(`/chat/video-events/${message.id}`, {
        method: 'PUT',
        body: JSON.stringify({ job_id: event.job!.id }),
      })
        .then((updated) => {
          const job = videoEvent(updated)?.job;
          if (!job) return;
          setMessages((current) =>
            current.map((item) => {
              const currentEvent = item.id === message.id ? videoEvent(item) : null;
              return currentEvent
                ? { ...item, content: JSON.stringify({ ...currentEvent, job }) }
                : item;
            }),
          );
        })
        .catch(() => {
          /* Keep the stored status; the card still shows its last known error. */
        });
    }
  }, [messages]);

  function newChat() {
    conversationLoad.current += 1;
    activeIdRef.current = null;
    setActiveId(null);
    setMessages([]);
    setInput('');
    setInputImages([]);
    uploadedImages.current.clear();
    setError('');
    setSidebarOpen(false);
    inputRef.current?.focus();
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const content = input.trim();
    if (!content || loading || sending || videoBusyId) return;
    setSending(true);
    setError('');
    setInput('');
    const optimistic: ChatMessage = {
      id: 'pending-' + Date.now(),
      conversation_id: activeId || '',
      role: 'user',
      content,
      created: Date.now() / 1000,
    };
    setMessages((current) => [...current, optimistic]);
    try {
      const result = await request<{
        conversation_id: string;
        title: string;
        messages: ChatMessage[];
      }>('/chat/messages', {
        method: 'POST',
        body: JSON.stringify({ conversation_id: activeId, content }),
      });
      setActiveId(result.conversation_id);
      activeIdRef.current = result.conversation_id;
      setMessages((current) => [
        ...current.filter((item) => item.id !== optimistic.id),
        ...result.messages,
      ]);
      await refreshConversations();
    } catch (reason) {
      setMessages((current) => current.filter((item) => item.id !== optimistic.id));
      setInput(content);
      setError((reason as Error).message);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  async function createVideo(event?: FormEvent) {
    event?.preventDefault();
    const prompt = input.trim();
    if (prompt.length < 5 || loading || sending || videoBusyId) return;
    setSending(true);
    setError('');
    setPlanningPhase(inputImage ? 'upload' : 'plan');
    try {
      if (config.video_model_available === false)
        throw new Error(
          'Model video đang chọn chưa khả dụng. Hãy cập nhật danh sách hoặc chọn model khác.',
        );
      const assetIds = await uploadInputImages();
      const assetId = assetIds[0];
      setPlanningPhase('plan');
      const plan = await request<VideoPlan>('/plans', {
        method: 'POST',
        body: JSON.stringify({
          prompt,
          duration,
          ratio,
          style: 'cinematic',
          ...config.render_target,
          conversation_id: activeId,
          asset_id: assetId,
          asset_ids: assetIds,
          planning_goal: assetIds.length ? 'sales' : undefined,
          require_planner: composeMode === 'video',
          use_planner: composeMode === 'video',
          style_reference_id:
            composeMode === 'video' && styles.selectedId ? styles.selectedId : undefined,
          ...(composeMode === 'video' && targetDuration
            ? { target_duration_seconds: targetDuration }
            : {}),
        }),
      });
      if (composeMode === 'clip') {
        plan.creation_mode = 'direct';
        plan.automatic_duration = config.duration_control === false;
      }
      const saved = await request<{ conversation_id: string; messages: ChatMessage[] }>(
        '/chat/video-events',
        {
          method: 'POST',
          body: JSON.stringify({ conversation_id: activeId, prompt, plan }),
        },
      );
      setActiveId(saved.conversation_id);
      activeIdRef.current = saved.conversation_id;
      setMessages((current) => [...current, ...saved.messages]);
      setInput('');
      setInputImages([]);
      uploadedImages.current.clear();
      const assistantMessage = saved.messages.find((item) => item.role === 'assistant');
      if (
        (assetId || (createReferenceAutomatically && plan.story_plan)) &&
        assistantMessage &&
        plan.project_id
      ) {
        setVideoBusyId(assistantMessage.id);
        setPlanningPhase('reference');
        try {
          const reference = await request<{
            draft_id: string;
            reference_image_url: string;
            story_plan?: VideoPlan['story_plan'];
            grounding_error?: string;
          }>(`/video-projects/${plan.project_id}/reference-image`, { method: 'POST' });
          const event = videoEvent(assistantMessage);
          if (event && reference.draft_id === event.plan.id)
            setMessages((current) =>
              current.map((item) =>
                item.id === assistantMessage.id
                  ? {
                      ...item,
                      content: JSON.stringify({
                        ...event,
                        reference_image_url: reference.reference_image_url,
                        plan: {
                          ...event.plan,
                          story_plan: reference.story_plan || event.plan.story_plan,
                        },
                      }),
                    }
                  : item,
              ),
            );
          if (reference.grounding_error) setError(reference.grounding_error);
        } catch (reason) {
          setError((reason as Error).message);
        } finally {
          setVideoBusyId(null);
        }
      }
      await refreshConversations();
    } catch (reason) {
      setInput(prompt);
      setError((reason as Error).message);
    } finally {
      setSending(false);
      setPlanningPhase(null);
      inputRef.current?.focus();
    }
  }

  async function generateVideo(
    message: ChatMessage,
    event: VideoEvent,
    confirmPaid: boolean,
    approveFirst = false,
    productReference?: boolean,
  ) {
    if (sending || videoBusyId) return;
    if (event.plan.mode === 'live' && !confirmPaid) {
      setError('Bạn cần xác nhận trước khi tạo video có tính phí.');
      return;
    }
    setVideoBusyId(message.id);
    setRenderingId(message.id);
    setError('');
    try {
      if (approveFirst && event.project_id && event.project_status !== 'APPROVED') {
        await request(`/video-projects/${event.project_id}/approve`, { method: 'POST' });
        setMessages((current) =>
          current.map((item) =>
            item.id === message.id
              ? { ...item, content: JSON.stringify({ ...event, project_status: 'APPROVED' }) }
              : item,
          ),
        );
      }
      const renderPath = event.project_id
        ? `/video-projects/${event.project_id}/renders`
        : `/plans/${event.plan.id}/generate`;
      const job = await request<VideoJob>(renderPath, {
        method: 'POST',
        body: JSON.stringify({
          confirm_paid: confirmPaid,
          expected_draft_id: event.plan.id,
          ...(productReference === undefined ? {} : { product_reference: productReference }),
        }),
      });
      const updated = await request<ChatMessage>(`/chat/video-events/${message.id}`, {
        method: 'PUT',
        body: JSON.stringify({ job_id: job.id }),
      });
      setMessages((current) => current.map((item) => (item.id === message.id ? updated : item)));
      await refreshConversations();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setVideoBusyId(null);
      setRenderingId(null);
    }
  }

  // Submits scripts one after another; each render then runs on its own provider stream.
  async function generateMany(
    entries: { message: ChatMessage; event: VideoEvent }[],
    confirmPaid: boolean,
  ) {
    const queue = entries.filter(({ event }) => readyToRender(event));
    if (!queue.length || sending || videoBusyId) return;
    if (queue.some(({ event }) => event.plan.mode === 'live') && !confirmPaid) {
      setError('Bạn cần xác nhận trước khi tạo video có tính phí.');
      return;
    }
    setVideoBusyId('batch');
    setBatchRender({ done: 0, total: queue.length });
    setError('');
    const failures: string[] = [];
    for (const [index, { message, event }] of queue.entries()) {
      try {
        if (event.project_status !== 'APPROVED')
          await request(`/video-projects/${event.project_id}/approve`, { method: 'POST' });
        const job = await request<VideoJob>(`/video-projects/${event.project_id}/renders`, {
          method: 'POST',
          body: JSON.stringify({ confirm_paid: confirmPaid, expected_draft_id: event.plan.id }),
        });
        const updated = await request<ChatMessage>(`/chat/video-events/${message.id}`, {
          method: 'PUT',
          body: JSON.stringify({ job_id: job.id }),
        });
        setMessages((current) => current.map((item) => (item.id === message.id ? updated : item)));
      } catch (reason) {
        failures.push(
          `${event.plan.story_plan?.core_value || event.prompt}: ${(reason as Error).message}`,
        );
      }
      setBatchRender({ done: index + 1, total: queue.length });
    }
    if (failures.length)
      setError(`${failures.length}/${queue.length} video chưa gửi được. ${failures[0]}`);
    setVideoBusyId(null);
    setBatchRender(null);
    await refreshConversations().catch(() => {});
  }

  async function prepareReference(message: ChatMessage, event: VideoEvent) {
    if (!event.project_id) return;
    setVideoBusyId(message.id);
    setError('');
    try {
      const result = await request<{
        draft_id: string;
        reference_image_url: string;
        story_plan?: VideoPlan['story_plan'];
        grounding_error?: string;
      }>(`/video-projects/${event.project_id}/reference-image`, { method: 'POST' });
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id && result.draft_id === event.plan.id
            ? {
                ...item,
                content: JSON.stringify({
                  ...event,
                  reference_image_url: result.reference_image_url,
                  plan: { ...event.plan, story_plan: result.story_plan || event.plan.story_plan },
                }),
              }
            : item,
        ),
      );
      if (result.grounding_error) setError(result.grounding_error);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setVideoBusyId(null);
    }
  }

  /** Merge the finished scenes again; no scene is rendered or charged a second time. */
  async function retryMerge(message: ChatMessage, event: VideoEvent) {
    if (!event.job) return;
    setVideoBusyId(message.id);
    setError('');
    try {
      await request(`/story-batches/${event.job.id}/merge`, { method: 'POST' });
    } catch (reason) {
      setError((reason as Error).message);
    }
    try {
      const updated = await request<ChatMessage>(`/chat/video-events/${message.id}`, {
        method: 'PUT',
        body: JSON.stringify({ job_id: event.job.id }),
      });
      const job = videoEvent(updated)?.job;
      if (job)
        setMessages((current) =>
          current.map((item) => {
            const currentEvent = item.id === message.id ? videoEvent(item) : null;
            return currentEvent
              ? { ...item, content: JSON.stringify({ ...currentEvent, job }) }
              : item;
          }),
        );
    } catch {
      /* The stored status stays; reloading the project shows the latest one. */
    } finally {
      setVideoBusyId(null);
    }
  }

  async function regenerateFailedReference(message: ChatMessage, event: VideoEvent) {
    if (!event.project_id) return;
    setVideoBusyId(message.id);
    setError('');
    try {
      const result = await request<{
        draft_id: string;
        reference_image_url: string;
        story_plan?: VideoPlan['story_plan'];
        grounding_error?: string;
      }>(`/video-projects/${event.project_id}/reference-image/regenerate`, { method: 'POST' });
      setMessages((current) =>
        current.map((item) => {
          if (item.id !== message.id || result.draft_id !== event.plan.id) return item;
          const latest = videoEvent(item);
          if (!latest || latest.plan.id !== event.plan.id) return item;
          return {
            ...item,
            content: JSON.stringify({
              ...latest,
              reference_image_url: result.reference_image_url,
              plan: { ...latest.plan, story_plan: result.story_plan || latest.plan.story_plan },
            }),
          };
        }),
      );
      if (result.grounding_error) setError(result.grounding_error);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setVideoBusyId(null);
    }
  }

  async function approveVideo(message: ChatMessage, event: VideoEvent) {
    if (!event.project_id) return;
    setVideoBusyId(message.id);
    setError('');
    try {
      await request(`/video-projects/${event.project_id}/approve`, { method: 'POST' });
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? { ...item, content: JSON.stringify({ ...event, project_status: 'APPROVED' }) }
            : item,
        ),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setVideoBusyId(null);
    }
  }

  async function reviseVideo(message: ChatMessage, event: VideoEvent, prompt: string) {
    if (!event.project_id) return;
    setVideoBusyId(message.id);
    setError('');
    try {
      const plan = await request<VideoPlan>(`/video-projects/${event.project_id}/plans`, {
        method: 'POST',
        body: JSON.stringify({
          prompt,
          duration: event.plan.spec.output.duration_seconds,
          ratio: event.plan.spec.output.aspect_ratio,
          style: 'cinematic',
          ...event.plan.render_target,
          asset_id: event.plan.spec.references?.[0]?.asset_id,
          asset_ids: event.plan.input_asset_ids,
          planning_goal: event.plan.planning_goal,
          require_planner: event.plan.creation_mode !== 'direct',
          use_planner: event.plan.creation_mode !== 'direct',
        }),
      });
      if (event.plan.creation_mode === 'direct') {
        plan.creation_mode = 'direct';
        plan.automatic_duration = event.plan.automatic_duration;
      }
      const updated = await request<ChatMessage>(`/chat/video-events/${message.id}/plan`, {
        method: 'PUT',
        body: JSON.stringify({ plan }),
      });
      const newEvent = videoEvent(updated);
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? {
                ...updated,
                content: JSON.stringify({
                  ...newEvent,
                  project_status: 'REVIEW',
                  reference_image_url: null,
                }),
              }
            : item,
        ),
      );
      await refreshConversations();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setVideoBusyId(null);
    }
  }

  async function reviseFailedScene(
    message: ChatMessage,
    sceneIndex: number,
    videoPrompt: string,
    voiceScript: string,
  ) {
    setVideoBusyId(message.id);
    setError('');
    try {
      const updated = await request<ChatMessage>(`/chat/video-events/${message.id}/failed-scene`, {
        method: 'PUT',
        body: JSON.stringify({
          scene_index: sceneIndex,
          video_prompt: videoPrompt,
          voice_script: voiceScript,
        }),
      });
      setMessages((current) => current.map((item) => (item.id === message.id ? updated : item)));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setVideoBusyId(null);
    }
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === 'Enter' &&
      (composeMode === 'chat' ? !event.shiftKey : event.ctrlKey || event.metaKey) &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void (composeMode === 'chat'
        ? sendMessage()
        : creationScope === 'campaign' && composeMode === 'video'
          ? createCampaign()
          : createVideo());
    }
  }

  function selectInputImages(files: File[]) {
    if (
      files.some(
        (file) =>
          file.size > 8 * 1024 * 1024 ||
          !['image/jpeg', 'image/png', 'image/webp'].includes(file.type),
      )
    ) {
      setError('Ảnh phải là JPEG, PNG hoặc WebP, tối đa 8 MB mỗi ảnh.');
      return;
    }
    const identity = (file: File) => `${file.name}:${file.size}:${file.lastModified}:${file.type}`;
    const uniqueFiles = new Map(inputImages.map((file) => [identity(file), file]));
    for (const file of files)
      if (!uniqueFiles.has(identity(file))) uniqueFiles.set(identity(file), file);
    const unique = Array.from(uniqueFiles.values());
    if (unique.length > 6) {
      setError('Tối đa 6 ảnh đầu vào. Hãy bỏ bớt ảnh trước khi thêm.');
      return;
    }
    setInputImages(unique);
    setError('');
  }

  function removeInputImage(index: number) {
    const file = inputImages[index];
    if (file) uploadedImages.current.delete(file);
    setInputImages((items) => items.filter((_, i) => i !== index));
  }

  function makePrimaryImage(index: number) {
    setInputImages((items) => {
      const primary = items[index];
      return primary ? [primary, ...items.filter((_, i) => i !== index)] : items;
    });
  }

  async function uploadInputImages(): Promise<string[]> {
    if (inputImages.length && !config.local_image_upload)
      throw new Error(
        'Model hoặc cấu hình hiện tại không nhận được ảnh đầu vào. Hãy bỏ ảnh hoặc chọn model khác.',
      );
    const ids = [];
    for (const image of inputImages) {
      let id = uploadedImages.current.get(image);
      if (!id) {
        const form = new FormData();
        form.append('file', image);
        const uploaded = await request<{ id: string }>('/assets', { method: 'POST', body: form });
        id = uploaded.id;
        uploadedImages.current.set(image, id);
      }
      ids.push(id);
    }
    return ids;
  }

  async function createCampaign(event?: FormEvent) {
    event?.preventDefault();
    if (
      input.trim().length < 5 ||
      loading ||
      sending ||
      videoBusyId ||
      !confirmPlanningCosts ||
      !canPlan
    )
      return;
    setSending(true);
    setError('');
    setPlanningPhase(inputImages.length ? 'upload' : 'plan');
    try {
      const signature = JSON.stringify({
        prompt: input.trim(),
        files: inputImages.map((file) => [file.name, file.size, file.lastModified, file.type]),
        target: config.render_target,
        activeId,
        scriptCount,
        targetDuration,
        duration,
        ratio,
      });
      if (campaignRequest.current?.signature !== signature)
        campaignRequest.current = { signature, id: crypto.randomUUID() };
      const assetIds = await uploadInputImages();
      setPlanningPhase('plan');
      const result = await request<MarketingCampaign>('/marketing-campaigns', {
        method: 'POST',
        body: JSON.stringify({
          prompt: input.trim(),
          request_id: campaignRequest.current.id,
          asset_ids: assetIds,
          duration,
          ratio,
          style: 'natural',
          ...config.render_target,
          target_duration_seconds: targetDuration || undefined,
          conversation_id: activeId,
          script_count: scriptCount,
          planning_goal: 'sales',
          require_planner: true,
          confirm_planning_costs: true,
        }),
      });
      if (activeId !== result.conversation_id) {
        setMessages([]);
        setActiveId(result.conversation_id);
        activeIdRef.current = result.conversation_id;
      }
      marketing.track(result);
      setInput('');
      setInputImages([]);
      uploadedImages.current.clear();
      setConfirmPlanningCosts(false);
      campaignRequest.current = null;
      await refreshConversations().catch(() =>
        setError('Chiến dịch đã xếp hàng nhưng chưa tải lại được lịch sử.'),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSending(false);
      setPlanningPhase(null);
    }
  }

  const campaignMessages = marketing.campaigns.flatMap((item) =>
    item.items.map((variant) => variant.message_id).filter(Boolean),
  );
  const missingCampaignMessages = campaignMessages
    .filter((id) => !messages.some((message) => message.id === id))
    .join(',');
  useEffect(() => {
    if (!activeId || !missingCampaignMessages) return;
    let alive = true;
    void request<ChatMessage[]>(`/chat/conversations/${activeId}/messages`)
      .then((items) => {
        if (!alive) return;
        setMessages((current) =>
          [
            ...current,
            ...items.filter((item) => !current.some((message) => message.id === item.id)),
          ].sort((a, b) => a.created - b.created),
        );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [activeId, missingCampaignMessages]);

  async function removeConversation(id: string) {
    try {
      await request(`/chat/conversations/${id}`, { method: 'DELETE' });
      setConversations((current) => current.filter((item) => item.id !== id));
      if (activeId === id) newChat();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function addMemory(event: FormEvent) {
    event.preventDefault();
    const content = memoryText.trim();
    if (content.length < 2) return;
    setError('');
    try {
      const saved = await request<Memory>('/memories', {
        method: 'POST',
        body: JSON.stringify({ content }),
      });
      setMemories((current) => [saved, ...current]);
      setMemoryText('');
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function removeMemory(id: string) {
    try {
      await request(`/memories/${id}`, { method: 'DELETE' });
      setMemories((current) => current.filter((item) => item.id !== id));
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  return {
    conversations,
    messages,
    memories,
    activeId,
    input,
    setInput,
    inputImage,
    inputImages,
    selectInputImages,
    removeInputImage,
    makePrimaryImage,
    creationScope,
    setCreationScope,
    scriptCount,
    setScriptCount,
    confirmPlanningCosts,
    setConfirmPlanningCosts,
    marketing,
    styles,
    createCampaign,
    createReferenceAutomatically,
    setCreateReferenceAutomatically,
    filter,
    setFilter,
    memoryText,
    setMemoryText,
    loading,
    sending,
    composeMode,
    setComposeMode,
    videoBusyId,
    renderingId,
    planningPhase,
    selectInputImage: (file: File) => selectInputImages([file]),
    sidebarOpen,
    setSidebarOpen,
    error,
    setError,
    ratio,
    setRatio,
    duration,
    setDuration,
    targetDuration,
    setTargetDuration,
    scrollRef,
    inputRef,
    imageInputRef,
    openConversation,
    newChat,
    sendMessage,
    createVideo,
    generateVideo,
    generateMany,
    batchRender,
    prepareReference,
    regenerateFailedReference,
    retryMerge,
    approveVideo,
    reviseVideo,
    reviseFailedScene,
    onInputKeyDown,
    removeConversation,
    addMemory,
    removeMemory,
  };
}
export type VideoWorkspaceController = ReturnType<typeof useVideoWorkspace>;
