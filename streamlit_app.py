"""설득의 문 — LLM 기반 설득 연습 게임 (Streamlit 버전)

흐름: 캐릭터(대답·속마음·표정) → 심판(점수·톤·전략) → Python(점수 누적·결과 판정) → 리포트(마음 변화·대화 성향)
캐릭터 이미지: images/stage1_단호함.png 처럼 파일을 올리면 그 그림을 쓰고, 없으면 기본 그림(SVG)을 그려요.
"""
import base64
import html
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="설득의 문", page_icon="🚪", layout="wide", initial_sidebar_state="expanded")

# ═════════════════════════════════════════════
# 1. 기본 설정
# ═════════════════════════════════════════════
MAX_TURNS = 5
# 한 턴 최대 20점(4항목×5점)을 25점으로 환산 → 5턴 만점 125점, 100점 넘기면 성공
SCORE_SCALE = 1.25
MOODS = ["단호함", "고민", "수긍"]
MOOD_ICON = {"단호함": "🛡️", "고민": "❓", "수긍": "🤝"}
TONES = ["자신감", "정중함", "위축", "공격적", "과도한 사과"]
STRATEGIES = ["논리 호소", "감정 호소", "신뢰·예의", "대안 제시"]
RESULT_RANK = {"fail": 0, "partial": 1, "success": 2}
IMAGE_DIR = "images"
KST = ZoneInfo("Asia/Seoul")

NAVY = "#2b3a63"
NAVY_DARK = "#1f2b4d"
BEIGE = "#fde7c2"

# ═════════════════════════════════════════════
# 2. 스테이지 설정 (인턴에 도전하는 한 학기)
# ═════════════════════════════════════════════
STAGE_ORDER = ["stage1", "stage2", "stage3", "stage4"]

