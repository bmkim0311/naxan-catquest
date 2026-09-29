from services.mail_service import claim_reward

# 테스트: 유저 ID 1번이, 우편 ID 1번을 수령 시도
print("--- 보상 수령 테스트 시작 ---")
claim_reward(1, 1) 
print("--- 테스트 종료 ---")
