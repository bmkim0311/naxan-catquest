import mysql.connector
from db import get_db_connection

def get_unread_mails(user_id):
    db = get_db_connection()
    if db is None:
        return []
    try:
        cursor = db.cursor(dictionary=True)
        query = "SELECT * FROM mail_box WHERE user_id = %s AND is_claimed = 0"
        cursor.execute(query, (user_id,))
        mails = cursor.fetchall()
        return mails
    finally:
        db.close()

def claim_reward(mail_id, user_id):
    db = get_db_connection()
    if db is None:
        raise Exception("DB 연결 실패")
    
    cursor = db.cursor()
    try:
        db.start_transaction()
        update_query = "UPDATE mail_box SET is_claimed = 1 WHERE mail_id = %s AND user_id = %s AND is_claimed = 0"
        cursor.execute(update_query, (mail_id, user_id))
        if cursor.rowcount == 0:
            raise Exception("이미 수령했거나 없는 우편입니다.")
        item_query = "INSERT INTO inventory (user_id, item_name) VALUES (%s, '강화석')"
        cursor.execute(item_query, (user_id,))
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        cursor.close()
        db.close()

def get_inventory(user_id):
    db = get_db_connection()
    if db is None:
        return []
    try:
        cursor = db.cursor(dictionary=True)
        query = "SELECT * FROM inventory WHERE user_id = %s"
        cursor.execute(query, (user_id,))
        items = cursor.fetchall()
        return items
    finally:
        db.close()
