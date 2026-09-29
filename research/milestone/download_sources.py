"""Download public PDF sources; preserve source URLs, retrieval times and SHA-256."""
import concurrent.futures
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sources = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
out = ROOT / 'pdf'
out.mkdir(exist_ok=True)

def download(source):
    result = {'id': source['id'], 'url': source['url'], 'retrieved_at': datetime.now(timezone.utc).isoformat()}
    try:
        request = urllib.request.Request(source['url'], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
            result['final_url'] = response.url
        if not data.startswith(b'%PDF-'):
            raise ValueError('Response is not a PDF; no file saved')
        target = out / (source['id'] + '.pdf')
        target.write_bytes(data)
        result.update(status='downloaded', file=target.relative_to(ROOT).as_posix(), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    except Exception as error:
        result.update(status='failed', error=str(error))
    return result

pdf_sources = [s for s in sources if '.pdf' in s['url'].lower() or '/media/documentation_file/' in s['url']]
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    results = list(pool.map(download, pdf_sources))
(ROOT / 'download-manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
lines = ['# Danh mục nguồn Milestone', '', 'Ngày rà soát: 14-09-2026. P0: đọc trước; P1: đọc theo tích hợp; P2: bổ sung/lịch sử.', '', 'Đây là danh mục nguồn, không phải chứng nhận tương thích. PDF tải được có liên kết cục bộ. HTML/repository là liên kết trực tuyến; chưa mirror toàn bộ.', '', '| ID | Nhóm | Ưu tiên | Nguồn | Ghi chú |', '|---|---|---|---|---|']
by_id = {r['id']: r for r in results}
for source in sources:
    local = by_id.get(source['id'], {})
    link = '[' + source['title'] + '](' + source['url'] + ')'
    if local.get('status') == 'downloaded':
        link += ' · [PDF local](' + local['file'] + ')'
    elif local.get('status') == 'failed':
        link += ' · Tải tự động thất bại'
    lines.append('| ' + ' | '.join([source['id'], source['category'], source['priority'], link, source['note']]) + ' |')
(ROOT / 'CATALOG.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'sources':len(sources), 'pdf_attempted':len(results), 'downloaded':sum(r['status']=='downloaded' for r in results), 'failures':[r for r in results if r['status']=='failed']}, ensure_ascii=False, indent=2))
