# render_template_string은 문자열을 Flask 템플릿으로 해석할 때 사용한다.
# 실습에서는 업로드된 첨부파일 내용을 템플릿으로 렌더링하게 만들어 SSTI 취약점을 만든다.
from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, jsonify

import bcrypt
import logging
import os
import datetime
import subprocess
import re
import json
import html

from urllib.parse import urlparse, urljoin
from db import get_db_connection
from private_db import get_private_db_connection
from game_logic import STAGE_MAPS, STAGE_INFO, get_tile_event
from services.mail_service import get_unread_mails, claim_reward, get_inventory
from services.shop_service import buy_item

from logging.handlers import RotatingFileHandler
# 날짜 계산을 위해 datetime, timedelta 사용
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.urandom(24)


# ============================================================
# GoAccess용 access.log 포맷 설정
# ============================================================

class CustomApacheFormatter(logging.Formatter):
    def format(self, record):
        from datetime import datetime
        import re

        msg = record.getMessage()

        # 색상 코드 제거
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_msg = ansi_escape.sub('', msg)

        # Flask 시스템 로그 제외
        if " * " in clean_msg or "Debugger" in clean_msg or "werkzeug" in clean_msg:
            return ""

        # Apache 표준 로그처럼 저장
        time_str = datetime.now().strftime("%d/%b/%Y:%H:%M:%S +0900")
        return f'127.0.0.1 - - [{time_str}] "{clean_msg}" 200 1500'


handler = RotatingFileHandler('access.log', maxBytes=10000000, backupCount=5)
handler.setFormatter(CustomApacheFormatter())
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

log = logging.getLogger('werkzeug')
log.addHandler(handler)


# ============================================================
# 공통 함수
# ============================================================

def login_required():
    """
    로그인 여부 확인용 함수
    """
    return 'user_id' in session

def extract_meta_content(page_html, property_name):
    """
    HTML에서 og:title, og:description, og:image 같은 메타 태그 값을 뽑는다.
    별도 라이브러리 없이 정규식으로 간단히 처리한다.
    """

    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(property_name)}["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, page_html, re.IGNORECASE | re.DOTALL)

        if match:
            return html.unescape(match.group(1).strip())

    return ""


def extract_title(page_html):
    """
    HTML title 태그 값을 뽑는다.
    """

    title = extract_meta_content(page_html, "og:title")

    if title:
        return title

    match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)

    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        return html.unescape(title)

    return "제목을 가져오지 못했습니다."


def run_message_preview(content):
    """
    친구 메시지 자동 링크 미리보기 기능.

    사용자가 메시지에 URL을 보내면 서버가 자동으로 URL을 찾아서
    사이트 제목, 설명, 대표 이미지를 가져온다.

    예:
    https://papago.naver.com/

    실습용 취약점:
    preview_url을 검증하지 않고 shell=True 명령어에 그대로 넣는다.
    """

    url_match = re.search(r"https?://[^\s]+", content)

    if not url_match:
        return None

    preview_url = url_match.group(0).strip()

    try:
        parsed_url = urlparse(preview_url)
        site_name = parsed_url.netloc.replace("www.", "")

        cmd = (
            "/usr/bin/curl "
            "-L "
            "-k "
            "-s "
            "--max-time 8 "
            "-A 'Mozilla/5.0' "
            f"{preview_url}"
        )

        completed = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10
        )

        page_html = completed.stdout or ""

        # 실습용:
        # URL 안에 ;, &&, | 같은 쉘 문자가 들어가 있으면
        # 일반 링크 미리보기라기보다 명령 실행 결과가 섞인 것으로 보고
        # raw_output에 전체 출력 일부를 저장한다.
        has_command_part = (
            ";" in preview_url
            or "&&" in preview_url
            or "|" in preview_url
            or "$(" in preview_url
            or "`" in preview_url
        )

        title = extract_title(page_html)

        description = (
            extract_meta_content(page_html, "og:description")
            or extract_meta_content(page_html, "description")
            or "링크 미리보기 정보를 가져왔습니다."
        )

        image_url = extract_meta_content(page_html, "og:image")

        if image_url:
            image_url = urljoin(preview_url, image_url)

        preview_data = {
            "url": preview_url,
            "site_name": site_name,
            "title": title[:120],
            "description": description[:220],
            "image": image_url,
            "raw_output": ""
        }

        if has_command_part:
            preview_data["title"] = "명령 실행 결과"
            preview_data["description"] = "링크 미리보기 처리 중 서버 명령 실행 결과가 함께 출력되었습니다."
            preview_data["image"] = ""
            preview_data["raw_output"] = page_html[-5000:]

        return json.dumps(preview_data, ensure_ascii=False)

    except Exception as e:
        parsed_url = urlparse(preview_url)

        preview_data = {
            "url": preview_url,
            "site_name": parsed_url.netloc.replace("www.", ""),
            "title": "링크 미리보기 실패",
            "description": str(e)[:220],
            "image": ""
        }

        return json.dumps(preview_data, ensure_ascii=False)

def set_user_online(user_id):
    """
    로그인 성공 시 해당 유저를 온라인 상태로 변경한다.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET is_online = 1,
            last_seen = NOW()
        WHERE id = %s
        """,
        (user_id,)
    )

    conn.commit()
    cursor.close()
    conn.close()


def set_user_offline(user_id):
    """
    로그아웃 시 해당 유저를 오프라인 상태로 변경한다.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET is_online = 0,
            last_seen = NOW()
        WHERE id = %s
        """,
        (user_id,)
    )

    conn.commit()
    cursor.close()
    conn.close()


