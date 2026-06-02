"""
Influencer Tracker - Standalone Data Collector
Reads videos from SQLite, fetches YouTube stats, writes results back.
Runs independently from Express server.
"""
import sys
import io
import json
import sqlite3
import time
import os
import urllib.request
import urllib.error
import urllib.parse

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tracker.db')
USER_AGENT = 'InfluencerTracker/1.0'


def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def https_get(url):
    """Fetch URL and return response body as string."""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        return body
    except Exception as e:
        raise


def extract_youtube_id(url):
    """Extract YouTube video ID from URL."""
    import re
    patterns = [
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/watch\?(?:.*&)?v=([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def fetch_youtube_stats(video_id, api_key):
    """Fetch video statistics from YouTube Data API v3."""
    url = f'https://www.googleapis.com/youtube/v3/videos?part=statistics,snippet&id={video_id}&key={api_key}'
    body = https_get(url)
    data = json.loads(body)
    if not data.get('items'):
        return None
    item = data['items'][0]
    snippet = item.get('snippet', {})
    stats = item.get('statistics', {})
    thumbnails = snippet.get('thumbnails', {})
    thumb = thumbnails.get('medium', {}).get('url', '') or \
            thumbnails.get('default', {}).get('url', '') or \
            thumbnails.get('high', {}).get('url', '')
    return {
        'title': snippet.get('title', ''),
        'channel': snippet.get('channelTitle', ''),
        'channel_id': snippet.get('channelId', ''),
        'thumbnail': thumb,
        'published_at': snippet.get('publishedAt', '')[:10] if snippet.get('publishedAt') else '',
        'views': int(stats.get('viewCount', 0)),
        'likes': int(stats.get('likeCount', 0)),
        'comments': int(stats.get('commentCount', 0)),
        'shares': 0,
        'saves': 0,
    }


def fetch_youtube_oembed(video_url):
    """Fetch video info from YouTube oEmbed (no API key needed)."""
    encoded = urllib.parse.quote(video_url, safe='')
    url = f'https://www.youtube.com/oembed?url={encoded}&format=json'
    body = https_get(url)
    data = json.loads(body)
    return {
        'title': data.get('title', ''),
        'channel': data.get('author_name', ''),
    }


def collect_all():
    """Main collection routine."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')

    # Get API key (env var takes priority for cloud/CI environments)
    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    if not api_key:
        row = conn.execute("SELECT value FROM settings WHERE key='yt_api_key'").fetchone()
        api_key = row['value'] if row else None

    # Get all YouTube videos
    videos = conn.execute("SELECT * FROM videos WHERE platform='youtube'").fetchall()
    if not videos:
        log(f'No YouTube videos found')
        conn.close()
        return {'total': 0, 'success': 0, 'failed': 0}

    log(f'Starting fetch for {len(videos)} YouTube videos')
    snapshot_at = time.strftime('%Y-%m-%d %H:%M:%S')
    success = 0
    failed = 0

    for video in videos:
        yt_id = extract_youtube_id(video['url'])
        if not yt_id:
            log(f'  SKIP: Cannot extract ID from {video["url"]}')
            failed += 1
            continue

        data = None
        source = 'none'

        # Try YouTube Data API first
        if api_key:
            try:
                data = fetch_youtube_stats(yt_id, api_key)
                if data:
                    source = 'api'
            except Exception as e:
                log(f'  API error for {yt_id}: {e}')

        # Fallback to oEmbed
        if not data:
            try:
                oe = fetch_youtube_oembed(video['url'])
                if oe:
                    data = {
                        'title': oe['title'] or video['title'],
                        'channel': oe['channel'] or video['channel'],
                        'channel_id': video['channel_id'] or '',
                        'thumbnail': video['thumbnail'] or '',
                        'published_at': video['published_at'] or '',
                        'views': video['views'] or 0,
                        'likes': video['likes'] or 0,
                        'comments': video['comments'] or 0,
                        'shares': video['shares'] or 0,
                        'saves': video['saves'] or 0,
                    }
                    source = 'oembed'
            except Exception as e:
                log(f'  oEmbed error for {yt_id}: {e}')

        if not data:
            log(f'  FAIL: {yt_id} ({video["title"][:40] if video["title"] else video["url"][:40]})')
            failed += 1
            continue

        # Update video record
        conn.execute('''
            UPDATE videos SET title=?, channel=?, channel_id=?, thumbnail=?, published_at=?,
                views=?, likes=?, comments=?, shares=?, saves=?,
                last_fetched=?, fetch_status='ok'
            WHERE id=?
        ''', (
            data['title'], data['channel'], data['channel_id'], data['thumbnail'],
            data['published_at'], data['views'], data['likes'], data['comments'],
            data['shares'], data['saves'], snapshot_at, video['id']
        ))

        # Save history snapshot
        try:
            conn.execute('''
                INSERT INTO history(video_id, snapshot_at, views, likes, comments, shares, saves)
                VALUES(?, ?, ?, ?, ?, ?, ?)
            ''', (video['id'], snapshot_at, data['views'], data['likes'],
                  data['comments'], data['shares'], data['saves']))
            success += 1
        except sqlite3.IntegrityError:
            # Duplicate snapshot, skip
            pass

        log(f'  OK [{source:6s}] {yt_id}: {data["views"]:,} views, {data["likes"]:,} likes - {data["title"][:50]}')

        # Throttle
        time.sleep(0.3)

    conn.commit()
    conn.close()

    log(f'Done: {success}/{len(videos)} videos updated ({failed} failed)')
    return {'total': len(videos), 'success': success, 'failed': failed}


if __name__ == '__main__':
    result = collect_all()
    sys.exit(0 if result['failed'] == 0 else 1)
