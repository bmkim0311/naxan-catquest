# Flask에서 필요한 기능들을 가져온다.
# Flask: 웹 서버 생성
# render_template_string: HTML 문자열을 바로 렌더링
# request: 사용자가 보낸 POST/GET 데이터 확인
from flask import Flask, render_template_string, request, redirect, session

# MariaDB/MySQL 연결을 위한 모듈
import mysql.connector

# 관리자 비밀번호 해시 검증, 이메일 인증코드 생성/검증에 사용
import bcrypt
import secrets
import hashlib
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from private_db import get_admin_private_db_connection

# 공지사항 첨부파일 업로드 폴더 생성을 위해 사용
import os

# 공지 배너 이미지 썸네일 생성을 위해 서버 명령 실행에 사용
# 실습에서는 취약한 이미지 후처리 로직을 만들기 위해 사용한다.
import subprocess


# Flask 앱 생성
app = Flask(__name__)
app.secret_key = os.environ.get('ADMIN_SECRET_KEY', 'nexan-admin-dev-secret-key')

# 관리자 로그인 세션 유지 시간
# 2차 인증까지 완료한 관리자는 6시간 동안 로그인 유지
app.permanent_session_lifetime = timedelta(hours=6)

# =========================
# 공지사항 첨부파일 업로드 폴더 설정
# =========================

# 업로드된 공지 첨부파일이 저장될 폴더
# static 경로 아래에 저장되기 때문에 웹에서 직접 접근할 수 있다.
UPLOAD_FOLDER = "static/uploads/notices"

# 업로드된 공지 배너 이미지 썸네일이 저장될 폴더
THUMBNAIL_FOLDER = "static/uploads/notices/thumbs"

# 업로드 폴더가 없으면 자동으로 생성한다.
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 썸네일 폴더가 없으면 자동으로 생성한다.
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)


# =========================
# DB 연결 함수
# =========================

def get_db_connection():
    """
    Cat Quest 게임 DB에 접속하는 함수.
    관리자 페이지에서 공지사항 등록, 아이템 등록/수정/삭제를 할 때 사용한다.

    utf8mb4를 사용해야 아이템 아이콘에 이모지(🎁, ⚔️, 🧪 등)를 저장할 수 있다.
    """

    return mysql.connector.connect(
        host='192.168.219.175',
        port=3306,
        user='nexan_user',
        password='nexan1234!',
        database='catquest',
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )

# =========================
# 관리자 로그인 / 이메일 2차 인증 설정
# =========================
EMAIL_CODE_SECRET = os.environ.get("EMAIL_CODE_SECRET", "nexan-admin-email-code-secret")

VERIFY_PAGE_HTML = """
<style>
    body { margin:0; min-height:100vh; background:#0f172a; color:#e2e8f0; font-family:'Segoe UI', sans-serif; display:flex; align-items:center; justify-content:center; }
    .box { width:380px; background:#1e293b; border:1px solid #334155; border-radius:12px; padding:30px; box-sizing:border-box; }
    h2 { margin-top:0; color:#38bdf8; text-align:center; }
    p { color:#94a3b8; text-align:center; line-height:1.5; }
    input { width:100%; padding:14px; margin-top:10px; box-sizing:border-box; background:#0f172a; color:white; border:1px solid #334155; border-radius:6px; text-align:center; font-size:20px; letter-spacing:6px; }
    button { width:100%; margin-top:20px; padding:12px; border:none; border-radius:6px; background:#38bdf8; color:#020617; font-weight:bold; cursor:pointer; }
    .error { margin-top:15px; color:#fca5a5; background:rgba(239,68,68,0.12); padding:10px; border-radius:6px; text-align:center; }
</style>
<div class="box">
    <h2>관리자 2차 인증</h2>
    <p>관리자 개인정보 DB에 등록된 이메일로 발송된<br>6자리 인증코드를 입력하세요.</p>
    <form method="POST">
        <input type="text" name="code" maxlength="6" pattern="[0-9]{6}" placeholder="000000" autocomplete="off" required>
        <button type="submit">인증하기</button>
    </form>
    {% if error %}
        <div class="error">{{ error }}</div>
    {% endif %}
</div>
"""


def admin_required():
    """관리자 2차 인증까지 끝난 상태인지 확인한다."""
    return "admin_id" in session


def hash_email_code(code):
    raw = f"{code}:{EMAIL_CODE_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_admin_by_username(username):
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT id, username, password
        FROM admins
        WHERE username = %s
        """,
        (username,)
    )

    admin = cursor.fetchone()
    cursor.close()
    db.close()
    return admin


def get_admin_private_info(admin_id):
    db = get_admin_private_db_connection()

    if db is None:
        return None

    cursor = db.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT admin_id, name, email, phone
        FROM admin_private_info
        WHERE admin_id = %s
        """,
        (admin_id,)
    )

    info = cursor.fetchone()
    cursor.close()
    db.close()
    return info


def create_admin_email_code(admin_id):
    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = hash_email_code(code)
    expires_at = datetime.now() + timedelta(minutes=5)

    db = get_admin_private_db_connection()
    cursor = db.cursor()

    cursor.execute(
        """
        UPDATE admin_login_codes
        SET used = 1
        WHERE admin_id = %s AND used = 0
        """,
        (admin_id,)
    )

    cursor.execute(
        """
        INSERT INTO admin_login_codes
            (admin_id, code_hash, expires_at, used, attempts)
        VALUES
            (%s, %s, %s, 0, 0)
        """,
        (admin_id, code_hash, expires_at)
    )

    db.commit()
    cursor.close()
    db.close()
    return code


def send_admin_login_code(email, code):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_from = os.environ.get("MAIL_FROM", smtp_user or "admin@nexan.local")

    subject = "[Nexan Admin] 관리자 로그인 인증코드"
    body = f"""
Nexan 관리자 페이지 로그인 인증코드입니다.

인증코드: {code}

이 코드는 5분 동안만 사용할 수 있습니다.
본인이 요청하지 않았다면 즉시 관리자에게 알려주세요.
"""

    if not smtp_host or not smtp_user or not smtp_password:
        app.logger.warning("=" * 60)
        app.logger.warning("[개발용 관리자 이메일 인증코드]")
        app.logger.warning(f"수신자: {email}")
        app.logger.warning(f"코드: {code}")
        app.logger.warning("=" * 60)
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = email

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


