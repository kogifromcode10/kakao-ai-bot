
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import turso_serverless

load_dotenv()

app = Flask(__name__)

# --------------------------------
# Turso 연결
# --------------------------------

def get_db():
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")

    if not url or not token:
        raise RuntimeError("Turso 환경변수가 설정되지 않았습니다.")

    return turso_serverless.connect(
        url,
        auth_token=token
    )


# --------------------------------
# 데이터베이스 초기화
# --------------------------------

def init_db():
    conn = get_db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memos_user
            ON memos(user_id)
        """)

        conn.commit()

    finally:
        conn.close()


# 서버 시작 시 테이블 확인
init_db()


# --------------------------------
# 시간
# --------------------------------

def current_time():
    return datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------
# 카카오톡 응답
# --------------------------------

def kakao_response(message):
    return jsonify({
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": message[:1000]
                    }
                }
            ]
        }
    })


# --------------------------------
# 메모 저장
# --------------------------------

def save_memo(user_id, content):
    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO memos
            (user_id, content, created_at)
            VALUES (?, ?, ?)
        """, (user_id, content, current_time()))

        conn.commit()

    finally:
        conn.close()


# --------------------------------
# 메모 목록
# --------------------------------

def get_memos(user_id):
    conn = get_db()

    try:
        cursor = conn.execute("""
            SELECT id, content, created_at
            FROM memos
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (user_id,))

        return cursor.fetchall()

    finally:
        conn.close()


# --------------------------------
# 메모 검색
# --------------------------------

def search_memos(user_id, keyword):
    conn = get_db()

    try:
        cursor = conn.execute("""
            SELECT id, content, created_at
            FROM memos
            WHERE user_id = ?
            AND content LIKE ?
            ORDER BY id DESC
            LIMIT 20
        """, (user_id, f"%{keyword}%"))

        return cursor.fetchall()

    finally:
        conn.close()


# --------------------------------
# 개별 메모 삭제
# --------------------------------

def delete_memo(user_id, memo_id):
    conn = get_db()

    try:
        cursor = conn.execute("""
            SELECT id FROM memos
            WHERE id = ? AND user_id = ?
        """, (memo_id, user_id))

        exists = cursor.fetchone()

        if not exists:
            return False

        conn.execute("""
            DELETE FROM memos
            WHERE id = ? AND user_id = ?
        """, (memo_id, user_id))

        conn.commit()
        return True

    finally:
        conn.close()


# --------------------------------
# 전체 메모 삭제
# --------------------------------

def delete_all_memos(user_id):
    conn = get_db()

    try:
        cursor = conn.execute("""
            SELECT COUNT(*)
            FROM memos
            WHERE user_id = ?
        """, (user_id,))

        count = cursor.fetchone()[0]

        conn.execute("""
            DELETE FROM memos
            WHERE user_id = ?
        """, (user_id,))

        conn.commit()
        return count

    finally:
        conn.close()


# --------------------------------
# 명령어 처리
# --------------------------------

def process_command(user_id, message):

    message = message.strip()

    if not message:
        return "메시지를 입력해 줘."

    # 메모 저장
    if message.startswith("저장 "):

        content = message[3:].strip()

        if not content:
            return "저장할 내용을 입력해 줘."

        if len(content) > 500:
            return "저장할 내용은 500자 이하로 입력해 줘."

        save_memo(user_id, content)

        return (
            "저장 완료!\n\n"
            + content
        )

    # 메모 목록
    elif message == "목록":

        memos = get_memos(user_id)

        if not memos:
            return "저장된 내용이 없어."

        result = ["내 저장 목록"]

        for memo in memos:
            result.append(
                f"\n번호: {memo[0]}\n"
                f"내용: {memo[1]}\n"
                f"시간: {memo[2]}"
            )

        return "\n".join(result)

    # 메모 검색
    elif message.startswith("검색 "):

        keyword = message[3:].strip()

        if not keyword:
            return "검색어를 입력해 줘."

        memos = search_memos(user_id, keyword)

        if not memos:
            return "검색 결과가 없어."

        result = ["검색 결과"]

        for memo in memos:
            result.append(
                f"\n번호: {memo[0]}\n"
                f"내용: {memo[1]}\n"
                f"시간: {memo[2]}"
            )

        return "\n".join(result)

    # 메모 삭제
    elif message.startswith("삭제 "):

        try:
            memo_id = int(message[3:].strip())

            if memo_id <= 0:
                return "올바른 번호를 입력해 줘."

        except ValueError:
            return "사용법: 삭제 번호"

        if delete_memo(user_id, memo_id):
            return f"{memo_id}번 메모를 삭제했어."

        return "해당 번호의 메모를 찾을 수 없어."

    # 전체 삭제 요청
    elif message == "전체삭제":

        return (
            "정말 모든 메모를 삭제할까?\n"
            "삭제하려면 '전체삭제 확인'을 입력해 줘."
        )

    # 전체 삭제 확인
    elif message == "전체삭제 확인":

        count = delete_all_memos(user_id)

        return f"삭제 완료! 총 {count}개의 메모를 삭제했어."

    # 도움말
    elif message == "도움말":

        return (
            " 챗봇 명령어\n\n"
            "저장 내용 - 메모 저장\n"
            "목록 - 메모 확인\n"
            "검색 내용 - 메모 검색\n"
            "삭제 번호 - 메모 삭제\n"
            "전체삭제 - 전체 삭제 요청\n"
            "전체삭제 확인 - 전체 삭제 실행\n"
            "도움말 - 명령어 안내"
        )

    return (
        "지원하지 않는 명령어야.\n"
        "'도움말'을 입력해 봐."
    )


# --------------------------------
# Flask 라우트
# --------------------------------

@app.route("/", methods=["GET"])
def home():
    return "Kakao Bot Server is running!"


@app.route("/health", methods=["GET"])
def health():

    try:
        conn = get_db()

        try:
            cursor = conn.execute("SELECT 1")
            result = cursor.fetchone()

        finally:
            conn.close()

        if not result or result[0] != 1:
            raise RuntimeError("Database test failed")

        return jsonify({
            "status": "ok",
            "database": "connected",
            "provider": "Turso"
        })

    except Exception as e:
        print("[DB HEALTH ERROR]", str(e))

        return jsonify({
            "status": "error",
            "database": "disconnected"
        }), 503


@app.route("/skill", methods=["POST"])
def skill():

    data = request.get_json(silent=True) or {}

    user_request = data.get("userRequest", {})
    message = user_request.get("utterance", "")

    user = user_request.get("user", {})
    user_id = user.get("id")

    if not user_id:
        return kakao_response(
            "사용자 정보를 확인할 수 없어."
        )

    print(
        f"[MESSAGE] user={user_id}, message={message}"
    )

    try:
        answer = process_command(user_id, message)

    except Exception as e:
        print("[ERROR]", str(e))

        answer = (
            "처리 중 오류가 발생했어. "
            "잠시 후 다시 시도해 줘."
        )

    return kakao_response(answer)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
