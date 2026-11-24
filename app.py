from flask import Flask, request, jsonify
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
from google import genai
import os
import turso_serverless

load_dotenv()

app = Flask(__name__)

# --------------------------------
# 설정
# --------------------------------

SEOUL = ZoneInfo("Asia/Seoul")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


def get_db():
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")

    if not url or not token:
        raise RuntimeError("Turso 환경변수가 설정되지 않았습니다.")

    return turso_serverless.connect(url, auth_token=token)


def current_time():
    return datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M:%S")


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
# 메모 저장 / 조회 / 검색 / 삭제
# --------------------------------

def save_memo(user_id, content):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO memos (user_id, content, created_at)
            VALUES (?, ?, ?)
        """, (user_id, content, current_time()))
        conn.commit()
    finally:
        conn.close()


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


def get_memos_for_analysis(user_id):
    """AI 분석용 메모. 사용자 본인의 최근 50개만 조회한다."""
    conn = get_db()
    try:
        cursor = conn.execute("""
            SELECT id, content, created_at
            FROM memos
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 50
        """, (user_id,))
        return cursor.fetchall()
    finally:
        conn.close()


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


def delete_memo(user_id, memo_id):
    conn = get_db()
    try:
        cursor = conn.execute("""
            SELECT id FROM memos
            WHERE id = ? AND user_id = ?
        """, (memo_id, user_id))

        if not cursor.fetchone():
            return False

        conn.execute("""
            DELETE FROM memos
            WHERE id = ? AND user_id = ?
        """, (memo_id, user_id))
        conn.commit()
        return True
    finally:
        conn.close()


def delete_all_memos(user_id):
    conn = get_db()
    try:
        cursor = conn.execute("""
            SELECT COUNT(*) FROM memos
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
# AI 일정 분석
# --------------------------------

def format_memos_for_ai(memos):
    lines = []
    for memo in memos:
        memo_id, content, created_at = memo
        lines.append(
            f"- 메모 번호: {memo_id}\n"
            f"  저장된 내용: {content}\n"
            f"  저장 시각: {created_at}"
        )
    return "\n".join(lines)


def analyze_schedule_with_ai(user_id, question="전체 일정 분석"):
    """
    사용자의 저장 목록을 Gemini AI에 전달해 일정과 우선순위를 분석한다.
    AI는 메모를 수정하거나 삭제하지 않고 분석 결과만 반환한다.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return (
            "AI 기능을 사용하려면 서버 환경변수에 GEMINI_API_KEY를 설정해야 해.\n"
            "키를 .env 또는 Render 환경변수에 등록한 뒤 서버를 재배포해 줘."
        )

    memos = get_memos_for_analysis(user_id)
    if not memos:
        return (
            "분석할 저장 목록이 없어.\n"
            "예시: 저장 수학 숙제 내일까지 끝내기"
        )

    now = datetime.now(SEOUL).strftime("%Y년 %m월 %d일 %H시 %M분")
    memo_text = format_memos_for_ai(memos)

    system_prompt = """
너는 한국어 개인 일정 및 할 일 관리 비서다.
사용자가 저장한 메모 목록과 질문을 바탕으로 현실적이고 실행 가능한 계획을 제안한다.

반드시 지킬 규칙:
1. 메모에 실제로 적혀 있는 사실과 네가 추론한 내용을 구분한다.
2. 마감일이 적혀 있지 않으면 임의로 날짜를 만들지 말고 '마감일 미정'이라고 표시한다.
3. 날짜가 상대적으로 적혀 있으면 현재 시각을 기준으로 해석하되, 해석이 불확실하면 불확실하다고 표시한다.
4. 이미 지난 마감일은 '기한 지남'으로 표시한다.
5. 우선순위는 마감 임박 여부, 중요도, 예상 소요 시간, 다른 일에 미치는 영향을 고려한다.
6. 일정 정보가 부족하면 필요한 정보를 짧게 질문한다.
7. 막연한 조언보다 지금 바로 실행할 수 있는 작은 행동을 제안한다.
8. 목록에 없는 일정이나 완료 여부를 지어내지 않는다.
9. 사용자가 특정 질문을 하면 전체 분석을 반복하지 말고 그 질문에 집중한다.
10. 한국어로 간결하고 친근하게 답한다.