STAGES = {
    "stage1": {
        "title": "추천서 부탁",
        "skill": "윗사람에게 정중하게 부탁하기",
        "character": "교수님",
        "situation": (
            "사용자는 여름방학 IT 기업 인턴에 지원하려고 한다. 지원서에 교수 추천서가 필요해서, "
            "지난 학기 '데이터분석' 수업을 들었던 교수님 연구실에 찾아왔다. 교수님과 개인적으로 대화해 본 적은 거의 없다."
        ),
        "goal": "교수님이 추천서를 써 주기로 하는 것",
        "persona": "깐깐하지만 공정한 50대 교수. 존댓말을 쓰고 말이 짧고 건조하다. 근거 없는 칭찬이나 막연한 부탁을 싫어한다.",
        "first_line": "추천서요? 나랑 수업 하나 들은 걸로는 쓸 말이 없을 것 같은데.",
        "hidden": [
            "잘 모르는 학생의 추천서는 자기 이름을 걸고 책임져야 해서 부담스럽다.",
            "쓸 만한 '구체적인 근거'가 없으면 형식적인 추천서밖에 못 쓴다고 생각한다.",
            "마감이 촉박한 부탁은 더 곤란하다.",
        ],
        "persuaded_by": "수업에서 했던 구체적 활동과 성과(예: 기말 프로젝트), 지원하는 인턴과 수업 내용의 연결점, "
                        "이력서·프로젝트 요약 자료를 미리 정리해 드리겠다는 제안, 넉넉한 마감 일정",
        "fails_by": "'아무거나 좋게 써 주세요' 같은 막연한 부탁, 마감 직전 부탁, 과한 아부",
        "extra": "",
        "criteria": {
            "구체적 근거": ("수업 활동·성과 등 추천서에 쓸 구체적인 근거를 제시했는가", 1),
            "연결성": ("지원하는 인턴과 수업·경험의 연결점을 설명했는가", 1),
            "준비성": ("자료 정리, 넉넉한 일정 등 교수님의 부담을 줄이는 준비를 했는가", 1),
            "예의": ("정중하고 예의 바른가. 과한 아부나 무례함은 감점", 1),
        },
        "goal_score": 100,
        "examples": [
            ("교수님, 지난 학기 데이터분석 수업 기말 프로젝트에서 서울시 따릉이 데이터를 분석해 A+를 받은 학생입니다. "
             "이번에 데이터 기반 서비스 기획 인턴에 지원하는데, 그 프로젝트 경험을 바탕으로 추천서를 부탁드려도 될까요? "
             "프로젝트 요약과 이력서를 정리해서 메일로 보내드리고, 마감은 3주 뒤입니다.",
             {"구체적 근거": 5, "연결성": 5, "준비성": 5, "예의": 5}),
            ("교수님 그냥 좋게 아무거나 써 주시면 안 될까요? 내일까지 내야 해서요.",
             {"구체적 근거": 0, "연결성": 0, "준비성": 0, "예의": 2}),
            ("교수님 수업이 정말 최고였어요! 교수님 같은 분께 추천서를 받고 싶어요.",
             {"구체적 근거": 1, "연결성": 0, "준비성": 0, "예의": 3}),
        ],
        "endings": {
            "success": ("🟢 흔쾌히 써줌", "준비를 꽤 해 왔네요. 정리한 자료 메일로 보내 주세요. 써 드리죠."),
            "partial": ("🟡 조건부로 써줌", "음… 자료를 먼저 보고 결정할게요. 쓸 수 있는 만큼만 쓰겠습니다."),
            "fail": ("🔴 거절", "미안하지만 이번엔 어렵겠네요. 학생을 더 잘 아는 분께 부탁해 보세요."),
        },
        "placeholder": "여기에 교수님을 설득할 메시지를 입력하세요.",
        "look": {"bg": "#e6ebf4", "skin": "#f3d3b8", "hair": "short", "hair_color": "#a7acb4",
                 "outfit": "cardigan", "outfit_color": "#5a6478", "glasses": True},
    },
    "stage2": {
        "title": "공모전 팀 갈등",
        "skill": "의견 충돌 속에서 합의하기",
        "character": "팀원",
        "situation": (
            "사용자와 팀원은 교내 아이디어 공모전 팀이고 마감은 2주 남았다. 팀원은 'AR 캠퍼스 투어 앱'을 고집하지만, "
            "사용자와 다른 팀원들은 구현 기간과 심사 기준(실현 가능성 30%)을 고려해 '빈 강의실 찾기 서비스'가 낫다고 본다. "
            "지난 회의에서 팀원의 아이디어는 제대로 논의되지 못하고 넘어갔다."
        ),
        "goal": "팀원이 기분 상하지 않고 방향 조정(절충안 포함)에 동의하는 것",
        "persona": "열정 많고 고집 센 동기. 반말을 쓰고 말이 빠르며 감정이 잘 드러난다.",
        "first_line": "아니, 내 아이디어가 훨씬 참신하잖아. 왜 자꾸 바꾸자는 건데?",
        "hidden": [
            "지난 회의에서 자기 아이디어가 무시당했다고 느껴 자존심이 상해 있다.",
            "사실 2주 안에 AR을 구현하기 어렵다는 건 어느 정도 알고 있다.",
            "자기 아이디어가 조금이라도 반영되면 마음이 풀린다.",
        ],
        "persuaded_by": "상대 아이디어의 장점을 먼저 구체적으로 인정하기, 두 안을 합친 절충안(예: 빈 강의실 찾기 + AR 길안내 일부), "
                        "심사 기준·일정 같은 객관적 근거, 지난 회의에 대한 사과",
        "fails_by": "'네 건 별로야' 같은 정면 반박, 다수결로 밀어붙이기, 감정적인 비난이나 비꼼",
        "extra": "",
        "criteria": {
            "인정": ("상대 아이디어의 장점을 인정하고 감정을 존중했는가", 1),
            "절충안": ("두 의견을 합치거나 조정하는 구체적인 대안을 제시했는가", 1),
            "객관적 근거": ("일정, 심사 기준 등 객관적인 근거를 들었는가", 1),
            "감정 조절": ("차분하게 말했는가. 비난·비꼼·밀어붙이기는 감점", 1),
        },
        "goal_score": 100,
        "examples": [
            ("지난 회의 때 네 아이디어 제대로 못 들은 건 미안해. AR 투어는 진짜 참신해. 근데 심사 기준에 실현 가능성이 30%라서, "
             "빈 강의실 찾기를 메인으로 하고 네 AR 길안내를 핵심 기능으로 넣으면 어때?",
             {"인정": 5, "절충안": 5, "객관적 근거": 5, "감정 조절": 5}),
            ("솔직히 AR은 무리야. 다수결로 정하자.",
             {"인정": 0, "절충안": 0, "객관적 근거": 1, "감정 조절": 1}),
            ("너 때문에 회의가 맨날 길어지잖아.",
             {"인정": 0, "절충안": 0, "객관적 근거": 0, "감정 조절": 0}),
        ],
        "endings": {
            "success": ("🟢 합의 성공", "…알겠어. 내 AR 길안내를 넣는 거면 해볼 만하네. 같이 가자."),
            "partial": ("🟡 마지못해 동의", "알았어, 일단 너희 안으로 가. 근데 솔직히 좀 서운하다."),
            "fail": ("🔴 팀 분위기 악화", "됐어. 그럼 너희끼리 정해. 나는 내 파트만 할게."),
        },
        "placeholder": "여기에 팀원을 설득할 메시지를 입력하세요.",
        "look": {"bg": "#fbeee2", "skin": "#f1cfb2", "hair": "messy", "hair_color": "#2f2a33",
                 "outfit": "hoodie", "outfit_color": "#d9734f", "glasses": False},
    },
    "stage3": {
        "title": "실수 보고",
        "skill": "책임감 있게 나쁜 소식 전하기",
        "character": "선배",
        "situation": (
            "사용자는 코딩 동아리의 '신입 부원 환영 해커톤' 행사 담당이다. 행사 장소(공학관 대강의실) 예약을 깜빡해서, "
            "행사 1주일 전에 다른 단체가 이미 예약했다는 걸 알게 됐다. 지금 이 사실을 동아리 회장인 선배에게 직접 말하러 왔다."
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
        "extra": "",
        "criteria": {
            "솔직함": ("돌려 말하거나 축소하지 않고 사실을 분명히 말했는가", 1),
            "책임 인정": ("남 탓이나 변명 없이 자기 실수로 인정했는가", 1),
            "해결 방안": ("대안 장소, 일정, 비용 등 구체적인 해결책을 제시했는가", 1),
            "태도": ("차분하고 간결한가. 사과만 반복하거나 지나치게 자책하면 감점", 1),
        },
        "goal_score": 100,
        "examples": [
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
            "fail": ("🔴 신뢰 하락", "…이건 내가 처리할게. 너는 일단 빠져 있어."),
        },
        "placeholder": "여기에 선배를 설득할 메시지를 입력하세요.",
        "look": {"bg": "#ecebf3", "skin": "#f5d9c4", "hair": "bun", "hair_color": "#4a342a",
                 "outfit": "hoodie", "outfit_color": "#2f3b5c", "glasses": False},
    },
    "stage4": {
        "title": "인턴 면접",
        "skill": "나를 어필하기",
        "character": "면접관",
        "situation": (
            "사용자는 대학생용 일정·모임 관리 앱 '모아(MOA)'를 운영하는 IT 스타트업의 '서비스 기획 인턴'(6주) 최종 면접에 들어왔다. "
            "면접관은 기획팀 팀장이며, 대학생 지원자의 경험과 태도를 확인하려 한다."
        ),
        "goal": "면접관에게 '같이 일하고 싶은 지원자'라는 확신을 주는 것(합격)",
        "persona": "차분하지만 날카로운 기획팀 팀장. 정중한 존댓말을 쓰고, 짧게 반응한 뒤 다음 질문을 던진다. 압박 질문을 섞는다.",
        "first_line": "자기소개는 서류로 봤으니까, 우리 회사에 왜 지원했는지부터 들어볼게요.",
        "hidden": [
            "스펙보다 압박 질문에도 침착한지, 같이 일하기 편한 사람인지를 본다.",
            "외운 듯한 모범답안을 들으면 바로 흥미를 잃는다.",
            "구체적인 경험과 거기서 배운 점을 말하면 크게 호감을 느낀다.",
        ],
        "persuaded_by": "실제 경험(팀 프로젝트, 갈등 해결, 실수 수습 등)을 근거로 한 답, 단점을 솔직히 말하고 개선 노력까지 말하기, "
                        "회사와 직무에 대한 구체적인 이해",
        "fails_by": "외운 듯한 모범답안, 압박 질문에 방어적·감정적 반응, 근거 없는 자신감",
        "extra": ("너는 면접관이다. 매 턴 사용자의 답변에 1문장으로 짧게 반응한 뒤, 다음 면접 질문을 하나 던진다. "
                  "질문 예: 본인의 단점, 실패하거나 실수했던 경험, 팀에서 갈등을 해결한 경험, 다른 지원자와 다른 점, 입사 후 하고 싶은 일. "
                  "같은 질문을 반복하지 않는다."),
        "criteria": {
            "경험 근거": ("실제 경험을 구체적인 근거로 들었는가", 1),
            "침착함": ("압박 질문에도 방어적이지 않고 차분하게 답했는가", 1),
            "솔직함": ("단점·한계를 솔직하게 말하고 개선 노력까지 말했는가. 외운 듯한 답은 감점", 1),
            "직무 이해": ("회사와 서비스 기획 직무를 구체적으로 이해하고 연결했는가", 1),
        },
        "goal_score": 100,
        "examples": [
            ("공모전에서 팀원과 아이디어 충돌이 있었을 때, 두 안의 장점을 합친 절충안을 내서 마감 안에 서비스를 완성했습니다. "
             "그 과정에서 사용자 문제를 먼저 정의하는 게 기획의 시작이라는 걸 배웠고, 귀사의 사용자 리서치 문화에서 그걸 더 키우고 싶습니다.",
             {"경험 근거": 5, "침착함": 5, "솔직함": 4, "직무 이해": 5}),
            ("저는 단점이 없습니다. 무엇이든 열심히 하는 성실한 인재입니다.",
             {"경험 근거": 0, "침착함": 3, "솔직함": 0, "직무 이해": 0}),
            ("그건 좀 억울한 질문인데요. 제 스펙 보시면 아실 텐데요.",
             {"경험 근거": 0, "침착함": 0, "솔직함": 1, "직무 이해": 0}),
        ],
        "endings": {
            "success": ("🟢 합격", "좋습니다. 같이 일해 보고 싶네요. 좋은 소식 곧 전해 드릴게요."),
            "partial": ("🟡 보류", "수고하셨습니다. 다른 지원자들과 함께 검토해 보고 연락드릴게요."),
            "fail": ("🔴 불합격", "네, 오늘 면접은 여기까지 하겠습니다. 수고하셨어요."),
        },
        "placeholder": "면접관의 질문에 답해 보세요.",
        "look": {"bg": "#edf0ea", "skin": "#f2d2bc", "hair": "bob", "hair_color": "#1f1b24",
                 "outfit": "blazer", "outfit_color": "#2b2d42", "glasses": False},
    },
}

# ═════════════════════════════════════════════
# 2-1. 대화 전에 보여줄 상황 설명 (브리핑)
#      여기 적힌 '나의 정보'는 캐릭터·심판에게도 똑같이 전달돼요.
# ═════════════════════════════════════════════
BRIEFS = {
    "stage1": {
        "headline": "인턴 지원서에 필요한 추천서, 교수님께 부탁해야 해요",
        "situation": [
            "여름방학 IT 기업 <b>'데이터 기반 서비스 기획' 인턴</b>에 지원하려고 해요.",
            "지원서에 <b>교수 추천서 1부</b>가 필요하고, 마감은 <b>3주 뒤</b>예요.",
            "지난 학기 수업을 들었던 교수님 연구실에 찾아왔어요.",
        ],
        "me": [
            "지난 학기 <b>'데이터분석'</b> 수업 수강, 성적 <b>A+</b>",
            "기말 프로젝트: <b>서울시 따릉이 이용 데이터 분석 → 대여소 재배치 제안</b> (팀장, 발표 1위)",
            "준비할 수 있는 자료: 이력서, 프로젝트 요약 1장",
        ],
        "opponent": "데이터분석 수업 담당 교수님. 깐깐하지만 공정해요. 개인적으로 대화해 본 적은 거의 없어요.",
        "tip": "교수님은 근거 없는 칭찬을 싫어해요. 무엇을 써 줄 수 있을지 떠올리게 해 주세요.",
    },
    "stage2": {
        "headline": "공모전 마감 2주 전, 팀 방향을 다시 정해야 해요",
        "situation": [
            "교내 <b>'캠퍼스 생활 개선 아이디어 공모전'</b>을 4명이 한 팀으로 준비 중이에요. 마감은 <b>2주 뒤</b>예요.",
            "심사 기준: <b>창의성 30 · 실현 가능성 30 · 사용자 가치 40</b>",
            "팀원은 <b>'AR 캠퍼스 투어 앱'</b>을 고집하고, 나와 다른 팀원들은 <b>'빈 강의실 찾기 서비스'</b>가 낫다고 생각해요.",
            "지난 회의 때 시간에 쫓겨 팀원의 아이디어를 제대로 듣지 못하고 넘어갔어요.",
        ],
        "me": [
            "빈 강의실 찾기 서비스: 2주 안에 웹으로 구현 가능, 학생 설문에서 '빈 강의실 찾기 어렵다' 응답 72%",
            "AR 투어 앱: 참신하지만 AR 개발 경험이 있는 팀원이 없음",
        ],
        "opponent": "열정 많고 고집 센 동기. 반말을 쓰고 감정이 잘 드러나요.",
        "tip": "누가 맞는지 이기는 게 목표가 아니에요. 팀원이 왜 이렇게 날이 서 있는지 생각해 보세요.",
    },
    "stage3": {
        "headline": "행사 1주일 전, 장소 예약을 놓친 걸 알게 됐어요",
        "situation": [
            "나는 코딩 동아리의 <b>'신입 부원 환영 해커톤'</b> 행사 담당이에요.",
            "행사: <b>다음 주 금요일 오후 6시</b>, 참가자 <b>40명</b>",
            "3주 전에 해야 했던 <b>공학관 대강의실 예약을 깜빡</b>했고, 오늘 확인해 보니 다른 단체가 이미 예약했어요.",
        ],
        "me": [
            "급히 알아본 대안 ①: <b>학생회관 세미나실</b> (최대 35명, 무료, 금요일 저녁 가능)",
            "급히 알아본 대안 ②: <b>도서관 그룹스터디룸 2개</b> (합쳐서 40명, 최대 4시간)",
            "오늘 안에 신청하면 둘 중 하나는 예약할 수 있어요.",
        ],
        "opponent": "동아리 회장 선배. 평소엔 다정하지만 일에는 엄격하고, 요즘 다른 동아리 일로 바빠요.",
        "tip": "사과만으로는 부족해요. 선배가 이 소식을 듣고 가장 걱정할 게 무엇일지 생각해 보세요.",
    },
    "stage4": {
        "headline": "드디어 최종 면접이에요",
        "situation": [
            "대학생용 일정·모임 관리 앱 <b>'모아(MOA)'</b>를 운영하는 IT 스타트업이에요.",
            "지원 직무: <b>서비스 기획 인턴</b> (6주)",
            "최종 1:1 면접, 면접관은 <b>기획팀 팀장</b>이에요. 면접관이 질문하고 내가 답해요.",
        ],
        "me": [
            "데이터분석 수업 프로젝트: 따릉이 데이터 분석 → 대여소 재배치 제안 (팀장)",
            "공모전에서 팀원과 아이디어가 부딪혔을 때 절충안을 내서 마감 안에 완성",
            "동아리 행사 장소 예약 실수 → 바로 보고하고 대안 장소로 수습",
        ],
        "opponent": "차분하지만 날카로운 기획팀 팀장. 압박 질문을 섞어요.",
        "tip": "앞 스테이지에서 겪은 일들이 모두 면접 소재예요. 외운 답보다 경험을 이야기하세요.",
    },
}


def strip_tags(t):
    return re.sub(r"<[^>]+>", "", t)


def stage_context(stage_id):
    """캐릭터·심판에게 줄 상황 설명 (브리핑의 사실까지 포함)"""
    stage, brief = STAGES[stage_id], BRIEFS[stage_id]
    facts = "\n".join("- " + strip_tags(x) for x in brief["situation"] + brief["me"])
    return f"{stage['situation']}\n[사용자 쪽 사실]\n{facts}"


RESULT_BUBBLE = {
    "success": ("훌륭해요!", "상대의 속마음을 정확히 읽은 설득이었어요."),
    "partial": ("좋아요!", "핵심은 잘 짚었어요. 조금만 더 다듬으면 완벽해요."),
    "fail": ("아쉬워요!", "상대가 무엇을 걱정하는지 다시 생각해 봐요."),
}

# ═════════════════════════════════════════════
# 3. Gemini 연결 (무료 Flash 모델 자동 선택)
# ═════════════════════════════════════════════
def get_api_key():
    """Streamlit 앱 설정의 Secrets 에 저장한 GEMINI_API_KEY 를 읽어요."""
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return os.environ.get("GEMINI_API_KEY")


@st.cache_resource
def get_client():
    return genai.Client(api_key=get_api_key())


MODEL_CANDIDATES = ["gemini-3.5-flash", "gemini-flash-latest", "gemini-2.5-flash"]


@st.cache_resource
def pick_model():
    for m in MODEL_CANDIDATES:
        try:
            get_client().models.generate_content(model=m, contents="hi")
            return m
        except Exception as e:
            print(f"{m} 사용 불가: {str(e)[:100]}")
    return None


def error_code(e):
    """오류에서 숫자 코드(429, 503 등)를 꺼내요."""
    code = getattr(e, "code", None)
    if isinstance(code, int):
        return code
    m = re.match(r"\s*(\d{3})", str(e))
    return int(m.group(1)) if m else None


def is_rate_limit(e):
    s = str(e)
    return error_code(e) == 429 or "RESOURCE_EXHAUSTED" in s


def is_busy(e):
    """Gemini 서버가 잠깐 바쁜 경우 (503 high demand 등) — 잠깐 기다리면 풀려요."""
    return error_code(e) in (500, 502, 503, 504) or "UNAVAILABLE" in str(e)


def friendly_error(e):
    """화면에는 한국어 안내만 보여주고, 자세한 오류는 Streamlit 로그에만 남겨요."""
    print(f"[오류] {type(e).__name__}: {str(e)[:300]}")
    if is_rate_limit(e):
        return "⏳ 지금 AI 사용량이 많아요. 1분쯤 기다렸다가 같은 메시지를 다시 보내 주세요. (턴은 줄지 않았어요)"
    if is_busy(e):
        return "😅 AI가 잠깐 바빠요. 잠시 후 같은 메시지를 다시 보내 주세요. (턴은 줄지 않았어요)"
    if isinstance(e, json.JSONDecodeError):
        return "🤔 AI 대답을 제대로 읽지 못했어요. 같은 메시지를 다시 보내 주세요. (턴은 줄지 않았어요)"
    if error_code(e) in (401, 403):
        return "🔑 AI 연결 설정(API 키)에 문제가 있어요. 앱 설정의 Secrets를 확인해 주세요."
    return "⚠️ AI와 연결이 잠깐 끊겼어요. 같은 메시지를 다시 보내 주세요. (턴은 줄지 않았어요)"


def ask_gemini_json(system_prompt, user_prompt, temperature, retries=2, client=None):
    """Gemini에게 JSON으로 대답을 받아요.
    - 서버가 바쁘면(503 등) 2초, 4초 기다렸다가 같은 모델로 다시 시도해요.
    - 그래도 안 되거나 사용량 한도(429)에 걸리면 다른 Flash 모델로 바꿔서 시도해요.
    - 모든 모델이 사용량 한도에 걸렸으면 20초 기다렸다가 한 번 더 시도해요.
    - 전부 실패하면 마지막 오류를 넘겨서 화면에 한국어 안내가 나오게 해요."""
    client = client or get_client()
    try:
        return _ask_all_models(system_prompt, user_prompt, temperature, retries, client)
    except Exception as e:
        if not is_rate_limit(e):
            raise
        print("[사용량 한도] 20초 기다렸다가 다시 시도해요.")
        time.sleep(20)
        return _ask_all_models(system_prompt, user_prompt, temperature, 0, client)


# ── 속도 올리기: AI가 대답하기 전에 오래 '생각'하지 않게 해요 ──
# 대화·채점은 깊은 추론이 필요 없어서, 생각 단계를 줄이면 대답이 훨씬 빨라져요.
NO_FAST_THINKING = set()  # 이 설정을 받아 주지 않는 모델은 여기에 기록하고 기본값으로 불러요


def fast_thinking(model):
    if model in NO_FAST_THINKING:
        return None
    try:
        if model.startswith("gemini-2.5"):
            return types.ThinkingConfig(thinking_budget=0)
        return types.ThinkingConfig(thinking_level="low")
    except Exception:  # 설치된 라이브러리가 이 설정을 모르면 기본값 사용
        NO_FAST_THINKING.add(model)
        return None


def _generate_json(client, model, system_prompt, user_prompt, temperature):
    def config():
        return types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            response_mime_type="application/json",
            thinking_config=fast_thinking(model),
        )
    try:
        res = client.models.generate_content(model=model, contents=user_prompt, config=config())
    except Exception as e:
        msg = str(e).lower()
        if error_code(e) == 400 and model not in NO_FAST_THINKING and ("think" in msg or "budget" in msg):
            NO_FAST_THINKING.add(model)  # 생각 줄이기를 지원 안 하는 모델 → 기본 설정으로 다시
            res = client.models.generate_content(model=model, contents=user_prompt, config=config())
        else:
            raise
    text = re.sub(r"^```(json)?|```$", "", (res.text or "").strip()).strip()
    return json.loads(text)


