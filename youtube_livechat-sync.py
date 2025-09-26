import os
import re
from datetime import datetime
import urllib.request, urllib.parse, json
import time
import ssl
import random
import requests
import obspython as obs
import threading

API_KEY = ""
CHANNEL_INPUT = ""
BROWSER_SOURCE = ""
COMPUTER_NAME = ""
WRITE_LOG_PATH = ""
READ_LOG_PATH = ""
BASE_INIT_INTERVAL = 5
REFRESH_COOLDOWN = 10
MAX_INIT_ATTEMPTS = 3
MAX_INIT_INTERVAL = 30
UPDATE_INTERVAL = 30

_video_id = None
_popout_chat_url = None
_last_posted_link = None
_last_log_mtime = None
_streaming_active = False
_inited = False
_init_timer_active = False
_monitor_timer_active = False
_update_timer_active = False
_current_settings = None
_init_attempt_count = 0
_api_call_count = 0
_total_quota_used = 0
_refresh_in_progress = False
_last_refresh_time = 0
_current_init_interval = BASE_INIT_INTERVAL
_consecutive_failures = 0
_last_request_time = 0
_request_lock = threading.Lock()
_pending_video_id = None
_video_id_lock = threading.Lock()
_last_scheduled_refresh = 0
_refresh_timer_active = False
_update_request_in_progress = False
_log_throttle_lock = threading.Lock()
_last_log_time = 0
_log_queue = []

SHARE_LINK_PATTERN = re.compile(r'https://youtube\.com/live/[a-zA-Z0-9_-]+\?feature=share')

def log_with_timestamp(level, message):
    global _last_log_time, _log_queue

    with _log_throttle_lock:
        current_time = time.time()

        if current_time - _last_log_time < 0.1:
            _log_queue.append((level, message))
            return

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        full_message = f"[{timestamp}] {message}"
        obs.script_log(level, full_message)
        _last_log_time = current_time

        if _log_queue:
            queued_level, queued_message = _log_queue.pop(0)
            queued_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            queued_full_message = f"[{queued_timestamp}] {queued_message}"
            obs.script_log(queued_level, queued_full_message)

def calculate_dynamic_interval(base_interval, failures, max_interval):
    if failures == 0:
        return base_interval
    interval = base_interval * (1.5 ** min(failures, 5))
    return min(interval, max_interval)

def create_ssl_context():
    context = ssl.create_default_context()
    return context

def refresh_browser_source():
    global _refresh_in_progress, _last_refresh_time

    current_video_id = get_current_video_id()
    if not current_video_id or not BROWSER_SOURCE:
        return

    if _refresh_in_progress:
        return

    src = obs.obs_get_source_by_name(BROWSER_SOURCE)
    if not src:
        return

    try:
        _refresh_in_progress = True
        _last_refresh_time = time.time()

        settings = obs.obs_source_get_settings(src)
        current_url = obs.obs_data_get_string(settings, "url")
        expected_url = f"https://www.youtube.com/live_chat?is_popout=1&v={current_video_id}"

        if current_url != expected_url:
            log_with_timestamp(obs.LOG_INFO, f"🔧 [REFRESH] URL mismatch, correcting: {current_url} -> {expected_url}")
            obs.obs_data_set_string(settings, "url", expected_url)
            obs.obs_source_update(src, settings)

        action = globals().get('_next_refresh_action', 0)

        if action == 2:
            log_with_timestamp(obs.LOG_INFO, "🧨 [REFRESH] One-shot HARD reload (shutdown) due to connection error")
            obs.obs_data_set_bool(settings, "shutdown", True)
            obs.obs_source_update(src, settings)
            time.sleep(0.1)
            obs.obs_data_set_bool(settings, "shutdown", False)
            obs.obs_source_update(src, settings)

        elif action == 1:
            log_with_timestamp(obs.LOG_INFO, "🔄 [REFRESH] One-shot FULL reload (restart_when_active) due to timeout")
            obs.obs_data_set_bool(settings, "restart_when_active", True)
            obs.obs_source_update(src, settings)
            time.sleep(0.1)
            obs.obs_data_set_bool(settings, "restart_when_active", False)
            obs.obs_source_update(src, settings)

        else:
            obs.obs_data_set_bool(settings, "refresh_cache", True)
            obs.obs_source_update(src, settings)
            time.sleep(0.1)
            obs.obs_data_set_bool(settings, "refresh_cache", False)
            obs.obs_source_update(src, settings)

        if action:
            globals()['_next_refresh_action'] = 0

        obs.obs_data_release(settings)
        obs.obs_source_release(src)

    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [REFRESH] Error: {e}")
        if 'settings' in locals():
            obs.obs_data_release(settings)
        if 'src' in locals():
            obs.obs_source_release(src)
    finally:
        _refresh_in_progress = False

