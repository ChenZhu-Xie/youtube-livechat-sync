# spyder_live_video_id.py
import re
import time
import requests

# 手动填写你的频道名/handle/链接，例如：
#   "@xczphysics"
#   "xczphysics"
#   "https://youtube.com/@尘竹-3-梦瑶"
CHANNEL_INPUT = "@xczphysics"

def normalize_channel_input(channel_input: str):
    if not channel_input:
        return None, None
    s = channel_input.strip()
    if s.startswith('https://'):
        if '/@' in s:
            return 'handle', s.split('/@')[-1].split('/')[0]
        if '/channel/' in s:
            return 'channel_id', s.split('/channel/')[-1].split('/')[0]
    if s.startswith('UC') and len(s) == 24:
        return 'channel_id', s
    if s.startswith('@'):
        return 'handle', s[1:]
    return 'handle', s

def build_channel_urls(channel_input: str):
    t, v = normalize_channel_input(channel_input)
    if not t or not v:
        return None, None
    if t == 'handle':
        live_url = f"https://www.youtube.com/@{v}/live"
        streams_url = f"https://www.youtube.com/@{v}/streams"
    else:
        live_url = f"https://www.youtube.com/channel/{v}/live"
        streams_url = f"https://www.youtube.com/channel/{v}/streams"
    return live_url, streams_url

def new_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.8',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Connection': 'keep-alive',
    })
    return s

def get_video_id_via_live(live_url: str, timeout=10):
    try:
        s = new_session()
        # 先允许重定向，很多情况下最终URL就是 /watch?v=...
        r = s.get(live_url, timeout=timeout, allow_redirects=True)
        final_url = r.url or ""
        if "/watch?v=" in final_url:
            vid = final_url.split("v=")[-1].split("&")[0]
            if len(vid) == 11:
                return vid
        # 再试一次不跟随重定向，读 Location
        r = s.get(live_url, timeout=timeout, allow_redirects=False)
        loc = r.headers.get("location", "")
        if "/watch?v=" in loc:
            vid = loc.split("v=")[-1].split("&")[0]
            if len(vid) == 11:
                return vid
        return None
    except requests.RequestException:
        return None

def fetch_html(url: str, timeout=10):
    ts = int(time.time() * 1000)  # 简单防缓存
    url = url + ("&_ts=" if "?" in url else "?_ts=") + str(ts)
    s = new_session()
    r = s.get(url, timeout=timeout, verify=True, allow_redirects=True)
    r.raise_for_status()
    return r.text

def extract_live_ids_from_streams_html(html: str):
    # 只收集带 "LIVE" 或 isLive 的条目，降低拿到预告/回放的概率
    ids = []
    seen = set()
    def add(vid):
        if len(vid) == 11 and vid not in seen:
            ids.append(vid)
            seen.add(vid)

    # videoRenderer / gridVideoRenderer 中带 LIVE 标记或 isLive:true
    patterns = [
        r'"videoRenderer":\{[^}]*"videoId":"([^"]+)"[^}]*"thumbnailOverlayTimeStatusRenderer":\{"style":"LIVE"',
        r'"gridVideoRenderer":\{[^}]*"videoId":"([^"]+)"[^}]*"thumbnailOverlayTimeStatusRenderer":\{"style":"LIVE"',
        r'"videoRenderer":\{[^}]*"videoId":"([^"]+)"[^}]*"isLiveNow":true',
        r'"gridVideoRenderer":\{[^}]*"videoId":"([^"]+)"[^}]*"isLive":true',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, flags=re.DOTALL):
            add(m.group(1))

    # 兜底：isLive:true 邻近 videoId
    for m in re.finditer(r'"videoId":"([^"]+)"[^}]{0,800}"isLive":true', html, flags=re.DOTALL):
        add(m.group(1))

    return ids

def verify_live_on_watch(video_id: str, timeout=8):
    # 不依赖 chat（避免禁言导致误判），看 watch 页的 isLive 信号
    url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
    try:
        s = new_session()
        r = s.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return False
        t = r.text
        return (
            '"isLive":true' in t or
            '"isLiveContent":true' in t or
            '"isLiveNow":true' in t
        ) and 'playerLivePlaybackErrorMessageRenderer' not in t
    except requests.RequestException:
        return False

def get_current_live_video_id(channel_input: str, timeout=20):
    live_url, streams_url = build_channel_urls(channel_input)
    if not live_url:
        print("❌ 频道输入不合法")
        return None

    # 1) 优先 /live 重定向
    vid = get_video_id_via_live(live_url, timeout=min(10, timeout))
    if vid:
        # 可选：快速确认一次 watch 页（更稳）
        if verify_live_on_watch(vid, timeout=6):
            return vid
        # 如果验证失败，继续尝试 streams
    # 2) 回退 /streams HTML
    try:
        html = fetch_html(streams_url, timeout=timeout)
    except Exception as e:
        print(f"❌ 获取 streams 页面失败: {e}")
        return None
    live_ids = extract_live_ids_from_streams_html(html)
    if not live_ids:
        return None
    # 验证候选，返回第一个确认 LIVE 的
    for cand in live_ids:
        if verify_live_on_watch(cand, timeout=6):
            return cand
    # 若都未通过校验，返回第一个候选（尽力而为）
    return live_ids[0]

# 在 Spyder 中直接运行这个单元即可
if __name__ == "__main__":
    print("🧪 开始 LIVE 视频ID 提取")
    t0 = time.time()
    vid = get_current_live_video_id(CHANNEL_INPUT, timeout=20)
    if vid:
        print(f"🎉 成功! LIVE 视频ID: {vid}")
        print(f"📎 分享链接: https://youtube.com/live/{vid}?feature=share")
        print(f"💬 聊天窗口: https://www.youtube.com/live_chat?is_popout=1&v={vid}")
    else:
        print("💔 未找到 LIVE 视频ID")
    print(f"⏱️ 用时: {time.time()-t0:.2f}s")