def _ask_all_models(system_prompt, user_prompt, temperature, retries, client):
    models = [MODEL] + [m for m in MODEL_CANDIDATES if m != MODEL]
    last_error = None
    for i, model in enumerate(models):
        attempts = retries + 1 if i == 0 else 1
        for attempt in range(attempts):
            try:
                return _generate_json(client, model, system_prompt, user_prompt, temperature)
            except json.JSONDecodeError as e:
                last_error = e  # 형식이 깨졌으면 다시 시도
            except Exception as e:
                last_error = e
                print(f"[{model}] {attempt + 1}번째 시도 실패: {str(e)[:100]}")
                if is_busy(e):
                    if attempt < attempts - 1:
                        time.sleep(2 * (attempt + 1))
                    continue
                if is_rate_limit(e) or error_code(e) == 404:
                    break  # 이 모델은 지금 못 쓰니 다음 모델로
                raise  # API 키 문제 등 기다려도 안 풀리는 오류
    raise last_error


# ═════════════════════════════════════════════
# 4. 캐릭터 · 심판 · 리포트 프롬프트
# ═════════════════════════════════════════════
def transcript(stage, history):
    who = lambda h: stage["character"] if h["role"] == "character" else "사용자"
    return "\n".join(f"{who(h)}: {h['text']}" for h in history)


