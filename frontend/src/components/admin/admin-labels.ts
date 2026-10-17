const errors: Record<string, string> = {
  native_speech_quality_failed: 'Lời thoại đọc thiếu, sai hoặc lặp',
  native_audio_missing: 'Clip không có âm thanh',
  visual_reference_drift: 'Hình lệch so với ảnh tham chiếu',
  clip_generation_failed: 'Provider tạo cảnh thất bại',
  clip_preparation_failed: 'Lỗi chuẩn bị cảnh',
  clip_submission_failed: 'Không gửi được cảnh',
  story_merge_failed: 'Ghép video thất bại',
  output_persistence_failed: 'Không tải được video về',
  execution_timeout: 'Quá thời gian chờ provider',
  seedvis_not_started: 'Seedvis chưa bắt đầu tác vụ',
  seedvis_rate_limit_exceeded: 'Seedvis giới hạn tốc độ',
  seedvis_credential_missing: 'Key Seedvis đã bị xóa',
  cancelled_by_admin: 'Admin đã hủy',
  voice_timeline_mismatch: 'Lệch thời lượng giọng đọc',
  planner_credentials_missing: 'Chưa có key OpenAI',
};

export function errorLabel(code: string | null | undefined) {
  if (!code) return '';
  const kind = (code.split(':', 1)[0] ?? '').trim();
  return errors[kind] || kind;
}

export const statusLabels: Record<string, string> = {
  GENERATING: 'Đang tạo',
  MERGING: 'Đang ghép',
  QUEUED: 'Đang chờ',
  SUBMITTING: 'Đang gửi',
  SUBMITTED: 'Đã gửi',
  COMPLETED: 'Hoàn thành',
  POST_PROCESSING: 'Đang lưu',
  FAILED: 'Lỗi',
  CANCELLED: 'Đã hủy',
  NEEDS_USER_ACTION: 'Cần kiểm tra',
};