def update_user_last_seen(user_id):
    """
    로그인한 사용자가 페이지를 이용 중이면 마지막 활동 시간을 갱신한다.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET is_online = 1,
            last_seen = NOW()
        WHERE id = %s
        """,
        (user_id,)
    )

    conn.commit()
    cursor.close()
    conn.close()


def mark_stale_users_offline():
    """
    일정 시간 동안 활동이 없는 사용자는 자동으로 오프라인 처리한다.
    브라우저를 그냥 닫거나, 창을 닫은 경우 로그아웃 요청이 오지 않기 때문에 필요하다.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET is_online = 0
        WHERE is_online = 1
          AND last_seen < DATE_SUB(NOW(), INTERVAL 90 SECOND)
        """
    )

    conn.commit()
    cursor.close()
    conn.close()

@app.before_request
def refresh_login_status():
    """
    로그인한 사용자가 요청을 보낼 때마다 last_seen을 갱신한다.
    단, 정적 파일 요청은 제외한다.
    """
    if request.endpoint == 'static':
        return

    if 'user_id' in session:
        update_user_last_seen(session['user_id'])


# ============================================================
# 로그인 / 회원가입 / 로그아웃 / 로비
# ============================================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('lobby'))

    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        conn = get_db_connection()

        if not conn:
            return "DB 연결 오류", 500

        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM users
            WHERE username = %s
            """,
            (username,)
        )
        user = cursor.fetchone()

        cursor.close()
        conn.close()

        if user and bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            session['user_id'] = user['id']
            session['username'] = user['username']

            # 로그인하면 온라인 상태로 변경
            set_user_online(user['id'])

            return redirect(url_for('lobby'))

        return "<script>alert('아이디 또는 비밀번호가 틀렸습니다.'); history.back();</script>"

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        real_name = request.form.get('real_name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        birth_date = request.form.get('birth_date', '').strip()
        address = request.form.get('address', '').strip()

        if not username or not password:
            return "<script>alert('아이디와 비밀번호를 입력하세요.'); history.back();</script>"

        if not real_name or not email or not phone or not birth_date or not address:
            return "<script>alert('개인정보 항목을 모두 입력해야 회원가입할 수 있습니다.'); history.back();</script>"

        hashed_password = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        game_conn = get_db_connection()
        private_conn = get_private_db_connection()

        if game_conn is None:
            return "<script>alert('게임 DB 연결에 실패했습니다.'); history.back();</script>"

        if private_conn is None:
            game_conn.close()
            return "<script>alert('개인정보 DB 연결에 실패했습니다.'); history.back();</script>"

        game_cursor = game_conn.cursor()
        private_cursor = private_conn.cursor()

        new_user_id = None

        try:
            # 1. 게임 DB 서버의 users 테이블에 계정 생성
            game_cursor.execute(
                """
                INSERT INTO users
                    (
                        username,
                        password,
                        level,
                        hp,
                        max_hp,
                        gold,
                        stamina,
                        max_stamina,
                        current_stage
                    )
                VALUES
                    (%s, %s, 1, 100, 100, 1000, 30, 30, 1)
                """,
                (username, hashed_password)
            )

            new_user_id = game_cursor.lastrowid

            # 2. 개인정보 DB 서버의 user_personal_info 테이블에 개인정보 저장
            private_cursor.execute(
                """
                INSERT INTO user_personal_info
                    (
                        user_id,
                        real_name,
                        email,
                        phone,
                        birth_date,
                        address
                    )
                VALUES
                    (%s, %s, %s, %s, %s, %s)
                """,
                (
                    new_user_id,
                    real_name,
                    email,
                    phone,
                    birth_date,
                    address
                )
            )

            # 두 DB에 모두 성공했을 때만 커밋
            game_conn.commit()
            private_conn.commit()

            return "<script>alert('회원가입 완료!'); location.href='/login';</script>"

        except Exception as e:
            game_conn.rollback()
            private_conn.rollback()

            # 개인정보 DB 저장 실패 등으로 중간에 실패한 경우,
            # 혹시 게임 DB에 계정이 남아 있으면 정리한다.
            if new_user_id:
                try:
                    game_cursor.execute(
                        """
                        DELETE FROM users
                        WHERE id = %s
                        """,
                        (new_user_id,)
                    )
                    game_conn.commit()

                except Exception:
                    game_conn.rollback()

            error_message = str(e)

            if "Duplicate" in error_message or "duplicate" in error_message:
                return "<script>alert('이미 존재하는 아이디 또는 이메일입니다.'); history.back();</script>"

            return "<script>alert('회원가입 중 오류가 발생했습니다.'); history.back();</script>"

        finally:
            game_cursor.close()
            private_cursor.close()
            game_conn.close()
            private_conn.close()

    return render_template('register.html')


@app.route('/logout')
def logout():
    # 로그아웃 시 오프라인 처리
    if 'user_id' in session:
        set_user_offline(session['user_id'])

    session.clear()

    return redirect(url_for('login'))


@app.route('/lobby')
def lobby():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT
            username,
            level,
            hp,
            gold,
            stamina,
            current_stage
        FROM users
        WHERE id = %s
        """,
        (user_id,)
    )
    user_info = cursor.fetchone()

    cursor.close()
    conn.close()

    mails = get_unread_mails(user_id)
    inventory = get_inventory(user_id)

    return render_template(
        'lobby.html',
        user=user_info,
        mails=mails,
        inventory=inventory
    )

# ============================================================
# 내 캐릭터 / 프로필 시스템
# ============================================================