답변 권장 형식:
[핵심 판단]
[우선순위]
1. 할 일 - 이유 - 지금 할 행동
[오늘의 추천 계획]
[확인할 점]
해당 항목이 필요 없으면 생략해도 된다.
"""

    user_prompt = f"""
현재 시각: {now}
사용자 질문: {question}

저장된 목록:
{memo_text}

위 자료만 근거로 분석해 줘.
"""

    try:
        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model=GEMINI_MODEL,
            input=system_prompt + "\n\n" + user_prompt,
            store=False
        )

        answer = interaction.output_text
        if not answer:
            return "AI가 분석 결과를 반환하지 않았어. 잠시 후 다시 시도해 줘."

        return "AI 일정 분석\n\n" + answer.strip()

    except Exception as e:
        print("[AI ERROR]", repr(e))
        return (
            "AI 일정 분석 중 오류가 발생했어.\n"
            "GEMINI_API_KEY, GEMINI_MODEL 설정과 서버 로그를 확인해 줘."
        )


def is_ai_schedule_question(message):
    """일정 관련 자연어 질문을 AI 분석으로 연결한다."""
    keywords = (
        "뭐부터", "무엇부터", "해야 할", "해야할", "할 일", "할일",
        "우선순위", "일정", "마감", "오늘", "내일", "이번 주",
        "이번주", "이번 달", "이번달", "계획", "급한", "미뤄"
    )
    return any(keyword in message for keyword in keywords)


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
        return "저장 완료!\n\n" + content

    # 목록 분석 명령
    elif message in ("분석", "일정분석", "목록분석", "일정 분석"):
        return analyze_schedule_with_ai(user_id)

    # 오늘 / 이번 주 분석
    elif message in ("오늘", "오늘할일", "오늘 할 일"):
        return analyze_schedule_with_ai(
            user_id,
            "오늘 날짜와 마감일을 기준으로 오늘 해야 할 일을 정리하고 실행 순서를 추천해 줘."
        )

    elif message in ("이번주", "이번 주", "이번주 일정", "이번 주 일정"):
        return analyze_schedule_with_ai(
            user_id,
            "이번 주에 처리해야 할 일을 정리하고 요일별 계획을 추천해 줘."
        )

    # AI에게 질문
    elif message.startswith("질문 "):
        question = message[3:].strip()
        if not question:
            return "질문 내용을 입력해 줘. 예: 질문 지금 뭐부터 해야 해?"
        return analyze_schedule_with_ai(user_id, question)

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
            "챗봇 명령어\n\n"
            "저장 내용 - 메모 저장\n"
            "목록 - 저장 목록 확인\n"
            "검색 내용 - 메모 검색\n"
            "삭제 번호 - 메모 삭제\n"
            "전체삭제 - 전체 삭제 요청\n"
            "전체삭제 확인 - 전체 삭제 실행\n\n"
            "AI 일정 기능\n"
            "분석 - 저장 목록 전체 분석\n"
            "오늘 - 오늘 할 일 추천\n"
            "이번주 - 이번 주 일정 분석\n"
            "질문 내용 - 저장 목록을 바탕으로 AI에게 질문\n\n"
            "예시:\n"
            "질문 지금 뭐부터 해야 해?"
        )

    # 그 외 일정 관련 자연어 질문도 AI로 연결
    elif is_ai_schedule_question(message):
        return analyze_schedule_with_ai(user_id, message)

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
            "provider": "Turso",
            "ai_provider": "Google Gemini",
            "ai_configured": bool(os.getenv("GEMINI_API_KEY"))
        })

    except Exception as e:
        print("[DB HEALTH ERROR]", repr(e))
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
        return kakao_response("사용자 정보를 확인할 수 없어.")

    print(f"[MESSAGE] user={user_id}, message={message}")

    try:
        answer = process_command(user_id, message)
    except Exception as e:
        print("[ERROR]", repr(e))
        answer = "처리 중 오류가 발생했어. 잠시 후 다시 시도해 줘."

    return kakao_response(answer)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
