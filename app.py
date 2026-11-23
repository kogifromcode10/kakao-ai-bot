
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return "Kakao AI Bot Server is running!"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "Flask server is working"
    })


@app.route("/skill", methods=["POST"])
def skill():
    data = request.get_json(silent=True) or {}

    # 카카오 오픈빌더가 보낸 사용자 메시지
    user_request = data.get("userRequest", {})
    utterance = user_request.get("utterance", "")

    print("받은 메시지:", utterance)

    # 우선 연결 테스트용 응답
    answer = f"메시지를 정상적으로 받았어: {utterance}"

    return jsonify({
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": answer
                    }
                }
            ]
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
