
from flask import Flask, request, jsonify
import sqlite3
import os
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)

# 데이터베이스 경로
DB_PATH = os.environ.get("DB_PATH", "bot.db")


# -----------------------------
# 데이터베이스 초기화
# -----------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
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


init_db()


# -----------------------------
# 카카오톡 응답
# -----------------------------

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


# -----------------------------
# 메모 저장
# -----------------------------

def save_memo(user_id, content):
    now = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO memos
            (user_id, content, created_at)
            VALUES (?, ?, ?)
        """, (user_id, content, now))


# -----------------------------
# 메모 목록
# -----------------------------

def get_memos(user_id):
    with get_db() as conn:
        return conn.execute("""
            SELECT id, content, created_at
            FROM memos
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (user_id,)).fetchall()


# -----------------------------
# 메모 검색
# -----------------------------

def search_memos(user_id, keyword):
    with get_db() as conn:
        return conn.execute("""
            SELECT id, content, created_at
            FROM memos
            WHERE user_id = ?
            AND content LIKE ?
            ORDER BY id DESC
            LIMIT 20
        """, (user_id, f"%{keyword}%")).fetchall()


# -----------------------------
# 메모 삭제
# -----------------------------

def delete_memo(user_id, memo_id):
    with get_db() as conn:
        cursor = conn.execute("""
            DELETE FROM memos
            WHERE id = ? AND user_id = ?
        """, (memo_id, user_id))

        return cursor.rowcount > 0


def delete_all_memos(user_id):
    with get_db() as conn:
        cursor = conn.execute("""
            DELETE FROM memos
            WHERE user_id = ?
        """, (user_id,))

        return cursor.rowcount


# -----------------------------
# 명령어 처리
# -----------------------------

def process_command(user_id, message):

    message = message.strip()

    if not message:
        return "메시지를 입력해 줘."

    # 저장
    if message.startswith("저장 "):
        content = message[3:].strip()

        if not content:
            return "저장할 내용을 입력해 줘."

        if len(content) > 500:
            return "저장할 내용은 500자 이하로 입력해 줘."

        save_memo(user_id, content)
        return f"저장 완료!\n\n{content}"

    # 목록
    elif message == "목록":
        memos = get_memos(user_id)

        if not memos:
            return "저장된 내용이 없어."

        result = ["[내 저장 목록]"]

        for memo in memos:
            result.append(
                f"\n번호: {memo['id']}\n"
                f"내용: {memo['content']}\n"
                f"시간: {memo['created_at']}"
            )

        return "\n".join(result)

    # 검색
    elif message.startswith("검색 "):
        keyword = message[3:].strip()

        if not keyword:
            return "검색어를 입력해 줘."

        memos = search_memos(user_id, keyword)

        if not memos:
            return "검색 결과가 없어."

        result = ["[검색 결과]"]

        for memo in memos:
            result.append(
                f"\n번호: {memo['id']}\n"
                f"내용: {memo['content']}"
            )

        return "\n".join(result)

    # 개별 삭제
    elif message.startswith("삭제 "):
        try:
            memo_id = int(message[3:].strip())
        except ValueError:
            return "사용법: 삭제 번호"

        if delete_memo(user_id, memo_id):
            return f"{memo_id}번 메모를 삭제했어."

        return "해당 번호의 메모를 찾을 수 없어."

    # 전체 삭제 확인
    elif message == "전체삭제":
        return (
            "정말 모든 메모를 삭제할까?\n"
            "삭제하려면 '전체삭제 확인'을 입력해 줘."
        )

    elif message == "전체삭제 확인":
        count = delete_all_memos(user_id)
        return f"삭제 완료! 총 {count}개의 메모를 삭제했어."

    # 도움말
    elif message == "도움말":
        return (
            "[챗봇 명령어]\n\n"
            "저장 내용 - 메모 저장\n"
            "목록 - 저장한 메모 확인\n"
            "검색 내용 - 메모 검색\n"
            "삭제 번호 - 메모 삭제\n"
            "전체삭제 - 전체 삭제 요청\n"
            "전체삭제 확인 - 전체 삭제 실행\n"
            "도움말 - 명령어 확인"
        )

    # 기본 응답
    else:
        return (
            "아직 지원하지 않는 명령어야.\n"
            "'도움말'을 입력해 봐."
        )


# -----------------------------
# Flask 라우트
# -----------------------------

@app.route("/", methods=["GET"])
def home():
    return "Kakao AI Bot Server is running!"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "database": "connected"
    })


@app.route("/skill", methods=["POST"])
def skill():

    data = request.get_json(silent=True) or {}

    user_request = data.get("userRequest", {})
    message = user_request.get("utterance", "").strip()

    user = user_request.get("user", {})
    user_id = user.get("id")

    if not user_id:
        return kakao_response(
            "사용자 정보를 확인할 수 없어. 다시 시도해 줘."
        )

    print(f"[메시지] 사용자: {user_id}, 내용: {message}")

    try:
        answer = process_command(user_id, message)
    except Exception as e:
        print("[오류]", str(e))
        answer = "처리 중 오류가 발생했어. 잠시 후 다시 시도해 줘."

    return kakao_response(answer)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
