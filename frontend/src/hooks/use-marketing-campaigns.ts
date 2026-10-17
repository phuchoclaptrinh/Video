'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from '@/lib/chat-api';
import type { MarketingCampaign } from '@/types/marketing-campaign';

export function useMarketingCampaigns(
  conversationId: string | null,
  onError: (error: string) => void,
) {
  const [campaigns, setCampaigns] = useState<MarketingCampaign[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const currentConversation = useRef(conversationId);
  const requestSerial = useRef(0);
  currentConversation.current = conversationId;
  const refresh = useCallback(async () => {
    if (!conversationId) return;
    const serial = ++requestSerial.current;
    const result = await request<MarketingCampaign[]>(
      `/marketing-campaigns?conversation_id=${encodeURIComponent(conversationId)}`,
    );
    if (currentConversation.current !== conversationId || serial !== requestSerial.current) return;
    setCampaigns(result);
    setSelectedId((id) => (result.some((item) => item.id === id) ? id : result[0]?.id || null));
  }, [conversationId]);
  useEffect(() => {
    setCampaigns((items) => items.filter((item) => item.conversation_id === conversationId));
    setSelectedId(null);
    void refresh().catch((error: Error) => {
      if (currentConversation.current === conversationId) onError(error.message);
    });
  }, [conversationId, refresh, onError]);
  const pending = campaigns.some((item) => ['QUEUED', 'WORKING', 'DRAFTING'].includes(item.status));
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => void refresh().catch(() => {}), 3000);
    return () => window.clearInterval(timer);
  }, [pending, refresh]);
  async function action(id: string, retry = false, confirmed = false) {
    if (busy || (retry && !confirmed)) return;
    setBusy(true);
    requestSerial.current += 1;
    try {
      const updated = await request<MarketingCampaign>(
        `/marketing-campaigns/${id}/${retry ? 'retry' : 'cancel'}`,
        {
          method: 'POST',
          ...(retry ? { body: JSON.stringify({ confirm_planning_costs: confirmed }) } : {}),
        },
      );
      if (currentConversation.current === updated.conversation_id)
        setCampaigns((items) => items.map((item) => (item.id === id ? updated : item)));
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const selected = campaigns.find((item) => item.id === selectedId) || campaigns[0];
  return {
    campaigns,
    selectedId: selected?.id || null,
    setSelectedId,
    selected,
    track: (campaign: MarketingCampaign) => {
      requestSerial.current += 1;
      setCampaigns((items) => [campaign, ...items.filter((item) => item.id !== campaign.id)]);
      setSelectedId(campaign.id);
    },
    busy,
    refresh,
    cancel: (id: string) => action(id),
    retry: (id: string, confirmed: boolean) => action(id, true, confirmed),
  };
}
