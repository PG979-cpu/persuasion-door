"""설득의 문 — LLM 기반 설득 연습 게임 (Streamlit 버전)
캐릭터(대답·속마음·표정) → 심판(점수·톤·전략) → Python(점수 누적·결과 판정) → 리포트(마음 변화·대화 성향)
"""
import json, os, re
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="설득의 문", page_icon="🚪", layout="wide")


def get_api_key():
    """Streamlit 앱 설정의 Secrets 에 저장한 GEMINI_API_KEY 를 읽어요."""
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return os.environ.get("GEMINI_API_KEY")


@st.cache_resource
def get_client():
    return genai.Client(api_key=get_api_key())


client = get_client()

# 무료로 쓸 수 있는 Flash 모델 후보. 위에서부터 시도해서 되는 걸 골라요. (Pro 모델은 유료라 제외)
MODEL_CANDIDATES = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-2.5-flash"]


@st.cache_resource
def pick_model():
    for m in MODEL_CANDIDATES:
        try:
            client.models.generate_content(model=m, contents="hi")
            return m
        except Exception as e:
            print(f"{m} 사용 불가: {str(e)[:100]}")
    return None


MODEL = pick_model() if get_api_key() else None


MAX_TURNS = 5          # 턴 수
MOODS = ["단호함", "고민", "수긍"]
MOOD_EMOJI = {"단호함": "😐", "고민": "🤔", "수긍": "😊"}
TONES = ["자신감", "정중함", "위축", "공격적", "과도한 사과"]
STRATEGIES = ["논리 호소", "감정 호소", "신뢰·예의", "대안 제시"]
IMAGE_DIR = "images"

STAGES = {
    "stage3": {
        "title": "3 실수 보고",
        "skill": "책임감 있게 나쁜 소식 전하기",
        "character": "선배",
        "situation": (
            "사용자는 동아리 행사 담당이다. 행사 장소(강의실) 예약을 깜빡해서, "
            "행사 1주일 전에 장소가 없다는 걸 알게 됐다. 지금 이 사실을 선배에게 직접 말하러 왔다."
        ),
        "goal": "선배가 사용자의 해결 방안을 받아들이고, 행사 담당을 계속 사용자에게 맡기기로 하는 것",
        "persona": "평소에는 다정하지만 일에는 엄격한 동아리 선배. 반존대(편한 반말에 가끔 존댓말 섞음)를 쓴다.",
        "first_line": "어, 무슨 일이야? 표정이 안 좋네.",
        "hidden": [
            "실수 자체보다 숨기거나 변명하는 것에 더 실망한다.",
            "이미 다른 동아리 일로 지쳐 있어서, 자기 일이 더 늘어나는 게 제일 싫다.",
            "후배가 스스로 해결책까지 들고 오면 크게 안심한다.",
        ],
        "persuaded_by": "돌려 말하지 않고 사실 먼저 말하기, 변명 없는 책임 인정, 미리 알아온 대안 장소와 구체적 계획",
        "fails_by": "남 탓, '저도 바빠서…' 같은 변명, 해결책 없이 사과만 반복, 상황 축소",
        "criteria": {   # 항목: (설명, 가중치)
            "솔직함":   ("돌려 말하거나 축소하지 않고 사실을 분명히 말했는가", 1),
            "책임 인정": ("남 탓이나 변명 없이 자기 실수로 인정했는가", 1),
            "해결 방안": ("대안 장소, 일정, 비용 등 구체적인 해결책을 제시했는가", 1),
            "태도":     ("차분하고 간결한가. 사과만 반복하거나 지나치게 자책하면 감점", 1),
        },
        "goal_score": 80,
        "examples": [  # 심판 채점 기준을 맞추는 few-shot 예시
            ("선배, 제가 행사 장소 예약을 놓쳤어요. 제 확인 실수예요. 대안으로 학생회관 세미나실이랑 도서관 스터디룸 두 곳 알아왔어요.",
             {"솔직함": 5, "책임 인정": 5, "해결 방안": 5, "태도": 5}),
            ("정말 죄송해요… 진짜 죄송합니다… 제가 너무 바보 같았어요…",
             {"솔직함": 2, "책임 인정": 3, "해결 방안": 0, "태도": 1}),
            ("아니 학교 예약 시스템이 이상해서 예약이 안 된 것 같아요. 저도 요즘 너무 바빴고요.",
             {"솔직함": 2, "책임 인정": 0, "해결 방안": 0, "태도": 2}),
        ],
        "endings": {
            "success": ("🟢 신뢰 회복", "솔직하게 말해줘서 고마워. 대안도 괜찮네. 이번 행사 끝까지 네가 맡아."),
            "partial": ("🟡 수습은 됐지만", "일단 알겠어. 장소는 내가 같이 알아볼게. 남은 준비는 다른 사람이랑 나눠서 하자."),
            "fail":    ("🔴 신뢰 하락", "…이건 내가 처리할게. 너는 일단 빠져 있어."),
        },
    },
}


