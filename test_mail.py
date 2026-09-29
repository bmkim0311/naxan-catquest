import mysql.connector

# DB 연결 정보 (본인의 상황에 맞게 수정하세요)
db = mysql.connector.connect(
  host="localhost", # DB 서버 IP
  user="root",            # DB 아이디
  password="1234", # DB 비밀번호
  database="catquest"
)

cursor = db.cursor()

# 테스트 우편 데이터 넣기
sql = "INSERT INTO mail_box (user_id, type, content) VALUES (%s, %s, %s)"
val = (1, 'GIFT', '오픈 기념 선물: 포션 5개')

cursor.execute(sql, val)
db.commit()

print("우편이 성공적으로 발송되었습니다!")
