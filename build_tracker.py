"""
红人追踪器 - 云端数据构建脚本
从 SQLite 导出最新数据，注入 HTML 模板，生成可部署的追踪器页面。
用法: python build_tracker.py
输出: web/index.html (自包含，可直接部署到 CloudStudio)
"""
import sys
import io
import json
import sqlite3
import os
import time

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'server', 'tracker.db')
TEMPLATE_PATH = os.path.join(BASE_DIR, 'web', 'index.html')
OUTPUT_PATH = os.path.join(BASE_DIR, 'web', 'index.html')
DATA_OUTPUT = os.path.join(BASE_DIR, 'web', 'data.json')


def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def export_from_db():
    """从 SQLite 导出所有追踪数据为 dict。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 视频列表
    videos = []
    rows = conn.execute('SELECT * FROM videos ORDER BY created_at DESC').fetchall()
    for r in rows:
        videos.append({
            'id': r['id'],
            'url': r['url'],
            'platform': r['platform'],
            'title': r['title'],
            'channel': r['channel'],
            'channel_id': r['channel_id'] or '',
            'published_at': r['published_at'] or '',
            'thumbnail': r['thumbnail'] or '',
            'views': r['views'] or 0,
            'likes': r['likes'] or 0,
            'comments': r['comments'] or 0,
            'shares': r['shares'] or 0,
            'saves': r['saves'] or 0,
            'last_fetched': r['last_fetched'] or '',
            'fetch_status': r['fetch_status'] or 'pending',
            'created_at': r['created_at'] or '',
        })

    # 2. 每日聚合历史 (按天汇总所有视频的数据)
    history = []
    hrows = conn.execute('''
        SELECT date(snapshot_at) as day,
               SUM(views) as views,
               SUM(likes) as likes,
               SUM(comments) as comments,
               SUM(shares) as shares,
               SUM(saves) as saves,
               COUNT(DISTINCT video_id) as videos
        FROM history
        GROUP BY date(snapshot_at)
        ORDER BY day ASC
    ''').fetchall()
    for r in hrows:
        history.append({
            'day': r['day'],
            'views': r['views'] or 0,
            'likes': r['likes'] or 0,
            'comments': r['comments'] or 0,
            'shares': r['shares'] or 0,
            'saves': r['saves'] or 0,
            'videos': r['videos'] or 0,
        })

    # 3. 每个视频的历史快照
    video_history = {}
    vhrows = conn.execute('''
        SELECT * FROM history ORDER BY video_id, snapshot_at ASC
    ''').fetchall()
    for r in vhrows:
        vid = r['video_id']
        if vid not in video_history:
            video_history[vid] = []
        video_history[vid].append({
            'id': r['id'],
            'video_id': r['video_id'],
            'snapshot_at': r['snapshot_at'],
            'views': r['views'] or 0,
            'likes': r['likes'] or 0,
            'comments': r['comments'] or 0,
            'shares': r['shares'] or 0,
            'saves': r['saves'] or 0,
        })

    # 4. 汇总统计
    total_views = sum(v['views'] for v in videos)
    total_likes = sum(v['likes'] for v in videos)
    total_comments = sum(v['comments'] for v in videos)
    video_count = len(videos)
    youtube_count = sum(1 for v in videos if v['platform'] == 'youtube')

    conn.close()

    data = {
        'videos': videos,
        'history': history,
        'video_history': video_history,
        'total_views': total_views,
        'total_likes': total_likes,
        'total_comments': total_comments,
        'video_count': video_count,
        'youtube_count': youtube_count,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }

    log(f'Export: {video_count} videos, {len(history)} history days, {len(video_history)} videos with history')
    log(f'Totals: {total_views:,} views, {total_likes:,} likes, {total_comments:,} comments')
    return data


def build_html(data):
    """读取模板，注入数据，写出最终 HTML。"""
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        template = f.read()

    data_json = json.dumps(data, ensure_ascii=False)

    if '__DATA_PLACEHOLDER__' not in template:
        log('ERROR: Template does not contain __DATA_PLACEHOLDER__')
        log('Falling back to regex replacement of existing DATA line...')
        import re
        # Fallback: replace existing inline data
        new_html = re.sub(
            r'const DATA = \{.*?\};',
            f'const DATA = {data_json};',
            template,
            count=1,
            flags=re.DOTALL
        )
        if 'const DATA = ' not in new_html:
            log('ERROR: Could not find DATA line in template')
            return None
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            f.write(new_html)
        log(f'Built (fallback): {OUTPUT_PATH} ({len(new_html):,} bytes)')
        return new_html

    html = template.replace('__DATA_PLACEHOLDER__', data_json)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    log(f'Built: {OUTPUT_PATH} ({len(html):,} bytes)')
    return html


def save_data_json(data):
    """同时保存 data.json 文件。"""
    with open(DATA_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    log(f'Saved: {DATA_OUTPUT}')


def main():
    log('=== 红人追踪器云端数据构建 ===')
    data = export_from_db()
    save_data_json(data)
    html = build_html(data)
    if html:
        log('DONE: 云端追踪器构建成功')
        return 0
    else:
        log('FAILED: 构建失败')
        return 1


if __name__ == '__main__':
    sys.exit(main())
