import mysql.connector
from mysql.connector import pooling
import logging

# DB 연결 정보 설정
db_config = {
    "host": "192.168.219.175",
    "user": "nexan_user",
    "password": "nexan1234!",
    "database": "catquest",
    "port": 3306
}

# 고급화 기술: 서비스 안정성을 위한 MySQL 커넥션 풀 생성
try:
    connection_pool = pooling.MySQLConnectionPool(
        pool_name="nexan_pool",
        pool_size=10,  # 동시에 유지할 연결 통로 개수
        pool_reset_session=True,
        **db_config
    )
    print("🚀 [SUCCESS] MariaDB Connection Pool이 성공적으로 생성되었습니다.")
except mysql.connector.Error as err:
    print(f"❌ [ERROR] DB Pool 생성 실패: {err}")
    exit(1)

def get_db_connection():
    """풀에서 안전하게 DB 커넥션을 꺼내오는 함수"""
    try:
        conn = connection_pool.get_connection()
        return conn
    except mysql.connector.Error as err:
        logging.error(f"DB 연결 실패 (Pool에서 획득 불가): {err}")
        return None
