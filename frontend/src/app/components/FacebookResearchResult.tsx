import { Action } from '../types';

interface FacebookResearchResultProps {
  data: NonNullable<Action['facebookResearch']>;
}

function cleanSnippet(value: string): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  const collapsed = text.replace(/\bFacebook(?:\s+Facebook){2,}\b/gi, 'Facebook');
  const withoutLeadingBoilerplate = collapsed
    .replace(/^(Facebook\b\s*)+/i, '')
    .replace(/^(Like|Comment|Share|Follow)\b\s*/i, '')
    .trim();
  return withoutLeadingBoilerplate || 'Không có snippet rõ.';
}

function cleanAuthorTitle(value: string): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return 'Facebook Post';
  return text
    .replace(/\s+Facebook.*$/i, '')
    .replace(/\s+Post.*$/i, "'s Post")
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function isUsableFacebookPostUrl(value: string): boolean {
  const url = String(value || '').trim();
  if (!url) return false;
  if (url.includes('/search/posts/')) return false;
  if (/\/permalink\//i.test(url) || /\/posts\//i.test(url) || /story_fbid=/i.test(url) || /permalink\.php/i.test(url)) return true;
  if (/\/photo\/\?/i.test(url) || /[?&]fbid=/i.test(url)) return true;
  return false;
}

function timeStatusLabel(value: string): string {
  if (value === 'recent_confirmed') return 'Đã xác nhận còn mới';
  if (value === 'stale_confirmed') return 'Đã xác nhận quá cũ';
  return '';
}

function timeStatusClass(value: string): string {
  if (value === 'recent_confirmed') return 'bg-green-100 text-green-700 border-green-200';
  if (value === 'time_unknown') return 'bg-amber-100 text-amber-700 border-amber-200';
  if (value === 'stale_confirmed') return 'bg-red-100 text-red-700 border-red-200';
  return 'bg-gray-100 text-gray-700 border-gray-200';
}

function authorInitials(value: string): string {
  const parts = String(value || '')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  if (!parts.length) return 'FB';
  return parts.map((part) => part[0]?.toUpperCase() || '').join('');
}

function buildTags(item: FacebookResearchResultProps['data']['items'][number]): string[] {
  const tags = new Set<string>();
  const reason = String(item.keepReason || '').toLowerCase();
  const summary = String(item.summary || '').toLowerCase();
  const author = String(item.author || '').toLowerCase();
  const combined = `${reason} ${summary} ${author}`;

  if (/devops|cloud|platform|sre/.test(combined)) tags.add('DevOps / Cloud');
  if (/intern|fresher/.test(combined)) tags.add('Intern · Fresher');
  if (/hcm|tphcm|ho chi minh|sai gon|hcmc/.test(combined)) tags.add('HCM');
  if (/recruit|tuyển|hiring|job/.test(combined)) tags.add('Bài tuyển');
  return Array.from(tags).slice(0, 3);
}

function buildReadableReasons(item: FacebookResearchResultProps['data']['items'][number]): string[] {
  const reasons = new Set<string>();
  const rawReason = String(item.keepReason || '').toLowerCase();
  const combined = `${rawReason} ${String(item.summary || '').toLowerCase()} ${String(item.author || '').toLowerCase()}`;

  if (/devops|cloud|platform|sre/.test(combined)) {
    reasons.add('Khớp từ khóa DevOps / Cloud / Platform');
  }
  if (/tuyển|tuyen|hiring|recruit|job/.test(combined)) {
    reasons.add('Có tín hiệu đây là bài tuyển dụng');
  }
  if (/intern|fresher|junior/.test(combined)) {
    reasons.add('Có nhắc tới level intern / fresher / junior');
  }
  if (/hcm|tphcm|ho chi minh|sai gon|hcmc/.test(combined)) {
    reasons.add('Có nhắc tới khu vực HCM / TP.HCM');
  }
  if (/mới|moi|today|1h|2h|3h|recent|fresh|thời gian mới/.test(combined)) {
    reasons.add('Có tín hiệu bài còn mới');
  }
  if (/đúng khu vực yêu cầu|đúng khu vực/.test(rawReason)) {
    reasons.add('Khớp khu vực bạn đang tìm');
  }
  if (/đúng role|role devops|cloud\/platform/.test(rawReason)) {
    reasons.add('Khớp vai trò bạn đang tìm');
  }
  if (/đúng bài tuyển/.test(rawReason)) {
    reasons.add('Đúng kiểu bài đăng tuyển, không phải bài hỏi đáp');
  }

  if (!reasons.size && item.keepReason) {
    reasons.add(String(item.keepReason).trim());
  }

  return Array.from(reasons).slice(0, 3);
}

function metaSummary(data: FacebookResearchResultProps['data']): string {
  const total = data.items.filter((item) => isUsableFacebookPostUrl(item.url)).length;
  const parts = [`${total} bài phù hợp`];
  if (data.counts.recentConfirmed) parts.push(`${data.counts.recentConfirmed} bài mới`);
  if (data.counts.staleConfirmed) parts.push(`${data.counts.staleConfirmed} bài cũ`);
  return parts.join(' · ');
}

export function FacebookResearchResult({ data }: FacebookResearchResultProps) {
  const visibleItems = data.items.filter((item) => isUsableFacebookPostUrl(item.url));

  return (
    <div className="w-full max-w-full overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900 p-4 space-y-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-50">Kết quả Facebook</p>
          <p className="text-xs text-zinc-400">{metaSummary(data)}</p>
        </div>
        <div className="rounded-full border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-300">
          Vừa phân tích xong
        </div>
      </div>

      {data.summaryText && (
        <div className="rounded-xl border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs font-medium text-violet-200">
          {data.summaryText}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <span className="rounded-full border border-violet-500/30 bg-violet-500/10 px-3 py-1 text-xs font-medium text-violet-200">
          Tất cả
        </span>
        <span className="rounded-full border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs text-zinc-300">
          Facebook Groups
        </span>
        {data.counts.recentConfirmed > 0 && (
          <span className="rounded-full border border-green-500/30 bg-green-500/10 px-3 py-1 text-xs font-medium text-green-200">
            Có bài mới
          </span>
        )}
      </div>

      <div className="space-y-2">
        {visibleItems.map((item, index) => {
          const statusLabel = timeStatusLabel(item.timeStatus);
          const tags = buildTags(item);
          const readableReasons = buildReadableReasons(item);
          const isFeatured = index === 0;
          return (
            <div
              key={`${item.rank}-${item.url || item.author}`}
              className={`w-full max-w-full overflow-hidden rounded-2xl border p-4 transition-colors ${
                isFeatured ? 'border-violet-400 border-l-4 border-l-violet-400 bg-zinc-900' : 'border-zinc-700 bg-zinc-800'
              }`}
            >
              <div className="flex gap-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-zinc-700 bg-zinc-800 text-sm font-semibold text-zinc-100">
                  {authorInitials(item.author)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="break-words text-sm font-semibold text-zinc-100 [overflow-wrap:anywhere]">
                        <span className="mr-1 text-white">{item.rank}.</span>
                        {cleanAuthorTitle(item.author)}
                      </p>
                      <p className="mt-1 break-words text-xs text-zinc-400 [overflow-wrap:anywhere]">
                        {item.author}
                      </p>
                    </div>
                    {statusLabel && (
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${timeStatusClass(item.timeStatus)}`}
                      >
                        {statusLabel}
                      </span>
                    )}
                  </div>

                  {tags.length > 0 && (
                    <div className="mb-3 flex flex-wrap gap-2">
                      {tags.map((tag, tagIndex) => (
                        <span
                          key={`${item.rank}-tag-${tagIndex}`}
                          className={`rounded-full border px-2.5 py-1 text-[11px] ${
                            /devops|cloud/i.test(tag)
                              ? 'border-violet-400/40 bg-violet-400/15 text-violet-100 font-semibold shadow-sm'
                              : /bài tuyển/i.test(tag)
                              ? 'border-amber-400/40 bg-amber-400/15 text-amber-100 font-semibold'
                              : tagIndex === 0
                              ? 'border-violet-500/30 bg-violet-500/10 text-violet-200'
                              : 'border-zinc-700 bg-zinc-800 text-zinc-300'
                          }`}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}

                  <div className="space-y-2 text-sm text-zinc-300">
                    {readableReasons.length > 0 && (
                      <p className="break-words [overflow-wrap:anywhere]">
                        <span className="font-medium text-zinc-100">Lý do giữ:</span>{' '}
                        {readableReasons.join(' · ')}
                      </p>
                    )}
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-3 border-t border-zinc-700 pt-3">
                    <div className="text-xs text-zinc-400">
                      {statusLabel || ''}
                    </div>
                    <a
                      className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-zinc-600 px-3 py-1.5 text-xs font-medium text-zinc-100 hover:bg-zinc-800"
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      title={item.url}
                    >
                      Xem bài
                    </a>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