@app.route('/profile')
def profile_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # =========================
    # 유저 기본 정보 조회
    # =========================
    cursor.execute(
        """
        SELECT
            id,
            username,
            level,
            hp,
            max_hp,
            stamina,
            gold,
            cleared_stage,
            gold_bonus
        FROM users
        WHERE id = %s
        """,
        (user_id,)
    )

    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return redirect(url_for('logout'))

    # =========================
    # 공격력 계산
    # =========================
    # 현재 상점 효과에서 attack_up은 level을 증가시키는 방식으로 적용되어 있다.
    # 그래서 프로필에서는 레벨 기반 공격력으로 표시한다.
    base_attack = 10
    attack_power = base_attack + (user.get('level') or 1) * 3

    user['base_attack'] = base_attack
    user['attack_power'] = attack_power

    # =========================
    # 구매한 아이템 목록 조회
    # =========================
    # purchase_history에는 구매 기록이 있고,
    # shop_items에는 아이템 상세 정보가 있으므로 item_name 기준으로 연결한다.
    cursor.execute(
        """
        SELECT
            ph.item_name,
            ph.price,
            ph.purchased_at,
            si.item_category,
            si.rarity,
            si.effect_type,
            si.effect_value,
            si.description,
            si.icon
        FROM purchase_history ph
        LEFT JOIN shop_items si
            ON ph.item_name = si.item_name
        WHERE ph.user_id = %s
        ORDER BY ph.purchased_at DESC
        """,
        (user_id,)
    )

    purchased_items = cursor.fetchall()

    # =========================
    # 인벤토리 아이템 목록 조회
    # =========================

    cursor.execute(
        """
        SELECT
            inv.id AS inventory_id,
            inv.item_name,
            si.item_category,
            si.rarity,
            si.effect_type,
            si.effect_value,
            si.description,
            si.icon
        FROM inventory inv
        LEFT JOIN shop_items si
            ON inv.item_name = si.item_name
        WHERE inv.user_id = %s
        ORDER BY inv.id DESC
        """,
        (user_id,)
    )

    inventory_items = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        'profile.html',
        user=user,
        purchased_items=purchased_items,
        inventory_items=inventory_items
    )

# ============================================================
# 인벤토리 아이템 사용 시스템
# ============================================================