def call_character(stage, history, user_msg, turn, ctx, client=None):
    """① 캐릭터: 대답 + 속마음 + 표정"""
    hidden = "\n- ".join(stage["hidden"])
    system = f"""너는 설득 연습 게임 속 캐릭터 '{stage['character']}'이다. 반드시 이 역할로만 대답한다.

[상황] {ctx}
[성격·말투] {stage['persona']}
[숨은 속사정 — 절대 직접 말하지 말 것]
- {hidden}
[마음이 움직이는 말] {stage['persuaded_by']}
[마음이 닫히는 말] {stage['fails_by']}
{stage['extra']}

[규칙]
- 1~3문장, 자연스러운 한국어 구어체로 대답한다.
- 숨은 속사정이 해결되기 전에는 쉽게 수긍하지 않는다. 한 번 좋은 말을 들었다고 바로 넘어가지 않는다.
- 사용자가 무례하거나 변명하면 실망·피로가 드러나게 반응한다.
- 지금은 {turn}/{MAX_TURNS}턴이다. 게임의 최종 결과는 네가 정하지 않으니 최종 결론(수락·거절·합격 등)은 말하지 않는다.
- inner_thought는 속으로 하는 생각 한 줄(20자 내외)이다.
- mood는 "단호함", "고민", "수긍" 중 하나다.

[출력 형식] JSON만 출력:
{{"reply": "대답", "inner_thought": "속마음 한 줄", "mood": "단호함|고민|수긍"}}"""
    prompt = f"[지금까지 대화]\n{transcript(stage, history)}\n\n[사용자의 새 발언]\n{user_msg}"
    out = ask_gemini_json(system, prompt, temperature=0.8, client=client)
    if out.get("mood") not in MOODS:
        out["mood"] = "고민"
    return out


def call_judge(stage, history, user_msg, ctx, client=None):
    """② 심판: 항목별 점수 + 한 줄 평가 + 사용자 톤/전략"""
    crit = "\n".join(f"- {k}: {v[0]} (0~5점)" for k, v in stage["criteria"].items())
    shots = "\n".join(f"발언: {ex}\n점수: {json.dumps(sc, ensure_ascii=False)}" for ex, sc in stage["examples"])
    score_keys = ", ".join(f'"{k}": 0' for k in stage["criteria"])
    tone_list, strategy_list = ", ".join(TONES), ", ".join(STRATEGIES)
    system = f"""너는 설득 연습 게임의 공정한 심판이다. 사용자의 '마지막 발언 하나'만 채점한다.

[상황] {ctx}
[사용자의 목표] {stage['goal']}

[채점 기준]
{crit}

[채점 예시]
{shots}

[분류]
- user_tone: {tone_list} 중 하나
- strategy: {strategy_list} 중 하나 (가장 두드러진 것)

[출력 형식] JSON만 출력:
{{"scores": {{{score_keys}}}, "comment": "25자 내외 한 줄 평가", "user_tone": "...", "strategy": "..."}}"""
    prompt = f"[지금까지 대화]\n{transcript(stage, history)}\n\n[채점할 사용자 발언]\n{user_msg}"
    out = ask_gemini_json(system, prompt, temperature=0.1, client=client)
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


def call_report(stage, game, result_label, ctx):
    """③ 리포트: 결정적 발언, 피드백, 한 줄 요약, 대화 성향, Before/After"""
    rec_text = "\n".join(
        f"{i+1}턴 | 사용자: {r['user']} | 점수 {r['turn_score']} | 톤 {r['tone']} | 전략 {r['strategy']} | 상대 표정 {r['mood']}"
        for i, r in enumerate(game["records"])
    )
    system = """너는 커뮤니케이션 코치다. 설득 연습 기록을 보고 따뜻하지만 정확하게 피드백한다.
성격이나 심리를 '진단'하지 말고, '이번 대화에서 나타난 패턴'으로만 말한다. 임상 용어는 쓰지 않는다.
[출력 형식] JSON만 출력:
{"key_line": "상대 마음을 가장 크게 움직인 사용자 발언(원문 그대로)",
 "key_reason": "왜 효과적이었는지 한 문장",
 "good_point": "잘한 점 한두 문장",
 "improve_point": "다음에 보완할 점 한두 문장",
 "one_line": "이번 설득을 요약하는 한 문장",
 "style_summary": "이번 대화에서 나타난 대화 성향 한두 문장",
 "before": "가장 아쉬웠던 사용자 발언(원문 그대로)",
 "after": "그 발언을 더 낫게 고친 예시"}"""
    prompt = (f"[상황] {ctx}\n[목표] {stage['goal']}\n[결과] {result_label}\n\n"
              f"[대화]\n{transcript(stage, game['history'])}\n\n[턴별 기록]\n{rec_text}")
    out = ask_gemini_json(system, prompt, temperature=0.4)
    if isinstance(out, list) and out and isinstance(out[0], dict):
        out = out[0]  # 가끔 [{...}] 처럼 목록으로 감싸서 줄 때가 있어요
    if not isinstance(out, dict) or not out.get("one_line"):
        raise ValueError("리포트 형식이 올바르지 않아요")
    return out


REPORT_FAILED = "(AI가 바빠서 리포트를 만들지 못했어요. 아래 '리포트 다시 만들기'를 눌러 주세요.)"


def make_report(game):
    """게임이 끝난 뒤 리포트를 만들어 game에 넣어요. 실패하면 다시 만들 수 있게 표시해 둬요."""
    stage = STAGES[game["stage_id"]]
    try:
        game["report"] = call_report(stage, game, stage["endings"][game["result"]][0],
                                     stage_context(game["stage_id"]))
    except Exception as e:
        print(f"[리포트 오류] {type(e).__name__}: {str(e)[:200]}")
        game["report"] = {"failed": True, "one_line": REPORT_FAILED}


# ═════════════════════════════════════════════
# 5. 게임 규칙 — 판정은 AI가 아니라 Python이 해요
# ═════════════════════════════════════════════
def now_hm():
    return datetime.now(KST).strftime("%H:%M")


def new_game(stage_id, started=False):
    stage = STAGES[stage_id]
    return {
        "stage_id": stage_id, "started": started,
        "history": [{"role": "character", "text": stage["first_line"], "time": now_hm(), "mood": "단호함"}],
        "records": [], "score": 0, "turn": 0,
        "mood": "단호함", "thought": "…", "done": False, "result": None, "report": None,
    }


def judge_result(stage, score):
    goal = stage["goal_score"]
    if score >= goal:
        return "success"
    if score >= goal * 0.6:
        return "partial"
    return "fail"


def play_turn(game, user_msg):
    stage = STAGES[game["stage_id"]]
    turn = game["turn"] + 1
    ctx = stage_context(game["stage_id"])
    # 캐릭터 대답과 심판 채점은 서로 기다릴 필요가 없어서 동시에 불러요 (대기 시간 약 절반)
    client = get_client()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_char = pool.submit(call_character, stage, game["history"], user_msg, turn, ctx, client)
        f_judge = pool.submit(call_judge, stage, game["history"], user_msg, ctx, client)
        char, judge = f_char.result(), f_judge.result()

    turn_score = round(sum(judge["scores"][k] * w for k, (_, w) in stage["criteria"].items()) * SCORE_SCALE)
    game["score"] = min(stage["goal_score"], game["score"] + turn_score)
    game["turn"] = turn
    game["mood"] = char["mood"]
    game["thought"] = char.get("inner_thought", "…")
    game["history"].append({"role": "user", "text": user_msg, "time": now_hm()})
    game["history"].append({"role": "character", "text": char.get("reply", "…"), "time": now_hm(), "mood": char["mood"]})
    game["records"].append({
        "user": user_msg, "scores": judge["scores"], "turn_score": turn_score,
        "comment": judge.get("comment", ""), "tone": judge["user_tone"],
        "strategy": judge["strategy"], "mood": char["mood"],
    })

    if game["score"] >= stage["goal_score"] or turn >= MAX_TURNS:
        game["done"] = True
        game["result"] = judge_result(stage, game["score"])
        make_report(game)
    return game


# ═════════════════════════════════════════════
# 6. 캐릭터 그림 (SVG) — images 폴더에 그림이 있으면 그걸 써요
# ═════════════════════════════════════════════
def _hair(look, part):
    c, s = look["hair_color"], look["hair"]
    if part == "back":
        if s == "bun":
            return f'<circle cx="100" cy="40" r="18" fill="{c}"/>'
        if s == "bob":
            return f'<path d="M50 132 Q40 52 100 44 Q160 52 150 132 Q150 140 136 138 L64 138 Q50 140 50 132 Z" fill="{c}"/>'
        return ""
    if s == "short":
        return f'<path d="M60 94 Q54 46 100 44 Q146 46 140 94 Q132 68 112 64 Q100 72 84 64 Q66 70 60 94 Z" fill="{c}"/>'
    if s == "messy":
        return (f'<path d="M57 96 Q48 52 80 44 L85 30 L97 43 L108 28 L116 43 L132 36 L129 50 Q152 60 143 96 '
                f'Q134 70 113 64 Q98 76 84 65 Q66 71 57 96 Z" fill="{c}"/>')
    if s == "bun":
        return (f'<path d="M59 100 Q52 48 100 45 Q148 48 141 100 Q134 70 117 64 Q103 77 86 66 Q68 72 63 100 Z" fill="{c}"/>'
                f'<path d="M61 96 Q55 118 63 132" stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round"/>'
                f'<path d="M139 96 Q145 118 137 132" stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round"/>')
    if s == "bob":
        return f'<path d="M60 94 Q57 48 100 46 Q143 48 140 94 Q128 64 100 66 Q72 64 60 94 Z" fill="{c}"/>'
    return ""


