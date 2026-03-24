import time
import random
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI()
rooms = {}

QUESTIONS = [
    # جغرافيا
    {"question": "ما هي عاصمة المملكة العربية السعودية؟", "options": ["جدة", "الرياض", "الدمام", "مكة المكرمة"], "correct_index": 1, "category": "جغرافيا"},
    {"question": "ما هو أطول نهر في العالم؟", "options": ["نهر النيل", "نهر الأمازون", "نهر المسيسيبي", "نهر دجلة"], "correct_index": 0, "category": "جغرافيا"},
    {"question": "ما هي أكبر قارة في العالم مساحةً؟", "options": ["أفريقيا", "أمريكا الشمالية", "آسيا", "أوروبا"], "correct_index": 2, "category": "جغرافيا"},
    {"question": "ما هي عاصمة اليابان؟", "options": ["أوساكا", "كيوتو", "هيروشيما", "طوكيو"], "correct_index": 3, "category": "جغرافيا"},
    {"question": "في أي قارة تقع البرازيل؟", "options": ["أفريقيا", "أوروبا", "آسيا", "أمريكا الجنوبية"], "correct_index": 3, "category": "جغرافيا"},
    # علوم
    {"question": "كم عدد الكواكب في مجموعتنا الشمسية؟", "options": ["٧ كواكب", "٨ كواكب", "٩ كواكب", "١٠ كواكب"], "correct_index": 1, "category": "علوم"},
    {"question": "ما هو أسرع حيوان بري في العالم؟", "options": ["الأسد", "النمر", "الفهد", "الحصان"], "correct_index": 2, "category": "علوم"},
    {"question": "كم يبلغ عدد عظام جسم الإنسان البالغ؟", "options": ["١٠٦", "٢٠٦", "٣٠٦", "٤٠٦"], "correct_index": 1, "category": "علوم"},
    {"question": "ما هو الغاز الأكثر وفرة في الغلاف الجوي للأرض؟", "options": ["الأكسجين", "ثاني أكسيد الكربون", "النيتروجين", "الهيدروجين"], "correct_index": 2, "category": "علوم"},
    {"question": "ما هو رمز عنصر الذهب في الجدول الدوري؟", "options": ["Go", "Gd", "Au", "Ag"], "correct_index": 2, "category": "علوم"},
    # تاريخ
    {"question": "في أي عام فُتحت مدينة القسطنطينية؟", "options": ["١٢٥٣م", "١٣٥٣م", "١٤٥٣م", "١٥٥٣م"], "correct_index": 2, "category": "تاريخ"},
    {"question": "من هو مؤسس الدولة السعودية الأولى؟", "options": ["الملك عبدالعزيز", "محمد بن سعود", "محمد بن عبدالوهاب", "تركي بن عبدالله"], "correct_index": 1, "category": "تاريخ"},
    # رياضيات
    {"question": "كم يساوي الجذر التربيعي لـ ١٤٤؟", "options": ["١١", "١٢", "١٣", "١٤"], "correct_index": 1, "category": "رياضيات"},
    {"question": "كم يساوي ١٥ × ١٥؟", "options": ["٢٠٠", "٢١٥", "٢٢٥", "٢٣٥"], "correct_index": 2, "category": "رياضيات"},
    {"question": "كم يساوي ٢ أس ١٠ ؟", "options": ["٥١٢", "٧٦٨", "١٠٢٤", "٢٠٤٨"], "correct_index": 2, "category": "رياضيات"},
]


# SPEED-BASED SCORING
def calc_score(elapsed_seconds: float) -> int:
    """10 pts at 0s → 1 pt at 15s (linear scale)"""
    return max(1, round(10 - elapsed_seconds * (9 / 15)))


#  BROADCAST HELPERS
async def broadcast_lobby_update(room_code: str):
    room = rooms[room_code]
    player_names = [p["name"] for p in room["players"]]
    msg = json.dumps({"action": "lobby_update", "code": room_code, "players": player_names})
    for p in room["players"]:
        await p["ws"].send_text(msg)


async def broadcast_leaderboard(room_code: str):
    room = rooms[room_code]
    lb = sorted(
        [{"name": p["name"], "score": p["score"], "streak": p["streak"]} for p in room["players"]],
        key=lambda x: x["score"], reverse=True
    )
    msg = json.dumps({"action": "update_leaderboard", "leaderboard": lb})
    for p in room["players"]:
        await p["ws"].send_text(msg)


async def send_question(room_code: str):
    room = rooms[room_code]
    idx = room["current_question_index"]
    if idx >= len(room["questions"]):
        # Sort final leaderboard
        lb = sorted(
            [{"name": p["name"], "score": p["score"]} for p in room["players"]],
            key=lambda x: x["score"], reverse=True
        )
        for p in room["players"]:
            await p["ws"].send_text(json.dumps({"action": "game_over", "leaderboard": lb}))
        return

    room["question_start_time"] = time.time()
    room["answered_players"] = set()

    q = room["questions"][idx]
    payload = json.dumps({
        "action": "new_question",
        "question": q["question"],
        "options": q["options"],
        "category": q.get("category", ""),
        "question_num": idx + 1,
        "total_questions": len(room["questions"]),
    })
    for p in room["players"]:
        await p["ws"].send_text(payload)