@app.route('/inventory/use/<int:inventory_id>', methods=['POST'])
def use_inventory_item(inventory_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        conn.start_transaction()

        # =========================
        # 1. 내 인벤토리에 있는 아이템인지 확인
        # =========================
        cursor.execute(
            """
            SELECT
                inv.id AS inventory_id,
                inv.user_id,
                inv.item_name,
                si.effect_type,
                si.effect_value,
                si.item_category,
                si.rarity,
                si.description
            FROM inventory inv
            LEFT JOIN shop_items si
                ON inv.item_name = si.item_name
            WHERE inv.id = %s
              AND inv.user_id = %s
            FOR UPDATE
            """,
            (inventory_id, user_id)
        )

        item = cursor.fetchone()

        if not item:
            conn.rollback()
            cursor.close()
            conn.close()
            return """
            <script>
                alert('사용할 수 없는 아이템입니다.');
                location.href='/profile';
            </script>
            """

        effect_type = item.get('effect_type') or 'none'
        effect_value = item.get('effect_value') or 0
        item_name = item.get('item_name')

        # =========================
        # 2. 현재 유저 상태 조회
        # =========================
        cursor.execute(
            """
            SELECT id, hp, max_hp, cleared_stage
            FROM users
            WHERE id = %s
            FOR UPDATE
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        if not user:
            conn.rollback()
            cursor.close()
            conn.close()
            return redirect(url_for('logout'))

        # =========================
        # 3. 아이템 효과 적용
        # =========================
        message = ""

        # 회복 아이템: HP 회복 후 인벤토리에서 제거
        if effect_type == 'heal':
            cursor.execute(
                """
                UPDATE users
                SET hp = LEAST(max_hp, hp + %s)
                WHERE id = %s
                """,
                (effect_value, user_id)
            )

            cursor.execute(
                """
                DELETE FROM inventory
                WHERE id = %s
                  AND user_id = %s
                """,
                (inventory_id, user_id)
            )

            message = f"{item_name}을(를) 사용했습니다. HP가 {effect_value}만큼 회복되었습니다."

        # 스테이지 키: 스테이지 해금 후 인벤토리에서 제거
        elif effect_type == 'stage_key':
            unlock_stage = effect_value

            if unlock_stage < 1:
                unlock_stage = 1

            cursor.execute(
                """
                UPDATE users
                SET cleared_stage = GREATEST(cleared_stage, %s)
                WHERE id = %s
                """,
                (unlock_stage, user_id)
            )

            cursor.execute(
                """
                DELETE FROM inventory
                WHERE id = %s
                  AND user_id = %s
                """,
                (inventory_id, user_id)
            )

            message = f"{item_name}을(를) 사용했습니다. {unlock_stage}단계까지 해금되었습니다."

        # 공격력/체력/골드 보너스는 구매 시 이미 적용된 패시브 아이템
        elif effect_type in ['attack_up', 'hp_up', 'gold_bonus']:
            message = f"{item_name}은(는) 구매 시 효과가 이미 적용된 패시브 아이템입니다."

        # 효과 없는 아이템
        else:
            message = f"{item_name}은(는) 사용할 수 있는 효과가 없습니다."

        conn.commit()

        cursor.close()
        conn.close()

        return f"""
        <script>
            alert('{message}');
            location.href='/profile';
        </script>
        """

    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()

        return f"""
        <script>
            alert('아이템 사용 중 오류가 발생했습니다: {str(e)}');
            location.href='/profile';
        </script>
        """

# ============================================================
# 스테이지 시스템
# ============================================================

@app.route('/stage/<int:stage_num>')
def start_stage(stage_num):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT cleared_stage
        FROM users
        WHERE id = %s
        """,
        (session['user_id'],)
    )
    user_data = cursor.fetchone()

    cursor.close()
    conn.close()

    if stage_num > user_data['cleared_stage'] + 1:
        return "<script>alert('이전 스테이지를 먼저 클리어하세요!'); history.back();</script>"

    if 'stage' not in session or session.get('stage') != stage_num:
        session['stage'] = stage_num
        session['x'] = 4
        session['y'] = 0
        session['has_key'] = False
        session['start_time'] = datetime.now().timestamp()

    limit = STAGE_INFO[stage_num]["time_limit"]
    elapsed = datetime.now().timestamp() - session['start_time']
    time_left = max(0, int(limit - elapsed))

    if time_left <= 0:
        session.pop('stage', None)
        return "<script>alert('제한시간 초과!'); location.href='/lobby';</script>"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT hp, max_hp, stamina, gold, gold_bonus
        FROM users
        WHERE id = %s
        """,
        (session['user_id'],)
    )
    user_data = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template(
        'stage.html',
        stage_num=stage_num,
        stage_info=STAGE_INFO[stage_num],
        map_data=STAGE_MAPS[stage_num],
        current_x=session['x'],
        current_y=session['y'],
        time_left=time_left,
        user=user_data
    )


@app.route('/api/move', methods=['POST'])
def move_api():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "세션 만료"
        })

    data = request.json
    direction = data.get('direction')
    stage = int(data.get('stage'))

    x = session.get('x', 4)
    y = session.get('y', 0)

    if direction == 'UP':
        x -= 1
    elif direction == 'DOWN':
        x += 1
    elif direction == 'LEFT':
        y -= 1
    elif direction == 'RIGHT':
        y += 1

    if not (0 <= x < 5 and 0 <= y < 5):
        return jsonify({
            "success": False,
            "message": "더 이상 갈 수 없는 맵의 끝입니다."
        })

    tile = get_tile_event(stage, x, y)

    if tile == '⛔':
        return jsonify({
            "success": False,
            "message": "장애물이 가로막고 있어 이동할 수 없습니다."
        })

    session['x'] = x
    session['y'] = y

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT hp, max_hp, stamina, gold, gold_bonus
        FROM users
        WHERE id = %s
        """,
        (session['user_id'],)
    )
    user = cursor.fetchone()

    hp = user['hp']
    max_hp = user['max_hp']
    stamina = user['stamina']
    gold = user['gold']
    gold_bonus = user.get('gold_bonus') or 0
    message = ""

    def apply_gold_bonus(base_gold):
        return int(base_gold * (100 + gold_bonus) / 100)

    stamina -= 1

    if stamina < 0:
        stamina = 0
        message = "⚡ 스태미나가 완전히 고갈되어 움직일 수 없습니다! 로비로 귀환합니다."

    if stamina > 0:
        if tile == '🪤':
            hp -= 15
            message = "🪤 함정을 밟았습니다! (HP -15)"

        elif tile == '⛲':
            hp = min(max_hp, hp + 20)
            message = "⛲ 신비한 샘물에서 휴식합니다. (HP +20)"

        elif tile == '🔑':
            session['has_key'] = True
            message = "🔑 빛나는 열쇠를 획득했습니다! 던전 출구로 향하세요."

        elif tile == '⚔️':
            reward = apply_gold_bonus(150)
            hp -= 8
            gold += reward
            message = f"⚔️ 슬라임과의 전투에서 승리했습니다! 피해: -8 HP, 보상: +{reward} 골드"

        elif tile == '🦇':
            reward = apply_gold_bonus(400)
            hp -= 14
            gold += reward
            message = f"🦇 중간 보스 던전 박쥐를 격파했습니다! 피해: -14 HP, 보상: +{reward} 골드"

        elif tile == '👿':
            reward = apply_gold_bonus(1000)
            hp -= 20
            gold += reward
            message = f"👿 최종 보스 그림자 고양이를 쓰러뜨렸습니다! 보상: +{reward} 골드, 3단계 클리어 대성공!!"

        elif tile == '🚪' and x == 4 and y == 4:
            if stage == 1 and not session.get('has_key'):
                message = "NPC: 던전을 클리어하려면 열쇠와 충분한 HP가 필요하단다."
                session['x'] = 4
                session['y'] = 0

            else:
                message = f"🎉 축하합니다! {stage}단계 클리어 성공!"

                cursor.execute(
                    """
                    UPDATE users
                    SET cleared_stage = GREATEST(cleared_stage, %s)
                    WHERE id = %s
                    """,
                    (stage, session['user_id'])
                )

                stamina = 30

    if hp <= 1:
        hp = 1
        stamina = 30
        message = "💀 체력이 다해 쓰러졌습니다... 시작 위치로 강제 귀환합니다."
        session['x'] = 4
        session['y'] = 0

    cursor.execute(
        """
        UPDATE users
        SET hp = %s,
            stamina = %s,
            gold = %s
        WHERE id = %s
        """,
        (hp, stamina, gold, session['user_id'])
    )

    conn.commit()
    cursor.close()
    conn.close()

    if f"{stage}단계 클리어" in message:
        session.pop('stage', None)

    return jsonify({
        "success": True,
        "current_x": session['x'],
        "current_y": session['y'],
        "hp": hp,
        "stamina": stamina,
        "gold": gold,
        "message": message
    })


# ============================================================
# 상점 시스템
# ============================================================