def scheduled_refresh():
    if _streaming_active and _inited:
        refresh_browser_source()

def _start_refresh_timer():
    global _refresh_timer_active
    if not _refresh_timer_active:
        obs.timer_add(scheduled_refresh, 5000)
        _refresh_timer_active = True

def _stop_refresh_timer():
    global _refresh_timer_active
    if _refresh_timer_active:
        obs.timer_remove(scheduled_refresh)
        _refresh_timer_active = False

def normalize_channel_input(channel_input):
    if not channel_input:
        return None, None

    channel_input = channel_input.strip()

    if channel_input.startswith('https://'):
        if '/@' in channel_input:
            handle = channel_input.split('/@')[-1].split('/')[0]
            return 'handle', handle
        elif '/channel/' in channel_input:
            channel_id = channel_input.split('/channel/')[-1].split('/')[0]
            return 'channel_id', channel_id

    if channel_input.startswith('UC') and len(channel_input) == 24:
        return 'channel_id', channel_input

    if channel_input.startswith('@'):
        return 'handle', channel_input[1:]
    else:
        return 'handle', channel_input

def build_channel_streams_url(channel_input):
    channel_type, clean_input = normalize_channel_input(channel_input)

    if not channel_type or not clean_input:
        return None

    if channel_type == 'handle':
        return f"https://www.youtube.com/@{clean_input}/streams"
    elif channel_type == 'channel_id':
        return f"https://www.youtube.com/channel/{clean_input}/streams"

    return None