def verify_admin_email_code(admin_id, input_code):
    input_hash = hash_email_code(input_code)

    db = get_admin_private_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT id, code_hash, expires_at, used, attempts
        FROM admin_login_codes
        WHERE admin_id = %s AND used = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (admin_id,)
    )

    row = cursor.fetchone()

    if not row:
        cursor.close()
        db.close()
        return False, "인증코드가 없습니다. 다시 로그인해주세요."

    if row["expires_at"] < datetime.now():
        cursor.execute("UPDATE admin_login_codes SET used = 1 WHERE id = %s", (row["id"],))
        db.commit()
        cursor.close()
        db.close()
        return False, "인증코드가 만료되었습니다. 다시 로그인해주세요."

    if row["attempts"] >= 5:
        cursor.execute("UPDATE admin_login_codes SET used = 1 WHERE id = %s", (row["id"],))
        db.commit()
        cursor.close()
        db.close()
        return False, "인증 시도 횟수를 초과했습니다. 다시 로그인해주세요."

    if row["code_hash"] != input_hash:
        cursor.execute(
            """
            UPDATE admin_login_codes
            SET attempts = attempts + 1
            WHERE id = %s
            """,
            (row["id"],)
        )
        db.commit()
        cursor.close()
        db.close()
        return False, "인증코드가 올바르지 않습니다."

    cursor.execute("UPDATE admin_login_codes SET used = 1 WHERE id = %s", (row["id"],))
    db.commit()
    cursor.close()
    db.close()
    return True, "인증되었습니다."


@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            return "<script>alert('관리자 아이디와 비밀번호를 입력하세요.'); history.back();</script>"

        admin = get_admin_by_username(username)

        if not admin or not bcrypt.checkpw(password.encode('utf-8'), admin['password'].encode('utf-8')):
            return "<script>alert('관리자 아이디 또는 비밀번호가 틀렸습니다.'); history.back();</script>"

        admin_private = get_admin_private_info(admin['id'])

        if not admin_private:
            return "<script>alert('관리자 개인정보가 등록되어 있지 않습니다.'); history.back();</script>"

        code = create_admin_email_code(admin['id'])
        send_admin_login_code(admin_private['email'], code)

        session['admin_pending_id'] = admin['id']
        session['admin_pending_username'] = admin['username']
        session.pop('admin_id', None)
        session.pop('admin_username', None)

        return redirect('/verify')

    return render_template_string("""
    <style>
        body { margin:0; min-height:100vh; background:#0f172a; color:#e2e8f0; font-family:'Segoe UI', sans-serif; display:flex; align-items:center; justify-content:center; }
        .box { width:360px; background:#1e293b; border:1px solid #334155; border-radius:12px; padding:30px; box-sizing:border-box; }
        h2 { margin-top:0; color:#38bdf8; text-align:center; }
        label { display:block; margin-top:14px; color:#cbd5e1; }
        input { width:100%; padding:12px; margin-top:6px; box-sizing:border-box; background:#0f172a; color:white; border:1px solid #334155; border-radius:6px; }
        button { width:100%; margin-top:20px; padding:12px; border:none; border-radius:6px; background:#38bdf8; color:#020617; font-weight:bold; cursor:pointer; }
    </style>
    <div class="box">
        <h2>NEXAN Admin Login</h2>
        <form method="POST">
            <label>관리자 아이디</label>
            <input type="text" name="username" required>
            <label>비밀번호</label>
            <input type="password" name="password" required>
            <button type="submit">로그인</button>
        </form>
    </div>
    """)


@app.route('/verify', methods=['GET', 'POST'])
def admin_verify():
    pending_admin_id = session.get('admin_pending_id')

    if not pending_admin_id:
        return redirect('/login')

    if request.method == 'POST':
        code = request.form.get('code', '').strip()

        ok, message = verify_admin_email_code(pending_admin_id, code)

        if not ok:
            return render_template_string(VERIFY_PAGE_HTML, error=message)

        # 2차 인증까지 성공한 관리자 세션은 영구 세션으로 설정
        session.permanent = True

        session['admin_id'] = pending_admin_id
        session['admin_username'] = session.get('admin_pending_username')
        session.pop('admin_pending_id', None)
        session.pop('admin_pending_username', None)
        
        return redirect('/')

    return render_template_string(VERIFY_PAGE_HTML, error=None)


@app.route('/logout')
def admin_logout():
    session.clear()
    return redirect('/login')


# =========================
# 관리자 페이지 공통 레이아웃
# =========================
def get_base_layout(content):
    """
    관리자 페이지 전체 공통 틀을 만드는 함수.
    왼쪽 사이드바와 오른쪽 메인 영역을 공통으로 사용한다.
    content에는 각 페이지의 실제 내용이 들어간다.
    """

    return f'''
    <style>
        /* 전체 화면 기본 스타일 */
        body {{
            margin: 0;
            display: flex;
            font-family: 'Segoe UI', sans-serif;
            background: #0f172a;
            color: #e2e8f0;
        }}

        /* 왼쪽 관리자 메뉴 */
        .sidebar {{
            width: 240px;
            height: 100vh;
            background: #1e293b;
            padding: 20px;
            border-right: 1px solid #334155;
            box-sizing: border-box;
        }}

        /* 사이드바 제목 */
        .sidebar h2 {{
            color: #38bdf8;
            font-size: 1.2rem;
        }}

        /* 사이드바 메뉴 링크 */
        .sidebar a {{
            display: block;
            color: #94a3b8;
            padding: 10px;
            text-decoration: none;
            border-radius: 5px;
            margin-bottom: 5px;
        }}

        /* 사이드바 메뉴 hover */
        .sidebar a:hover {{
            background: #334155;
            color: white;
        }}

        /* 오른쪽 메인 영역 */
        .main {{
            flex: 1;
            padding: 40px;
        }}

        /* 페이지 내용 카드 */
        .card {{
            background: #1e293b;
            padding: 25px;
            border-radius: 8px;
            border: 1px solid #334155;
        }}

        /* 입력창 공통 스타일 */
        input, textarea {{
            background: #0f172a;
            color: white;
            border: 1px solid #334155;
            padding: 8px;
            border-radius: 4px;
        }}

        /* 버튼 공통 스타일 */
        button {{
            cursor: pointer;
            border-radius: 4px;
            font-weight: bold;
        }}
    </style>

    <!-- 왼쪽 사이드바 메뉴 -->
    <div class="sidebar">
        <h2>NEXAN Admin</h2>

        <!-- 관리자 대시보드 이동 -->
        <a href="/">대시보드</a>

        <!-- 공지사항 관리 페이지 이동 -->
        <a href="/notice">공지사항 관리</a>

        <!-- 게임 업그레이드 관리 페이지 이동 -->
        <a href="/upgrade">게임 업그레이드</a>

        <!-- 로그아웃 -->
        <a href="/logout">로그아웃</a>
    </div>

    <!-- 오른쪽 메인 내용 영역 -->
    <div class="main">
        <div class="card">{content}</div>
    </div>
    '''


