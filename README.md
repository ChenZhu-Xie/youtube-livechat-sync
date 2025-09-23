# YouTube Live Chat Sync

Automated YouTube Live Chat URL management and 🔄 cross-device stream link sharing for OBS Studio.

1. **Usage**:
   - *[Tutorial 1](https://youtu.be/xXMUXBz1P74)*, *[Tutorial 2](https://youtu.be/XyUmUvaArpQ)*
   - *[Quicker 1](https://getquicker.net/Sharedaction?code=61157382-208a-4708-33a6-08ddf80356fe)*, *[Quicker 2](https://getquicker.net/Sharedaction?code=54d4f5ea-45ff-4928-33a9-08ddf80356fe)*
2. **My everyday Dual-Channel live-streaming setup**:
   - *[@xczphysics](https://www.youtube.com/@xczphysics/streams)*
   - *[@尘竹-3-梦瑶](https://www.youtube.com/@尘竹-3-梦瑶/streams)*
3. **Other wonderful OBS stuff**:
   - *[youtube-live-chat-overlay](https://github.com/EuSouGuil/youtube-live-chat-overlay)*
4. **Products that functions similarly to mine**:
   - *[youtube-chat-browser-source-updater](https://github.com/jimpalompa/OBS-Studio-Plugins-Scripts-Themes)*
   - *[Twidget](https://youtu.be/GjlEzcKAVCI?si=PoIndk-xf2tETBG6)*

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Required Settings](#required-settings)
  - [Optional Settings](#optional-settings)
- [YouTube API Setup](#youtube-api-setup)
- [Multi-PC Setup](#multi-pc-setup)
- [API Quota Management](#api-quota-management)
- [Log Format](#log-format)
- [Troubleshooting](#troubleshooting)
  - [Common Issues](#common-issues)
  - [Debug Tips](#debug-tips)
- [Requirements](#requirements)
- [Contributing](#contributing)
- [License](#license)
- [Disclaimer](#disclaimer)

## Features

- **🚀 Automatic Chat URL Detection**
  - Primary HTML parsing method with API fallback for reliable video ID detection
- **🔗 Cross-Device Stream Sharing**
  - Share live stream links between multiple computers automatically (with [verysync](https://www.verysync.com/))
- **⚡ Real-time Monitoring**
  - Continuous browser source URL monitoring and correction with periodic video ID updates
- **🛡️ Enhanced API Protection**
  - Advanced quota management with exponential backoff, request rate limiting, and consecutive failure tracking
- **🔄 Auto Browser Refresh**
  - Automatic browser source cache refresh every 4 seconds to prevent chat display issues
- **📝 JSON Logging**
  - Structured logging system for stream data [synchronization](https://www.verysync.com/) with timestamped entries
- **🎯 Smart Channel Input**
  - Supports channel IDs, handles (@username), and full YouTube URLs with intelligent normalization
- **⚙️ Thread-Safe Operations**
  - Background processing with thread locks for reliable multi-timer coordination
- **🔧 Pending Video ID System**
  - Smooth video ID transitions with background updates to prevent chat interruption

## How It Works

1. **Stream Detection** → Automatically detects stream start/stop events and initializes chat monitoring
2. **Video ID Fetching** → Uses HTML parsing (primary) and YouTube API (fallback) to find live video IDs
3. **URL Management** → Updates OBS browser source with correct YouTube Studio chat popout URLs
4. **Cross-Device Sync** → Writes/reads JSON log files for automatic stream link sharing between computers
5. **Real-time Updates** → Periodic video ID updates and browser source refresh to maintain chat connectivity
6. **Smart Retry Logic** → Exponential backoff with failure tracking for robust error recovery
7. **Background Processing** → Thread-safe background video ID updates with pending system for seamless transitions

## Installation

1. Download the script file `youtube_livechat_sync.py`
2. In OBS Studio, go to **Tools** → **Scripts**
3. Click the **+** button and select the downloaded script
4. Configure the required settings (see Configuration section)

## Configuration

### Required Settings

| Setting | Description | Example |
|---------|-------------|---------|
| **YouTube API Key** | YouTube Data API v3 key (Optional - HTML parsing works without it) | `AIza...` |
| **Channel Handle/ID/URL** | YouTube channel identifier (multiple formats supported) | `@username` or `UC...` or full URL |
| **Browser Source Name** | Name of your OBS browser source for chat display | `YouTube Chat` |
| **Computer Identifier** | Unique name for this computer in multi-PC setups | `StreamPC1` |
| **Write Log File Path** | Directory or file path where this PC writes its stream logs | `C:\StreamLogs\PC1.jsonl` |
| **Read Log Directory Path** | Directory path to read other PC's stream logs | `\\OtherPC\SharedLogs\PC2.jsonl` |

<img width="1723" height="897" alt="image" src="https://github.com/user-attachments/assets/51aba7ad-cdda-48a1-a387-a1301e84c3cf" />
<img width="1350" height="209" alt="image" src="https://github.com/user-attachments/assets/6443cfe7-50b8-4cda-9d1d-b0b3609a9791" />

### Optional Settings

| Setting | Default | Description |
|---------|---------|-------------|
| **Base Init Interval (sec)** | 10 | Initial retry interval for stream detection |
| **Monitor & Refresh Interval (sec)** | 4 | Frequency of URL monitoring and browser refresh |
| **Maximum Init Attempts** | 3 | Maximum initialization retry attempts |
| **Max Init Retry Interval (sec)** | 30 | Maximum retry interval with exponential backoff |
| **Video ID Update Interval (sec)** | 30 | Frequency of background video ID updates |

## YouTube API Setup

**Note**: API key is now optional - the script primarily uses HTML parsing which doesn't require API quota.

For API fallback functionality:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable **YouTube Data API v3**
4. Create credentials (API Key)
5. Copy the API key to the script configuration

## Multi-PC Setup

For cross-device streaming setups:

1. **PC 1 (Main)**: Set `Write Log File Path` to a shared folder
2. **PC 2 (Secondary)**: Set `Read Log Directory Path` to the same shared folder
3. Both PCs will automatically sync stream links through JSON log files

### Example File Structure
```
\SharedFolder\StreamLogs\
├── MainPC.jsonl      # Written by main streaming PC
└── SecondaryPC.jsonl # Written by secondary PC
```

<img width="1056" height="608" alt="image" src="https://github.com/user-attachments/assets/fadb83fa-dc9e-4afd-97ad-4828d076077a" />

## API Quota Management

Enhanced quota protection system:
- **Primary HTML Parsing**: Reduces API dependency by 90%+ with intelligent page parsing
- **Request Rate Limiting**: Thread-safe request spacing with minimum 2-second intervals
- **Exponential Backoff**: Dynamic interval calculation (1.5x multiplier) up to 30s maximum
- **Failure Tracking**: Consecutive failure counting for intelligent retry logic
- **Quota Monitoring**: Real-time API usage tracking and logging with detailed statistics

## Log Format

Stream data is logged in JSON Lines format:
```json
{
  "timestamp": "2024-01-15T14:30:00.123456",
  "videoId": "dQw4w9WgXcQ",
  "shareLink": "https://youtube.com/live/dQw4w9WgXcQ?feature=share",
  "popoutChatUrl": "https://studio.youtube.com/live_chat?is_popout=1&v=dQw4w9WgXcQ",
  "sourceComputer": "StreamPC1"
}
```

<img width="2766" height="1812" alt="image" src="https://github.com/user-attachments/assets/8d5c3927-4262-4122-98d0-8af744f4dbe3" />

## Troubleshooting

### Common Issues

**❌ "HTML parsing failed, trying API fallback"**
- Network connectivity issues or YouTube page structure changes
- API fallback will automatically engage if available
- Check internet connection and firewall settings

**❌ "Both HTML and API methods failed"**
- Ensure your stream is actually live on YouTube
- Verify channel input format (handle, ID, or URL)
- Check that chat is enabled for your stream

**❌ "Browser source refresh failed"**
- Verify browser source name matches exactly in OBS
- Check that the browser source exists in current scene collection
- Ensure OBS has proper permissions for source updates

**❌ "Video ID update timeout"**
- YouTube servers may be slow or unresponsive
- Script will automatically retry with exponential backoff
- Background updates continue without interrupting current chat

**❌ "Cross-device sync not working"**
- Verify file permissions for log directories
- Check network connectivity between PCs
- Ensure shared folder paths are accessible

<img width="1614" height="691" alt="image" src="https://github.com/user-attachments/assets/f4e6af89-d6c7-436e-b3f4-42fc6451e732" />

### Debug Tips

1. **Monitor OBS Script Log**: Check timestamped log entries for detailed operation status
2. **Verify Channel Input**: Test different channel input formats (@handle, channel ID, full URL)
3. **Check File Permissions**: Ensure read/write access to log directories
4. **Network Connectivity**: Verify network access between PCs for cross-device sync
5. **Browser Source Settings**: Confirm browser source URL updates are working correctly
6. **API Usage Tracking**: Monitor quota usage in logs to optimize API calls

## Requirements

- **OBS Studio** 27.0+ with Python scripting support
- **Python Packages**: `requests` library (usually bundled with OBS)
- **YouTube Data API v3** key (optional - for API fallback only)
- **Network access** for cross-device setups and YouTube connectivity

## Contributing

Contributions welcome! Please feel free to submit issues, feature requests, or pull requests. Focus areas:
- YouTube page structure change handling
- Additional channel input format support
- Performance optimizations for large multi-PC setups
- Enhanced error recovery mechanisms

## License

GPLv3.0 License - feel free to use and modify for your streaming needs.

## Disclaimer

- This tool uses HTML parsing as primary method and YouTube Data API v3 as fallback. 
- Please be mindful of your API quota limits and YouTube's Terms of Service. 
- The HTML parsing method may be affected by YouTube page structure changes.

---

⭐ If this script helps your streaming setup, please give it a star!