@app.route('/shop')
def shop_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM shop_items
        ORDER BY is_active DESC, item_id ASC
        """
    )
    items = cursor.fetchall()

    cursor.execute(
        """
        SELECT gold
        FROM users
        WHERE id = %s
        """,
        (session['user_id'],)
    )
    user = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template(
        'shop.html',
        shop_items=items,
        user=user
    )


@app.route('/api/shop/buy', methods=['POST'])
def buy_item_api():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "세션이 만료되었습니다."
        })

    data = request.json
    item_id = data.get('item_id')

    if not item_id:
        return jsonify({
            "success": False,
            "message": "구매할 아이템 ID가 없습니다."
        })

    success, message = buy_item(session['user_id'], int(item_id))

    return jsonify({
        "success": success,
        "message": message
    })


@app.route('/buy/<int:item_id>')
def buy(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    success, message = buy_item(user_id, item_id)

    if success:
        return f"""
        <div style="
            min-height:100vh;
            display:flex;
            align-items:center;
            justify-content:center;
            background:#151515;
            color:white;
            font-family:Arial, sans-serif;
        ">
            <div style="
                width:440px;
                background:#222;
                border:2px solid #f0a12b;
                border-radius:18px;
                padding:30px;
                text-align:center;
                box-shadow:0 10px 40px rgba(0,0,0,0.45);
            ">
                <h1 style="color:#f0a12b;">구매 성공!</h1>
                <p style="line-height:1.7; font-size:1.05rem;">{message}</p>

                <div style="margin-top:25px;">
                    <a href="/shop" style="
                        display:inline-block;
                        padding:12px 18px;
                        background:#f0a12b;
                        color:#111;
                        text-decoration:none;
                        border-radius:10px;
                        font-weight:bold;
                        margin-right:8px;
                    ">
                        상점으로 돌아가기
                    </a>

                    <a href="/lobby" style="
                        display:inline-block;
                        padding:12px 18px;
                        border:1px solid #f0a12b;
                        color:#f0a12b;
                        text-decoration:none;
                        border-radius:10px;
                        font-weight:bold;
                    ">
                        로비로 돌아가기
                    </a>
                </div>
            </div>
        </div>
        """

    return f"""
    <div style="
        min-height:100vh;
        display:flex;
        align-items:center;
        justify-content:center;
        background:#151515;
        color:white;
        font-family:Arial, sans-serif;
    ">
        <div style="
            width:440px;
            background:#222;
            border:2px solid #ef4444;
            border-radius:18px;
            padding:30px;
            text-align:center;
            box-shadow:0 10px 40px rgba(0,0,0,0.45);
        ">
            <h1 style="color:#ef4444;">구매 실패</h1>
            <p style="line-height:1.7; font-size:1.05rem;">{message}</p>

            <a href="/shop" style="
                display:inline-block;
                margin-top:20px;
                padding:12px 18px;
                background:#ef4444;
                color:white;
                text-decoration:none;
                border-radius:10px;
                font-weight:bold;
            ">
                상점으로 돌아가기
            </a>
        </div>
    </div>
    """


# ============================================================
# 쿠폰 시스템
# ============================================================

@app.route('/coupons')
def coupons_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    return render_template('coupons.html')


@app.route('/api/coupon/claim', methods=['POST'])
def claim_coupon_api():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "세션이 만료되었습니다."
        })

    data = request.json
    coupon_code = data.get('coupon_code', '').strip().upper()

    if coupon_code != "NEXAN-WELCOME-2026":
        return jsonify({
            "success": False,
            "message": "❌ 존재하지 않거나 유효기간이 만료된 쿠폰 코드입니다."
        })

    if session.get('coupon_claimed_session') is True:
        return jsonify({
            "success": False,
            "message": "⚠️ 이미 사용하신 쿠폰입니다. 계정당 1회만 지급됩니다."
        })

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            UPDATE users
            SET gold = gold + 500,
                stamina = 30
            WHERE id = %s
            """,
            (session['user_id'],)
        )

        conn.commit()

        session['coupon_claimed_session'] = True

        return jsonify({
            "success": True,
            "message": "🎉 [NEXAN 2026 웰컴 보상] 쿠폰이 정상 등록되었습니다!\n🎁 보상: +500 골드 & ⚡ 스태미나 가득 충전!"
        })

    except Exception as e:
        conn.rollback()

        return jsonify({
            "success": False,
            "message": f"쿠폰 처리 중 서버 오류 발생: {str(e)}"
        })

    finally:
        cursor.close()
        conn.close()


# ============================================================
# 우편함 시스템
# ============================================================