# =========================
# 관리자 메인 대시보드
# =========================
@app.route('/')
def admin_home():
    if not admin_required():
        return redirect('/login')

    """
    CatQuest 게임 운영 관리자 메인 화면.

    기존 기능은 유지하면서,
    관리자 페이지가 실제 게임회사 운영툴처럼 보이도록 구성한다.

    표시 내용:
    - 전체 유저 수
    - 등록된 공지 수
    - 상점 아이템 수
    - 서버 운영 상태
    - 운영자 빠른 관리 메뉴
    - 게임 운영 브리핑
    """

    # 기본 통계값
    stats = {
        "total_users": 0,
        "notice_count": 0,
        "shop_item_count": 0,
        "server_status": "ONLINE",
        "security_status": "2FA ENABLED"
    }

    # DB에서 실제 운영 통계 가져오기
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # 전체 유저 수
        cursor.execute("SELECT COUNT(*) AS cnt FROM users")
        result = cursor.fetchone()
        stats["total_users"] = result["cnt"] if result else 0

        # 공지사항 수
        cursor.execute("SELECT COUNT(*) AS cnt FROM notices")
        result = cursor.fetchone()
        stats["notice_count"] = result["cnt"] if result else 0

        # 상점 아이템 수
        cursor.execute("SELECT COUNT(*) AS cnt FROM shop_items")
        result = cursor.fetchone()
        stats["shop_item_count"] = result["cnt"] if result else 0

        cursor.close()
        db.close()

    except Exception as e:
        # DB 통계 조회 실패 시에도 관리자 페이지 자체는 열리게 한다.
        print(f"[관리자 대시보드 통계 조회 오류] {e}")

    return get_base_layout(f'''
        <style>
            .gm-header {{
                margin-bottom: 30px;
                padding: 26px;
                border-radius: 18px;
                background:
                    linear-gradient(135deg, rgba(56,189,248,0.18), rgba(15,23,42,0.95)),
                    radial-gradient(circle at top right, rgba(251,191,36,0.18), transparent 35%);
                border: 1px solid #334155;
                position: relative;
                overflow: hidden;
            }}

            .gm-header::after {{
                content: "CATQUEST";
                position: absolute;
                right: 24px;
                bottom: -14px;
                font-size: 4.2rem;
                font-weight: 900;
                color: rgba(148,163,184,0.07);
                letter-spacing: 4px;
            }}

            .gm-title {{
                margin: 0;
                font-size: 2rem;
                color: #f8fafc;
                letter-spacing: 1px;
            }}

            .gm-subtitle {{
                margin-top: 8px;
                color: #94a3b8;
                line-height: 1.6;
            }}

            .gm-badge-row {{
                margin-top: 18px;
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
            }}

            .gm-badge {{
                padding: 7px 12px;
                border-radius: 999px;
                font-size: 0.78rem;
                font-weight: 800;
                border: 1px solid #334155;
                background: rgba(15,23,42,0.75);
                color: #cbd5e1;
            }}

            .gm-badge.online {{
                color: #34d399;
                border-color: rgba(52,211,153,0.55);
                background: rgba(16,185,129,0.12);
            }}

            .gm-badge.security {{
                color: #38bdf8;
                border-color: rgba(56,189,248,0.55);
                background: rgba(56,189,248,0.12);
            }}

            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
                gap: 18px;
                margin-bottom: 26px;
            }}

            .stat-card {{
                background: linear-gradient(145deg, #1e293b, #0f172a);
                border: 1px solid #334155;
                border-radius: 18px;
                padding: 24px;
                position: relative;
                overflow: hidden;
                min-height: 120px;
            }}

            .stat-card::after {{
                position: absolute;
                right: 16px;
                bottom: 10px;
                font-size: 3.4rem;
                opacity: 0.12;
            }}

            .stat-card.users::after {{
                content: "🐾";
            }}

            .stat-card.notice::after {{
                content: "📢";
            }}

            .stat-card.shop::after {{
                content: "🛒";
            }}

            .stat-card.server::after {{
                content: "🛡️";
            }}

            .stat-label {{
                color: #94a3b8;
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.7px;
                text-transform: uppercase;
                display: block;
                margin-bottom: 12px;
            }}

            .stat-value {{
                color: #38bdf8;
                font-size: 2rem;
                font-weight: 900;
            }}

            .stat-desc {{
                margin-top: 8px;
                color: #64748b;
                font-size: 0.88rem;
            }}

            .gm-layout {{
                display: grid;
                grid-template-columns: 1.5fr 1fr;
                gap: 20px;
            }}

            .gm-panel {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 18px;
                padding: 24px;
            }}

            .gm-panel h3 {{
                margin-top: 0;
                color: #f8fafc;
                font-size: 1.15rem;
            }}

            .briefing-list {{
                display: flex;
                flex-direction: column;
                gap: 12px;
                margin-top: 16px;
            }}

            .briefing-item {{
                padding: 14px;
                border-radius: 12px;
                background: rgba(15,23,42,0.65);
                border: 1px solid #334155;
                color: #cbd5e1;
                line-height: 1.5;
            }}

            .briefing-item strong {{
                color: #38bdf8;
            }}

            .quick-grid {{
                display: grid;
                gap: 12px;
            }}

            .quick-btn {{
                width: 100%;
                padding: 14px 16px;
                border: none;
                border-radius: 12px;
                font-weight: 900;
                text-align: left;
                cursor: pointer;
                background: #334155;
                color: #f8fafc;
                transition: 0.2s;
            }}

            .quick-btn:hover {{
                transform: translateY(-2px);
                background: #475569;
            }}

            .quick-btn.primary {{
                background: #38bdf8;
                color: #020617;
            }}

            .quick-btn.warning {{
                background: #f59e0b;
                color: #111827;
            }}

            .quick-btn.danger {{
                background: #ef4444;
                color: white;
            }}

            .operation-table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 16px;
                overflow: hidden;
                border-radius: 12px;
            }}

            .operation-table th,
            .operation-table td {{
                padding: 13px;
                border-bottom: 1px solid #334155;
                text-align: left;
            }}

            .operation-table th {{
                background: #334155;
                color: #cbd5e1;
                font-size: 0.85rem;
            }}

            .operation-table td {{
                color: #94a3b8;
            }}

            .status-dot {{
                display: inline-block;
                width: 9px;
                height: 9px;
                background: #34d399;
                border-radius: 50%;
                margin-right: 7px;
                box-shadow: 0 0 10px rgba(52,211,153,0.7);
            }}

            @media (max-width: 900px) {{
                .gm-layout {{
                    grid-template-columns: 1fr;
                }}
            }}
        </style>

        <div class="gm-header">
            <h1 class="gm-title">CATQUEST GM COMMAND CENTER</h1>
            <div class="gm-subtitle">
                넥산 CatQuest 운영팀 전용 관리자 페이지입니다.<br>
                공지사항, 상점 아이템, 게임 운영 상태를 한 곳에서 관리합니다.
            </div>

            <div class="gm-badge-row">
                <span class="gm-badge online">● SERVER {stats["server_status"]}</span>
                <span class="gm-badge security">● ADMIN {stats["security_status"]}</span>
                <span class="gm-badge">GM MODE</span>
                <span class="gm-badge">NEXAN INTERNAL</span>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card users">
                <span class="stat-label">Total Players</span>
                <div class="stat-value">{stats["total_users"]}</div>
                <div class="stat-desc">등록된 전체 모험가 계정</div>
            </div>

            <div class="stat-card notice">
                <span class="stat-label">Game Notices</span>
                <div class="stat-value">{stats["notice_count"]}</div>
                <div class="stat-desc">현재 등록된 공지사항</div>
            </div>

            <div class="stat-card shop">
                <span class="stat-label">Shop Items</span>
                <div class="stat-value">{stats["shop_item_count"]}</div>
                <div class="stat-desc">상점에 등록된 판매 아이템</div>
            </div>

            <div class="stat-card server">
                <span class="stat-label">Admin Security</span>
                <div class="stat-value" style="font-size:1.35rem; color:#34d399;">2FA ON</div>
                <div class="stat-desc">관리자 이메일 2차 인증 적용</div>
            </div>
        </div>

        <div class="gm-layout">
            <div class="gm-panel">
                <h3>🎮 실시간 운영 브리핑</h3>

                <div class="briefing-list">
                    <div class="briefing-item">
                        <strong>월드 상태</strong><br>
                        CatQuest 게임 월드는 정상 운영 중입니다. 관리자 인증은 이메일 2차 인증을 통해 보호됩니다.
                    </div>

                    <div class="briefing-item">
                        <strong>공지 관리</strong><br>
                        이벤트, 점검, 업데이트 공지를 등록할 수 있습니다. 공지 배너 이미지는 서버에서 썸네일 처리됩니다.
                    </div>

                    <div class="briefing-item">
                        <strong>상점 관리</strong><br>
                        아이템 이름, 가격, 재고, 일일 구매 제한을 운영자가 직접 조정할 수 있습니다.
                    </div>

                    <div class="briefing-item">
                        <strong>보안 시나리오</strong><br>
                        관리자 페이지는 로그인 후 이메일 인증코드를 요구하여 관리자 계정 탈취 위험을 줄이도록 구성되었습니다.
                    </div>
                </div>

                <table class="operation-table">
                    <tr>
                        <th>운영 항목</th>
                        <th>상태</th>
                        <th>관리 위치</th>
                    </tr>
                    <tr>
                        <td>공지사항</td>
                        <td><span class="status-dot"></span>운영 가능</td>
                        <td>/notice</td>
                    </tr>
                    <tr>
                        <td>상점 아이템</td>
                        <td><span class="status-dot"></span>운영 가능</td>
                        <td>/upgrade</td>
                    </tr>
                    <tr>
                        <td>관리자 2차 인증</td>
                        <td><span class="status-dot"></span>적용됨</td>
                        <td>/login → /verify</td>
                    </tr>
                </table>
            </div>

            <div class="gm-panel">
                <h3>⚡ GM 빠른 작업</h3>

                <div class="quick-grid">
                    <button class="quick-btn primary" onclick="location.href='/notice'">
                        📢 공지사항 등록 / 이벤트 안내
                    </button>

                    <button class="quick-btn" onclick="location.href='/upgrade'">
                        🛒 상점 아이템 가격 / 재고 관리
                    </button>

                    <button class="quick-btn warning" onclick="alert('점검 모드는 아직 화면용 버튼입니다.')">
                        🧰 서버 점검 모드 준비
                    </button>

                    <button class="quick-btn danger" onclick="alert('긴급 공지 기능은 추후 연결 예정입니다.')">
                        🚨 긴급 공지 발송
                    </button>
                </div>

                <div style="margin-top:24px; padding:18px; border-radius:14px; background:rgba(15,23,42,0.65); border:1px solid #334155;">
                    <h3 style="margin-top:0;">🐱 오늘의 GM 메모</h3>
                    <p style="color:#94a3b8; line-height:1.6;">
                        업데이트 전에는 공지사항을 먼저 등록하고,<br>
                        상점 아이템 가격 변경 후에는 실제 게임 화면에서 구매 테스트를 진행하세요.
                    </p>
                </div>

                <div style="margin-top:16px; padding:18px; border-radius:14px; background:rgba(56,189,248,0.08); border:1px solid rgba(56,189,248,0.35);">
                    <h3 style="margin-top:0; color:#38bdf8;">🔐 관리자 보안</h3>
                    <p style="color:#94a3b8; line-height:1.6;">
                        현재 관리자 페이지는 ID/PW 로그인 후<br>
                        개인정보 DB의 관리자 이메일 기반 2차 인증을 요구합니다.
                    </p>
                </div>
            </div>
        </div>
    ''')

