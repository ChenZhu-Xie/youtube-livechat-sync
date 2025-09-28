# -*- coding: utf-8 -*-
"""
YouTube Live Chat Manager - Sequential init, main-thread timers, main-thread OBS ops
"""
import os
import re
import json
import time
import ssl
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from collections import deque

import requests
import obspython as obs

DEFAULT_BASE_INIT_INTERVAL = 1
DEFAULT_REFRESH_COOLDOWN = 12
DEFAULT_MAX_INIT_ATTEMPTS = 3
DEFAULT_MAX_INIT_INTERVAL = 23
DEFAULT_UPDATE_INTERVAL = 23

SHARE_LINK_PATTERN = re.compile(r'https://youtube\.com/live/[a-zA-Z0-9_-]+\?feature=share')

class Logger:
    def __init__(self):
        self._lock = threading.Lock()
        self._seq = 0

    def log(self, level, message):
        with self._lock:
            self._seq += 1
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            thread_name = threading.current_thread().name
            formatted_msg = f"[{ts}][{thread_name}][#{self._seq:06d}] {message}"
            obs.script_log(level, formatted_msg)

logger = Logger()

class MainThreadDispatcher:
    def __init__(self, interval_ms=33, max_tasks_per_tick=16):
        self.interval_ms = int(interval_ms)
        self.max_tasks_per_tick = max_tasks_per_tick
        self._queue_lock = threading.Lock()
        self._queue = deque()
        self._active = False
        self._task_seq = 0

    def _is_important(self, label):
        if not label:
            return False
        if label.startswith(("start_", "stop_", "debug")):
            return True
        if label in ("init:apply_url", "apply_url_to_source"):
            return True
        return False

    def start(self):
        if self._active:
            return
        self._active = True
        obs.timer_add(self._pump, self.interval_ms)
        logger.log(obs.LOG_INFO, f"🧰 [DISPATCH] started (interval={self.interval_ms}ms)")

    def stop(self):
        if not self._active:
            return
        try:
            obs.timer_remove(self._pump)
        except Exception:
            pass
        self._active = False
        with self._queue_lock:
            self._queue.clear()
        logger.log(obs.LOG_INFO, "🧰 [DISPATCH] stopped")

    def post(self, fn, *, delay_ms=0, label=None):
        run_at = time.time() + max(0, delay_ms) / 1000.0
        with self._queue_lock:
            self._task_seq += 1
            task_id = self._task_seq
            self._queue.append((run_at, label, fn, task_id, time.time()))
        if self._is_important(label):
            logger.log(obs.LOG_INFO, f"📌 [DISPATCH] queued#{task_id}: {label}, delay={delay_ms}ms")
        return task_id

    def _pump(self):
        now = time.time()
        items = []
        executed = 0
        with self._queue_lock:
            while self._queue and executed < self.max_tasks_per_tick:
                run_at, label, fn, task_id, queued_at = self._queue[0]
                if run_at > now:
                    break
                self._queue.popleft()
                items.append((label, fn, task_id, queued_at))
                executed += 1
        for label, fn, task_id, queued_at in items:
            try:
                if self._is_important(label):
                    wait_ms = int((time.time() - queued_at) * 1000)
                    logger.log(obs.LOG_INFO, f"▶️ [DISPATCH] running#{task_id}: {label}, waited={wait_ms}ms")
                fn()
            except Exception as e:
                logger.log(obs.LOG_ERROR, f"❌ [DISPATCH] task#{task_id} error: {e}")

_dispatcher = MainThreadDispatcher()

