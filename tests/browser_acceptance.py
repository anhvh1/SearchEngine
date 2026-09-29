"""Run against the seeded local acceptance server (port 8765)."""
import json
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

config = json.loads(Path('data/acceptance.json').read_text())
token = next(p['token'] for p in config['principals'] if p['name'] == 'administrator')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765')
    page.get_by_label('Mã truy cập').fill(token)
    page.get_by_role('button', name='Đăng nhập', exact=True).click()
    expect(page.get_by_role('heading', name='Tìm sự kiện')).to_be_visible()
    page.get_by_label('Nội dung tìm kiếm').fill('người lạ')
    page.locator('#search-form').get_by_role('button', name='Tìm kiếm', exact=True).click()
    expect(page.locator('#result-count')).to_contain_text('2 bản ghi')
    page.locator('.result-row').first.click()
    expect(page.locator('#evidence')).to_contain_text('gate-01')
    page.screenshot(path='data/search-desktop.png', full_page=True)
    page.get_by_role('button', name='Hồ sơ phân tích', exact=True).click()
    expect(page.get_by_label('Cấu hình hồ sơ JSON')).to_be_visible()
    profile = {'id': 'browser-'+uuid.uuid4().hex[:8], 'name': 'Browser acceptance', 'version': 1,
               'priority': -100, 'match': {'site_id': 'demo', 'message': 'Phát hiện người lạ tại Cổng chính'},
               'mapping': {'zone': {'path': 'payload.zone', 'type': 'string', 'required': True}}, 'family': 'intrusion'}
    page.get_by_label('Cấu hình hồ sơ JSON').fill(json.dumps(profile, ensure_ascii=False))
    page.get_by_role('button', name='Lấy mẫu từ site').click()
    expect(page.locator('#sample-json')).not_to_have_value('[]')
    page.get_by_role('button', name='Lưu phiên bản', exact=True).click()
    expect(page.locator('#profile-output')).to_contain_text('Đã lưu')
    page.get_by_role('button', name='Thử mẫu', exact=True).click()
    expect(page.locator('#profile-output')).to_contain_text('"valid": true')
    page.get_by_role('button', name='Kích hoạt', exact=True).click()
    expect(page.locator('#profile-output')).to_contain_text('Đã kích hoạt')
    page.get_by_role('button', name='Danh mục nguồn', exact=True).click()
    expect(page.locator('#catalog-list')).to_contain_text('FACEME.UNKNOWN_PERSON')
    page.get_by_role('button', name='Vận hành', exact=True).click()
    expect(page.locator('#operations-output')).to_contain_text('records')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='Tìm kiếm', exact=True).first.click()
    page.screenshot(path='data/search-mobile.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow'
    assert not errors, errors
    page.get_by_role('button', name='Đăng xuất', exact=True).click()
    expect(page.get_by_label('Mã truy cập')).to_be_visible()
    browser.close()
print('Browser acceptance passed: login, search, evidence, profiles, discovery, operations, responsive, logout')
