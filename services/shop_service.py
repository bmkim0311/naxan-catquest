# db.py 파일 안에 있는 get_db_connection 함수를 가져온다.
# 이 함수는 Cat Quest DB에 접속할 때 사용한다.
from db import get_db_connection


def buy_item(user_id, item_id):
    """
    상점에서 아이템을 구매하는 함수.

    이제 관리자 페이지에서 설정한 아이템 효과가 실제로 유저에게 적용된다.

    적용되는 효과:
    - attack_up   : 유저 level 증가 = 공격력 증가처럼 사용
    - hp_up       : 최대 HP 증가 + 현재 HP도 같이 회복
    - heal        : 현재 HP 회복
    - gold_bonus  : 전투 보상 골드 증가율 증가
    - stage_key   : cleared_stage 증가 = 스테이지 해금
    - none        : 효과 없음, 인벤토리에만 추가
    """

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        db.start_transaction()

        # =========================
        # 1. 아이템 정보 조회
        # =========================
        cursor.execute(
            """
            SELECT
                item_id,
                item_name,
                price,
                daily_limit,
                stock,
                item_category,
                rarity,
                effect_type,
                effect_value,
                required_level,
                description,
                icon,
                is_active
            FROM shop_items
            WHERE item_id = %s
            FOR UPDATE
            """,
            (item_id,)
        )

        item = cursor.fetchone()

        if not item:
            raise Exception("존재하지 않는 아이템입니다.")

        if item["is_active"] != 1:
            raise Exception("현재 판매 중인 아이템이 아닙니다.")

        if item["price"] <= 0:
            raise Exception("잘못된 아이템 가격입니다.")

        if item["stock"] <= 0:
            raise Exception("재고가 부족합니다.")

        # =========================
        # 2. 유저 정보 조회
        # =========================
        cursor.execute(
            """
            SELECT
                id,
                gold,
                level,
                hp,
                max_hp,
                cleared_stage,
                gold_bonus
            FROM users
            WHERE id = %s
            FOR UPDATE
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        if not user:
            raise Exception("존재하지 않는 유저입니다.")

        if user["gold"] < item["price"]:
            raise Exception("골드가 부족합니다.")

        required_level = item.get("required_level") or 1

        if user["level"] < required_level:
            raise Exception(f"필요 레벨이 부족합니다. Lv.{required_level} 이상부터 구매할 수 있습니다.")

        # =========================
        # 3. 오늘 구매 횟수 확인
        # =========================
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM purchase_history
            WHERE user_id = %s
              AND item_name = %s
              AND DATE(purchased_at) = CURDATE()
            """,
            (user_id, item["item_name"])
        )

        today_count = cursor.fetchone()["count"]

        if today_count >= item["daily_limit"]:
            raise Exception(f"하루 구매 제한({item['daily_limit']}개)을 초과했습니다.")

        # =========================
        # 4. 골드 차감 / 재고 감소
        # =========================
        cursor.execute(
            """
            UPDATE users
            SET gold = gold - %s
            WHERE id = %s
            """,
            (item["price"], user_id)
        )

        cursor.execute(
            """
            UPDATE shop_items
            SET stock = stock - 1
            WHERE item_id = %s
            """,
            (item_id,)
        )

        # =========================
        # 5. 아이템 효과 적용
        # =========================
        effect_type = item.get("effect_type") or "none"
        effect_value = item.get("effect_value") or 0

        effect_message = "특별한 효과는 없습니다."

        if effect_type == "attack_up":
            cursor.execute(
                """
                UPDATE users
                SET level = level + %s
                WHERE id = %s
                """,
                (effect_value, user_id)
            )

            effect_message = f"⚔️ 공격력이 {effect_value}만큼 증가했습니다."

        elif effect_type == "hp_up":
            cursor.execute(
                """
                UPDATE users
                SET max_hp = max_hp + %s,
                    hp = hp + %s
                WHERE id = %s
                """,
                (effect_value, effect_value, user_id)
            )

            effect_message = f"❤️ 최대 체력이 {effect_value}만큼 증가했습니다."

        elif effect_type == "heal":
            cursor.execute(
                """
                UPDATE users
                SET hp = LEAST(max_hp, hp + %s)
                WHERE id = %s
                """,
                (effect_value, user_id)
            )

            effect_message = f"🧪 체력을 {effect_value}만큼 회복했습니다."

        elif effect_type == "gold_bonus":
            cursor.execute(
                """
                UPDATE users
                SET gold_bonus = gold_bonus + %s
                WHERE id = %s
                """,
                (effect_value, user_id)
            )

            effect_message = f"💰 전투 골드 보너스가 {effect_value}% 증가했습니다."

        elif effect_type == "stage_key":
            # effect_value가 2면 2단계까지 해금, 3이면 3단계까지 해금
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

            effect_message = f"🗝️ {unlock_stage}단계까지 입장 권한이 해금되었습니다."

        # =========================
        # 6. 인벤토리에 아이템 추가
        # =========================
        cursor.execute(
            """
            INSERT INTO inventory (user_id, item_name)
            VALUES (%s, %s)
            """,
            (user_id, item["item_name"])
        )

        # =========================
        # 7. 구매 기록 저장
        # =========================
        cursor.execute(
            """
            INSERT INTO purchase_history (user_id, item_name, price)
            VALUES (%s, %s, %s)
            """,
            (user_id, item["item_name"], item["price"])
        )

        db.commit()

        return True, f"{item['item_name']} 구매 성공! {effect_message}"

    except Exception as e:
        db.rollback()
        return False, str(e)

    finally:
        cursor.close()
        db.close()