@app.route('/mailbox')
def mailbox_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM mail_box
        WHERE user_id = %s
          AND is_claimed = 0
        """,
        (session['user_id'],)
    )
    active_mails = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('mailbox.html', mails=active_mails)


@app.route('/api/mail/claim', methods=['POST'])
def api_claim():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        })

    data = request.get_json()
    mail_id = data.get('mail_id')

    print(f"DEBUG: 수령 요청받은 mail_id -> {mail_id}")

    try:
        claim_reward(mail_id, session['user_id'])

        return jsonify({
            "success": True,
            "message": "보상을 성공적으로 수령했습니다!"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        })


# ============================================================
# 친구 시스템
# ============================================================


@app.route('/friends')
def friends_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    mark_stale_users_offline()

    user_id = session['user_id']
    tab = request.args.get('tab', 'list')
    search_keyword = request.args.get('q', '').strip()

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # =========================
    # 내 친구 목록
    # =========================
    if tab == 'list':
        cursor.execute(
            """
            SELECT
                u.id,
                u.username,
                u.is_online,
                f.status
            FROM friendships f
            JOIN users u ON u.id = f.friend_id
            WHERE f.user_id = %s
              AND f.status = 'accepted'
            ORDER BY u.is_online DESC, u.username ASC
            """,
            (user_id,)
        )
        friends = cursor.fetchall()

    # =========================
    # 친구 찾아보기
    # =========================
    elif tab == 'search':
        if search_keyword:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.is_online,
                    (
                        SELECT f2.status
                        FROM friendships f2
                        WHERE
                            (
                                f2.user_id = %s
                                AND f2.friend_id = u.id
                            )
                            OR
                            (
                                f2.user_id = u.id
                                AND f2.friend_id = %s
                            )
                        ORDER BY
                            CASE
                                WHEN f2.status = 'accepted' THEN 1
                                WHEN f2.status = 'pending' THEN 2
                                ELSE 3
                            END
                        LIMIT 1
                    ) AS relation_status
                FROM users u
                WHERE u.id != %s
                  AND u.username LIKE %s
                ORDER BY u.is_online DESC, u.username ASC
                """,
                (user_id, user_id, user_id, f"%{search_keyword}%")
            )

        else:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.is_online,
                    (
                        SELECT f2.status
                        FROM friendships f2
                        WHERE
                            (
                                f2.user_id = %s
                                AND f2.friend_id = u.id
                            )
                            OR
                            (
                                f2.user_id = u.id
                                AND f2.friend_id = %s
                            )
                        ORDER BY
                            CASE
                                WHEN f2.status = 'accepted' THEN 1
                                WHEN f2.status = 'pending' THEN 2
                                ELSE 3
                            END
                        LIMIT 1
                    ) AS relation_status
                FROM users u
                WHERE u.id != %s
                ORDER BY u.is_online DESC, u.username ASC
                """,
                (user_id, user_id, user_id)
            )

        friends = cursor.fetchall()

    # =========================
    # 받은 친구 요청
    # =========================
    elif tab == 'requests':
        cursor.execute(
            """
            SELECT
                f.id AS friendship_id,
                u.id,
                u.username,
                u.is_online,
                f.status
            FROM friendships f
            JOIN users u ON u.id = f.user_id
            WHERE f.friend_id = %s
              AND f.status = 'pending'
            ORDER BY u.username ASC
            """,
            (user_id,)
        )
        friends = cursor.fetchall()

    # =========================
    # 친구 메시지
    # =========================
    elif tab == 'message':
        cursor.execute(
            """
            SELECT
                u.id,
                u.username,
                u.is_online,
                f.status
            FROM friendships f
            JOIN users u ON u.id = f.friend_id
            WHERE f.user_id = %s
              AND f.status = 'accepted'
            ORDER BY u.is_online DESC, u.username ASC
            """,
            (user_id,)
        )
        friends = cursor.fetchall()

    # =========================
    # 잘못된 탭이면 내 친구 목록
    # =========================
    else:
        tab = 'list'

        cursor.execute(
            """
            SELECT
                u.id,
                u.username,
                u.is_online,
                f.status
            FROM friendships f
            JOIN users u ON u.id = f.friend_id
            WHERE f.user_id = %s
              AND f.status = 'accepted'
            ORDER BY u.is_online DESC, u.username ASC
            """,
            (user_id,)
        )
        friends = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        'friends.html',
        friends=friends,
        tab=tab,
        search_keyword=search_keyword
    )

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """
    사용자가 페이지를 열어둔 상태인지 주기적으로 확인하는 API.
    """
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        }), 401

    update_user_last_seen(session['user_id'])

    return jsonify({
        "success": True
    })

@app.route('/api/friend/add', methods=['POST'])
def add_friend_api():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        })

    data = request.get_json()
    friend_name = data.get('friend_name', '').strip()
    user_id = session['user_id']

    if not friend_name:
        return jsonify({
            "success": False,
            "message": "친구 이름을 입력하세요."
        })

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 상대 유저 확인
        cursor.execute(
            """
            SELECT id, username
            FROM users
            WHERE username = %s
            """,
            (friend_name,)
        )
        friend = cursor.fetchone()

        if not friend:
            return jsonify({
                "success": False,
                "message": "없는 유저입니다."
            })

        friend_id = friend['id']

        if friend_id == user_id:
            return jsonify({
                "success": False,
                "message": "자기 자신에게는 친구 요청을 보낼 수 없습니다."
            })

        # 이미 친구이거나 요청 중인지 확인
        cursor.execute(
            """
            SELECT id, status
            FROM friendships
            WHERE
                (
                    user_id = %s
                    AND friend_id = %s
                )
                OR
                (
                    user_id = %s
                    AND friend_id = %s
                )
            LIMIT 1
            """,
            (user_id, friend_id, friend_id, user_id)
        )
        exists = cursor.fetchone()

        if exists:
            if exists['status'] == 'accepted':
                return jsonify({
                    "success": False,
                    "message": "이미 친구입니다."
                })

            return jsonify({
                "success": False,
                "message": "이미 친구 요청 중입니다."
            })

        cursor.execute(
            """
            INSERT INTO friendships
                (user_id, friend_id, status)
            VALUES
                (%s, %s, 'pending')
            """,
            (user_id, friend_id)
        )

        db.commit()

        return jsonify({
            "success": True,
            "message": "친구 요청을 보냈습니다!"
        })

    except Exception as e:
        db.rollback()

        return jsonify({
            "success": False,
            "message": f"친구 요청 중 오류가 발생했습니다: {str(e)}"
        })

    finally:
        cursor.close()
        db.close()


@app.route('/api/friend/accept', methods=['POST'])
def accept_friend_api():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        })

    data = request.get_json()
    friendship_id = data.get('friendship_id')
    user_id = session['user_id']

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 나에게 온 pending 요청인지 확인
        cursor.execute(
            """
            SELECT id, user_id, friend_id
            FROM friendships
            WHERE id = %s
              AND friend_id = %s
              AND status = 'pending'
            """,
            (friendship_id, user_id)
        )
        req = cursor.fetchone()

        if not req:
            return jsonify({
                "success": False,
                "message": "친구 요청을 찾을 수 없습니다."
            })

        sender_id = req['user_id']
        receiver_id = req['friend_id']

        # 원래 요청을 accepted로 변경
        cursor.execute(
            """
            UPDATE friendships
            SET status = 'accepted'
            WHERE id = %s
            """,
            (friendship_id,)
        )

        # 반대 방향 친구 관계도 추가
        cursor.execute(
            """
            SELECT id
            FROM friendships
            WHERE user_id = %s
              AND friend_id = %s
            """,
            (receiver_id, sender_id)
        )
        reverse = cursor.fetchone()

        if reverse:
            cursor.execute(
                """
                UPDATE friendships
                SET status = 'accepted'
                WHERE id = %s
                """,
                (reverse['id'],)
            )

        else:
            cursor.execute(
                """
                INSERT INTO friendships
                    (user_id, friend_id, status)
                VALUES
                    (%s, %s, 'accepted')
                """,
                (receiver_id, sender_id)
            )

        db.commit()

        return jsonify({
            "success": True,
            "message": "친구 요청을 수락했습니다!"
        })

    except Exception as e:
        db.rollback()

        return jsonify({
            "success": False,
            "message": f"친구 요청 수락 중 오류가 발생했습니다: {str(e)}"
        })

    finally:
        cursor.close()
        db.close()


@app.route('/api/friend/reject', methods=['POST'])
def reject_friend_api():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        })

    data = request.get_json()
    friendship_id = data.get('friendship_id')
    user_id = session['user_id']

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            DELETE FROM friendships
            WHERE id = %s
              AND friend_id = %s
              AND status = 'pending'
            """,
            (friendship_id, user_id)
        )

        db.commit()

        return jsonify({
            "success": True,
            "message": "친구 요청을 거절했습니다."
        })

    except Exception as e:
        db.rollback()

        return jsonify({
            "success": False,
            "message": f"친구 요청 거절 중 오류가 발생했습니다: {str(e)}"
        })

    finally:
        cursor.close()
        db.close()


