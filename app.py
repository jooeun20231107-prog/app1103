"""장보기 알림메모 | 실행: streamlit run app.py
Python 3.10+ 권장. requirements.txt와 함께 GitHub에 업로드하세요.
Streamlit Community Cloud: Main file path = app.py.
배포 뒤 '연결 / QR'에 앱의 공개 주소를 입력하면 현재 장보기 QR을 만듭니다.
주의: SQLite는 실행 서버의 로컬 저장소입니다. Cloud 재시작/재배포 시 데이터가
유실될 수 있습니다. 장기 보관은 CSV 다운로드를 사용하세요.
QR URL을 아는 사람은 동일 장보기를 보고 수정할 수 있습니다(인증 없음).
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

import qrcode
import streamlit as st

st.set_page_config(page_title="장보기 알림메모", page_icon="🛒", layout="centered", initial_sidebar_state="collapsed")
CATEGORIES = ["🥬 야채", "🍎 과일", "🥩 육류", "🐟 수산", "🥚 유제품·달걀", "🍚 곡물·식품", "🧂 양념", "🧻 생활용품", "📦 기타"]
PRIORITIES = {1: "1 · 필수", 2: "2 · 보통", 3: "3 · 여유 있을 때"}
DB_PATH = os.environ.get("SHOPPING_DB_PATH", "shopping.sqlite3")

def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, list_id TEXT NOT NULL, category TEXT NOT NULL,
            name TEXT NOT NULL, priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
            target INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            unit_price INTEGER NOT NULL DEFAULT 0, quantity REAL NOT NULL DEFAULT 0,
            amount INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, purchased TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS ix_items_list ON items(list_id, status)")


def read_items(list_id):
    with connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM items WHERE list_id=? ORDER BY priority,created,id", (list_id,))]


def mutate(sql, params):
    with connect() as db:
        db.execute(sql, params)


def now():
    return datetime.now(timezone.utc).isoformat()


def won(value):
    return f"{int(value):,}원"


def line_total(price, quantity):
    return int((Decimal(str(price)) * Decimal(str(quantity))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def shared_url(base, list_id):
    parts = urlsplit(base.strip())
    if parts.scheme not in ('https', 'http') or not parts.netloc or parts.username or parts.password:
        raise ValueError('https://로 시작하는 공개 앱 주소를 입력하세요.')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'list']
    query.append(('list', list_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


st.markdown("""<style>
.block-container{max-width:520px;padding:0.65rem 0.8rem 1rem!important}
header[data-testid="stHeader"]{height:0.2rem;background:transparent}
#MainMenu,footer{visibility:hidden}
h1{font-size:1.45rem!important;padding:0!important;margin:0!important}
p{margin-bottom:.35rem} div[data-testid="stVerticalBlock"]{gap:.45rem}
div[data-testid="stMetric"]{background:#f0f8f3;border-radius:12px;padding:.35rem .65rem}
div[data-testid="stMetricValue"]{font-size:1.45rem}
div[data-testid="stMetricLabel"]{font-size:.8rem}
.stButton button,.stFormSubmitButton button{min-height:2.5rem;border-radius:10px}
div[data-testid="stExpander"]{border-radius:12px}
@media(max-width:600px){.block-container{padding-top:.5rem!important}}
</style>""", unsafe_allow_html=True)

try:
    initialize()
except sqlite3.Error:
    st.error('저장소를 열지 못했습니다. 앱 관리자가 쓰기 가능한 저장 경로를 확인해 주세요.')
    st.stop()

raw_id = st.query_params.get('list', '')
try:
    list_id = str(uuid.UUID(raw_id))
except (ValueError, TypeError, AttributeError):
    list_id = str(uuid.uuid4())
    st.query_params['list'] = list_id

# 상태 변경 뒤 재실행 시에도 사용자가 같은 장보기를 유지합니다.
rows = read_items(list_id)
pending = [r for r in rows if r['status'] == 'pending']
held = [r for r in rows if r['status'] == 'held']
cart = [r for r in rows if r['status'] == 'cart']
total = sum(r['amount'] for r in cart)

st.title('🛒 장보기 알림메모')
st.caption('1 필수 · 2 보통 · 3 여유 있을 때 ｜ 비싸면 보류하세요')
a, b = st.columns(2)
a.metric('현재 카트 총액', won(total))
b.metric('남은 메모 / 담은 종류', f'{len(pending)} / {len(cart)}')

with st.expander('＋ 상품 추가', expanded=not rows):
    with st.form('add_item', clear_on_submit=True):
        name = st.text_input('상품명', max_chars=80, placeholder='예: 양파 1망, 사과 1봉')
        c1, c2 = st.columns(2)
        category = c1.selectbox('카테고리', CATEGORIES)
        priority = c2.selectbox('중요도', [1, 2, 3], format_func=PRIORITIES.get, index=1)
        target = st.number_input('단가 상한(원) · 0이면 미설정', min_value=0, max_value=100000000, step=100, value=0)
        st.caption('단가 상한은 구매 시 입력하는 1개·1팩·1kg 등 한 단위의 가격과 비교합니다.')
        if st.form_submit_button('메모에 추가', type='primary', use_container_width=True):
            if not name.strip():
                st.warning('상품명을 입력해 주세요.')
            else:
                mutate('INSERT INTO items (id,list_id,category,name,priority,target,created) VALUES (?,?,?,?,?,?,?)',
                       (str(uuid.uuid4()), list_id, category, name.strip(), priority, target, now()))
                st.rerun()

mode = st.radio('목록', ['📝 살 것', '⏸ 보류', '🛒 카트'], horizontal=True, label_visibility='collapsed')
current = pending if mode == '📝 살 것' else held if mode == '⏸ 보류' else cart

if not current:
    st.info('아직 담은 상품이 없어요.' if mode == '🛒 카트' else '목록이 비어 있어요. 상품을 추가하거나 다른 목록을 확인하세요.')
else:
    by_id = {r['id']: r for r in current}
    def item_label(item_id):
        r = by_id[item_id]
        tail = f" · {won(r['amount'])}" if r['status'] == 'cart' else ''
        return f"[{r['priority']}] {r['name']} · {r['category'].split(' ', 1)[-1]}{tail}"
    selected_id = st.selectbox('상품 선택', list(by_id), format_func=item_label, key=f'select_{mode}', label_visibility='collapsed')
    item = by_id[selected_id]
    st.caption(f"{PRIORITIES[item['priority']]} · 단가 상한 {won(item['target']) if item['target'] else '없음'}")

    if mode == '🛒 카트':
        st.write(f"**{won(item['unit_price'])} × {item['quantity']:g} = {won(item['amount'])}**")
        with st.form(f"edit_{selected_id}"):
            x, y = st.columns(2)
            price = x.number_input('단가(원)', min_value=0, max_value=100000000, value=int(item['unit_price']), step=100)
            qty = y.number_input('수량', min_value=0.01, max_value=100000.0, value=float(item['quantity']), step=1.0, format='%.2f')
            x, y = st.columns(2)
            save = x.form_submit_button('가격·수량 수정', use_container_width=True)
            restore = y.form_submit_button('메모로 되돌리기', use_container_width=True)
            if save:
                mutate("UPDATE items SET unit_price=?,quantity=?,amount=? WHERE id=? AND list_id=? AND status='cart'",
                       (price, qty, line_total(price, qty), selected_id, list_id))
                st.rerun()
            if restore:
                mutate("UPDATE items SET status='pending',unit_price=0,quantity=0,amount=0,purchased=NULL WHERE id=? AND list_id=? AND status='cart'", (selected_id, list_id))
                st.rerun()
    elif mode == '⏸ 보류':
        st.caption('비싸거나 오늘 필요하지 않아 보류한 상품입니다. 총액에 포함하지 않습니다.')
        if st.button('다시 살 것에 넣기', use_container_width=True):
            mutate("UPDATE items SET status='pending' WHERE id=? AND list_id=? AND status='held'", (selected_id, list_id))
            st.rerun()
    else:
        # 폼 밖에서 입력받아 상한 초과/예상 총액을 즉시 표시합니다.
        x, y = st.columns(2)
        price = x.number_input('구매 단가(원)', min_value=0, max_value=100000000, step=100, value=0, key=f'price_{selected_id}')
        qty = y.number_input('수량', min_value=0.01, max_value=100000.0, step=1.0, value=1.0, format='%.2f', key=f'qty_{selected_id}')
        subtotal = line_total(price, qty)
        st.caption(f'이 상품 {won(subtotal)} · 담은 뒤 총액 {won(total + subtotal)}')
        over = item['target'] > 0 and price > item['target']
        confirmed = True
        if over:
            st.warning(f"단가 상한보다 {won(price - item['target'])} 비싸요. 오늘은 보류해도 좋아요.")
            confirmed = st.checkbox('상한을 초과해도 구매', key=f'over_{selected_id}')
        x, y = st.columns(2)
        if x.button('✓ 카트에 담기', type='primary', use_container_width=True, disabled=not confirmed):
            mutate("UPDATE items SET status='cart',unit_price=?,quantity=?,amount=?,purchased=? WHERE id=? AND list_id=? AND status='pending'",
                   (price, qty, subtotal, now(), selected_id, list_id))
            st.rerun()
        if y.button('⏸ 오늘은 보류', use_container_width=True):
            mutate("UPDATE items SET status='held' WHERE id=? AND list_id=? AND status='pending'", (selected_id, list_id))
            st.rerun()

    with st.expander('선택 상품 수정 / 삭제'):
        with st.form(f'meta_{selected_id}'):
            new_name = st.text_input('상품명 수정', value=item['name'], max_chars=80)
            new_cat = st.selectbox('카테고리 수정', CATEGORIES, index=CATEGORIES.index(item['category']))
            new_priority = st.selectbox('중요도 수정', [1, 2, 3], index=item['priority'] - 1, format_func=PRIORITIES.get)
            new_target = st.number_input('단가 상한 수정(원)', min_value=0, max_value=100000000, value=int(item['target']), step=100)
            if st.form_submit_button('상품 정보 저장', use_container_width=True):
                if not new_name.strip():
                    st.warning('상품명은 비울 수 없어요.')
                else:
                    mutate('UPDATE items SET name=?,category=?,priority=?,target=? WHERE id=? AND list_id=?',
                           (new_name.strip(), new_cat, new_priority, new_target, selected_id, list_id))
                    st.rerun()
        delete_ok = st.checkbox('구매가 아니라 잘못 등록한 항목을 영구 삭제합니다.', key=f'delete_ok_{selected_id}')
        if st.button('선택 상품 영구 삭제', disabled=not delete_ok, key=f'delete_{selected_id}'):
            mutate('DELETE FROM items WHERE id=? AND list_id=?', (selected_id, list_id))
            st.rerun()

with st.expander('🔗 연결 / QR · 내역 저장'):
    st.caption('앱을 GitHub → Streamlit Community Cloud에 배포한 뒤 공개 주소를 입력하세요. QR 스캔 시 이 장보기가 바로 열립니다.')
    base = st.text_input('배포된 앱 주소', placeholder='https://your-app.streamlit.app', key='public_base')
    if base.strip():
        try:
            url = shared_url(base, list_id)
            st.link_button('이 장보기 열기', url, use_container_width=True)
            st.code(url, language=None)
            qr = qrcode.make(url)
            image = io.BytesIO()
            qr.save(image, format='PNG')
            st.image(image.getvalue(), width=180)
            st.download_button('QR 이미지 저장', image.getvalue(), 'shopping_qr.png', 'image/png')
        except ValueError as exc:
            st.warning(str(exc))
    st.caption('QR·주소를 아는 사람은 함께 수정할 수 있어요. 공개 게시하지 마세요. 다른 휴대폰의 변경은 새로고침으로 불러옵니다.')
    if st.button('공유 목록 새로고침', use_container_width=True):
        st.rerun()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['카테고리','상품명','중요도','단가상한(원)','상태','구매단가(원)','수량','금액(원)','담은시각(UTC)'])
    for r in rows:
        writer.writerow([r['category'], r['name'], r['priority'], r['target'],
                         {'pending':'살 것','held':'보류','cart':'카트'}[r['status']],
                         r['unit_price'], r['quantity'], r['amount'], r['purchased'] or ''])
    st.download_button('전체 내역 CSV 저장', output.getvalue().encode('utf-8-sig'), 'shopping.csv', 'text/csv', use_container_width=True)
    st.caption('서버 임시 저장 방식입니다. 재배포·서버 초기화 시 유실될 수 있으니 중요한 내역은 CSV로 보관하세요. 이 앱은 푸시 알림을 전송하지 않습니다.')
    if st.button('새 장보기 시작', use_container_width=True):
        st.query_params['list'] = str(uuid.uuid4())
        st.rerun()
    st.caption('새 장보기를 시작해도 기존 목록은 삭제되지 않아요. 이전 QR·주소로 다시 열 수 있습니다.')
