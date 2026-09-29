# CatQuest

Flask와 MariaDB를 기반으로 구현한 웹 게임 프로젝트입니다.

## 주요 구성
- 사용자 회원가입 및 로그인
- 3단계 맵 기반 게임 진행
- 상점 및 아이템 구매
- 우편함 및 보상 수령
- 사용자 프로필 및 채팅
- 공지사항 기능
- 관리자 페이지
- MariaDB 연동
- Gunicorn 기반 실행 구성

## 기술 스택
- Python
- Flask
- MariaDB / MySQL Connector
- HTML / CSS / JavaScript
- Gunicorn

## 주요 파일
```text
catquest/
├─ app.py              # 사용자 웹 애플리케이션
├─ admin_app.py        # 관리자 웹 애플리케이션
├─ db.py               # DB 연결 및 커넥션 풀
├─ private_db.py       # 별도 DB 연결
├─ game_logic.py       # 게임 맵 및 진행 로직
├─ services/           # 상점·우편 등 서비스 로직
├─ templates/          # 사용자 화면 템플릿
├─ static/             # 정적 파일 및 업로드 리소스
└─ requirements.txt    # Python 의존성
```

> 포트폴리오 정리를 위해 Git 내부 기록, 로컬 가상환경, 캐시, 실행 로그 및 백업 파일만 제외했습니다.  
> 애플리케이션 소스 코드와 화면/기능 구성은 수정하지 않았습니다.
