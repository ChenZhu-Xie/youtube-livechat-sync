# OBS YouTube Live Chat Sync

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

- **🚀 Automatic Chat URL Updates**: Automatically fetches and updates YouTube Live Chat URLs in your OBS Browser Source when streaming starts
- **🔗 Cross-Device Stream Sharing**: Share live stream links between multiple computers automatically (with [verysync](https://www.verysync.com/))
- **⚡ Real-time Monitoring**: Monitors and corrects manually modified browser source URLs
- **🛡️ API Quota Protection**: Built-in rate limiting and exponential backoff to protect YouTube API quota
- **📝 JSON Logging**: Structured logging system for stream data [synchronization](https://www.verysync.com/)
- **⏰ Timestamped Logs**: Detailed logging with automatic timestamps for debugging

## How It Works

1. **Stream Start** → Automatically fetches your YouTube Live `videoId` and updates local browser source URL
2. **Cross-Device [Sync](https://www.verysync.com/)** → Writes stream share links to JSON log files for other devices to read
3. **Real-time Monitoring** → Continuously monitors and [syncs](https://www.verysync.com/) URLs between devices
4. **Stream End** → Automatically cleans up all background tasks

## Installation

1. Download the script file `youtube_livechat_sync.py`
2. In OBS Studio, go to **Tools** → **Scripts**
3. Click the **+** button and select the downloaded script
4. Configure the required settings (see Configuration section)

## Configuration

### Required Settings

| Setting | Description | Example |
|---------|-------------|---------|
| **API Key** | YouTube Data API v3 key | `AIza...` |
| **Channel ID** | Your YouTube channel ID | `UC...` |
| **Browser Source** | Name of your OBS browser source | `YouTube Chat` |
| **Computer Name** | Identifier for this computer | `StreamPC1` |
| **Log Write Path** | Where this PC writes its logs | `C:\StreamLogs\` |
| **Log Read Path** | Where to read other PC's logs | `\\OtherPC\SharedLogs\` |

<img width="1557" height="1182" alt="image" src="https://github.com/user-attachments/assets/8999367c-9581-4f27-b81f-82b887218936" />
<img width="1547" height="212" alt="image" src="https://github.com/user-attachments/assets/bf3fbfb8-8e2e-4ef3-b6b3-6cf40250bbce" />

### Optional Settings

| Setting | Default | Description |
|---------|---------|-------------|
| **Init Retry Interval** | 30s | Time between initialization attempts |
| **Monitor Interval** | 5s | URL verification and [sync](https://www.verysync.com/) frequency |
| **Max Init Attempts** | 3 | Maximum initialization retry count |

## YouTube API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable **YouTube Data API v3**
4. Create credentials (API Key)
5. Copy the API key to the script configuration

## Multi-PC Setup

For cross-device streaming setups:

1. **PC 1 (Main)**: Set `Log Write Path` to a shared folder
2. **PC 2 (Secondary)**: Set `Log Read Path` to the same shared folder
3. Both PCs will automatically [sync](https://www.verysync.com/) stream links

### Example File Structure
```
\SharedFolder\StreamLogs
├── MainPC.jsonl      # Written by main streaming PC
└── SecondaryPC.jsonl # Written by secondary PC
```

<img width="1077" height="513" alt="image" src="https://github.com/user-attachments/assets/08cfe469-ca60-4800-a576-e5ca3c0454e0" />

## API Quota Management

The script includes built-in protection against YouTube API quota exhaustion:

- **Rate Limiting**: Maximum 6 API calls per minute
- **Exponential Backoff**: 30s → 45s → 67.5s retry intervals
- **Attempt Limits**: Maximum 3 initialization attempts
- **Quota Tracking**: Real-time monitoring of API usage

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

**❌ "未找到直播视频"**
- Ensure your stream is actually live on YouTube
- Check that your Channel ID is correct
- Verify API key has proper permissions

**❌ "Live Chat 未就绪"**
- Wait a few moments after starting stream
- Ensure chat is enabled for your stream
- Check stream settings in YouTube Studio

**❌ "API调用频率过高"**
- Script automatically handles rate limiting
- Reduce monitor interval if needed
- Check for other applications using the same API key

<img width="1659" height="513" alt="image" src="https://github.com/user-attachments/assets/4abaadb8-e085-4fb8-8ecc-6ba52437e20b" />

### Debug Tips

1. Check OBS Script Log for detailed timestamps
2. Verify file permissions for log directories
3. Test API key with Google's API Explorer
4. Ensure network connectivity ([VerySync](https://www.verysync.com/)) between PCs

## Requirements

- **OBS Studio** 27.0+ with Python scripting support
- **YouTube Data API v3** key with quota available
- **Python** (bundled with OBS)
- **Network access** for cross-device setups

## Contributing

Contributions welcome! Please feel free to submit issues, feature requests, or pull requests.

## License

GPLv3.0 License - feel free to use and modify for your streaming needs.

## Disclaimer

This tool uses the YouTube Data API v3. Please be mindful of your API quota limits and YouTube's Terms of Service.

---

⭐ If this script helps your streaming setup, please give it a star!
