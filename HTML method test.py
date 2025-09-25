import re
import requests
import time

def normalize_channel_input(channel_input):
    """标准化频道输入格式"""
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
    """构建频道直播页面URL"""
    channel_type, clean_input = normalize_channel_input(channel_input)

    if not channel_type or not clean_input:
        return None

    if channel_type == 'handle':
        return f"https://www.youtube.com/@{clean_input}/streams"
    elif channel_type == 'channel_id':
        return f"https://www.youtube.com/channel/{clean_input}/streams"

    return None

def get_video_id_html(channel_input, timeout=30):
    """从HTML页面提取视频ID - 最小测试用例"""
    try:
        streams_url = build_channel_streams_url(channel_input)
        if not streams_url:
            print(f"❌ 无法构建URL: {channel_input}")
            return None

        print(f"🔍 访问URL: {streams_url}")

        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })

        response = session.get(streams_url, timeout=timeout, verify=True)
        response.raise_for_status()
        
        html_content = response.text
        print(f"✅ 成功获取HTML，长度: {len(html_content)} 字符")

        # 第一个模式：videoRenderer
        pattern1 = r'"videoRenderer":\{"videoId":"[^"]+"'
        result = re.search(pattern1, html_content)

        if result is None:
            # 第二个模式：gridVideoRenderer
            pattern2 = r'"gridVideoRenderer":\{"videoId":"[^"]+"'
            result = re.search(pattern2, html_content)
            print("🔄 尝试第二个模式")

        if result is not None:
            matched_string = result.group()
            print(f"🎯 匹配到: {matched_string}")
            
            # 提取视频ID
            video_id_pattern = r':"([^"]+)"'
            video_id_match = re.search(video_id_pattern, matched_string)

            if video_id_match:
                video_id = video_id_match.group(1)
                print(f"✅ 提取到视频ID: {video_id}")
                return video_id

        print("❌ 未找到视频ID模式")
        return None

    except requests.exceptions.Timeout:
        print(f"⏰ 请求超时 ({timeout}秒)")
        return None
    except requests.exceptions.ConnectionError:
        print("🔌 连接错误")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"🌐 HTTP错误: {e}")
        return None
    except Exception as e:
        print(f"❌ 未知错误: {e}")
        return None

# 测试用例
if __name__ == "__main__":
    # 测试不同的输入格式
    test_cases = [
        "@xczphysics",           # 频道handle
        "xczphysics",            # 不带@的频道名
        "https://youtube.com/@xczphysics",  # 完整URL
        # "UCxxxxxxxxxxxxxxxxxx",   # 频道ID
        # "https://youtube.com/channel/UCxxxxxxxxxxxxxxxxxx",  # 频道ID URL
    ]
    
    print("🧪 开始HTML视频ID提取测试")
    print("=" * 50)
    
    for i, test_input in enumerate(test_cases, 1):
        print(f"\n📋 测试 {i}: {test_input}")
        print("-" * 30)
        
        start_time = time.time()
        video_id = get_video_id_html(test_input, timeout=15)
        end_time = time.time()
        
        if video_id:
            print(f"🎉 成功! 视频ID: {video_id}")
            print(f"⏱️  耗时: {end_time - start_time:.2f}秒")
        else:
            print("💔 失败")
        
        print("-" * 30)
        
        # 避免请求过于频繁
        if i < len(test_cases):
            time.sleep(2)
    
    print("\n🏁 测试完成")