# =========================
# 공지사항 관리 페이지
# =========================
@app.route('/notice', methods=['GET', 'POST'])
def manage_notice():
    if not admin_required():
        return redirect('/login')

    """
    공지사항 관리 페이지.

    기능:
    1. 새 공지 등록
    2. 기존 공지 목록 조회
    3. 공지 수정
    4. 공지 삭제
    5. 첨부파일/썸네일 정보 유지 또는 교체

    기존 디자인과 업로드 기능은 유지하면서,
    운영자가 공지를 실제로 관리할 수 있게 확장한다.
    """

    def get_notice_pk_column(cursor):
        """
        notices 테이블의 기본 키 컬럼명을 자동으로 확인한다.
        프로젝트마다 id 또는 notice_id를 쓸 수 있어서 둘 다 대응한다.
        """
        cursor.execute("SHOW COLUMNS FROM notices")
        columns = [row["Field"] for row in cursor.fetchall()]

        if "id" in columns:
            return "id"

        if "notice_id" in columns:
            return "notice_id"

        # 둘 다 없으면 가장 흔한 id를 기본값으로 둔다.
        return "id"

    def process_notice_attachment(upload_file):
        """
        공지 첨부파일 저장 및 썸네일 생성 처리.
        기존 공지 등록 때 사용하던 업로드/썸네일 생성 로직을 함수로 분리했다.
        """

        if not upload_file or not upload_file.filename:
            return None, None, None, None

        # 사용자가 업로드한 원본 파일명
        filename = upload_file.filename

        # 실습용 취약점 유지:
        # secure_filename()을 사용하지 않고 원본 파일명을 그대로 사용한다.
        save_path = os.path.join(UPLOAD_FOLDER, filename)

        # 파일 저장
        upload_file.save(save_path)

        # DB에 저장할 첨부파일 이름과 웹 접근 경로
        attachment_name = filename
        attachment_path = "/" + save_path

        # 썸네일 파일명 생성
        thumb_filename = "thumb_" + filename
        thumb_save_path = os.path.join(THUMBNAIL_FOLDER, thumb_filename)
        thumbnail_path = "/" + thumb_save_path

        # 기존 실습용 취약 이미지 후처리 명령 유지
        command = (
            f"export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
            f"/usr/bin/convert {save_path} -resize 420x220 {thumb_save_path}"
        )

        try:
            completed = subprocess.run(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5
            )

            thumbnail_log = completed.stdout

        except subprocess.TimeoutExpired:
            thumbnail_log = "썸네일 생성 시간이 초과되었습니다."

        except Exception as e:
            thumbnail_log = f"썸네일 생성 중 오류 발생: {e}"

        return attachment_name, attachment_path, thumbnail_path, thumbnail_log

    # DB 연결
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # notices 테이블 기본 키 컬럼명 확인
    pk_column = get_notice_pk_column(cursor)

    # 수정 모드인지 확인
    edit_id = request.args.get("edit_id")

    # =========================
    # POST 요청 처리
    # =========================
    if request.method == 'POST':
        action = request.form.get("action")

        # -------------------------
        # 새 공지 등록
        # -------------------------
        if action == "create":
            title = request.form.get('title')
            content = request.form.get('content')
            upload_file = request.files.get('attachment')

            if not title or not content:
                cursor.close()
                db.close()
                return "<script>alert('제목과 내용을 모두 입력하세요.'); history.back();</script>"

            attachment_name, attachment_path, thumbnail_path, thumbnail_log = process_notice_attachment(upload_file)

            cursor.execute(
                """
                INSERT INTO notices
                (
                    title,
                    content,
                    attachment_name,
                    attachment_path,
                    thumbnail_path,
                    thumbnail_log
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    title,
                    content,
                    attachment_name,
                    attachment_path,
                    thumbnail_path,
                    thumbnail_log
                )
            )

            db.commit()
            cursor.close()
            db.close()

            return "<script>alert('공지사항 등록 완료!'); location.href='/notice';</script>"

        # -------------------------
        # 기존 공지 수정
        # -------------------------
        elif action == "update":
            notice_id = request.form.get("notice_id")
            title = request.form.get("title")
            content = request.form.get("content")
            upload_file = request.files.get("attachment")

            if not notice_id or not title or not content:
                cursor.close()
                db.close()
                return "<script>alert('수정할 공지 정보가 부족합니다.'); history.back();</script>"

            # 새 첨부파일이 있으면 첨부파일/썸네일 정보까지 교체
            if upload_file and upload_file.filename:
                attachment_name, attachment_path, thumbnail_path, thumbnail_log = process_notice_attachment(upload_file)

                cursor.execute(
                    f"""
                    UPDATE notices
                    SET
                        title = %s,
                        content = %s,
                        attachment_name = %s,
                        attachment_path = %s,
                        thumbnail_path = %s,
                        thumbnail_log = %s
                    WHERE {pk_column} = %s
                    """,
                    (
                        title,
                        content,
                        attachment_name,
                        attachment_path,
                        thumbnail_path,
                        thumbnail_log,
                        notice_id
                    )
                )

            # 새 첨부파일이 없으면 제목/내용만 수정하고 기존 첨부파일은 유지
            else:
                cursor.execute(
                    f"""
                    UPDATE notices
                    SET
                        title = %s,
                        content = %s
                    WHERE {pk_column} = %s
                    """,
                    (
                        title,
                        content,
                        notice_id
                    )
                )

            db.commit()
            cursor.close()
            db.close()

            return "<script>alert('공지사항 수정 완료!'); location.href='/notice';</script>"

        # -------------------------
        # 기존 공지 삭제
        # -------------------------
        elif action == "delete":
            notice_id = request.form.get("notice_id")

            if not notice_id:
                cursor.close()
                db.close()
                return "<script>alert('삭제할 공지 ID가 없습니다.'); history.back();</script>"

            cursor.execute(
                f"""
                DELETE FROM notices
                WHERE {pk_column} = %s
                """,
                (notice_id,)
            )

            db.commit()
            cursor.close()
            db.close()

            return "<script>alert('공지사항 삭제 완료!'); location.href='/notice';</script>"

    # =========================
    # GET 요청 처리
    # =========================

    # 수정할 공지 데이터
    edit_notice = None

    if edit_id:
        cursor.execute(
            f"""
            SELECT *
            FROM notices
            WHERE {pk_column} = %s
            """,
            (edit_id,)
        )

        edit_notice = cursor.fetchone()

    # 전체 공지 목록 조회
    cursor.execute(
        f"""
        SELECT *
        FROM notices
        ORDER BY {pk_column} DESC
        """
    )

    notices = cursor.fetchall()

    cursor.close()
    db.close()

    # =========================
    # 수정 모드 입력값
    # =========================
    if edit_notice:
        form_title = edit_notice.get("title", "")
        form_content = edit_notice.get("content", "")
        form_action = "update"
        form_button = "수정 완료"
        form_heading = "공지사항 수정"
        hidden_notice_id = f'<input type="hidden" name="notice_id" value="{edit_notice[pk_column]}">'
        cancel_button = '''
            <button
                type="button"
                onclick="location.href='/notice'"
                style="
                    padding:10px 20px;
                    background:#64748b;
                    color:white;
                    border:none;
                    font-weight:bold;
                    margin-left:8px;
                "
            >
                수정 취소
            </button>
        '''

        current_file_html = ""

        if edit_notice.get("attachment_name"):
            current_file_html = f'''
                <p style="color:#94a3b8; font-size:0.9rem;">
                    현재 첨부파일:
                    <strong>{edit_notice.get("attachment_name")}</strong>
                </p>
            '''

    else:
        form_title = ""
        form_content = ""
        form_action = "create"
        form_button = "등록하기"
        form_heading = "공지사항 등록"
        hidden_notice_id = ""
        cancel_button = ""
        current_file_html = ""

    # =========================
    # 공지 목록 HTML 생성
    # =========================
    if notices:
        notice_rows = ""

        for notice in notices:
            notice_id = notice[pk_column]
            notice_title = notice.get("title", "")
            notice_content = notice.get("content", "")
            attachment_name = notice.get("attachment_name")
            attachment_path = notice.get("attachment_path")
            thumbnail_path = notice.get("thumbnail_path")

            attachment_html = "<span style='color:#64748b;'>없음</span>"

            if attachment_name and attachment_path:
                attachment_html = f'''
                    <a href="{attachment_path}" target="_blank" style="color:#38bdf8;">
                        {attachment_name}
                    </a>
                '''

            thumbnail_html = "<span style='color:#64748b;'>없음</span>"

            if thumbnail_path:
                thumbnail_html = f'''
                    <a href="{thumbnail_path}" target="_blank" style="color:#38bdf8;">
                        썸네일 보기
                    </a>
                '''

            notice_rows += f'''
                <tr style="border-bottom:1px solid #334155;">
                    <td style="padding:14px; color:#94a3b8;">{notice_id}</td>

                    <td style="padding:14px; color:#f8fafc; font-weight:bold;">
                        {notice_title}
                    </td>

                    <td style="padding:14px; color:#94a3b8; max-width:360px;">
                        <div style="
                            white-space:nowrap;
                            overflow:hidden;
                            text-overflow:ellipsis;
                        ">
                            {notice_content}
                        </div>
                    </td>

                    <td style="padding:14px;">
                        {attachment_html}
                    </td>

                    <td style="padding:14px;">
                        {thumbnail_html}
                    </td>

                    <td style="padding:14px;">
                        <button
                            type="button"
                            onclick="location.href='/notice?edit_id={notice_id}'"
                            style="
                                background:#38bdf8;
                                color:#020617;
                                border:none;
                                padding:7px 10px;
                                font-weight:bold;
                                margin-right:6px;
                            "
                        >
                            수정
                        </button>

                        <form method="POST" style="display:inline;">
                            <input type="hidden" name="action" value="delete">
                            <input type="hidden" name="notice_id" value="{notice_id}">

                            <button
                                type="submit"
                                onclick="return confirm('정말 이 공지사항을 삭제하시겠습니까?');"
                                style="
                                    background:#ef4444;
                                    color:white;
                                    border:none;
                                    padding:7px 10px;
                                    font-weight:bold;
                                "
                            >
                                삭제
                            </button>
                        </form>
                    </td>
                </tr>
            '''

    else:
        notice_rows = '''
            <tr>
                <td colspan="6" style="padding:25px; text-align:center; color:#64748b;">
                    등록된 공지사항이 없습니다.
                </td>
            </tr>
        '''

    # =========================
    # 화면 출력
    # =========================
    return get_base_layout(f'''
        <div style="background:#1e293b; padding:20px; border-radius:8px; margin-bottom:22px;">
            <h2 style="color:#38bdf8;">📢 {form_heading}</h2>

            <p style="color:#94a3b8; line-height:1.6;">
                게임 공지사항과 공지 배너 이미지를 등록하거나 수정합니다.<br>
                첨부파일을 새로 업로드하지 않으면 기존 첨부파일은 그대로 유지됩니다.
            </p>

            <form method="POST" enctype="multipart/form-data">
                <input type="hidden" name="action" value="{form_action}">
                {hidden_notice_id}

                <label style="display:block; margin-bottom:6px; color:#cbd5e1;">
                    공지 제목
                </label>

                <input
                    type="text"
                    name="title"
                    value="{form_title}"
                    placeholder="제목을 입력하세요"
                    style="
                        width:100%;
                        margin-bottom:14px;
                        padding:10px;
                        box-sizing:border-box;
                    "
                    required
                >

                <label style="display:block; margin-bottom:6px; color:#cbd5e1;">
                    공지 내용
                </label>

                <textarea
                    name="content"
                    placeholder="공지 내용을 입력하세요"
                    style="
                        width:100%;
                        height:140px;
                        box-sizing:border-box;
                        margin-bottom:14px;
                    "
                    required
                >{form_content}</textarea>

                <label style="display:block; margin-bottom:8px; color:#cbd5e1;">
                    공지 배너 이미지 / 첨부파일 업로드
                </label>

                {current_file_html}

                <input
                    type="file"
                    name="attachment"
                    style="width:100%; margin-bottom:15px;"
                >

                <p style="color:#94a3b8; font-size:0.9rem; line-height:1.6;">
                    PNG, JPG, GIF 등의 공지 배너 이미지를 업로드할 수 있습니다.<br>
                    수정 시 새 파일을 선택하지 않으면 기존 첨부파일을 유지합니다.
                </p>

                <button
                    type="submit"
                    style="
                        padding:10px 20px;
                        background:#38bdf8;
                        color:#020617;
                        border:none;
                        font-weight:bold;
                    "
                >
                    {form_button}
                </button>

                {cancel_button}
            </form>
        </div>

        <div style="background:#1e293b; padding:20px; border-radius:8px;">
            <h2 style="color:#38bdf8;">📋 등록된 공지사항 목록</h2>

            <table style="width:100%; border-collapse:collapse; margin-top:15px;">
                <tr style="background:#334155; text-align:left;">
                    <th style="padding:14px;">ID</th>
                    <th style="padding:14px;">제목</th>
                    <th style="padding:14px;">내용 미리보기</th>
                    <th style="padding:14px;">첨부파일</th>
                    <th style="padding:14px;">썸네일</th>
                    <th style="padding:14px;">관리</th>
                </tr>

                {notice_rows}
            </table>
        </div>
    ''')

# =========================
# 게임 업그레이드 / 상점 아이템 관리 페이지
# =========================
@app.route('/upgrade', methods=['GET', 'POST'])
def game_upgrade():
    if not admin_required():
        return redirect('/login')

    """
    상점 아이템을 더 다양하게 관리하는 페이지.

    기능:
    1. 신규 아이템 등록
    2. 아이템 이름/가격/재고/일일 제한 수정
    3. 아이템 분류, 등급, 효과 종류, 효과 수치 관리
    4. 필요 레벨, 설명, 아이콘, 판매 상태 관리
    5. 아이템 삭제
    """

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    if request.method == 'POST':
        action = request.form.get('action')

        # =========================
        # 신규 아이템 등록
        # =========================
        if action == 'create':
            item_name = request.form.get('item_name', '').strip()
            price = request.form.get('price', '0')
            stock = request.form.get('stock', '0')
            daily_limit = request.form.get('daily_limit', '1')
            item_category = request.form.get('item_category', '기타')
            rarity = request.form.get('rarity', '일반')
            effect_type = request.form.get('effect_type', 'none')
            effect_value = request.form.get('effect_value', '0')
            required_level = request.form.get('required_level', '1')
            description = request.form.get('description', '').strip()
            icon = request.form.get('icon', '🎁').strip()
            is_active = request.form.get('is_active', '1')

            if not item_name:
                cursor.close()
                db.close()
                return "<script>alert('아이템 이름을 입력하세요.'); history.back();</script>"

            cursor.execute(
                """
                INSERT INTO shop_items
                (
                    item_name,
                    price,
                    stock,
                    daily_limit,
                    item_category,
                    rarity,
                    effect_type,
                    effect_value,
                    required_level,
                    description,
                    icon,
                    is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item_name,
                    price,
                    stock,
                    daily_limit,
                    item_category,
                    rarity,
                    effect_type,
                    effect_value,
                    required_level,
                    description,
                    icon,
                    is_active
                )
            )

            db.commit()

            cursor.close()
            db.close()

            return "<script>alert('아이템 등록 완료!'); location.href='/upgrade';</script>"

        # =========================
        # 기존 아이템 수정
        # =========================
        elif action == 'update':
            item_id = request.form.get('item_id')
            item_name = request.form.get('item_name', '').strip()
            price = request.form.get('price', '0')
            stock = request.form.get('stock', '0')
            daily_limit = request.form.get('daily_limit', '1')
            item_category = request.form.get('item_category', '기타')
            rarity = request.form.get('rarity', '일반')
            effect_type = request.form.get('effect_type', 'none')
            effect_value = request.form.get('effect_value', '0')
            required_level = request.form.get('required_level', '1')
            description = request.form.get('description', '').strip()
            icon = request.form.get('icon', '🎁').strip()
            is_active = request.form.get('is_active', '1')

            if not item_id or not item_name:
                cursor.close()
                db.close()
                return "<script>alert('수정할 아이템 정보가 부족합니다.'); history.back();</script>"

            cursor.execute(
                """
                UPDATE shop_items
                SET
                    item_name = %s,
                    price = %s,
                    stock = %s,
                    daily_limit = %s,
                    item_category = %s,
                    rarity = %s,
                    effect_type = %s,
                    effect_value = %s,
                    required_level = %s,
                    description = %s,
                    icon = %s,
                    is_active = %s
                WHERE item_id = %s
                """,
                (
                    item_name,
                    price,
                    stock,
                    daily_limit,
                    item_category,
                    rarity,
                    effect_type,
                    effect_value,
                    required_level,
                    description,
                    icon,
                    is_active,
                    item_id
                )
            )

            db.commit()

            cursor.close()
            db.close()

            return "<script>alert('아이템 수정 완료!'); location.href='/upgrade';</script>"

        # =========================
        # 아이템 삭제
        # =========================
        elif action == 'delete':
            item_id = request.form.get('item_id')

            if not item_id:
                cursor.close()
                db.close()
                return "<script>alert('삭제할 아이템 ID가 없습니다.'); history.back();</script>"

            cursor.execute(
                "DELETE FROM shop_items WHERE item_id = %s",
                (item_id,)
            )

            db.commit()

            cursor.close()
            db.close()

            return "<script>alert('아이템 삭제 완료!'); location.href='/upgrade';</script>"

    # =========================
    # 아이템 목록 조회
    # =========================
    cursor.execute(
        """
        SELECT
            item_id,
            item_name,
            price,
            stock,
            daily_limit,
            item_category,
            rarity,
            effect_type,
            effect_value,
            required_level,
            description,
            icon,
            is_active
        FROM shop_items
        ORDER BY item_id ASC
        """
    )

    items = cursor.fetchall()

    cursor.close()
    db.close()

    # =========================
    # 아이템 카드 HTML 생성
    # =========================
    item_cards = ""

    if items:
        for item in items:
            active_selected = "selected" if item.get('is_active') == 1 else ""
            inactive_selected = "selected" if item.get('is_active') == 0 else ""

            category = item.get('item_category') or '기타'
            rarity = item.get('rarity') or '일반'
            effect_type = item.get('effect_type') or 'none'
            description = item.get('description') or ''
            icon = item.get('icon') or '🎁'

            item_cards += f'''
                <div class="item-card">
                    <form method="POST">
                        <input type="hidden" name="action" value="update">
                        <input type="hidden" name="item_id" value="{item['item_id']}">

                        <div class="item-card-header">
                            <div class="item-icon">{icon}</div>

                            <div>
                                <div class="item-title">
                                    #{item['item_id']} 
                                    <input
                                        type="text"
                                        name="item_name"
                                        value="{item['item_name']}"
                                        required
                                    >
                                </div>

                                <div class="item-subtitle">
                                    현재 분류: {category} / 등급: {rarity}
                                </div>
                            </div>
                        </div>

                        <div class="item-form-grid">
                            <label>
                                가격
                                <input type="number" name="price" value="{item['price']}" min="0">
                            </label>

                            <label>
                                재고
                                <input type="number" name="stock" value="{item['stock']}" min="0">
                            </label>

                            <label>
                                일일 구매 제한
                                <input type="number" name="daily_limit" value="{item['daily_limit']}" min="1">
                            </label>

                            <label>
                                필요 레벨
                                <input type="number" name="required_level" value="{item.get('required_level') or 1}" min="1">
                            </label>

                            <label>
                                아이템 분류
                                <select name="item_category">
                                    <option value="무기" {"selected" if category == "무기" else ""}>무기</option>
                                    <option value="방어구" {"selected" if category == "방어구" else ""}>방어구</option>
                                    <option value="소모품" {"selected" if category == "소모품" else ""}>소모품</option>
                                    <option value="재화" {"selected" if category == "재화" else ""}>재화</option>
                                    <option value="이벤트" {"selected" if category == "이벤트" else ""}>이벤트</option>
                                    <option value="기타" {"selected" if category == "기타" else ""}>기타</option>
                                </select>
                            </label>

                            <label>
                                등급
                                <select name="rarity">
                                    <option value="일반" {"selected" if rarity == "일반" else ""}>일반</option>
                                    <option value="고급" {"selected" if rarity == "고급" else ""}>고급</option>
                                    <option value="희귀" {"selected" if rarity == "희귀" else ""}>희귀</option>
                                    <option value="영웅" {"selected" if rarity == "영웅" else ""}>영웅</option>
                                    <option value="전설" {"selected" if rarity == "전설" else ""}>전설</option>
                                </select>
                            </label>

                            <label>
                                효과 종류
                                <select name="effect_type">
                                    <option value="none" {"selected" if effect_type == "none" else ""}>효과 없음</option>
                                    <option value="attack_up" {"selected" if effect_type == "attack_up" else ""}>공격력 증가</option>
                                    <option value="hp_up" {"selected" if effect_type == "hp_up" else ""}>체력 증가</option>
                                    <option value="gold_bonus" {"selected" if effect_type == "gold_bonus" else ""}>골드 보너스</option>
                                    <option value="heal" {"selected" if effect_type == "heal" else ""}>체력 회복</option>
                                    <option value="stage_key" {"selected" if effect_type == "stage_key" else ""}>스테이지 해금</option>
                                </select>
                            </label>

                            <label>
                                효과 수치
                                <input type="number" name="effect_value" value="{item.get('effect_value') or 0}">
                            </label>

                            <label>
                                아이콘
                                <input type="text" name="icon" value="{icon}" maxlength="10">
                            </label>

                            <label>
                                판매 상태
                                <select name="is_active">
                                    <option value="1" {active_selected}>판매중</option>
                                    <option value="0" {inactive_selected}>판매중지</option>
                                </select>
                            </label>
                        </div>

                        <label class="desc-label">
                            아이템 설명
                            <textarea name="description">{description}</textarea>
                        </label>

                        <div class="item-actions">
                            <button type="submit" class="btn-save">
                                수정 저장
                            </button>
                    </form>

                            <form method="POST" style="display:inline;">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="item_id" value="{item['item_id']}">

                                <button
                                    type="submit"
                                    class="btn-delete"
                                    onclick="return confirm('정말 이 아이템을 삭제하시겠습니까?');"
                                >
                                    삭제
                                </button>
                            </form>
                        </div>
                </div>
            '''
    else:
        item_cards = '''
            <div style="padding:30px; text-align:center; color:#64748b;">
                등록된 아이템이 없습니다.
            </div>
        '''

    return get_base_layout(f'''
        <style>
            .shop-header {{
                margin-bottom: 24px;
                padding: 24px;
                border-radius: 18px;
                background:
                    linear-gradient(135deg, rgba(56,189,248,0.16), rgba(15,23,42,0.95)),
                    radial-gradient(circle at top right, rgba(251,191,36,0.14), transparent 35%);
                border: 1px solid #334155;
            }}

            .shop-header h2 {{
                margin: 0;
                color: #f8fafc;
                font-size: 1.8rem;
            }}

            .shop-header p {{
                color: #94a3b8;
                line-height: 1.6;
                margin-bottom: 0;
            }}

            .create-box {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 18px;
                padding: 22px;
                margin-bottom: 24px;
            }}

            .create-box h3 {{
                margin-top: 0;
                color: #38bdf8;
            }}

            .form-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 14px;
            }}

            label {{
                color: #cbd5e1;
                font-size: 0.9rem;
                font-weight: bold;
            }}

            input, select, textarea {{
                width: 100%;
                margin-top: 6px;
                padding: 9px;
                box-sizing: border-box;
                background: #0f172a;
                color: white;
                border: 1px solid #334155;
                border-radius: 6px;
            }}

            textarea {{
                min-height: 80px;
                resize: vertical;
            }}

            .btn-create {{
                margin-top: 16px;
                padding: 11px 18px;
                background: #38bdf8;
                color: #020617;
                border: none;
                border-radius: 8px;
                font-weight: 900;
            }}

            .item-list {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
                gap: 18px;
            }}

            .item-card {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 18px;
                padding: 20px;
            }}

            .item-card-header {{
                display: flex;
                gap: 14px;
                align-items: center;
                margin-bottom: 18px;
            }}

            .item-icon {{
                width: 54px;
                height: 54px;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 16px;
                font-size: 1.7rem;
            }}

            .item-title {{
                color: #f8fafc;
                font-weight: 900;
            }}

            .item-title input {{
                display: inline-block;
                width: 220px;
                margin-left: 8px;
            }}

            .item-subtitle {{
                margin-top: 6px;
                color: #64748b;
                font-size: 0.85rem;
            }}

            .item-form-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 12px;
            }}

            .desc-label {{
                display: block;
                margin-top: 12px;
            }}

            .item-actions {{
                margin-top: 16px;
                display: flex;
                gap: 10px;
            }}

            .btn-save {{
                background: #38bdf8;
                color: #020617;
                border: none;
                padding: 10px 14px;
                border-radius: 8px;
                font-weight: 900;
            }}

            .btn-delete {{
                background: #ef4444;
                color: white;
                border: none;
                padding: 10px 14px;
                border-radius: 8px;
                font-weight: 900;
            }}
        </style>

        <div class="shop-header">
            <h2>🛒 CatQuest 아이템 상점 관리</h2>
            <p>
                게임 내 판매 아이템을 등록하고, 가격/재고/등급/효과/판매 상태를 관리합니다.<br>
                공격력 증가, 체력 증가, 회복 아이템, 이벤트 아이템 등 다양한 아이템을 운영할 수 있습니다.
            </p>
        </div>

        <div class="create-box">
            <h3>신규 아이템 등록</h3>

            <form method="POST">
                <input type="hidden" name="action" value="create">

                <div class="form-grid">
                    <label>
                        아이템 이름
                        <input type="text" name="item_name" placeholder="예: 철 검" required>
                    </label>

                    <label>
                        가격
                        <input type="number" name="price" value="100" min="0">
                    </label>

                    <label>
                        재고
                        <input type="number" name="stock" value="10" min="0">
                    </label>

                    <label>
                        일일 구매 제한
                        <input type="number" name="daily_limit" value="1" min="1">
                    </label>

                    <label>
                        아이템 분류
                        <select name="item_category">
                            <option value="무기">무기</option>
                            <option value="방어구">방어구</option>
                            <option value="소모품">소모품</option>
                            <option value="재화">재화</option>
                            <option value="이벤트">이벤트</option>
                            <option value="기타">기타</option>
                        </select>
                    </label>

                    <label>
                        등급
                        <select name="rarity">
                            <option value="일반">일반</option>
                            <option value="고급">고급</option>
                            <option value="희귀">희귀</option>
                            <option value="영웅">영웅</option>
                            <option value="전설">전설</option>
                        </select>
                    </label>

                    <label>
                        효과 종류
                        <select name="effect_type">
                            <option value="none">효과 없음</option>
                            <option value="attack_up">공격력 증가</option>
                            <option value="hp_up">체력 증가</option>
                            <option value="gold_bonus">골드 보너스</option>
                            <option value="heal">체력 회복</option>
                            <option value="stage_key">스테이지 해금</option>
                        </select>
                    </label>

                    <label>
                        효과 수치
                        <input type="number" name="effect_value" value="0">
                    </label>

                    <label>
                        필요 레벨
                        <input type="number" name="required_level" value="1" min="1">
                    </label>

                    <label>
                        아이콘
                        <input type="text" name="icon" value="🎁" maxlength="10">
                    </label>

                    <label>
                        판매 상태
                        <select name="is_active">
                            <option value="1">판매중</option>
                            <option value="0">판매중지</option>
                        </select>
                    </label>
                </div>

                <label style="display:block; margin-top:14px;">
                    아이템 설명
                    <textarea name="description" placeholder="아이템 설명을 입력하세요."></textarea>
                </label>

                <button type="submit" class="btn-create">
                    아이템 등록
                </button>
            </form>
        </div>

        <div class="item-list">
            {item_cards}
        </div>
    ''')

# =========================
# Flask 서버 실행
# =========================
if __name__ == '__main__':
    """
    관리자 페이지 서버 실행 부분.
    host='0.0.0.0'은 외부에서도 접속 가능하게 하는 설정.
    port=8888은 관리자 페이지 접속 포트.
    debug=True는 오류 화면을 자세히 보여주므로 개발 중에는 편하지만,
    실제 운영/시연 때는 debug=False로 바꾸는 것이 안전하다.
    """

    app.run(host='0.0.0.0', port=8888, debug=True)