class YouTubeService:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    def __init__(self, api_key=None):
        self.api_key = api_key
        self._request_lock = threading.Lock()
        self._last_request_time = 0
        self.consecutive_failures = 0
        self.api_call_count = 0
        self.total_quota_used = 0

    def _rate_limit(self, min_interval=2):
        with self._request_lock:
            now = time.time()
            delta = now - self._last_request_time
            if delta < min_interval:
                time.sleep(min_interval - delta)
            self._last_request_time = time.time()

    def normalize_channel_input(self, channel_input):
        if not channel_input:
            return None, None
        s = channel_input.strip()
        if s.startswith('https://'):
            if '/@' in s:
                handle = s.split('/@')[-1].split('/')[0]
                return 'handle', handle
            elif '/channel/' in s:
                channel_id = s.split('/channel/')[-1].split('/')[0]
                return 'channel_id', channel_id
        if s.startswith('UC') and len(s) == 24:
            return 'channel_id', s
        if s.startswith('@'):
            return 'handle', s[1:]
        return 'handle', s

    def build_streams_url(self, channel_input):
        t, c = self.normalize_channel_input(channel_input)
        if not t:
            return None
        if t == 'handle':
            return f"https://www.youtube.com/@{c}/streams"
        return f"https://www.youtube.com/channel/{c}/streams"

    def get_video_id_html(self, channel_input, timeout=23):
        t, c = self.normalize_channel_input(channel_input)
        try:
            streams_url = self.build_streams_url(channel_input)
            if not streams_url:
                logger.log(obs.LOG_WARNING, "🌐 [HTML] invalid channel input (empty or unrecognized)")
                return None

            self._rate_limit(min_interval=2)
            headers = {
                'User-Agent': self.USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            logger.log(
                obs.LOG_INFO,
                f"🌐 [HTML] request start: url={streams_url}, channel_type={t}, key={c}, timeout={timeout}s"
            )
            session = requests.Session()
            session.headers.update(headers)
            t0 = time.time()
            resp = session.get(streams_url, timeout=(min(30, timeout//2), timeout), allow_redirects=True)
            elapsed = time.time() - t0
            status = resp.status_code
            redirect_count = len(resp.history)
            text = resp.text
            content_len = len(text) if text is not None else 0
            logger.log(
                obs.LOG_INFO,
                f"🌐 [HTML] response: status={status}, redirects={redirect_count}, elapsed={elapsed:.2f}s, len={content_len}"
            )
            resp.raise_for_status()

            m = re.search(r'"videoRenderer":\{"videoId":"([^"]+)"', text)
            if not m:
                m = re.search(r'"gridVideoRenderer":\{"videoId":"([^"]+)"', text)

            if m:
                video_id = m.group(1)
                logger.log(obs.LOG_INFO, f"🟢 [HTML] videoId found: {video_id}")
                self.consecutive_failures = 0
                return video_id

            logger.log(obs.LOG_INFO, "ℹ️ [HTML] no live videoId detected on streams page")
            self.consecutive_failures += 1
            return None

        except requests.exceptions.Timeout:
            logger.log(obs.LOG_WARNING, f"⏰ [HTML] timeout after {timeout}s (type={t}, key={c})")
            self.consecutive_failures += 1
            return None
        except requests.exceptions.ConnectionError as e:
            logger.log(obs.LOG_WARNING, f"🔌 [HTML] connection error: {e}")
            self.consecutive_failures += 1
            return None
        except requests.exceptions.HTTPError as e:
            logger.log(obs.LOG_WARNING, f"🌐 [HTML] HTTP error: {e}")
            self.consecutive_failures += 1
            return None
        except Exception as e:
            logger.log(obs.LOG_WARNING, f"⚠️ [HTML] unexpected: {e}")
            self.consecutive_failures += 1
            return None

    def _create_ssl_context(self):
        return ssl.create_default_context()

    def _api_search_handle(self, handle):
        if not self.api_key:
            return None
        try:
            self.api_call_count += 1
            self.total_quota_used += 100
            q = urllib.parse.urlencode({
                "part": "snippet",
                "q": handle,
                "type": "channel",
                "key": self.api_key,
                "maxResults": 1
            })
            logger.log(obs.LOG_INFO, f"🌐 [API] resolve handle -> channelId: @{handle}")
            ctx = self._create_ssl_context()
            t0 = time.time()
            with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{q}", timeout=20, context=ctx) as r:
                data = json.load(r)
            elapsed = time.time() - t0
            items = data.get("items", [])
            logger.log(obs.LOG_INFO, f"🌐 [API] search channels: items={len(items)}, elapsed={elapsed:.2f}s, quota+=100")
            if items:
                item = items[0]
                channel_id = None
                if 'snippet' in item and 'channelId' in item['snippet']:
                    channel_id = item['snippet']['channelId']
                elif 'id' in item and 'channelId' in item['id']:
                    channel_id = item['id']['channelId']
                if channel_id:
                    logger.log(obs.LOG_INFO, f"🔍 [API] channelId found: {channel_id}")
                    time.sleep(1)
                    return channel_id
            time.sleep(1)
            logger.log(obs.LOG_INFO, "ℹ️ [API] channelId not found by handle")
            return None
        except Exception as e:
            logger.log(obs.LOG_ERROR, f"❌ [API] handle resolve error: {e}")
            time.sleep(1)
            return None

    def get_video_id_api(self, channel_input):
        try:
            t, clean = self.normalize_channel_input(channel_input)
            if not clean:
                logger.log(obs.LOG_WARNING, "🌐 [API] invalid channel input (empty)")
                return None

            if t == 'channel_id':
                channel_id = clean
                logger.log(obs.LOG_INFO, f"🌐 [API] using channelId directly: {channel_id}")
            else:
                channel_id = self._api_search_handle(clean)
                if not channel_id:
                    logger.log(obs.LOG_INFO, "ℹ️ [API] skip live search (channelId unresolved)")
                    return None

            self.api_call_count += 1
            self.total_quota_used += 100
            logger.log(obs.LOG_INFO, f"🌐 [API] search live videos by channel: {channel_id}")
            q1 = urllib.parse.urlencode({
                "part": "id",
                "channelId": channel_id,
                "eventType": "live",
                "type": "video",
                "key": self.api_key,
                "maxResults": 1
            })
            ctx = self._create_ssl_context()
            t0 = time.time()
            with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/search?{q1}", timeout=20, context=ctx) as r:
                data = json.load(r)
            elapsed = time.time() - t0
            items = data.get("items", [])
            logger.log(obs.LOG_INFO, f"🌐 [API] live search: items={len(items)}, elapsed={elapsed:.2f}s, quota+=100")

            if items:
                video_id = items[0]["id"]["videoId"]
                self.api_call_count += 1
                self.total_quota_used += 100
                logger.log(obs.LOG_INFO, f"🌐 [API] fetch liveStreamingDetails: videoId={video_id}")
                q2 = urllib.parse.urlencode({"part": "liveStreamingDetails", "id": video_id, "key": self.api_key})
                t1 = time.time()
                with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?{q2}", timeout=20, context=ctx) as r2:
                    d2 = json.load(r2)
                elapsed2 = time.time() - t1
                items2 = d2.get("items", [])
                logger.log(obs.LOG_INFO, f"🌐 [API] details: items={len(items2)}, elapsed={elapsed2:.2f}s, quota+=100")
                if items2:
                    details = items2[0].get("liveStreamingDetails", {})
                    has_chat = bool(details.get("activeLiveChatId"))
                    logger.log(obs.LOG_INFO, f"🔎 [API] activeLiveChatId={has_chat}")
                    if has_chat:
                        logger.log(obs.LOG_INFO, f"🔵 [API] videoId: {video_id}")
                        return video_id
            logger.log(obs.LOG_INFO, "ℹ️ [API] no live video found or missing activeLiveChatId")
            return None
        except Exception as e:
            logger.log(obs.LOG_ERROR, f"❌ [API] error: {e}")
            time.sleep(1)
            return None

class BrowserSourceManager:
    def __init__(self, source_name):
        self.source_name = source_name
        self._refresh_in_progress = False
        self._lock = threading.Lock()
        self.next_refresh_action = 0

    def _get_src(self):
        return obs.obs_get_source_by_name(self.source_name)

    def _set_setting_bool(self, src, key, value):
        settings = obs.obs_source_get_settings(src)
        try:
            obs.obs_data_set_bool(settings, key, value)
            obs.obs_source_update(src, settings)
        finally:
            obs.obs_data_release(settings)

    def _set_setting_string(self, src, key, value):
        settings = obs.obs_source_get_settings(src)
        try:
            obs.obs_data_set_string(settings, key, value)
            obs.obs_source_update(src, settings)
        finally:
            obs.obs_data_release(settings)

    def apply_url_to_source_main(self, url):
        if not self.source_name or not url:
            return False
        src = self._get_src()
        if not src:
            logger.log(obs.LOG_WARNING, f"⚠️ [OBS] source not found: {self.source_name}")
            return False
        try:
            self._set_setting_string(src, "url", url)
            logger.log(obs.LOG_INFO, f"✅ [OBS] url applied: {url}")
            return True
        finally:
            obs.obs_source_release(src)

    def refresh_main(self, expected_url=None):
        with self._lock:
            if self._refresh_in_progress:
                logger.log(obs.LOG_INFO, "⏳ [REFRESH] skip (in-progress)")
                return
            self._refresh_in_progress = True

        def finish():
            with self._lock:
                self._refresh_in_progress = False
            logger.log(obs.LOG_INFO, "♻️ [REFRESH] complete")

        src = self._get_src()
        if not src:
            logger.log(obs.LOG_WARNING, f"⚠️ [REFRESH] source missing: {self.source_name}")
            finish()
            return

        try:
            if expected_url:
                settings = obs.obs_source_get_settings(src)
                try:
                    current_url = obs.obs_data_get_string(settings, "url")
                finally:
                    obs.obs_data_release(settings)
                if current_url != expected_url:
                    logger.log(obs.LOG_INFO, f"🔧 [REFRESH] fix url: {current_url} -> {expected_url}")
                    self._set_setting_string(src, "url", expected_url)

            action = self.next_refresh_action or 0
            self.next_refresh_action = 0

            if action == 0:
                def step1():
                    s = self._get_src()
                    if not s:
                        finish()
                        return
                    try:
                        self._set_setting_bool(s, "refresh_cache", True)
                        logger.log(obs.LOG_INFO, "🔄 [REFRESH] cache=true")
                    finally:
                        obs.obs_source_release(s)
                    def step2():
                        s2 = self._get_src()
                        if not s2:
                            finish()
                            return
                        try:
                            self._set_setting_bool(s2, "refresh_cache", False)
                            logger.log(obs.LOG_INFO, "🔄 [REFRESH] cache=false")
                        finally:
                            obs.obs_source_release(s2)
                        finish()
                    _dispatcher.post(step2, delay_ms=80, label="refresh_cache:off")
                _dispatcher.post(step1, label="refresh_cache:on")

            else:
                def step1():
                    s = self._get_src()
                    if not s:
                        finish()
                        return
                    try:
                        self._set_setting_bool(s, "restart_when_active", False)
                        logger.log(obs.LOG_INFO, "🧨 [REFRESH] restart_when_active=false")
                    finally:
                        obs.obs_source_release(s)
                    def step2():
                        s2 = self._get_src()
                        if not s2:
                            finish()
                            return
                        try:
                            self._set_setting_bool(s2, "restart_when_active", True)
                            logger.log(obs.LOG_INFO, "🧨 [REFRESH] restart_when_active=true")
                        finally:
                            obs.obs_source_release(s2)
                        finish()
                    _dispatcher.post(step2, delay_ms=200, label="reload:restart=true")
                _dispatcher.post(step1, label="reload:restart=false")

        finally:
            obs.obs_source_release(src)

class LogManager:
    def __init__(self, write_log_path, read_log_path, computer_name):
        self.write_log_path = write_log_path or ""
        self.read_log_path = read_log_path or ""
        self.computer_name = computer_name or "PC"
        self._last_mtime = None
        self._lock = threading.Lock()

    def write_share(self, video_id, popout_chat_url):
        if not self.write_log_path:
            return
        now_iso = datetime.now().isoformat()
        share_link = f"https://youtube.com/live/{video_id}?feature=share"
        entry = {
            "timestamp": now_iso,
            "videoId": video_id,
            "shareLink": share_link,
            "popoutChatUrl": popout_chat_url,
            "sourceComputer": self.computer_name
        }

        if self.write_log_path.lower().endswith('.jsonl'):
            path = self.write_log_path
        else:
            os.makedirs(self.write_log_path, exist_ok=True)
            path = os.path.join(self.write_log_path, f"{self.computer_name}.jsonl")

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.log(obs.LOG_INFO, "📝 [LOG] share link written")
        except Exception as e:
            logger.log(obs.LOG_ERROR, f"❌ [LOG] write error: {e}")

    def _find_remote_log_file(self):
        if not self.read_log_path:
            return None
        if self.read_log_path.lower().endswith('.jsonl'):
            if os.path.exists(self.read_log_path):
                return self.read_log_path
            return None
        try:
            for fn in os.listdir(self.read_log_path):
                if not fn.lower().endswith('.jsonl'):
                    continue
                if fn == f"{self.computer_name}.jsonl":
                    continue
                return os.path.join(self.read_log_path, fn)
            return None
        except Exception as e:
            logger.log(obs.LOG_WARNING, f"⚠️ [REMOTE] read dir error: {e}")
            return None

    def fetch_latest_share(self):
        try:
            path = self._find_remote_log_file()
            if not path or not os.path.exists(path):
                return None
            current_mtime = os.path.getmtime(path)
            if self._last_mtime is not None and current_mtime <= self._last_mtime:
                return None
            self._last_mtime = current_mtime

            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return None
            for i in range(len(lines)-1, -1, -1):
                line = lines[i].strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    share = d.get("shareLink", "")
                    if SHARE_LINK_PATTERN.match(share):
                        logger.log(obs.LOG_INFO, "📨 [REMOTE] new share link")
                        return share
                except Exception:
                    continue
            return None
        except Exception as e:
            logger.log(obs.LOG_WARNING, f"⚠️ [REMOTE] read error: {e}")
            return None

class LiveChatManager:
    def __init__(self):
        self.api_key = ""
        self.channel_input = ""
        self.browser_source_name = ""
        self.computer_name = ""
        self.write_log_path = ""
        self.read_log_path = ""
        self.base_init_interval = DEFAULT_BASE_INIT_INTERVAL
        self.refresh_cooldown = DEFAULT_REFRESH_COOLDOWN
        self.max_init_attempts = DEFAULT_MAX_INIT_ATTEMPTS
        self.max_init_interval = DEFAULT_MAX_INIT_INTERVAL
        self.update_interval = DEFAULT_UPDATE_INTERVAL

        self.yt_service = YouTubeService(api_key=None)
        self.browser_mgr = BrowserSourceManager(source_name="")
        self.log_mgr = LogManager("", "", "")

        self._video_id = None
        self._popout_url = None
        self._pending_video_id = None
        self._video_lock = threading.Lock()

        self._inited = False
        self._streaming_active = False

        self._shutdown_event = threading.Event()

        self._init_worker_thread = None
        self._init_stop_event = threading.Event()

        self._monitor_timer_active = False
        self._update_timer_active = False
        self._refresh_timer_active = False

        self._monitor_timer_fn = self._monitor_callback
        self._update_timer_fn = self._update_callback
        self._refresh_timer_fn = self._refresh_callback

        self._update_lock = threading.Lock()
        self._update_request_in_progress = False

    def update_config(self, settings):
        self.api_key = obs.obs_data_get_string(settings, "api_key") or ""
        self.channel_input = obs.obs_data_get_string(settings, "channel_input") or ""
        self.browser_source_name = obs.obs_data_get_string(settings, "browser_source") or ""
        self.computer_name = obs.obs_data_get_string(settings, "computer_name") or "PC1"
        self.write_log_path = obs.obs_data_get_string(settings, "write_log_path") or ""
        self.read_log_path = obs.obs_data_get_string(settings, "read_log_path") or ""
        self.base_init_interval = obs.obs_data_get_int(settings, "base_init_interval") or DEFAULT_BASE_INIT_INTERVAL
        self.refresh_cooldown = obs.obs_data_get_int(settings, "refresh_cooldown") or DEFAULT_REFRESH_COOLDOWN
        self.max_init_attempts = obs.obs_data_get_int(settings, "max_init_attempts") or DEFAULT_MAX_INIT_ATTEMPTS
        self.max_init_interval = obs.obs_data_get_int(settings, "max_init_interval") or DEFAULT_MAX_INIT_INTERVAL
        self.update_interval = obs.obs_data_get_int(settings, "update_interval") or DEFAULT_UPDATE_INTERVAL

        self.yt_service = YouTubeService(api_key=self.api_key if self.api_key else None)
        self.browser_mgr = BrowserSourceManager(source_name=self.browser_source_name)
        self.log_mgr = LogManager(self.write_log_path, self.read_log_path, self.computer_name)

    def get_current_video_id(self):
        with self._video_lock:
            return self._pending_video_id or self._video_id

    def set_primary_video_id(self, video_id):
        with self._video_lock:
            self._video_id = video_id
            if video_id:
                self._popout_url = f"https://www.youtube.com/live_chat?is_popout=1&v={video_id}"

    def set_pending_video_id(self, video_id):
        with self._video_lock:
            self._pending_video_id = video_id

    def apply_pending_video_id(self):
        with self._video_lock:
            if not self._pending_video_id or self._pending_video_id == self._video_id:
                return False
            old = self._video_id
            self._video_id = self._pending_video_id
            self._popout_url = f"https://www.youtube.com/live_chat?is_popout=1&v={self._video_id}"
            self._pending_video_id = None
            popout_url = self._popout_url
            vid = self._video_id

        def do_apply():
            ok = self.browser_mgr.apply_url_to_source_main(popout_url)
            if ok:
                logger.log(obs.LOG_INFO, f"🔄 [UPDATE] videoId applied: {old} -> {vid}")
        _dispatcher.post(do_apply, label="apply_url_to_source")

        threading.Thread(target=lambda: self.log_mgr.write_share(vid, popout_url),
                         daemon=True, name="ShareWriteWorker").start()
        return True

    def calculate_dynamic_interval(self, base_interval, failures, max_interval):
        if failures == 0:
            return base_interval
        interval = base_interval * (1.5 ** min(failures, 5))
        return min(interval, max_interval)

    def _init_worker_main(self):
        logger.log(obs.LOG_INFO, "🧵 [WORKER] InitWorker started")
        attempt_count = 0
        consecutive_failures = 0

        while not self._init_stop_event.is_set() and self._streaming_active and not self._shutdown_event.is_set():
            attempt_count += 1
            if attempt_count > self.max_init_attempts:
                logger.log(obs.LOG_ERROR, f"❌ [INIT] max attempts reached: {self.max_init_attempts}")
                break

            current_interval = self.calculate_dynamic_interval(
                self.base_init_interval, consecutive_failures, self.max_init_interval
            )

            logger.log(obs.LOG_INFO, f"🚀 [INIT] attempt {attempt_count}/{self.max_init_attempts} "
                                     f"(failures={consecutive_failures}, next={current_interval}s)")

            start_time = time.time()
            video_id = None

            try:
                logger.log(obs.LOG_INFO, "🔎 [INIT/HTML] probing streams page for live videoId...")
                video_id = self.yt_service.get_video_id_html(self.channel_input, timeout=23)
            except Exception as e:
                logger.log(obs.LOG_ERROR, f"❌ [INIT] HTML unexpected: {e}")

            if not video_id and self.api_key and not self._shutdown_event.is_set():
                try:
                    logger.log(obs.LOG_INFO, "🔁 [INIT/API] HTML failed -> trying API fallback")
                    video_id = self.yt_service.get_video_id_api(self.channel_input)
                except Exception as e:
                    logger.log(obs.LOG_ERROR, f"❌ [INIT] API unexpected: {e}")

            if self._shutdown_event.is_set() or not self._streaming_active:
                break

            if video_id:
                self.set_primary_video_id(video_id)
                popout_url = f"https://www.youtube.com/live_chat?is_popout=1&v={video_id}"

                _dispatcher.post(
                    lambda: self.browser_mgr.apply_url_to_source_main(popout_url),
                    label="init:apply_url"
                )
                logger.log(obs.LOG_INFO, f"🟢 [INIT] chat prepared: {video_id}")

                threading.Thread(target=lambda: self.log_mgr.write_share(video_id, popout_url),
                                 daemon=True, name="ShareWriteWorker").start()

                self._inited = True
                logger.log(obs.LOG_INFO, f"🏁 [INIT] success! quota={self.yt_service.total_quota_used}")

                _dispatcher.post(self._start_monitor_timer_main, delay_ms=500, label="start_monitor_timer")
                _dispatcher.post(self._start_refresh_timer_main, delay_ms=900, label="start_refresh_timer")
                _dispatcher.post(self._start_update_timer_main, delay_ms=1300, label="start_update_timer")
                break

            consecutive_failures += 1
            elapsed = time.time() - start_time
            wait_time = max(current_interval - elapsed, 0.5)
            logger.log(obs.LOG_INFO, f"⏭️ [INIT] attempt {attempt_count} failed, retry in {wait_time:.1f}s")
            if self._init_stop_event.wait(timeout=wait_time) or self._shutdown_event.is_set() or not self._streaming_active:
                break

        logger.log(obs.LOG_INFO, "🏁 [INIT] worker finished")

    def _start_init_worker(self):
        if self._init_worker_thread and self._init_worker_thread.is_alive():
            return
        self._init_stop_event.clear()
        self._init_worker_thread = threading.Thread(
            target=self._init_worker_main, daemon=True, name="InitWorker"
        )
        logger.log(obs.LOG_INFO, "🧵 [WORKER] request start InitWorker")
        self._init_worker_thread.start()

    def _stop_init_worker(self):
        if self._init_worker_thread:
            self._init_stop_event.set()
            if self._init_worker_thread.is_alive():
                self._init_worker_thread.join(timeout=2)
            logger.log(obs.LOG_INFO, "🧵 [WORKER] InitWorker stopped")

    def _monitor_callback(self):
        if not self._streaming_active or self._shutdown_event.is_set():
            return
        logger.log(obs.LOG_INFO, "⏱️ [CALLBACK] monitor fired")
        def worker():
            threading.current_thread().name = "MonitorWorker"
            try:
                if self._shutdown_event.is_set() or not self._streaming_active:
                    return
                _ = self.apply_pending_video_id()
                if self._shutdown_event.is_set() or not self._streaming_active:
                    return
                latest = self.log_mgr.fetch_latest_share()
                if latest:
                    self.post_share_link_to_chat(latest)
            except Exception as e:
                logger.log(obs.LOG_WARNING, f"⚠️ [CALLBACK] monitor error: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _update_callback(self):
        with self._update_lock:
            if not self._streaming_active or not self._inited or self._shutdown_event.is_set():
                return
            if self._update_request_in_progress:
                logger.log(obs.LOG_INFO, "⏭️ [UPDATE] skip (in-progress)")
                return
            self._update_request_in_progress = True

        logger.log(obs.LOG_INFO, "⏱️ [CALLBACK] update fired")

        def worker():
            threading.current_thread().name = "UpdateWorker"
            try:
                if self._shutdown_event.is_set() or not self._streaming_active:
                    return
                streams_url = self.yt_service.build_streams_url(self.channel_input)
                curr = self.get_current_video_id()
                logger.log(obs.LOG_INFO, f"🔎 [UPDATE/HTML] probing: url={streams_url}, current={curr}")
                t1 = time.time()
                new_video_id = self.yt_service.get_video_id_html(self.channel_input, timeout=23)
                html_elapsed = time.time() - t1
                if self._shutdown_event.is_set() or not self._streaming_active:
                    return
                logger.log(obs.LOG_INFO, f"ℹ️ [UPDATE/HTML] probe done in {html_elapsed:.2f}s")
                if new_video_id:
                    if new_video_id != curr:
                        logger.log(obs.LOG_INFO, f"🟢 [UPDATE/HTML] got new videoId: {new_video_id} (pending apply)")
                        self.set_pending_video_id(new_video_id)
                        self.browser_mgr.next_refresh_action = 0
                    else:
                        logger.log(obs.LOG_INFO, "ℹ️ [UPDATE/HTML] same videoId, no change")
                        self.browser_mgr.next_refresh_action = 0
                else:
                    logger.log(obs.LOG_INFO, "ℹ️ [UPDATE/HTML] no live videoId")
                    self.browser_mgr.next_refresh_action = 1
            except Exception as e:
                logger.log(obs.LOG_WARNING, f"⚠️ [UPDATE] worker error: {e}")
            finally:
                with self._update_lock:
                    self._update_request_in_progress = False

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_callback(self):
        if self._shutdown_event.is_set() or not self._inited or not self._streaming_active:
            return
        logger.log(obs.LOG_INFO, "⏱️ [CALLBACK] refresh fired")
        current_id = self.get_current_video_id()
        if not current_id:
            return
        expected = f"https://www.youtube.com/live_chat?is_popout=1&v={current_id}"
        self.browser_mgr.refresh_main(expected_url=expected)

    def _start_monitor_timer(self):
        logger.log(obs.LOG_INFO, "🕒 [TIMER] request start_monitor")
        _dispatcher.post(self._start_monitor_timer_main, label="start_monitor_timer")

    def _start_monitor_timer_main(self):
        if not self._monitor_timer_active:
            obs.timer_add(self._monitor_timer_fn, int(self.refresh_cooldown * 1000))
            self._monitor_timer_active = True
            logger.log(obs.LOG_INFO, f"🕒 [TIMER] monitor added ({int(self.refresh_cooldown)}s)")

    def _stop_monitor_timer_main(self):
        if self._monitor_timer_active:
            try:
                obs.timer_remove(self._monitor_timer_fn)
            except Exception as e:
                logger.log(obs.LOG_WARNING, f"⚠️ [TIMER] monitor remove error: {e}")
            self._monitor_timer_active = False
            logger.log(obs.LOG_INFO, "🕒 [TIMER] monitor removed")

    def _start_update_timer(self):
        logger.log(obs.LOG_INFO, "🕒 [TIMER] request start_update")
        _dispatcher.post(self._start_update_timer_main, label="start_update_timer")

    def _start_update_timer_main(self):
        if not self._update_timer_active:
            obs.timer_add(self._update_timer_fn, int(self.update_interval * 1000))
            self._update_timer_active = True
            logger.log(obs.LOG_INFO, f"🕒 [TIMER] update added ({int(self.update_interval)}s)")

    def _stop_update_timer_main(self):
        if self._update_timer_active:
            try:
                obs.timer_remove(self._update_timer_fn)
            except Exception as e:
                logger.log(obs.LOG_WARNING, f"⚠️ [TIMER] update remove error: {e}")
            self._update_timer_active = False
            logger.log(obs.LOG_INFO, "🕒 [TIMER] update removed")

    def _start_refresh_timer(self):
        logger.log(obs.LOG_INFO, "🕒 [TIMER] request start_refresh")
        _dispatcher.post(self._start_refresh_timer_main, label="start_refresh_timer")

    def _start_refresh_timer_main(self):
        if not self._refresh_timer_active:
            obs.timer_add(self._refresh_timer_fn, 10000)
            self._refresh_timer_active = True
            logger.log(obs.LOG_INFO, "🕒 [TIMER] refresh added (10s)")

    def _stop_refresh_timer_main(self):
        if self._refresh_timer_active:
            try:
                obs.timer_remove(self._refresh_timer_fn)
            except Exception as e:
                logger.log(obs.LOG_WARNING, f"⚠️ [TIMER] refresh remove error: {e}")
            self._refresh_timer_active = False
            logger.log(obs.LOG_INFO, "🕒 [TIMER] refresh removed")

    def post_share_link_to_chat(self, link):
        if not hasattr(self, "_last_posted_link"):
            self._last_posted_link = None
        if link == self._last_posted_link:
            return
        if not SHARE_LINK_PATTERN.match(link):
            return
        try:
            logger.log(obs.LOG_INFO, f"📤 [POST] ready: {link}")
            self._last_posted_link = link
        except Exception as e:
            logger.log(obs.LOG_ERROR, f"❌ [POST] error: {e}")

    def on_stream_started(self):
        if self._streaming_active:
            return
        _dispatcher.start()
        self._shutdown_event.clear()
        self._streaming_active = True
        self._reset_state()
        logger.log(obs.LOG_INFO, "🎬 [EVENT] streaming started")
        self._start_init_worker()

    def on_stream_stopped(self):
        if not self._streaming_active:
            self._shutdown_event.set()
            self._stop_all_main()
            _dispatcher.stop()
            logger.log(obs.LOG_INFO, f"🛑 [EVENT] streaming stopped (forced), quota={self.yt_service.total_quota_used}")
            return
        self._streaming_active = False
        self._shutdown_event.set()
        self._stop_all_main()
        _dispatcher.stop()
        logger.log(obs.LOG_INFO, f"🛑 [EVENT] streaming stopped, quota={self.yt_service.total_quota_used}")

    def _reset_state(self):
        with self._video_lock:
            self._video_id = None
            self._popout_url = None
            self._pending_video_id = None
        self._inited = False
        with self._update_lock:
            self._update_request_in_progress = False
        self._last_posted_link = None
        self._stop_all_main()

    def _stop_all_main(self):
        self._stop_init_worker()
        self._stop_monitor_timer_main()
        self._stop_update_timer_main()
        self._stop_refresh_timer_main()

    def _stop_all(self):
        self._stop_all_main()

_manager = LiveChatManager()
_current_settings = None

def script_description():
    return "YouTube Live Chat Manager - Stream detection, cross-device sharing, browser source auto-refresh (sequential init, main-thread timers/OBS ops)"

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
    obs.obs_properties_add_int(p, "update_interval", "Video ID Update Interval (sec)", 10, 300, 5)
    return p

def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "base_init_interval", DEFAULT_BASE_INIT_INTERVAL)
    obs.obs_data_set_default_int(settings, "refresh_cooldown", DEFAULT_REFRESH_COOLDOWN)
    obs.obs_data_set_default_int(settings, "max_init_attempts", DEFAULT_MAX_INIT_ATTEMPTS)
    obs.obs_data_set_default_int(settings, "max_init_interval", DEFAULT_MAX_INIT_INTERVAL)
    obs.obs_data_set_default_int(settings, "update_interval", DEFAULT_UPDATE_INTERVAL)
    obs.obs_data_set_default_string(settings, "computer_name", "PC1")

def script_update(settings):
    global _manager, _current_settings
    _current_settings = settings
    _manager.update_config(settings)

def script_load(settings):
    global _current_settings, _manager
    _current_settings = settings
    obs.obs_frontend_add_event_callback(on_frontend_event)
    logger.log(obs.LOG_INFO, "🚀 [LOAD] script loaded")

    _dispatcher.start()

    _manager.update_config(settings)

    try:
        if obs.obs_frontend_streaming_active():
            _manager.on_stream_started()
    except Exception as e:
        logger.log(obs.LOG_WARNING, f"⚠️ [LOAD] check streaming error: {e}")

def script_unload():
    global _manager
    try:
        obs.obs_frontend_remove_event_callback(on_frontend_event)
    except Exception as e:
        logger.log(obs.LOG_WARNING, f"⚠️ [UNLOAD] remove event cb error: {e}")

    logger.log(obs.LOG_INFO, f"👋 [UNLOAD] unloading, quota={_manager.yt_service.total_quota_used}")

    _manager.on_stream_stopped()

def on_frontend_event(event):
    try:
        if event == obs.OBS_FRONTEND_EVENT_STREAMING_STARTED:
            _manager.on_stream_started()
        elif event in (obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED, obs.OBS_FRONTEND_EVENT_EXIT):
            _manager.on_stream_stopped()
    except Exception as e:
        logger.log(obs.LOG_ERROR, f"❌ [EVENT] frontend error {event}: {e}")

def script_save(settings):
    pass

def force_refresh_now():
    current_id = _manager.get_current_video_id()
    if current_id:
        expected_url = f"https://www.youtube.com/live_chat?is_popout=1&v={current_id}"
        _dispatcher.post(lambda: _manager.browser_mgr.refresh_main(expected_url=expected_url),
                         label="debug:refresh_now")
        logger.log(obs.LOG_INFO, "🔧 [DEBUG] refresh queued")
    else:
        logger.log(obs.LOG_WARNING, "⚠️ [DEBUG] no videoId to refresh")