#  ROUTES
@app.get("/")
async def get_home():
    return FileResponse("index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            #  CREATE ROOM 
            if msg["action"] == "create_room":
                code = str(random.randint(0, 9999)).zfill(4)
                # Avoid duplicate codes
                while code in rooms:
                    code = str(random.randint(0, 9999)).zfill(4)

                host_name = msg["name"]
                shuffled = random.sample(QUESTIONS, min(10, len(QUESTIONS)))

                rooms[code] = {
                    "host_ws": websocket,
                    "players": [{"name": host_name, "ws": websocket, "score": 0, "streak": 0}],
                    "questions": shuffled,
                    "current_question_index": 0,
                    "is_started": False,
                    "answered_players": set(),
                    "question_start_time": None,
                }
                await websocket.send_text(json.dumps({"action": "room_created", "code": code, "is_host": True}))
                await broadcast_lobby_update(code)

            #  JOIN ROOM 
            elif msg["action"] == "join_room":
                code = msg["code"]
                name = msg["name"].strip() or "لاعب"

                if code not in rooms:
                    await websocket.send_text(json.dumps({"action": "error", "message": "رمز الغرفة غير صحيح!"}))
                elif rooms[code]["is_started"]:
                    await websocket.send_text(json.dumps({"action": "error", "message": "اللعبة بدأت بالفعل، لا يمكن الانضمام!"}))
                else:
                    # Deduplicate name
                    existing = [p["name"] for p in rooms[code]["players"]]
                    original = name
                    counter = 2
                    while name in existing:
                        name = f"{original}_{counter}"
                        counter += 1

                    rooms[code]["players"].append({"name": name, "ws": websocket, "score": 0, "streak": 0})
                    await websocket.send_text(json.dumps({"action": "join_successful", "code": code, "name": name}))
                    await broadcast_lobby_update(code)

            #  START GAME 
            elif msg["action"] == "start_game":
                code = msg["code"]
                if code in rooms:
                    rooms[code]["is_started"] = True
                    rooms[code]["current_question_index"] = 0
                    await send_question(code)

            #  NEXT QUESTION 
            elif msg["action"] == "next_question":
                code = msg["code"]
                if code in rooms:
                    rooms[code]["current_question_index"] += 1
                    await send_question(code)

            #  SUBMIT ANSWER 
            elif msg["action"] == "submit_answer":
                code = msg["code"]
                player_name = msg["name"]
                answer_index = msg["answer_index"]

                if code not in rooms:
                    continue

                room = rooms[code]

                #  Lock: prevent double submission
                if player_name in room["answered_players"]:
                    continue
                room["answered_players"].add(player_name)

                elapsed = time.time() - (room["question_start_time"] or time.time())
                current_q = room["questions"][room["current_question_index"]]
                is_correct = (answer_index == current_q["correct_index"])

                points_earned = 0
                streak_bonus = 0
                new_streak = 0

                for player in room["players"]:
                    if player["name"] == player_name:
                        if is_correct:
                            points_earned = calc_score(elapsed)
                            player["streak"] += 1
                            new_streak = player["streak"]
                            if new_streak >= 2:
                                streak_bonus = new_streak  # +1 per streak level
                                points_earned += streak_bonus
                            player["score"] += points_earned
                        else:
                            player["streak"] = 0

                        await player["ws"].send_text(json.dumps({
                            "action": "answer_result",
                            "is_correct": is_correct,
                            "correct_index": current_q["correct_index"],
                            "points_earned": points_earned,
                            "streak": new_streak,
                            "streak_bonus": streak_bonus,
                        }))
                        break

                await broadcast_leaderboard(code)

                answered_count = len(room["answered_players"])
                total = len(room["players"])

                progress_msg = json.dumps({
                    "action": "answer_progress",
                    "answered": answered_count,
                    "total": total,
                })
                for p in room["players"]:
                    await p["ws"].send_text(progress_msg)

                # Notify host when everyone answered
                if answered_count >= total:
                    await room["host_ws"].send_text(json.dumps({"action": "all_answered"}))

    except WebSocketDisconnect:
        #  CLEANUP ON DISCONNECT 
        to_delete = []
        for code, room in rooms.items():
            was_host = room["host_ws"] == websocket
            room["players"] = [p for p in room["players"] if p["ws"] != websocket]

            if not room["players"]:
                to_delete.append(code)
            else:
                if was_host:
                    # Transfer host to first remaining player
                    room["host_ws"] = room["players"][0]["ws"]
                    await room["host_ws"].send_text(json.dumps({"action": "you_are_host"}))
                # Update lobby for remaining players
                await broadcast_lobby_update(code)

        for code in to_delete:
            del rooms[code]