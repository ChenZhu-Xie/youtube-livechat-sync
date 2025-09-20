import os
import re
from datetime import datetime
import urllib.request, urllib.parse, json
import obspython as obs

# ─── 脚本配置（在 OBS 脚本面板填写）────────────────────────────────
API_KEY          = ""    
CHANNEL_ID       = ""    
BROWSER_SOURCE   = ""    
COMPUTER_NAME    = ""    
WRITE_LOG_PATH   = ""    
READ_LOG_PATH    = ""    
INIT_INTERVAL    = 30_000 # 初始化重试间隔(ms)，默认 10 秒
MONITOR_INTERVAL = 5_000  # 轮询监控间隔(ms)，默认 5 秒
MAX_INIT_ATTEMPTS = 3     # 最大初始化尝试次数
BACKOFF_MULTIPLIER = 1.5  # 退避乘数

# ─── 内部状态变量 ──────────────────────────────────────────────────
_video_id            = None
_popout_chat_url     = None
_last_posted_link    = None 
_last_log_mtime      = None 
_streaming_active    = False
_inited              = False
_init_timer_added    = False
_monitor_timer_added = False
_current_settings    = None
_init_attempt_count  = 0
_current_init_interval = INIT_INTERVAL
_last_api_call_time  = 0
_api_call_count      = 0
_total_quota_used    = 0

# 正则表达式匹配 YouTube 分享链接格式
SHARE_LINK_PATTERN = re.compile(r'https://youtube\.com/live/[a-zA-Z0-9_-]+\?feature=share')