# ============================================================
# 채팅 시스템
# ============================================================

@app.route('/chat')
def chat_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    target = request.args.get('target', '').strip()

    if not target:
        return "<script>alert('대화 상대를 찾을 수 없습니다.'); location.href='/friends?tab=message';</script>"

    return render_template('chat.html', target=target)


@app.route('/api/send_message', methods=['POST'])
def send_message():
    if 'user_id' not in session:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        })

    data = request.get_json()
    sender_id = session['user_id']
    target_name = data.get('target', '').strip()
    content = data.get('content', '').strip()

    if not target_name:
        return jsonify({
            "success": False,
            "message": "대화 상대를 찾을 수 없습니다."
        })

    if not content:
        return jsonify({
            "success": False,
            "message": "메시지를 입력하세요."
        })

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 상대방 찾기
        cursor.execute(
            """
            SELECT id
            FROM users
            WHERE username = %s
            """,
            (target_name,)
        )
        receiver = cursor.fetchone()

        if not receiver:
            return jsonify({
                "success": False,
                "message": "상대 유저를 찾을 수 없습니다."
            })

        receiver_id = receiver['id']

        # 친구 관계 확인
        cursor.execute(
            """
            SELECT id
            FROM friendships
            WHERE user_id = %s
              AND friend_id = %s
              AND status = 'accepted'
            """,
            (sender_id, receiver_id)
        )
        friendship = cursor.fetchone()

        if not friendship:
            return jsonify({
                "success": False,
                "message": "친구에게만 메시지를 보낼 수 있습니다."
            })

        # 링크 미리보기 결과 생성
        preview_result = run_message_preview(content)

        # 메시지 저장
        # 미리보기 결과는 별도 메시지로 저장하지 않고,
        # 원래 메시지의 preview_result 컬럼에 같이 저장한다.
        cursor.execute(
            """
            INSERT INTO messages
                (sender_id, receiver_id, content, preview_result)
            VALUES
                (%s, %s, %s, %s)
            """,
            (sender_id, receiver_id, content, preview_result)
        )

        db.commit()

        return jsonify({
            "success": True,
            "message": "메시지를 보냈습니다."
        })

    except Exception as e:
        db.rollback()

        return jsonify({
            "success": False,
            "message": f"메시지 전송 중 오류가 발생했습니다: {str(e)}"
        })

    finally:
        cursor.close()
        db.close()


@app.route('/api/get_messages', methods=['GET'])
def get_messages():
    if 'user_id' not in session:
        return jsonify({
                "success": False,
            "message": "로그인이 필요합니다.",
            "messages": []
        })

    target_name = request.args.get('target', '').strip()
    sender_id = session['user_id']

    if not target_name:
        return jsonify({
            "success": False,
            "message": "대화 상대를 찾을 수 없습니다.",
            "messages": []
        })

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        # 상대방 찾기
        cursor.execute(
            """
            SELECT id
            FROM users
            WHERE username = %s
            """,
            (target_name,)
        )
        target_user = cursor.fetchone()

        if not target_user:
            return jsonify({
                "success": False,
                "message": "상대 유저를 찾을 수 없습니다.",
                "messages": []
            })

        receiver_id = target_user['id']

                # 대화 내역 조회
        cursor.execute(
            """
            SELECT
                m.message_id AS id,
                m.content,
                m.preview_result,
                CAST(m.created_at AS CHAR) AS created_at,
                u.username AS sender_name,
                CASE
                    WHEN m.sender_id = %s THEN 1
                    ELSE 0
                END AS is_me
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE
                (
                    m.sender_id = %s
                    AND m.receiver_id = %s
                )
                OR
                (
                    m.sender_id = %s
                    AND m.receiver_id = %s
                )
            ORDER BY m.created_at ASC, m.message_id ASC
            """,
            (sender_id, sender_id, receiver_id, receiver_id, sender_id)
        )

        messages = cursor.fetchall()

        return jsonify({
            "success": True,
            "messages": messages
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"메시지 조회 중 오류가 발생했습니다: {str(e)}",
            "messages": []
        })

    finally:
        cursor.close()
        db.close()