def _outfit(look):
    c = look["outfit_color"]
    body = f'<path d="M26 200 Q28 148 100 144 Q172 148 174 200 Z" fill="{c}"/>'
    o = look["outfit"]
    if o == "hoodie":
        return body + (f'<path d="M68 150 Q100 178 132 150" stroke="#00000033" stroke-width="9" fill="none" stroke-linecap="round"/>'
                       '<path d="M92 166 L90 190 M108 166 L110 190" stroke="#ffffffcc" stroke-width="3" stroke-linecap="round"/>')
    if o == "blazer":
        return body + ('<path d="M84 146 L100 178 L116 146 Z" fill="#ffffff"/>'
                       '<path d="M80 148 L100 194 L90 200 L64 200 L66 160 Z" fill="#00000030"/>'
                       '<path d="M120 148 L100 194 L110 200 L136 200 L134 160 Z" fill="#00000030"/>')
    if o == "cardigan":
        return body + ('<path d="M84 146 L100 172 L116 146 Z" fill="#ffffff"/>'
                       '<path d="M97 154 L103 154 L105 182 L100 189 L95 182 Z" fill="#8b3a3a"/>'
                       '<circle cx="112" cy="182" r="2.5" fill="#ffffffaa"/><circle cx="112" cy="194" r="2.5" fill="#ffffffaa"/>')
    return body


def _face(look, mood):
    hc = look["hair_color"]
    ink, lip = "#2b2b2b", "#9a4a3e"
    nose = '<path d="M100 104 Q97 112 101 113" stroke="#c99a7e" stroke-width="2.5" fill="none" stroke-linecap="round"/>'
    if mood == "단호함":
        f = (f'<path d="M74 86 L92 90 M108 90 L126 86" stroke="{hc}" stroke-width="4.5" stroke-linecap="round"/>'
             f'<ellipse cx="84" cy="101" rx="4.5" ry="5.5" fill="{ink}"/><ellipse cx="116" cy="101" rx="4.5" ry="5.5" fill="{ink}"/>'
             f'<path d="M90 124 L110 124" stroke="{lip}" stroke-width="3.5" stroke-linecap="round"/>')
    elif mood == "고민":
        f = (f'<path d="M74 84 Q83 78 92 84 M108 89 L126 87" stroke="{hc}" stroke-width="4.5" fill="none" stroke-linecap="round"/>'
             f'<ellipse cx="86" cy="101" rx="4.5" ry="5.5" fill="{ink}"/><ellipse cx="118" cy="101" rx="4.5" ry="5.5" fill="{ink}"/>'
             f'<path d="M90 125 Q95 121 100 125 Q105 129 110 125" stroke="{lip}" stroke-width="3" fill="none" stroke-linecap="round"/>')
    else:
        f = (f'<path d="M74 86 Q83 81 92 86 M108 86 Q117 81 126 86" stroke="{hc}" stroke-width="4.5" fill="none" stroke-linecap="round"/>'
             f'<path d="M78 102 Q84 95 90 102 M110 102 Q116 95 122 102" stroke="{ink}" stroke-width="3.5" fill="none" stroke-linecap="round"/>'
             '<ellipse cx="74" cy="113" rx="7" ry="4" fill="#f29b93" opacity=".55"/>'
             '<ellipse cx="126" cy="113" rx="7" ry="4" fill="#f29b93" opacity=".55"/>'
             '<path d="M89 119 Q100 132 111 119 Z" fill="#b8473f"/>')
    if look.get("glasses"):
        f += ('<circle cx="84" cy="101" r="11" stroke="#3b3b3b" stroke-width="2.5" fill="none"/>'
              '<circle cx="116" cy="101" r="11" stroke="#3b3b3b" stroke-width="2.5" fill="none"/>'
              '<path d="M95 101 L105 101" stroke="#3b3b3b" stroke-width="2.5"/>')
    return nose + f


def _deco(mood):
    if mood == "고민":
        return ('<path d="M150 58 l7 -10 M158 66 l10 -6 M160 78 l11 -1" stroke="#2b2b2b" stroke-width="3" stroke-linecap="round"/>'
                '<path d="M146 92 Q151 101 146 105 Q141 101 146 92 Z" fill="#8cc8ee"/>')
    if mood == "수긍":
        return ('<path d="M156 52 l3 8 l8 3 l-8 3 l-3 8 l-3 -8 l-8 -3 l8 -3 Z" fill="#f2b84b"/>'
                '<path d="M40 64 l2 5 l5 2 l-5 2 l-2 5 l-2 -5 l-5 -2 l5 -2 Z" fill="#f2b84b"/>')
    return '<path d="M150 70 L168 66 M150 80 L168 82" stroke="#2b2b2b55" stroke-width="3" stroke-linecap="round"/>'


def character_svg(look, mood, fit="slice"):
    skin = look["skin"]
    tilt = ' transform="rotate(-4 100 110)"' if mood == "고민" else ""
    return (
        f'<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="{"xMidYMax meet" if fit == "meet" else "xMidYMid slice"}" '
        'style="width:100%;height:100%;display:block">'
        f'<rect width="200" height="200" fill="{look["bg"]}"/>'
        '<circle cx="30" cy="34" r="26" fill="#ffffff" opacity=".45"/>'
        '<rect x="150" y="110" width="40" height="60" rx="6" fill="#ffffff" opacity=".35"/>'
        f'{_outfit(look)}'
        f'<g{tilt}>{_hair(look, "back")}'
        f'<rect x="89" y="120" width="22" height="30" rx="9" fill="{skin}"/>'
        f'<ellipse cx="59" cy="102" rx="7" ry="10" fill="{skin}"/><ellipse cx="141" cy="102" rx="7" ry="10" fill="{skin}"/>'
        f'<ellipse cx="100" cy="96" rx="41" ry="47" fill="{skin}"/>'
        f'{_hair(look, "front")}{_face(look, mood)}</g>'
        f'{_deco(mood)}</svg>'
    )


@st.cache_data
def image_data_uri(path, mtime):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = path.rsplit(".", 1)[-1].lower()
    return f"data:image/{'jpeg' if ext in ('jpg', 'jpeg') else ext};base64,{b64}"


def character_art(stage_id, mood, fit="slice"):
    """images 폴더에 그림이 있으면 그 그림, 없으면 기본 SVG"""
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = os.path.join(IMAGE_DIR, f"{stage_id}_{mood}.{ext}")
        if os.path.exists(p):
            uri = image_data_uri(p, os.path.getmtime(p))
            return f'<img src="{uri}" style="width:100%;height:100%;object-fit:cover;display:block"/>'
    return character_svg(STAGES[stage_id]["look"], mood, fit)