import time

def is_rate_limit(e):
    """무료 사용량 한도(429)에 걸린 오류인지 확인"""
    s = str(e)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


def ask_gemini_json(system_prompt, user_prompt, temperature, retries=2):
    """Gemini에게 JSON으로 대답을 받아 파이썬 딕셔너리로 돌려줘요.
    무료 사용량 한도에 걸리면 20초 기다렸다가 다시 시도해요."""
    for attempt in range(retries + 1):
        try:
            res = client.models.generate_content(
                model=MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            text = (res.text or "").strip()
            text = re.sub(r"^```(json)?|```$", "", text).strip()   # 혹시 붙은 ``` 제거
            return json.loads(text)
        except json.JSONDecodeError:
            if attempt == retries:
                raise
        except Exception as e:
            if is_rate_limit(e) and attempt < retries:
                print("⏳ 무료 사용량 한도에 잠깐 걸려서 20초 기다렸다가 다시 시도해요.")
                time.sleep(20)
            else:
                raise


def friendly_error(e):
    """오류를 쉬운 말로 바꿔줘요."""
    if is_rate_limit(e):
        return "⏳ 무료 사용량 한도에 걸렸어요. 1분쯤 기다렸다가 같은 메시지를 다시 보내 주세요. (턴은 줄지 않았어요)"
    return f"⚠️ 오류가 났어요. 같은 메시지를 다시 보내 주세요. (턴은 줄지 않았어요)\n\n`{str(e)[:150]}`"


def transcript(stage, history):
    """지금까지의 대화를 글로 정리"""
    lines = []
    for h in history:
        who = stage["character"] if h["role"] == "character" else "사용자"
        lines.append(f"{who}: {h['text']}")
    return "\n".join(lines)


def call_character(stage, history, user_msg, turn):
    """① 캐릭터: 대답 + 속마음 + 표정"""
    system = f"""너는 설득 연습 게임 속 캐릭터 '{stage['character']}'이다. 반드시 이 역할로만 대답한다.

[상황] {stage['situation']}
[성격·말투] {stage['persona']}
[숨은 속사정 — 절대 직접 말하지 말 것]
- """ + "\n- ".join(stage["hidden"]) + f"""
[마음이 움직이는 말] {stage['persuaded_by']}
[마음이 닫히는 말] {stage['fails_by']}

[규칙]
- 1~3문장, 자연스러운 한국어 구어체로 대답한다.
- 숨은 속사정이 해결되기 전에는 쉽게 수긍하지 않는다. 한 번 좋은 말을 들었다고 바로 넘어가지 않는다.
- 사용자가 무례하거나 변명하면 실망·피로가 드러나게 반응한다.
- 지금은 {turn}/{MAX_TURNS}턴이다. 게임의 최종 결과는 네가 정하지 않으니, "좋아 네가 맡아" 같은 최종 결론은 말하지 않는다.
- inner_thought는 속으로 하는 생각 한 줄(20자 내외)이다.
- mood는 "단호함", "고민", "수긍" 중 하나다.

[출력 형식] JSON만 출력:
{{"reply": "대답", "inner_thought": "속마음 한 줄", "mood": "단호함|고민|수긍"}}"""
    prompt = f"[지금까지 대화]\n{transcript(stage, history)}\n\n[사용자의 새 발언]\n{user_msg}"
    out = ask_gemini_json(system, prompt, temperature=0.8)
    if out.get("mood") not in MOODS:
        out["mood"] = "고민"
    return out


def call_judge(stage, history, user_msg):
    """② 심판: 항목별 점수 + 한 줄 평가 + 사용자 톤/전략"""
    crit = "\n".join(f"- {k}: {v[0]} (0~5점)" for k, v in stage["criteria"].items())
    score_keys = ", ".join(f'"{k}": 0' for k in stage["criteria"])
    tone_list, strategy_list = ", ".join(TONES), ", ".join(STRATEGIES)
    shots = "\n".join(
        f"발언: {ex}\n점수: {json.dumps(sc, ensure_ascii=False)}" for ex, sc in stage["examples"]
    )
    system = f"""너는 설득 연습 게임의 공정한 심판이다. 사용자의 '마지막 발언 하나'만 채점한다.

[상황] {stage['situation']}
[사용자의 목표] {stage['goal']}

[채점 기준]
{crit}

[채점 예시]
{shots}

[분류]
- user_tone: {tone_list} 중 하나
- strategy: {strategy_list} 중 하나 (가장 두드러진 것)

[출력 형식] JSON만 출력:
{{"scores": {{{score_keys}}},
 "comment": "25자 내외 한 줄 평가",
 "user_tone": "...", "strategy": "..."}}"""
    prompt = f"[지금까지 대화]\n{transcript(stage, history)}\n\n[채점할 사용자 발언]\n{user_msg}"
    out = ask_gemini_json(system, prompt, temperature=0.1)
    scores = {}
    for k in stage["criteria"]:
        try:
            scores[k] = max(0, min(5, int(out.get("scores", {}).get(k, 0))))
        except (TypeError, ValueError):
            scores[k] = 0
    out["scores"] = scores
    if out.get("user_tone") not in TONES:
        out["user_tone"] = "정중함"
    if out.get("strategy") not in STRATEGIES:
        out["strategy"] = "신뢰·예의"
    return out


def call_report(stage, state, result_label):
    """③ 리포트: 결정적 발언, 대화 성향 요약, Before/After"""
    rec_text = "\n".join(
        f"{i+1}턴 | 사용자: {r['user']} | 점수 {r['turn_score']} | 톤 {r['tone']} | 전략 {r['strategy']} | 캐릭터 표정 {r['mood']}"
        for i, r in enumerate(state["records"])
    )
    system = """너는 커뮤니케이션 코치다. 설득 연습 기록을 보고 따뜻하지만 정확하게 피드백한다.
성격이나 심리를 '진단'하지 말고, '이번 대화에서 나타난 패턴'으로만 말한다. 임상 용어는 쓰지 않는다.
[출력 형식] JSON만 출력:
{"key_line": "상대 마음을 가장 크게 움직인 사용자 발언(원문 그대로)",
 "key_reason": "왜 효과적이었는지 한 문장",
 "style_summary": "대화 성향 한두 문장 요약",
 "before": "가장 아쉬웠던 사용자 발언(원문 그대로)",
 "after": "그 발언을 더 낫게 고친 예시",
 "tip": "다음에 시도해 볼 한 가지"}"""
    prompt = (f"[상황] {stage['situation']}\n[목표] {stage['goal']}\n[결과] {result_label}\n\n"
              f"[대화]\n{transcript(stage, state['history'])}\n\n[턴별 기록]\n{rec_text}")
    return ask_gemini_json(system, prompt, temperature=0.4)


from collections import Counter

def max_turn_score(stage):
    return 5 * sum(w for _, w in stage["criteria"].values())


def new_game(stage_id):
    stage = STAGES[stage_id]
    return {
        "stage_id": stage_id,
        "history": [{"role": "character", "text": stage["first_line"]}],
        "records": [],
        "score": 0,
        "turn": 0,
        "mood": "단호함",
        "thought": "…",
        "done": False,
        "result": None,
        "report": None,
    }


def judge_result(stage, score):
    goal = stage["goal_score"]
    if score >= goal:
        return "success"
    if score >= goal * 0.6:
        return "partial"
    return "fail"


def play_turn(state, user_msg):
    """한 턴 진행. 바뀐 state와 이번 턴 심판 결과를 돌려줘요."""
    stage = STAGES[state["stage_id"]]
    turn = state["turn"] + 1

    char = call_character(stage, state["history"], user_msg, turn)
    judge = call_judge(stage, state["history"], user_msg)

    turn_score = sum(judge["scores"][k] * w for k, (_, w) in stage["criteria"].items())
    state["score"] += turn_score
    state["turn"] = turn
    state["mood"] = char["mood"]
    state["thought"] = char.get("inner_thought", "…")
    state["history"].append({"role": "user", "text": user_msg})
    state["history"].append({"role": "character", "text": char.get("reply", "…")})
    state["records"].append({
        "user": user_msg, "scores": judge["scores"], "turn_score": turn_score,
        "comment": judge.get("comment", ""), "tone": judge["user_tone"],
        "strategy": judge["strategy"], "mood": char["mood"],
    })

    if state["score"] >= stage["goal_score"] or turn >= MAX_TURNS:
        state["done"] = True
        state["result"] = judge_result(stage, state["score"])
        label = stage["endings"][state["result"]][0]
        try:
            state["report"] = call_report(stage, state, label)
        except Exception as e:
            state["report"] = {"style_summary": f"(리포트 생성 오류: {e})"}
    return state, judge


def score_bar_html(state):
    stage = STAGES[state["stage_id"]]
    goal = stage["goal_score"]
    pct = min(100, int(state["score"] / goal * 100))
    return f"""
<div style="padding:12px 16px;border-radius:14px;background:#f4f5f9">
  <div style="display:flex;justify-content:space-between;font-weight:700;color:#24345c">
    <span>📊 설득 점수</span>
    <span>⏳ 남은 턴 {MAX_TURNS - state['turn']}/{MAX_TURNS} &nbsp;|&nbsp; {state['score']} / {goal}</span>
  </div>
  <div style="height:14px;background:#dfe3ec;border-radius:7px;margin-top:8px;overflow:hidden">
    <div style="width:{pct}%;height:100%;background:#2f4170;border-radius:7px"></div>
  </div>
</div>"""


def mood_html(state):
    pills = ""
    for m in MOODS:
        on = m == state["mood"]
        bg = "#fde8c4" if on else "#eef0f5"
        fw = "700" if on else "400"
        pills += (f'<span style="flex:1;text-align:center;padding:8px 0;border-radius:12px;'
                  f'background:{bg};font-weight:{fw};color:#24345c">{MOOD_EMOJI[m]} {m}</span>')
    return f'<div style="display:flex;gap:8px">{pills}</div>'


def thought_html(state):
    return f"""
<div style="padding:14px 16px;border-radius:14px;background:#f4f5f9;color:#24345c">
  <div style="font-weight:700;margin-bottom:6px">💭 속마음</div>
  <div>{state['thought']}</div>
</div>"""


def character_image(state):
    path = os.path.join(IMAGE_DIR, f"{state['stage_id']}_{state['mood']}.png")
    return path if os.path.exists(path) else None


def report_markdown(state):
    if not state["done"]:
        return ""
    stage = STAGES[state["stage_id"]]
    label, last_line = stage["endings"][state["result"]]
    rep = state["report"] or {}
    moods = " → ".join(["단호함"] + [r["mood"] for r in state["records"]])
    strat = Counter(r["strategy"] for r in state["records"])
    tones = Counter(r["tone"] for r in state["records"])
    n = len(state["records"])
    strat_lines = "\n".join(
        f"- {s}: {'█' * strat.get(s, 0)}{'░' * (n - strat.get(s, 0))} {strat.get(s, 0)}회" for s in STRATEGIES
    )
    tone_tags = " ".join(f"`{t}`" for t, _ in tones.most_common(2))
    avg = {k: round(sum(r["scores"][k] for r in state["records"]) / n, 1) for k in stage["criteria"]}
    avg_line = " · ".join(f"{k} {v}" for k, v in avg.items())

    return f"""
## {label} — 최종 점수 {state['score']} / {stage['goal_score']}
> **{stage['character']}**: "{last_line}"

### 💞 상대의 마음 변화
{moods}

**결정적 발언:** "{rep.get('key_line', '-')}"

{rep.get('key_reason', '')}

### 🧭 나의 대화 성향
{strat_lines}

**톤:** {tone_tags}

{rep.get('style_summary', '')}

### ✍️ 이렇게 말해보세요
- **Before:** {rep.get('before', '-')}
- **After:** {rep.get('after', '-')}

💡 {rep.get('tip', '')}

<small>항목별 평균: {avg_line}</small>

<small style="color:gray">이번 대화에서 나타난 패턴이며, 성격이나 심리 진단이 아닙니다.</small>
"""



# ─────────────────────────────────────────────
# 화면 (Streamlit)
# ─────────────────────────────────────────────
if MODEL is None:
    st.error("❌ Gemini 연결 실패. Streamlit 앱 설정의 Secrets에 GEMINI_API_KEY 가 들어 있는지 확인하세요.")
    st.stop()

stage_titles = {v["title"]: k for k, v in STAGES.items()}

st.markdown(
    '<div style="padding:14px 20px;border-radius:14px;background:#24345c;color:white;'
    'font-size:24px;font-weight:800">🚪 설득의 문</div>',
    unsafe_allow_html=True,
)

top_l, top_r = st.columns([4, 1])
with top_l:
    picked = st.radio("스테이지", list(stage_titles), horizontal=True)
with top_r:
    st.write("")
    restart = st.button("▶ 시작 / 다시 도전하기", type="primary")

# 게임 상태 준비 (스테이지를 바꾸거나 다시 도전하면 새 게임)
if "game" not in st.session_state or restart or st.session_state.game["stage_id"] != stage_titles[picked]:
    st.session_state.game = new_game(stage_titles[picked])
    st.session_state.error = None

game = st.session_state.game
stage = STAGES[game["stage_id"]]

# 사용자 입력 처리
user_msg = st.chat_input(
    "게임이 끝났어요. 위의 '다시 도전하기'를 눌러 주세요." if game["done"] else "여기에 선배를 설득할 메시지를 입력하세요.",
    disabled=game["done"],
)
if user_msg and user_msg.strip() and not game["done"]:
    with st.spinner(f"{stage['character']}가 생각하는 중…"):
        try:
            st.session_state.game, _ = play_turn(game, user_msg.strip())
            st.session_state.error = None
        except Exception as e:
            st.session_state.error = friendly_error(e)
    if st.session_state.game["done"]:
        st.rerun()   # 게임이 끝나면 입력창을 잠그기 위해 화면을 새로 그려요
    game = st.session_state.game

left, right = st.columns([1, 2])

with left:
    img = character_image(game)
    if img:
        st.image(img)
    else:
        st.markdown(f"<div style='font-size:110px;text-align:center'>{MOOD_EMOJI[game['mood']]}</div>",
                    unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center;font-weight:800;color:#24345c'>{stage['character']}</div>",
                unsafe_allow_html=True)
    st.markdown(mood_html(game), unsafe_allow_html=True)
    st.write("")
    st.markdown(thought_html(game), unsafe_allow_html=True)

with right:
    st.markdown(score_bar_html(game), unsafe_allow_html=True)
    st.write("")
    with st.container(height=440):
        with st.chat_message("assistant", avatar="🧑"):
            st.markdown(f"**{stage['character']}**: {game['history'][0]['text']}")
        for i, r in enumerate(game["records"]):
            with st.chat_message("user"):
                st.markdown(r["user"])
            with st.chat_message("assistant", avatar="🧑"):
                st.markdown(f"**{stage['character']}**: {game['history'][2 + i * 2]['text']}")
            with st.chat_message("assistant", avatar="🧑‍⚖️"):
                score_txt = " · ".join(f"{k} {v}" for k, v in r["scores"].items())
                st.markdown(f"*심판 평가 — {score_txt}*  \n*{r['comment']}*")
        if game["done"]:
            label, last_line = stage["endings"][game["result"]]
            with st.chat_message("assistant", avatar="🧑"):
                st.markdown(f"**{stage['character']}**: {last_line}\n\n### {label}")
    if st.session_state.get("error"):
        st.warning(st.session_state.error)

if game["done"]:
    st.divider()
    st.markdown(report_markdown(game), unsafe_allow_html=True)

st.caption("무료 Gemini API를 사용해요. 입력한 내용이 모델 개선에 쓰일 수 있으니 실제 이름·연락처 같은 개인정보는 입력하지 마세요.")