# ─── 新增：带时间戳的日志函数 ─────────────────────────────────────────
def log_with_timestamp(level, message):
    """自定义日志函数，自动添加 YYYY-MM-DD HH:MM:SS 格式的时间戳"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full_message = f"[{timestamp}] {message}"
    obs.script_log(level, full_message)

# ─── 核心功能代码 ──────────────────────────────────────────────────

def script_description():
    return (
        "功能：自动维护 YouTube Live Chat 链接，并实现跨设备直播链接分享。\n"
        "1. 开始推流 → 获取 videoId，更新本地浏览器源 URL。\n"
        "2. 将本机直播的 'shareLink' 等信息以 JSON Lines 格式写入日志文件。\n"
        "3. 初始化后，定时监控并执行：\n"
        "   - 修正被手动修改的本地浏览器源 URL。\n"
        "   - 读取另一台电脑的日志，获取其最新的 'shareLink' 并推送到本直播间。\n"
        "4. 停止推流/退出 → 自动清理所有后台任务。\n"
        "5. 配额保护：最多尝试3次初始化，指数退避策略（10s→15s→22.5s）。"
    )

def script_properties():
    p = obs.obs_properties_create()
    obs.obs_properties_add_text(p, "api_key", "API Key", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(p, "channel_id", "Channel ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "browser_source", "Browser Source", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "computer_name", "Computer Name (for logs)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "write_log_path", "Log Write Path (This PC)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "read_log_path", "Log Read Path (Other PC)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(p, "init_interval", "初始化重试间隔(ms)", 1_000, 600_000, 1_000)
    obs.obs_properties_add_int(p, "monitor_interval", "URL 校验与同步间隔(ms)", 1_000, 60_000, 1_000)
    obs.obs_properties_add_int(p, "max_init_attempts", "最大初始化尝试次数", 1, 10, 1)
    return p

def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "init_interval", INIT_INTERVAL)
    obs.obs_data_set_default_int(settings, "monitor_interval", MONITOR_INTERVAL)
    obs.obs_data_set_default_int(settings, "max_init_attempts", MAX_INIT_ATTEMPTS)
    obs.obs_data_set_default_string(settings, "computer_name", "MyPC")

def script_update(settings):
    global API_KEY, CHANNEL_ID, BROWSER_SOURCE, COMPUTER_NAME, WRITE_LOG_PATH, READ_LOG_PATH
    global INIT_INTERVAL, MONITOR_INTERVAL, MAX_INIT_ATTEMPTS, _current_settings
    _current_settings = settings
    API_KEY = obs.obs_data_get_string(settings, "api_key")
    CHANNEL_ID = obs.obs_data_get_string(settings, "channel_id")
    BROWSER_SOURCE = obs.obs_data_get_string(settings, "browser_source")
    COMPUTER_NAME = obs.obs_data_get_string(settings, "computer_name")
    WRITE_LOG_PATH = obs.obs_data_get_string(settings, "write_log_path")
    READ_LOG_PATH = obs.obs_data_get_string(settings, "read_log_path")
    INIT_INTERVAL = obs.obs_data_get_int(settings, "init_interval")
    MONITOR_INTERVAL = obs.obs_data_get_int(settings, "monitor_interval")
    MAX_INIT_ATTEMPTS = obs.obs_data_get_int(settings, "max_init_attempts")

def _rate_limit_check():
    global _last_api_call_time, _api_call_count
    current_time = datetime.now().timestamp()
    
    if current_time - _last_api_call_time > 60:
        _api_call_count = 0
        _last_api_call_time = current_time
    
    if _api_call_count >= 6:
        log_with_timestamp(obs.LOG_WARNING, "[RATE_LIMIT] API调用频率过高，跳过本次请求")
        return False
    
    return True

def _http_get(url):
    global _api_call_count, _total_quota_used
    if not _rate_limit_check():
        raise Exception("Rate limit exceeded")
    
    _api_call_count += 1
    _total_quota_used += 100
    
    with urllib.request.urlopen(url) as r:
        return json.load(r)

def log_share_link_to_file(video_id, popout_chat_url):
    if not WRITE_LOG_PATH:
        log_with_timestamp(obs.LOG_WARNING, "[LOG] 未配置日志写入路径 (Write Log Path)，跳过写入。")
        return

    now = datetime.now()
    time_iso = now.isoformat()
    share_link = f"https://youtube.com/live/{video_id}?feature=share"
    
    log_entry = {
        "timestamp": time_iso,
        "videoId": video_id,
        "shareLink": share_link,
        "popoutChatUrl": popout_chat_url,
        "sourceComputer": COMPUTER_NAME
    }

    try:
        log_file_path = os.path.join(WRITE_LOG_PATH, f"{COMPUTER_NAME}.jsonl") if not WRITE_LOG_PATH.lower().endswith('.jsonl') else WRITE_LOG_PATH
        log_dir = os.path.dirname(log_file_path)
        os.makedirs(log_dir, exist_ok=True)

        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(log_entry) + "\n")
        log_with_timestamp(obs.LOG_INFO, f"[LOG] 已将分享链接写入: {log_file_path}")
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"[LOG] 写入日志文件时发生错误: {e}")

def fetch_latest_share_link():
    global _last_log_mtime
    if not READ_LOG_PATH: return None

    try:
        log_file_path = None
        if READ_LOG_PATH.lower().endswith('.jsonl'):
            log_file_path = READ_LOG_PATH
        else:
            own_log_filename = f"{COMPUTER_NAME}.jsonl"
            for filename in os.listdir(READ_LOG_PATH):
                if filename.lower().endswith('.jsonl') and filename != own_log_filename:
                    log_file_path = os.path.join(READ_LOG_PATH, filename)
                    break
        
        if not log_file_path or not os.path.exists(log_file_path): return None

        current_mtime = os.path.getmtime(log_file_path)
        if _last_log_mtime is not None and current_mtime <= _last_log_mtime: return None
        _last_log_mtime = current_mtime

        with open(log_file_path, "r", encoding="utf-8") as log_file: lines = log_file.readlines()
        if not lines: return None

        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line: continue
            try:
                data = json.loads(line)
                share_link = data.get("shareLink", "")
                if SHARE_LINK_PATTERN.match(share_link):
                    log_with_timestamp(obs.LOG_INFO, f"[REMOTE] 找到有效分享链接: {share_link}")
                    return share_link
            except (ValueError, KeyError): continue
        
        return None
    except Exception as e:
        log_with_timestamp(obs.LOG_WARNING, f"[REMOTE] 读取远程日志时出错: {e}")
        return None

def post_share_link_to_chat(link):
    global _last_posted_link
    if link == _last_posted_link: return

    if not SHARE_LINK_PATTERN.match(link):
        log_with_timestamp(obs.LOG_WARNING, f"[POST] 链接格式无效，跳过发送: {link}")
        return

    try:
        log_with_timestamp(obs.LOG_INFO, f"[POST] 检测到新链接，准备发送到直播间: {link}")
        _last_posted_link = link
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"[POST] 发送链接到直播间时出错: {e}")

def init_live_chat():
    global _video_id, _popout_chat_url, _inited, _init_timer_added, _monitor_timer_added, _init_attempt_count, _total_quota_used
    if not _streaming_active or _inited: return

    _init_attempt_count += 1
    if _init_attempt_count > MAX_INIT_ATTEMPTS:
        log_with_timestamp(obs.LOG_ERROR, f"[INIT] 已达到最大尝试次数 ({MAX_INIT_ATTEMPTS})，停止初始化。总配额消耗: {_total_quota_used} units")
        if _init_timer_added: obs.timer_remove(init_live_chat); _init_timer_added = False
        return

    log_with_timestamp(obs.LOG_INFO, f"[INIT] 第 {_init_attempt_count}/{MAX_INIT_ATTEMPTS} 次尝试初始化，当前总配额消耗: {_total_quota_used} units")

    try:
        log_with_timestamp(obs.LOG_INFO, f"[API] 调用 Search API，预计消耗 100 units...")
        q1 = urllib.parse.urlencode({"part": "id", "channelId": CHANNEL_ID, "eventType": "live", "type": "video", "key": API_KEY, "maxResults": 1})
        r1 = _http_get(f"https://www.googleapis.com/youtube/v3/search?{q1}")
        log_with_timestamp(obs.LOG_INFO, f"[API] Search API 调用完成，当前总配额消耗: {_total_quota_used} units")
        
        items = r1.get("items", [])
        if not items:
            log_with_timestamp(obs.LOG_INFO, f"[INIT] 未找到直播视频，继续轮询... 当前总配额消耗: {_total_quota_used} units")
            _apply_backoff()
            return
            
        _video_id = items[0]["id"]["videoId"]
        log_with_timestamp(obs.LOG_INFO, f"[API] 调用 Videos API，预计消耗 100 units...")
        q2 = urllib.parse.urlencode({"part": "liveStreamingDetails", "id": _video_id, "key": API_KEY})
        r2 = _http_get(f"https://www.googleapis.com/youtube/v3/videos?{q2}")
        log_with_timestamp(obs.LOG_INFO, f"[API] Videos API 调用完成，当前总配额消耗: {_total_quota_used} units")
        
        details = r2["items"][0].get("liveStreamingDetails", {})
        if not details.get("activeLiveChatId"):
            log_with_timestamp(obs.LOG_INFO, f"[INIT] Live Chat 未就绪，继续轮询... 当前总配额消耗: {_total_quota_used} units")
            _apply_backoff()
            return

        src = obs.obs_get_source_by_name(BROWSER_SOURCE)
        if src:
            settings = obs.obs_source_get_settings(src)
            _popout_chat_url = f"https://studio.youtube.com/live_chat?is_popout=1&v={_video_id}"
            obs.obs_data_set_string(settings, "url", _popout_chat_url)
            obs.obs_source_update(src, settings)
            obs.obs_data_release(settings)
            obs.obs_source_release(src)
            log_with_timestamp(obs.LOG_INFO, f"[INIT] 已设置 Browser Source → {_popout_chat_url}")
            log_share_link_to_file(_video_id, _popout_chat_url)
        else:
            log_with_timestamp(obs.LOG_ERROR, f"[INIT] 未找到浏览器源「{BROWSER_SOURCE}」")

        _inited = True
        log_with_timestamp(obs.LOG_INFO, f"[INIT] ✅ 初始化成功！总共尝试了 {_init_attempt_count} 次，总配额消耗: {_total_quota_used} units")
        
        if _init_timer_added: obs.timer_remove(init_live_chat); _init_timer_added = False
        if not _monitor_timer_added: obs.timer_add(monitor_and_sync, MONITOR_INTERVAL); _monitor_timer_added = True
            
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"[INIT] 第 {_init_attempt_count} 次尝试发生错误: {e}，当前总配额消耗: {_total_quota_used} units")
        _apply_backoff()

def _apply_backoff():
    global _current_init_interval, _init_timer_added
    if _init_timer_added: obs.timer_remove(init_live_chat); _init_timer_added = False
    
    _current_init_interval = min(int(_current_init_interval * BACKOFF_MULTIPLIER), 300_000)
    backoff_seconds = _current_init_interval / 1000
    log_with_timestamp(obs.LOG_INFO, f"[BACKOFF] 下次重试间隔: {backoff_seconds:.1f}秒 (第{_init_attempt_count + 1}次尝试)")
    
    obs.timer_add(init_live_chat, _current_init_interval)
    _init_timer_added = True

def monitor_and_sync():
    global _total_quota_used
    log_with_timestamp(obs.LOG_DEBUG, f"[MONITOR] 定期检查中... 当前总配额消耗: {_total_quota_used} units")
    
    if _popout_chat_url:
        src = obs.obs_get_source_by_name(BROWSER_SOURCE)
        if src:
            settings = obs.obs_source_get_settings(src)
            current_url = obs.obs_data_get_string(settings, "url")
            if current_url != _popout_chat_url:
                obs.obs_data_set_string(settings, "url", _popout_chat_url)
                obs.obs_source_update(src, settings)
                log_with_timestamp(obs.LOG_INFO, f"[MONITOR] URL 被修改，已重置 → {_popout_chat_url}")
            obs.obs_data_release(settings)
            obs.obs_source_release(src)

    latest_share_link = fetch_latest_share_link()
    if latest_share_link:
        post_share_link_to_chat(latest_share_link)

def on_frontend_event(event):
    global _streaming_active, _init_timer_added, _total_quota_used
    if event == obs.OBS_FRONTEND_EVENT_STREAMING_STARTED:
        if _streaming_active: return
        _streaming_active = True
        _reset_state()
        script_update(_current_settings)
        log_with_timestamp(obs.LOG_INFO, "[EVENT] 推流开始 → 启动初始化定时器（延迟首次查询）")
        
        if not _init_timer_added:
            obs.timer_add(init_live_chat, INIT_INTERVAL)
            _init_timer_added = True
            
    elif event in (obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED, obs.OBS_FRONTEND_EVENT_EXIT):
        if not _streaming_active: return
        _streaming_active = False
        _stop_all()
        log_with_timestamp(obs.LOG_INFO, f"[EVENT] 推流停止/退出 → 脚本停工，本次会话总配额消耗: {_total_quota_used} units")

def _reset_state():
    global _video_id, _popout_chat_url, _inited, _last_posted_link, _last_log_mtime
    global _init_attempt_count, _current_init_interval, _api_call_count, _total_quota_used
    
    _video_id, _popout_chat_url, _last_posted_link, _last_log_mtime, _inited = None, None, None, None, False
    _init_attempt_count = 0
    _current_init_interval = INIT_INTERVAL
    _api_call_count = 0
    _total_quota_used = 0
    _stop_all()

def _stop_all():
    global _init_timer_added, _monitor_timer_added
    if _init_timer_added: obs.timer_remove(init_live_chat); _init_timer_added = False
    if _monitor_timer_added: obs.timer_remove(monitor_and_sync); _monitor_timer_added = False

def script_load(settings):
    global _current_settings
    _current_settings = settings
    obs.obs_frontend_add_event_callback(on_frontend_event)
    log_with_timestamp(obs.LOG_INFO, "[LOAD] 脚本已加载。")
    if obs.obs_frontend_streaming_active():
        log_with_timestamp(obs.LOG_INFO, "[LOAD] 已在推流 → 模拟初始化")
        on_frontend_event(obs.OBS_FRONTEND_EVENT_STREAMING_STARTED)

def script_unload():
    global _total_quota_used
    obs.obs_frontend_remove_event_callback(on_frontend_event)
    log_with_timestamp(obs.LOG_INFO, f"[UNLOAD] 脚本卸载，总配额消耗: {_total_quota_used} units")
    _stop_all()