# ═════════════════════════════════════════════
# 7. 화면 디자인 (CSS)
# ═════════════════════════════════════════════
CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
html, body, [class*="css"], .stApp, button, input, textarea { font-family: 'Pretendard', -apple-system, sans-serif !important; }
.stApp { background: #f5f6fa; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }
#MainMenu, footer { visibility: hidden; }

/* ── 상단 바 ── */
.st-key-topbar { background: #2b3a63; border-radius: 18px; padding: 12px 20px; margin-bottom: 18px;
                 box-shadow: 0 6px 18px rgba(31,43,77,.18); }
.logo { display:flex; align-items:center; gap:12px; color:#fff; font-size:28px; font-weight:800; letter-spacing:-0.5px; }
.st-key-topbar button { border-radius: 999px !important; border: 2px solid transparent !important; background: #eef0f6 !important;
                        color: #2b3a63 !important; font-weight: 700 !important; padding: 6px 4px !important; min-height: 44px; }
.st-key-topbar button p { font-size: 15px !important; font-weight: 700 !important; white-space: normal !important; word-break: keep-all; }
.st-key-topbar button [data-testid="stMarkdownContainer"] { overflow: visible !important; text-overflow: clip !important; white-space: normal !important; }
/* 노트북처럼 좁은 화면에서는 상단 버튼을 두 줄로 나눠서 글자가 '…'로 잘리지 않게 */
@media (max-width: 1200px) {
  .st-key-topbar [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; row-gap: 8px !important; }
  .st-key-topbar [data-testid="stColumn"] { flex: 1 1 auto !important; width: auto !important; min-width: fit-content !important; }
  .st-key-topbar button { padding: 6px 12px !important; }
}
.st-key-topbar button:hover { background: #ffffff !important; }
.st-key-topbar [class*="st-key-tabon_"] button { background: #1f2b4d !important; color: #fff !important; border: 2px solid #ffffff !important; }
.st-key-topbar button:disabled { background: #4a5679 !important; color: #b9c0d4 !important; opacity: 1; }
.pill { background:#fde7c2; color:#2b3a63; border-radius:999px; padding:10px 18px; font-weight:800; text-align:center; white-space:nowrap; }

/* ── 카드 공통 ── */
.card { background:#fff; border:1px solid #e7e9f0; border-radius:20px; padding:20px; box-shadow:0 4px 14px rgba(31,43,77,.06); }
.card-title { display:flex; align-items:center; gap:10px; font-size:20px; font-weight:800; color:#24345c; }

/* ── 캐릭터 카드 ── */
.char-card { background:#fff; border:1px solid #e7e9f0; border-radius:20px; overflow:hidden; box-shadow:0 4px 14px rgba(31,43,77,.06); }
.char-art { position:relative; height:330px; overflow:hidden; }
.name-badge { position:absolute; left:50%; bottom:12px; transform:translateX(-50%); background:#2b3a63; color:#fff;
              border:3px solid #fff; border-radius:999px; padding:8px 30px; font-size:20px; font-weight:800; white-space:nowrap; }
.moods { display:flex; gap:10px; padding:16px 16px 6px; }
.mood { flex:1; text-align:center; border-radius:14px; background:#f0f1f6; color:#8a90a6; padding:10px 0; font-weight:600; }
.mood .ic { font-size:20px; display:block; margin-bottom:2px; }
.mood.on { background:#fde7c2; color:#24345c; font-weight:800; box-shadow: inset 0 0 0 2px #f6cf8f; }
.thought { margin:10px 16px 18px; background:#f3f4f8; border-radius:16px; padding:16px 18px; }
.thought .t-title { font-weight:800; color:#24345c; font-size:18px; margin-bottom:8px; }
.thought .t-body { color:#24345c; font-size:17px; }

/* ── 점수 카드 ── */
.score { display:flex; align-items:center; gap:18px; }
.score .label { font-size:20px; font-weight:800; color:#24345c; white-space:nowrap; }
.bar { flex:1; height:16px; background:#e3e6ee; border-radius:8px; overflow:hidden; }
.bar > div { height:100%; background:#3b4b78; border-radius:8px; }
.score .num { font-size:30px; font-weight:800; color:#24345c; white-space:nowrap; }
.score .num small { font-size:20px; color:#5b6380; font-weight:700; }

/* ── 채팅 ── */
.st-key-chatcard { background:#fff; border:1px solid #e7e9f0; border-radius:20px; padding:10px 18px 14px;
                   box-shadow:0 4px 14px rgba(31,43,77,.06); margin-top:14px; }
.chat { height:440px; overflow-y:auto; display:flex; flex-direction:column-reverse; }
.row { display:flex; gap:12px; margin:14px 0; align-items:flex-end; }
.row.me { justify-content:flex-end; }
.avatar { width:52px; height:52px; border-radius:50%; overflow:hidden; flex:none; border:2px solid #fff; box-shadow:0 2px 6px rgba(0,0,0,.12); }
.me-avatar { width:52px; height:52px; border-radius:50%; background:#dfe3ef; flex:none; display:flex; align-items:center; justify-content:center; font-size:26px; }
.who { font-weight:800; color:#24345c; margin-bottom:6px; font-size:15px; }
.bubble { background:#eef0f5; color:#1f2640; padding:14px 18px; border-radius:18px; font-size:16px; line-height:1.55; max-width:560px; }
.me .bubble { background:#2f3e6b; color:#fff; }
.time { color:#9aa0b3; font-size:12px; white-space:nowrap; }
.judge { display:flex; flex-wrap:wrap; align-items:center; gap:10px 16px; background:#f3f4f8; border-radius:16px; padding:12px 18px; margin:8px 0 14px; }
.judge .jt { font-weight:800; color:#24345c; padding-right:14px; border-right:1px solid #d6d9e3; }
.judge .chip { color:#2b3346; font-size:15px; }
.judge .chip b { display:inline-block; background:#3b4b78; color:#fff; border-radius:7px; padding:1px 8px; margin-left:6px; }
.judge .cm { flex-basis:100%; color:#6b7189; font-size:13.5px; }
.ending { text-align:center; margin:16px 0; font-weight:800; color:#24345c; font-size:18px; }

/* ── 입력창 ── */
.st-key-chatcard [data-testid="stForm"] { border:none; padding:0; border-top:1px solid #eceef4; padding-top:12px; }
.st-key-chatcard input { border-radius:14px !important; padding:14px 16px !important; font-size:16px !important; }
.st-key-chatcard [data-testid="stFormSubmitButton"] button { background:#2b3a63 !important; color:#fff !important; border:none !important;
        border-radius:14px !important; min-height:50px; font-weight:800 !important; }

/* ── 결과 화면 ── */
.result-left { text-align:center; }
.sparkle { font-size:30px; color:#f2b84b; letter-spacing:10px; }
.r-title { font-size:28px; font-weight:800; color:#24345c; margin:6px 0 2px; }
.r-sub { color:#5b6380; margin-bottom:16px; }
.final { background:#fdf0dc; border-radius:20px; padding:18px; margin:6px 0 14px; }
.final .lbl { font-weight:800; color:#24345c; font-size:20px; }
.final .big { font-size:84px; font-weight:900; color:#24345c; line-height:1; }
.final .big small { font-size:40px; font-weight:600; color:#3d4566; }
.praise { background:#e8ebf6; border-radius:999px; padding:12px 18px; margin:0 20px 14px; }
.praise b { display:block; color:#24345c; font-size:19px; }
.praise span { color:#3d4566; font-size:14.5px; }
.lastline { background:#f3f4f8; border-radius:16px; padding:12px 16px; color:#24345c; font-size:15px; text-align:left; }
.lastline b { display:block; margin-bottom:4px; }
.next-hint { margin-top:12px; background:#f3f4f8; border-radius:16px; padding:14px; color:#24345c; font-weight:600; }
.sec { background:#fafbfd; border:1px solid #eceef4; border-radius:18px; padding:16px 18px; }
.sec-title { font-weight:800; color:#24345c; font-size:18px; margin-bottom:12px; }
.hbar { display:flex; align-items:center; gap:12px; margin:10px 0; color:#2b3346; }
.hbar .n { width:84px; white-space:nowrap; }
.hbar .b { flex:1; height:14px; background:#e3e6ee; border-radius:7px; overflow:hidden; }
.hbar .b > div { height:100%; background:#3b4b78; border-radius:7px; }
.hbar .v { width:56px; text-align:right; font-weight:800; color:#24345c; }
.hbar .v small { color:#8a90a6; font-weight:600; }
.fb p { color:#2b3346; line-height:1.6; margin:0 0 10px; }
.summary { background:#eef0f7; border-radius:14px; padding:14px; text-align:center; color:#24345c; font-weight:600; font-size:16px; }
.timeline { display:flex; align-items:center; gap:4px; flex-wrap:nowrap; margin-bottom:12px; }
.tl { text-align:center; font-size:12px; color:#5b6380; }
.tl .face { width:38px; height:38px; border-radius:50%; overflow:hidden; border:2px solid #fff; box-shadow:0 2px 6px rgba(0,0,0,.12); margin:0 auto 3px; }
.arrow { color:#b0b5c6; font-weight:800; }
.key { background:#fff6e6; border-left:4px solid #f2b84b; border-radius:10px; padding:10px 12px; color:#24345c; font-size:14.5px; }
.tag { display:inline-block; background:#e8ebf6; color:#24345c; border-radius:999px; padding:4px 12px; margin:4px 6px 0 0; font-weight:700; font-size:13.5px; }
.ba { display:flex; gap:12px; }
.ba > div { flex:1; border-radius:14px; padding:12px 14px; font-size:14.5px; line-height:1.55; }
.ba .before { background:#fbeceb; color:#7a2e28; }
.ba .after { background:#e9f5ec; color:#1f5b33; }
.ba b { display:block; margin-bottom:4px; }
.notice { color:#9aa0b3; font-size:12.5px; text-align:center; margin-top:10px; }
.st-key-retry button { border-radius:16px !important; border:2px solid #d6d9e3 !important; background:#fff !important; color:#24345c !important;
                       min-height:54px; font-weight:800 !important; }
.st-key-next button { border-radius:16px !important; background:#2b3a63 !important; color:#fff !important; border:none !important;
                      min-height:54px; font-weight:800 !important; }

/* ── 상황 브리핑 ── */
.brief { background:#fff; border:1px solid #e7e9f0; border-radius:20px; padding:26px 28px; box-shadow:0 4px 14px rgba(31,43,77,.06); }
.brief .tag2 { display:inline-block; background:#2b3a63; color:#fff; border-radius:999px; padding:5px 14px; font-weight:800; font-size:13px; }
.brief h2 { font-size:28px; font-weight:800; color:#24345c; margin:12px 0 4px; letter-spacing:-0.5px; }
.brief .skill { color:#6b7189; margin-bottom:18px; }
.bsec { background:#f6f7fb; border-radius:16px; padding:16px 18px; margin-bottom:12px; }
.bsec .bt { font-weight:800; color:#24345c; font-size:17px; margin-bottom:8px; }
.bsec ul { margin:0; padding-left:20px; color:#2b3346; line-height:1.75; font-size:15.5px; }
.bsec p { margin:0; color:#2b3346; line-height:1.65; font-size:15.5px; }
.bsec.goal { background:#fdf0dc; }
.bsec.tip { background:#eef0f7; }
.bgrid { display:flex; gap:12px; }
.bgrid > .bsec { flex:1; }
.st-key-start button { border-radius:16px !important; background:#2b3a63 !important; color:#fff !important; border:none !important;
                       min-height:58px; font-weight:800 !important; }
.st-key-start button p { font-size:18px !important; font-weight:800 !important; }

.st-key-recall button { background:transparent !important; border:1px solid #d6d9e3 !important; border-radius:999px !important;
                        min-height:30px !important; padding:2px 10px !important; color:#5b6380 !important; }
.st-key-recall button p { font-size:13px !important; font-weight:600 !important; }
.st-key-recall button:hover { background:#f3f4f8 !important; color:#24345c !important; }

/* ── 사이드바 ── */
[data-testid="stSidebar"] { background:#ffffff; border-right:1px solid #e7e9f0; }
.sb-title { font-size:24px; font-weight:800; color:#24345c; }
.sb-sub { color:#6b7189; margin-bottom:14px; }
.sb-stage { display:flex; gap:12px; align-items:center; border-radius:16px; padding:12px; margin:8px 0; background:#f5f6fa; border:2px solid transparent; }
.sb-stage.now { border-color:#2b3a63; background:#eef0f7; }
.sb-stage.lock { opacity:.55; }
.sb-num { width:34px; height:34px; border-radius:50%; background:#2b3a63; color:#fff; display:flex; align-items:center; justify-content:center; font-weight:800; flex:none; }
.sb-stage.lock .sb-num { background:#a3a9bd; }
.sb-name { font-weight:800; color:#24345c; }
.sb-skill { font-size:12.5px; color:#6b7189; }
.sb-state { margin-left:auto; font-size:13px; font-weight:800; color:#24345c; white-space:nowrap; }
.sb-progress { background:#e3e6ee; border-radius:6px; height:10px; overflow:hidden; margin:6px 0 4px; }
.sb-progress > div { background:#f2b84b; height:100%; }
.sb-rule { background:#f5f6fa; border-radius:14px; padding:12px 14px; color:#3d4566; font-size:13.5px; line-height:1.6; margin-top:10px; }
</style>
"""

# ═════════════════════════════════════════════
# 8. 화면 조각 (HTML)
# ═════════════════════════════════════════════
esc = lambda s: html.escape(str(s or ""))

LOGO = ('<div class="logo"><svg width="34" height="40" viewBox="0 0 34 40"><path d="M3 4 L22 1 L22 39 L3 36 Z" fill="#fff"/>'
        '<path d="M22 1 L31 4 L31 36 L22 39" fill="none" stroke="#fff" stroke-width="2.5"/>'
        '<circle cx="18" cy="21" r="1.8" fill="#2b3a63"/></svg>설득의 문</div>')


def character_card_html(game):
    stage = STAGES[game["stage_id"]]
    moods = "".join(
        f'<div class="mood{" on" if m == game["mood"] else ""}"><span class="ic">{MOOD_ICON[m]}</span>{m}</div>' for m in MOODS
    )
    return (f'<div class="char-card"><div class="char-art" style="background:{stage["look"]["bg"]}">{character_art(game["stage_id"], game["mood"], "meet")}'
            f'<div class="name-badge">{esc(stage["character"])}</div></div>'
            f'<div class="moods">{moods}</div>'
            f'<div class="thought"><div class="t-title">💭 속마음</div><div class="t-body">{esc(game["thought"]) if game.get("started") else "대화를 시작하면 속마음이 보여요."}</div></div></div>')


def score_card_html(game):
    goal = STAGES[game["stage_id"]]["goal_score"]
    pct = min(100, int(game["score"] / goal * 100))
    return (f'<div class="card"><div class="score"><span class="label">📊 설득 점수</span>'
            f'<div class="bar"><div style="width:{pct}%"></div></div>'
            f'<span class="num">{game["score"]} <small>/ {goal}</small></span></div></div>')


def chat_html(game):
    stage = STAGES[game["stage_id"]]
    sid = game["stage_id"]
    parts = []

    def char_row(text, t, mood):
        parts.append(f'<div class="row"><div class="avatar">{character_art(sid, mood)}</div>'
                     f'<div><div class="who">{esc(stage["character"])}</div><div class="bubble">{esc(text)}</div></div>'
                     f'<div class="time">{t}</div></div>')

    h = game["history"]
    char_row(h[0]["text"], h[0]["time"], "단호함")
    for i, r in enumerate(game["records"]):
        u, c = h[1 + i * 2], h[2 + i * 2]
        parts.append(f'<div class="row me"><div class="time">{u["time"]}</div>'
                     f'<div><div class="who" style="text-align:right">나</div><div class="bubble">{esc(u["text"])}</div></div>'
                     f'<div class="me-avatar">👤</div></div>')
        chips = "".join(f'<span class="chip">{esc(k)}<b>{v}</b></span>' for k, v in r["scores"].items())
        parts.append(f'<div class="judge"><span class="jt">📋 심판 평가</span>{chips}<span class="cm">{esc(r["comment"])}</span></div>')
        char_row(c["text"], c["time"], c.get("mood", "고민"))
    if game["done"]:
        label, last_line = stage["endings"][game["result"]]
        char_row(last_line, now_hm(), "수긍" if game["result"] == "success" else "단호함")
        parts.append(f'<div class="ending">{label}</div>')
    return f'<div class="chat"><div>{"".join(parts)}</div></div>'


def result_left_html(game):
    stage = STAGES[game["stage_id"]]
    label, last_line = stage["endings"][game["result"]]
    head, sub = RESULT_BUBBLE[game["result"]]
    return (f'<div class="card result-left"><div class="sparkle">✦ ✧ ✦</div>'
            f'<div class="r-title">{label}</div><div class="r-sub">{esc(stage["title"])} · {esc(stage["skill"])}</div>'
            f'<div class="final"><div class="lbl">최종 점수</div><div class="big">{game["score"]}<small> / {stage["goal_score"]}</small></div></div>'
            f'<div class="praise"><b>{head}</b><span>{sub}</span></div>'
            f'<div class="lastline"><b>{esc(stage["character"])}의 마지막 말</b>“{esc(last_line)}”</div></div>')


def result_right_html(game):
    stage = STAGES[game["stage_id"]]
    rep = game["report"] or {}
    recs = game["records"]
    n = max(1, len(recs))

    # 항목별 평균
    bars = ""
    for k in stage["criteria"]:
        avg = sum(r["scores"][k] for r in recs) / n
        bars += (f'<div class="hbar"><span class="n">{esc(k)}</span><div class="b"><div style="width:{avg / 5 * 100:.0f}%"></div></div>'
                 f'<span class="v">{avg:.1f}<small> / 5</small></span></div>')

    # 마음 변화
    moods = ["단호함"] + [r["mood"] for r in recs]
    tl = '<span class="arrow">→</span>'.join(
        f'<div class="tl"><div class="face">{character_art(game["stage_id"], m)}</div>{"시작" if i == 0 else f"{i}턴"}</div>'
        for i, m in enumerate(moods))

    # 대화 성향
    strat = Counter(r["strategy"] for r in recs)
    sbars = "".join(
        f'<div class="hbar"><span class="n">{s}</span><div class="b"><div style="width:{strat.get(s, 0) / n * 100:.0f}%"></div></div>'
        f'<span class="v">{strat.get(s, 0)}<small>회</small></span></div>' for s in STRATEGIES)
    tags = "".join(f'<span class="tag">#{esc(t)}</span>' for t, _ in Counter(r["tone"] for r in recs).most_common(2))

    return f"""
<div class="card">
  <div class="card-title" style="margin-bottom:14px">📊 상세 분석</div>
  <div style="display:flex;gap:14px;margin-bottom:14px">
    <div class="sec" style="flex:1"><div class="sec-title">항목별 평균 점수</div>{bars}</div>
    <div class="sec fb" style="flex:1"><div class="sec-title">💡 피드백</div>
      <p>{esc(rep.get('good_point', ''))}</p><p>{esc(rep.get('improve_point', ''))}</p></div>
  </div>
  <div style="display:flex;gap:14px;margin-bottom:14px">
    <div class="sec" style="flex:1"><div class="sec-title">💞 상대의 마음 변화</div>
      <div class="timeline">{tl}</div>
      <div class="key"><b>결정적 발언</b> “{esc(rep.get('key_line', '-'))}”<br>{esc(rep.get('key_reason', ''))}</div></div>
    <div class="sec" style="flex:1"><div class="sec-title">🧭 나의 대화 성향</div>{sbars}
      <div>{tags}</div><p style="color:#3d4566;margin:10px 0 0;font-size:14.5px">{esc(rep.get('style_summary', ''))}</p></div>
  </div>
  <div class="sec" style="margin-bottom:14px"><div class="sec-title">✍️ 이렇게 말해보세요</div>
    <div class="ba"><div class="before"><b>Before</b>{esc(rep.get('before', '-'))}</div>
    <div class="after"><b>After</b>{esc(rep.get('after', '-'))}</div></div></div>
  <div class="sec"><div class="sec-title">🚩 한 줄 요약</div><div class="summary">{esc(rep.get('one_line', ''))}</div></div>
  <div class="notice">이번 대화에서 나타난 패턴이며, 성격이나 심리 진단이 아닙니다.</div>
</div>"""



def brief_html(stage_id):
    stage, b = STAGES[stage_id], BRIEFS[stage_id]
    num = STAGE_ORDER.index(stage_id) + 1
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)   # 브리핑 문구는 코드에 직접 쓴 내용이라 HTML 허용
    first = "면접관이 먼저 질문해요." if stage_id == "stage4" else f"{esc(stage['character'])}의 첫마디로 대화가 시작돼요."
    return f"""
<div class="brief">
  <span class="tag2">STAGE {num} · {esc(stage['title'])}</span>
  <h2>{b['headline']}</h2>
  <div class="skill">연습할 능력: <b>{esc(stage['skill'])}</b></div>
  <div class="bsec"><div class="bt">🧭 지금 상황</div><ul>{li(b['situation'])}</ul></div>
  <div class="bsec"><div class="bt">🙋 나에 대한 정보</div><ul>{li(b['me'])}</ul></div>
  <div class="bgrid">
    <div class="bsec"><div class="bt">👤 대화 상대</div><p>{b['opponent']}</p></div>
    <div class="bsec goal"><div class="bt">🎯 목표</div><p>{esc(stage['goal']).replace('사용자', '나')}</p></div>
  </div>
  <div class="bgrid">
    <div class="bsec"><div class="bt">📏 규칙</div><p>{MAX_TURNS}턴 안에 설득 점수 <b>{stage['goal_score']}점</b>을 채우면 성공!<br>{first}</p></div>
    <div class="bsec tip"><div class="bt">💡 힌트</div><p>{b['tip']}</p></div>
  </div>
</div>"""


# ═════════════════════════════════════════════
# 9. 진행 상태 (잠금 해제)
# ═════════════════════════════════════════════
def init_state():
    ss = st.session_state
    ss.setdefault("best", {})          # 스테이지별 최고 결과
    ss.setdefault("current", "stage1")
    ss.setdefault("demo", False)
    ss.setdefault("error", None)
    if "game" not in ss:
        ss.game = new_game(ss.current)


def is_unlocked(sid):
    idx = STAGE_ORDER.index(sid)
    if idx == 0 or st.session_state.demo:
        return True
    return RESULT_RANK.get(st.session_state.best.get(STAGE_ORDER[idx - 1]), -1) >= RESULT_RANK["partial"]


def go_stage(sid, started=False):
    st.session_state.current = sid
    st.session_state.game = new_game(sid, started)
    st.session_state.error = None


def start_talk():
    st.session_state.game["started"] = True


def record_best(game):
    sid, res = game["stage_id"], game["result"]
    if RESULT_RANK[res] > RESULT_RANK.get(st.session_state.best.get(sid), -1):
        st.session_state.best[sid] = res


# ═════════════════════════════════════════════
# 10. 화면 그리기
# ═════════════════════════════════════════════
def draw_sidebar():
    best = st.session_state.best
    cleared = sum(1 for s in STAGE_ORDER if RESULT_RANK.get(best.get(s), -1) >= 1)
    with st.sidebar:
        st.markdown('<div class="sb-title">🚪 설득의 문</div><div class="sb-sub">인턴에 도전하는 한 학기</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="font-weight:800;color:#24345c">진행도 {cleared} / 4</div>'
                    f'<div class="sb-progress"><div style="width:{cleared / 4 * 100:.0f}%"></div></div>', unsafe_allow_html=True)
        rows = ""
        for i, sid in enumerate(STAGE_ORDER, 1):
            s = STAGES[sid]
            unlocked = is_unlocked(sid)
            if not unlocked:
                state, cls = "🔒 잠김", " lock"
            elif best.get(sid):
                state, cls = s["endings"][best[sid]][0].split(" ", 1)[0] + " 완료", ""
            else:
                state, cls = "🔓 도전 가능", ""
            if sid == st.session_state.current:
                cls += " now"
            rows += (f'<div class="sb-stage{cls}"><div class="sb-num">{i}</div>'
                     f'<div><div class="sb-name">{s["title"]}</div><div class="sb-skill">{s["skill"]}</div></div>'
                     f'<div class="sb-state">{state}</div></div>')
        st.markdown(rows, unsafe_allow_html=True)
        st.markdown('<div class="sb-rule">🎯 <b>규칙</b><br>5턴 안에 목표 점수를 넘기면 성공!<br>'
                    '🟡 부분 성공 이상이면 다음 스테이지가 열려요.<br><br>💡 <b>공략 팁</b><br>'
                    '상대가 왜 망설이는지, <b>속마음</b>에서 힌트를 찾아보세요.</div>', unsafe_allow_html=True)
        st.write("")
        st.session_state.demo = st.toggle("🔓 시연 모드 (모든 스테이지 열기)", value=st.session_state.demo)


def draw_topbar(game):
    with st.container(key="topbar"):
        cols = st.columns([2.1, 1.25, 1.35, 1.2, 1.2, 1.35], vertical_alignment="center")
        cols[0].markdown(LOGO, unsafe_allow_html=True)
        for i, sid in enumerate(STAGE_ORDER):
            unlocked = is_unlocked(sid)
            label = f"{i + 1}  {STAGES[sid]['title']}" if unlocked else f"🔒 {STAGES[sid]['title']}"
            key = f"tabon_{sid}" if sid == st.session_state.current else f"tab_{sid}"
            with cols[i + 1]:
                st.button(label, key=key, disabled=not unlocked, on_click=go_stage, args=(sid,), width="stretch")
        if game["done"]:
            pill = "🏁 결과 보기"
        elif not game.get("started"):
            pill = "📋 상황 확인"
        else:
            pill = f"⏳ 남은 턴 <b>{MAX_TURNS - game['turn']}/{MAX_TURNS}</b>"
        cols[5].markdown(f'<div class="pill">{pill}</div>', unsafe_allow_html=True)


def draw_brief(game):
    sid = game["stage_id"]
    left, right = st.columns([1, 2.05], gap="medium")
    with left:
        st.markdown(character_card_html(game), unsafe_allow_html=True)
    with right:
        st.markdown(brief_html(sid), unsafe_allow_html=True)
        st.write("")
        st.button("▶ 상황을 확인했어요. 대화 시작하기", key="start", on_click=start_talk, width="stretch")


@st.dialog("📋 지금 상황", width="large")
def show_brief_dialog(stage_id):
    st.markdown(brief_html(stage_id), unsafe_allow_html=True)


def draw_play(game):
    stage = STAGES[game["stage_id"]]
    left, right = st.columns([1, 2.05], gap="medium")
    with left:
        st.markdown(character_card_html(game), unsafe_allow_html=True)
    with right:
        st.markdown(score_card_html(game), unsafe_allow_html=True)
        with st.container(key="chatcard"):
            st.markdown(chat_html(game), unsafe_allow_html=True)
            if st.session_state.error:
                st.warning(st.session_state.error)
            with st.form("send", clear_on_submit=True, border=False):
                c1, c2 = st.columns([5, 1.1], vertical_alignment="center")
                msg = c1.text_input("메시지", placeholder=stage["placeholder"], label_visibility="collapsed")
                sent = c2.form_submit_button("➤ 보내기", width="stretch")
            _, rc = st.columns([4, 1.1])
            if rc.button("📋 상황 다시 보기", key="recall", width="stretch"):
                show_brief_dialog(game["stage_id"])
    if sent and msg and msg.strip():
        with st.spinner(f"{stage['character']}이(가) 생각하는 중…"):
            try:
                st.session_state.game = play_turn(game, msg.strip())
                st.session_state.error = None
                if st.session_state.game["done"]:
                    record_best(st.session_state.game)
            except Exception as e:
                st.session_state.error = friendly_error(e)
        st.rerun()


def draw_result(game):
    sid = game["stage_id"]
    idx = STAGE_ORDER.index(sid)
    left, right = st.columns([1, 2.05], gap="medium")
    with left:
        st.markdown(result_left_html(game), unsafe_allow_html=True)
        nxt = STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None
        if nxt and is_unlocked(nxt):
            hint = f"🏆 다음 스테이지 <b>{STAGES[nxt]['title']}</b>가 열렸어요!"
        elif nxt:
            hint = "🔒 🟡 이상을 받으면 다음 스테이지가 열려요. 다시 도전해 보세요!"
        else:
            hint = "🎓 모든 스테이지를 끝냈어요. 수고했어요!"
        st.markdown(f'<div class="next-hint">{hint}</div>', unsafe_allow_html=True)
    with right:
        st.markdown(result_right_html(game), unsafe_allow_html=True)
        b0, b1, b2 = st.columns([1.6, 1, 1])
        if (game.get("report") or {}).get("failed"):
            if b0.button("🔄 리포트 다시 만들기", key="remake_report", width="stretch"):
                with st.spinner("리포트를 다시 만드는 중…"):
                    make_report(game)
                st.rerun()
        b1.button("↻ 다시 도전하기", key="retry", on_click=go_stage, args=(sid, True), width="stretch")
        if nxt and is_unlocked(nxt):
            b2.button("다음 스테이지 →", key="next", on_click=go_stage, args=(nxt,), width="stretch")
        else:
            b2.button("🏠 처음으로", key="next", on_click=go_stage, args=("stage1",), width="stretch")
        with st.expander("💬 대화 다시 보기"):
            st.markdown(chat_html(game), unsafe_allow_html=True)


# ═════════════════════════════════════════════
# 11. 실행
# ═════════════════════════════════════════════
st.markdown(CSS, unsafe_allow_html=True)
MODEL = pick_model() if get_api_key() else None
if MODEL is None:
    pick_model.clear()  # 실패 결과를 저장해 두지 않아서, 새로고침하면 다시 연결을 시도해요
    if get_api_key():
        st.warning("😅 AI가 잠깐 바빠서 연결하지 못했어요. 잠시 후 새로고침(F5) 해 주세요.")
    else:
        st.error("❌ Gemini 연결 실패. Streamlit 앱 설정의 Secrets에 GEMINI_API_KEY 가 들어 있는지 확인하세요.")
    st.stop()

init_state()
draw_sidebar()
game = st.session_state.game
draw_topbar(game)
if game["done"]:
    draw_result(game)
elif not game.get("started"):
    draw_brief(game)
else:
    draw_play(game)
st.markdown('<div class="notice">무료 Gemini API를 사용해요. 실제 이름·연락처 같은 개인정보는 입력하지 마세요.</div>',
            unsafe_allow_html=True)
