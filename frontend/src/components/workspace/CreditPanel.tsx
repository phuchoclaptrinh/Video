'use client';

import clsx from 'clsx';
import { Coins } from 'lucide-react';
import { priceLabel, useCredits } from '@/lib/credits';
import { videoModelLabel } from '@/types/workspace';
import { formatTime } from '@/components/admin/AdminConsole';

/** Balance chip in the top bar; admins are never charged and see that instead of a balance. */
export function CreditBadge({ onOpen }: { onOpen: () => void }) {
  const credits = useCredits();
  if (!credits) return null;
  if (credits.exempt)
    return (
      <button
        className="flow-credit-badge exempt"
        title="Tài khoản admin không bị trừ credit"
        onClick={onOpen}
      >
        <Coins size={15} />
        Không giới hạn
      </button>
    );
  return (
    <button
      className={clsx('flow-credit-badge', credits.balance <= 0 && 'empty')}
      title="Số credit còn lại"
      onClick={onOpen}
    >
      <Coins size={15} />
      {credits.balance.toLocaleString('vi-VN')}
    </button>
  );
}

export function CreditHistory() {
  const credits = useCredits();
  if (!credits) return null;
  if (credits.exempt)
    return (
      <section className="flow-credit-section">
        <div className="flow-section-heading">
          <h3>Credit</h3>
          <span className="flow-tag">Không giới hạn</span>
        </div>
        <p className="flow-model-caption">
          Tài khoản admin không bị trừ credit. Thành viên thấy số dư và lịch sử tại đây; bảng giá và
          cộng/trừ credit cho từng người nằm trong trang Admin, mục Credit và Người dùng.
        </p>
      </section>
    );
  const { pricing } = credits;
  const rates = Object.entries(pricing.video_clip).filter(([key]) => key !== 'default');
  return (
    <section className="flow-credit-section">
      <div className="flow-section-heading">
        <h3>Credit</h3>
        <span className="flow-credit-balance">
          {credits.balance.toLocaleString('vi-VN')} <small>credit</small>
        </span>
      </div>
      <p className="flow-model-caption">
        Video:{' '}
        {rates
          .map(
            ([key, price]) => `${videoModelLabel(key.split(':')[1] || key)} ${priceLabel(price)}`,
          )
          .join(', ')}
        {pricing.video_clip.default && ` · model khác ${priceLabel(pricing.video_clip.default)}`} ·
        kịch bản {pricing.plan} · chiến lược chiến dịch {pricing.campaign} · ảnh tham chiếu{' '}
        {pricing.image}. Cảnh lỗi không ra video được tự hoàn credit.
      </p>
      {credits.history.length > 0 && (
        <ul className="flow-credit-history">
          {credits.history.slice(0, 20).map((entry) => (
            <li key={entry.id}>
              <span>
                <b>{entry.label}</b>
                {entry.note && <small>{entry.note}</small>}
              </span>
              <span className={clsx('flow-credit-delta', entry.delta > 0 ? 'plus' : 'minus')}>
                {entry.delta > 0 ? '+' : ''}
                {entry.delta}
                <small>{entry.pending ? 'đang render' : formatTime(entry.created)}</small>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