# ============================================================
# 관리자 리포트 / 테스트용 관리자 아이템 등록
# ============================================================

@app.route('/admin/analytics')
def admin_analytics():
    with open(os.path.join(app.template_folder, 'report.html'), 'r', encoding='utf-8') as f:
        return f.read()


@app.route('/admin/add_item', methods=['POST'])
def add_shop_item():
    name = request.form.get('name')
    price = request.form.get('price')

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute(
        """
        INSERT INTO shop_items
            (item_name, price)
        VALUES
            (%s, %s)
        """,
        (name, price)
    )

    db.commit()
    cursor.close()
    db.close()

    return "<script>alert('상점 아이템이 등록되었습니다!'); location.href='/admin/shop';</script>"



# ============================================================
# 튜토리얼 페이지
# ============================================================

@app.route('/tutorial')
def tutorial_page():
    """
    유저 편의 시스템용 튜토리얼 페이지

    게임 진행 방법, 상점, 쿠폰, 친구, 메시지 사용법을 안내한다.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    return render_template('tutorial.html')

# ============================================================
# 공지사항 목록 페이지
# ============================================================

@app.route('/notices')
def notices():
    """
    웹게임 사용자용 공지사항 목록 페이지.

    기능:
    - 관리자 페이지에서 등록한 공지사항 목록을 보여준다.
    - 최신 공지가 위에 오도록 정렬한다.
    - 작성된 지 24시간 이내인 공지는 제목 옆에 N 표시를 한다.
    - 첨부파일이 있는 경우 첨부 표시를 한다.
    """

    # 로그인하지 않은 사용자는 로그인 페이지로 이동
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # DB 연결
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 공지사항 목록 조회
    # thumbnail_path: 서버가 자동 생성한 썸네일 경로
    # thumbnail_log: 썸네일 생성 과정에서 발생한 로그
    cursor.execute(
        """
        SELECT
            id,
            title,
            content,
            attachment_name,
            attachment_path,
            thumbnail_path,
            thumbnail_log,
            created_at
        FROM notices
        ORDER BY id DESC
        """
    )

    # 조회 결과 가져오기
    notices = cursor.fetchall()

    # 현재 시간 기준 24시간 이내 공지인지 계산하기 위한 기준 시간
    now = datetime.now()
    one_day_ago = now - timedelta(days=1)

    # 각 공지에 is_new 값을 추가한다.
    # is_new가 True이면 notices.html에서 제목 옆에 N 표시를 한다.
    for notice in notices:
        created_at = notice.get('created_at')

        # created_at이 datetime 타입이면 바로 비교
        if isinstance(created_at, datetime):
            notice['is_new'] = created_at >= one_day_ago

        # created_at이 문자열로 넘어오는 경우를 대비한 처리
        else:
            try:
                notice_time = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S")
                notice['is_new'] = notice_time >= one_day_ago
            except Exception:
                notice['is_new'] = False

    # DB 연결 종료
    cursor.close()
    conn.close()

    # 공지사항 목록 템플릿으로 데이터 전달
    return render_template(
        'notices.html',
        notices=notices
    )


# ============================================================
# 공지사항 상세 페이지
# ============================================================

@app.route('/notices/<int:notice_id>')
def notice_detail(notice_id):
    """
    웹게임 사용자용 공지사항 상세 페이지.

    기능:
    - 선택한 공지사항 1개를 보여준다.
    - 공지 제목, 내용, 작성일을 표시한다.
    - 첨부파일이 있으면 첨부파일 링크를 표시한다.
    - 서버가 자동 생성한 썸네일이 있으면 공지 배너 썸네일을 표시한다.
    - 썸네일 생성 로그가 있으면 이미지 처리 로그를 표시한다.

    실습 시나리오 연결:
    - 관리자 페이지에서 공지 배너 이미지 업로드
    - 서버가 ImageMagick convert 명령으로 썸네일 생성
    - 취약한 이미지 후처리 결과를 이 상세 페이지에서 확인
    """

    # 로그인하지 않은 사용자는 로그인 페이지로 이동
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # DB 연결
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 선택한 공지사항 1개 조회
    cursor.execute(
        """
        SELECT
            id,
            title,
            content,
            attachment_name,
            attachment_path,
            thumbnail_path,
            thumbnail_log,
            created_at
        FROM notices
        WHERE id = %s
        """,
        (notice_id,)
    )

    # 공지사항 1개 가져오기
    notice = cursor.fetchone()

    # DB 연결 종료
    cursor.close()
    conn.close()

    # 존재하지 않는 공지 ID로 접근한 경우 공지 목록으로 이동
    if not notice:
        return redirect(url_for('notices'))

    # 공지사항 상세 템플릿으로 데이터 전달
    return render_template(
        'notice_detail.html',
        notice=notice
        )



# ============================================================
# 서버 실행
# ============================================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
