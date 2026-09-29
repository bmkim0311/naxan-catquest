import os
import logging
import mysql.connector
from mysql.connector import pooling


# ============================================================
# 개인정보 DB 서버 연결 설정
# - 사용자 개인정보 DB와 관리자 개인정보 DB는 같은 서버에 둔다.
# - 하지만 DB 이름과 DB 계정은 서로 다르게 분리한다.
# ============================================================

PRIVACY_DB_HOST = os.environ.get("PRIVACY_DB_HOST", "192.168.219.119")
PRIVACY_DB_PORT = int(os.environ.get("PRIVACY_DB_PORT", "3306"))

user_private_db_config = {
    "host": PRIVACY_DB_HOST,
    "port": PRIVACY_DB_PORT,
    "user": os.environ.get("USER_PRIVATE_DB_USER", "user_private_user"),
    "password": os.environ.get("USER_PRIVATE_DB_PASSWORD", "userprivate1234!"),
    "database": os.environ.get("USER_PRIVATE_DB_NAME", "catquest_user_private"),
    "charset": "utf8mb4",
}

admin_private_db_config = {
    "host": PRIVACY_DB_HOST,
    "port": PRIVACY_DB_PORT,
    "user": os.environ.get("ADMIN_PRIVATE_DB_USER", "admin_private_user"),
    "password": os.environ.get("ADMIN_PRIVATE_DB_PASSWORD", "adminprivate1234!"),
    "database": os.environ.get("ADMIN_PRIVATE_DB_NAME", "catquest_admin_private"),
    "charset": "utf8mb4",
}

try:
    user_private_connection_pool = pooling.MySQLConnectionPool(
        pool_name="nexan_user_private_pool",
        pool_size=5,
        pool_reset_session=True,
        **user_private_db_config,
    )

    admin_private_connection_pool = pooling.MySQLConnectionPool(
        pool_name="nexan_admin_private_pool",
        pool_size=5,
        pool_reset_session=True,
        **admin_private_db_config,
    )

    print("🔐 [SUCCESS] User/Admin Private DB Connection Pools 생성 완료")

except mysql.connector.Error as err:
    print(f"❌ [ERROR] Private DB Pool 생성 실패: {err}")
    exit(1)


def get_user_private_db_connection():
    """회원가입 때 사용하는 일반 사용자 개인정보 DB 연결"""
    try:
        return user_private_connection_pool.get_connection()
    except mysql.connector.Error as err:
        logging.error(f"User Private DB 연결 실패: {err}")
        return None


def get_admin_private_db_connection():
    """관리자 이메일/2차 인증코드 저장용 관리자 개인정보 DB 연결"""
    try:
        return admin_private_connection_pool.get_connection()
    except mysql.connector.Error as err:
        logging.error(f"Admin Private DB 연결 실패: {err}")
        return None


# 기존 app.py가 get_private_db_connection()을 이미 사용 중이므로
# 회원가입 코드가 안 깨지도록 기존 함수명은 사용자 개인정보 DB로 연결되게 유지한다.
def get_private_db_connection():
    return get_user_private_db_connection()
