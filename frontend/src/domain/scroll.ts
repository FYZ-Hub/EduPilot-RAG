/**
 * 自动滚动判定（UI_SPEC 6.5）：用户接近底部时跟随新 token，主动向上滚动后暂停。
 *
 * 纯函数，便于确定性测试；阈值之外的距离视为「用户正在阅读历史」。
 */

/** 距底部小于该像素数时继续自动跟随。 */
export const AUTO_SCROLL_THRESHOLD = 80

export interface ScrollMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

export function isNearBottom(
  metrics: ScrollMetrics,
  threshold: number = AUTO_SCROLL_THRESHOLD,
): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}