def get_video_id_html(channel_input, timeout=30):
    global _consecutive_failures, _last_request_time

    with _request_lock:
        current_time = time.time()
        if current_time - _last_request_time < 2:
            time.sleep(2 - (current_time - _last_request_time))
        _last_request_time = time.time()

    try:
        streams_url = build_channel_streams_url(channel_input)
        if not streams_url:
            return None

        log_with_timestamp(obs.LOG_INFO, f"🌐 [HTML] Making HTTP request to: {streams_url}")

        session = requests.Session()

        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

        timeout_config = (min(30, timeout//2), timeout)

        try:
            with session:
                response = session.get(
                    streams_url,
                    timeout=timeout_config,
                    verify=True,
                    allow_redirects=True
                )
                response.raise_for_status()
                stdout = response.text

            pattern1 = r'"videoRenderer":\{"videoId":"[^"]+"'
            result = re.search(pattern1, stdout)

            if result is None:
                pattern2 = r'"gridVideoRenderer":\{"videoId":"[^"]+"'
                result = re.search(pattern2, stdout)

            if result is not None:
                matched_string = result.group()
                video_id_pattern = r':"([^"]+)"'
                video_id_match = re.search(video_id_pattern, matched_string)

                if video_id_match:
                    video_id = video_id_match.group(1)
                    log_with_timestamp(obs.LOG_INFO, f"✅ [HTML] Found video ID: {video_id}")
                    _consecutive_failures = 0
                    return video_id

            return None

        except requests.exceptions.Timeout:
            log_with_timestamp(obs.LOG_WARNING, f"⏰ [HTML] Request timeout after {timeout} seconds")
            _consecutive_failures += 1
            globals()['_next_refresh_action'] = max(globals().get('_next_refresh_action', 0), 1)
            return None

        except requests.exceptions.ConnectionError:
            log_with_timestamp(obs.LOG_WARNING, f"🔌 [HTML] Connection error")
            _consecutive_failures += 1
            globals()['_next_refresh_action'] = 2
            return None

        except requests.exceptions.HTTPError as e:
            log_with_timestamp(obs.LOG_WARNING, f"🌐 [HTML] HTTP error: {e}")
            _consecutive_failures += 1
            return None

    except Exception as e:
        log_with_timestamp(obs.LOG_WARNING, f"⚠️ [HTML] Unexpected error: {e}")
        _consecutive_failures += 1
        return None

def handle_to_channel_id_api(handle, api_key):
    try:
        global _api_call_count, _total_quota_used
        _api_call_count += 1
        _total_quota_used += 100

        log_with_timestamp(obs.LOG_INFO, f"🌐 [API] Making HTTP request for handle conversion: {handle}")

        q = urllib.parse.urlencode({
            "part": "snippet",
            "q": handle,
            "type": "channel",
            "key": api_key,
            "maxResults": 1
        })

        context = create_ssl_context()
        with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{q}", timeout=20, context=context) as r:
            data = json.load(r)

        items = data.get("items", [])
        if items:
            channel_id = items[0]["snippet"]["channelId"]
            log_with_timestamp(obs.LOG_INFO, f"🔍 [API] Converted handle to channel ID: {channel_id}")
            return channel_id

        return None
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [API] Handle conversion failed: {e}")
        return None

def get_video_id_api(channel_input, api_key):
    try:
        global _api_call_count, _total_quota_used

        channel_type, clean_input = normalize_channel_input(channel_input)
        if not clean_input:
            return None

        if channel_type == 'channel_id':
            channel_id = clean_input
        else:
            channel_id = handle_to_channel_id_api(clean_input, api_key)
            if not channel_id:
                return None

        _api_call_count += 1
        _total_quota_used += 100

        log_with_timestamp(obs.LOG_INFO, f"🌐 [API] Making HTTP request for live streams: {channel_id}")

        q1 = urllib.parse.urlencode({
            "part": "id",
            "channelId": channel_id,
            "eventType": "live",
            "type": "video",
            "key": api_key,
            "maxResults": 1
        })

        context = create_ssl_context()
        with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{q1}", timeout=20, context=context) as r:
            data = json.load(r)

        items = data.get("items", [])
        if items:
            video_id = items[0]["id"]["videoId"]

            _api_call_count += 1
            _total_quota_used += 100

            log_with_timestamp(obs.LOG_INFO, f"🌐 [API] Making HTTP request for video details: {video_id}")

            q2 = urllib.parse.urlencode({"part": "liveStreamingDetails", "id": video_id, "key": api_key})
            with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?{q2}", timeout=20, context=context) as r:
                data = json.load(r)

            details = data["items"][0].get("liveStreamingDetails", {})
            if details.get("activeLiveChatId"):
                log_with_timestamp(obs.LOG_INFO, f"✅ [API] Found live video ID: {video_id}")
                return video_id

        return None
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [API] Error: {e}")
        return None

def get_current_video_id():
    with _video_id_lock:
        if _pending_video_id:
            return _pending_video_id
        return _video_id

def set_primary_video_id(video_id):
    global _video_id, _popout_chat_url
    with _video_id_lock:
        _video_id = video_id
        if video_id:
            _popout_chat_url = f"https://www.youtube.com/live_chat?is_popout=1&v={video_id}"

def set_pending_video_id(video_id):
    global _pending_video_id
    with _video_id_lock:
        _pending_video_id = video_id

def apply_pending_video_id():
    global _video_id, _popout_chat_url, _pending_video_id
    with _video_id_lock:
        if _pending_video_id and _pending_video_id != _video_id:
            old_id = _video_id
            _video_id = _pending_video_id
            _popout_chat_url = f"https://www.youtube.com/live_chat?is_popout=1&v={_video_id}"

            src = obs.obs_get_source_by_name(BROWSER_SOURCE)
            if src:
                settings = obs.obs_source_get_settings(src)
                obs.obs_data_set_string(settings, "url", _popout_chat_url)
                obs.obs_source_update(src, settings)
                obs.obs_data_release(settings)
                obs.obs_source_release(src)

            log_with_timestamp(obs.LOG_INFO, f"🔄 [UPDATE] Video ID applied: {old_id} -> {_video_id}")
            log_share_link_to_file(_video_id, _popout_chat_url)
            return True
        return False

def update_video_id_periodically():
    global _update_request_in_progress

    if not _streaming_active or not _inited:
        return

    if _update_request_in_progress:
        return

    def background_update():
        global _update_request_in_progress
        try:
            _update_request_in_progress = True
            new_video_id = get_video_id_html(CHANNEL_INPUT, timeout=30)
            if new_video_id:
                set_pending_video_id(new_video_id)
        except Exception:
            pass
        finally:
            _update_request_in_progress = False

    threading.Thread(target=background_update, daemon=True).start()

def script_description():
    return "YouTube Live Chat Manager - Stream start detection, cross-device link sharing, browser source auto-refresh with HTML parsing and API fallback"

def script_properties():
    p = obs.obs_properties_create()
    obs.obs_properties_add_text(p, "api_key", "YouTube API Key (Optional)", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(p, "channel_input", "Channel Handle/ID/URL", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "browser_source", "Browser Source Name", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "computer_name", "Computer Identifier", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "write_log_path", "Write Log File Path", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(p, "read_log_path", "Read Log Directory Path", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(p, "base_init_interval", "Base Init Interval (sec)", 1, 60, 1)
    obs.obs_properties_add_int(p, "refresh_cooldown", "Monitor & Refresh Interval (sec)", 1, 300, 1)
    obs.obs_properties_add_int(p, "max_init_attempts", "Maximum Init Attempts", 1, 20, 1)
    obs.obs_properties_add_int(p, "max_init_interval", "Max Init Retry Interval (sec)", 3, 300, 5)
    obs.obs_properties_add_int(p, "update_interval", "Video ID Update Interval (sec)", 30, 300, 5)
    return p

def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "base_init_interval", BASE_INIT_INTERVAL)
    obs.obs_data_set_default_int(settings, "refresh_cooldown", REFRESH_COOLDOWN)
    obs.obs_data_set_default_int(settings, "max_init_attempts", MAX_INIT_ATTEMPTS)
    obs.obs_data_set_default_int(settings, "max_init_interval", MAX_INIT_INTERVAL)
    obs.obs_data_set_default_int(settings, "update_interval", UPDATE_INTERVAL)
    obs.obs_data_set_default_string(settings, "computer_name", "PC1")

def script_update(settings):
    global API_KEY, CHANNEL_INPUT, BROWSER_SOURCE, COMPUTER_NAME, WRITE_LOG_PATH, READ_LOG_PATH
    global BASE_INIT_INTERVAL, REFRESH_COOLDOWN, MAX_INIT_ATTEMPTS, MAX_INIT_INTERVAL, UPDATE_INTERVAL, _current_settings

    _current_settings = settings
    API_KEY = obs.obs_data_get_string(settings, "api_key")
    CHANNEL_INPUT = obs.obs_data_get_string(settings, "channel_input")
    BROWSER_SOURCE = obs.obs_data_get_string(settings, "browser_source")
    COMPUTER_NAME = obs.obs_data_get_string(settings, "computer_name")
    WRITE_LOG_PATH = obs.obs_data_get_string(settings, "write_log_path")
    READ_LOG_PATH = obs.obs_data_get_string(settings, "read_log_path")
    BASE_INIT_INTERVAL = obs.obs_data_get_int(settings, "base_init_interval")
    REFRESH_COOLDOWN = obs.obs_data_get_int(settings, "refresh_cooldown")
    MAX_INIT_ATTEMPTS = obs.obs_data_get_int(settings, "max_init_attempts")
    MAX_INIT_INTERVAL = obs.obs_data_get_int(settings, "max_init_interval")
    UPDATE_INTERVAL = obs.obs_data_get_int(settings, "update_interval")

def log_share_link_to_file(video_id, popout_chat_url):
    if not WRITE_LOG_PATH:
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
        log_with_timestamp(obs.LOG_INFO, f"📝 [LOG] Share link written to file")
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [LOG] Write error: {e}")

def fetch_latest_share_link():
    global _last_log_mtime
    if not READ_LOG_PATH:
        return None

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

        if not log_file_path or not os.path.exists(log_file_path):
            return None

        current_mtime = os.path.getmtime(log_file_path)
        if _last_log_mtime is not None and current_mtime <= _last_log_mtime:
            return None
        _last_log_mtime = current_mtime

        with open(log_file_path, "r", encoding="utf-8") as log_file:
            lines = log_file.readlines()
        if not lines:
            return None

        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                share_link = data.get("shareLink", "")
                if SHARE_LINK_PATTERN.match(share_link):
                    log_with_timestamp(obs.LOG_INFO, f"📨 [REMOTE] New share link found")
                    return share_link
            except (ValueError, KeyError):
                continue

        return None
    except Exception as e:
        log_with_timestamp(obs.LOG_WARNING, f"⚠️ [REMOTE] Read error: {e}")
        return None

def post_share_link_to_chat(link):
    global _last_posted_link
    if link == _last_posted_link:
        return

    if not SHARE_LINK_PATTERN.match(link):
        return

    try:
        log_with_timestamp(obs.LOG_INFO, f"📤 [POST] Ready to post share link to chat")
        _last_posted_link = link
    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [POST] Error: {e}")

def init_live_chat():
    global _inited, _init_timer_active
    global _init_attempt_count, _current_init_interval, _consecutive_failures

    if not _streaming_active or _inited:
        return

    _init_attempt_count += 1
    if _init_attempt_count > MAX_INIT_ATTEMPTS:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [INIT] Max attempts reached, stopping init")
        _stop_init_timer()
        return

    _current_init_interval = calculate_dynamic_interval(BASE_INIT_INTERVAL, _consecutive_failures, MAX_INIT_INTERVAL)

    log_with_timestamp(obs.LOG_INFO, f"🚀 [INIT] Attempt {_init_attempt_count}/{MAX_INIT_ATTEMPTS} (failures: {_consecutive_failures}, next interval: {_current_init_interval}s)")

    try:
        video_id = None
        video_id = get_video_id_html(CHANNEL_INPUT, timeout=30)

        if not video_id and API_KEY:
            video_id = get_video_id_api(CHANNEL_INPUT, API_KEY)

        if not video_id:
            log_with_timestamp(obs.LOG_INFO, f"🔄 [INIT] Both methods failed, retrying in {_current_init_interval}s")
            _restart_init_timer()
            return

        set_primary_video_id(video_id)

        src = obs.obs_get_source_by_name(BROWSER_SOURCE)
        if src:
            settings = obs.obs_source_get_settings(src)
            obs.obs_data_set_string(settings, "url", _popout_chat_url)
            obs.obs_source_update(src, settings)
            obs.obs_data_release(settings)
            obs.obs_source_release(src)

        log_with_timestamp(obs.LOG_INFO, f"🎮 [INIT] Chat URL prepared: {video_id}")
        log_share_link_to_file(video_id, _popout_chat_url)

        _inited = True
        _consecutive_failures = 0
        log_with_timestamp(obs.LOG_INFO, f"🎉 [INIT] Success! Quota used: {_total_quota_used}")

        _stop_init_timer()

        def start_monitor_timer():
            _start_monitor_timer()

        def start_refresh_timer():
            _start_refresh_timer()

        def start_update_timer():
            _start_update_timer()

        obs.timer_add(start_monitor_timer, 2000)
        obs.timer_add(start_refresh_timer, 5000)
        obs.timer_add(start_update_timer, 10000)

    except Exception as e:
        log_with_timestamp(obs.LOG_ERROR, f"❌ [INIT] Unexpected error: {e}")
        _consecutive_failures += 1

def _restart_init_timer():
    global _init_timer_active
    if _init_timer_active:
        obs.timer_remove(init_live_chat)
        _init_timer_active = False

    obs.timer_add(init_live_chat, int(_current_init_interval * 1000))
    _init_timer_active = True

def _start_init_timer():
    global _init_timer_active
    if not _init_timer_active:
        obs.timer_add(init_live_chat, BASE_INIT_INTERVAL * 1000)
        _init_timer_active = True

def _stop_init_timer():
    global _init_timer_active
    if _init_timer_active:
        obs.timer_remove(init_live_chat)
        _init_timer_active = False

def _start_monitor_timer():
    global _monitor_timer_active
    if not _monitor_timer_active:
        obs.timer_add(monitor_and_sync, REFRESH_COOLDOWN * 1000)
        _monitor_timer_active = True

def _stop_monitor_timer():
    global _monitor_timer_active
    if _monitor_timer_active:
        obs.timer_remove(monitor_and_sync)
        _monitor_timer_active = False

def _start_update_timer():
    global _update_timer_active
    if not _update_timer_active:
        obs.timer_add(update_video_id_periodically, UPDATE_INTERVAL * 1000)
        _update_timer_active = True

def _stop_update_timer():
    global _update_timer_active
    if _update_timer_active:
        obs.timer_remove(update_video_id_periodically)
        _update_timer_active = False

def monitor_and_sync():
    video_id_changed = apply_pending_video_id()

    latest_share_link = fetch_latest_share_link()
    if latest_share_link:
        post_share_link_to_chat(latest_share_link)

def on_frontend_event(event):
    global _streaming_active
    if event == obs.OBS_FRONTEND_EVENT_STREAMING_STARTED:
        if _streaming_active:
            return
        _streaming_active = True
        _reset_state()
        script_update(_current_settings)
        log_with_timestamp(obs.LOG_INFO, "🎬 [EVENT] Stream started")
        _start_init_timer()

    elif event in (obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED, obs.OBS_FRONTEND_EVENT_EXIT):
        if not _streaming_active:
            return
        _streaming_active = False
        _stop_all()
        log_with_timestamp(obs.LOG_INFO, f"🛑 [EVENT] Stream stopped, quota used: {_total_quota_used}")

def _reset_state():
    global _video_id, _popout_chat_url, _inited, _last_posted_link, _last_log_mtime
    global _init_attempt_count, _api_call_count, _total_quota_used
    global _refresh_in_progress, _last_refresh_time
    global _current_init_interval, _consecutive_failures, _pending_video_id, _update_request_in_progress

    _video_id, _popout_chat_url, _last_posted_link, _last_log_mtime, _inited = None, None, None, None, False
    _init_attempt_count = 0
    _api_call_count = 0
    _total_quota_used = 0
    _refresh_in_progress = False
    _last_refresh_time = 0
    _current_init_interval = BASE_INIT_INTERVAL
    _consecutive_failures = 0
    _pending_video_id = None
    _update_request_in_progress = False
    _stop_all()

def _stop_all():
    _stop_init_timer()
    _stop_monitor_timer()
    _stop_update_timer()
    _stop_refresh_timer()

def script_load(settings):
    global _current_settings
    _current_settings = settings
    obs.obs_frontend_add_event_callback(on_frontend_event)
    log_with_timestamp(obs.LOG_INFO, "🚀 [LOAD] Script loaded")
    if obs.obs_frontend_streaming_active():
        on_frontend_event(obs.OBS_FRONTEND_EVENT_STREAMING_STARTED)

def script_unload():
    obs.obs_frontend_remove_event_callback(on_frontend_event)
    log_with_timestamp(obs.LOG_INFO, f"👋 [UNLOAD] Script unloaded, total quota: {_total_quota_used}")
    _stop_all()