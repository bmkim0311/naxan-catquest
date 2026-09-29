import mysql.connector

def get_unread_mails(user_id):
    db = mysql.connector.connect(host="localhost", user="root", password="1234", database="catquest")
    cursor = db.cursor(dictionary=True)
    
    # 1. 수령하지 않은 우편만 가져오기
    query = "SELECT * FROM mail_box WHERE user_id = %s AND is_claimed = 0"
    cursor.execute(query, (user_id,))
    mails = cursor.fetchall()
    
    db.close()
    return mails

# 결과 출력 (나중에 웹 UI로 전달될 데이터)
print(get_unread_mails(1))
