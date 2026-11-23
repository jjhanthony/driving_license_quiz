"""
학과시험 문제은행 PDF -> data/questions.js 변환기
(도로교통공단 '(1,2종) 학과시험 문제은행[한국어]_(2026.3.9.시행).pdf' 서식 기준)

사용법:
    pip install pymupdf
    python tools/parse_pdf.py question-bank.pdf [--dump]

결과:
    data/questions.js   (window.QUESTIONS = [...])  -> index.html 이 읽음
    data/images/*.jpg   (사진형/일러스트형/안전표지 문제 그림)
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
OUT_JS = ROOT / "data" / "questions.js"
IMG_DIR = ROOT / "data" / "images"

CIRCLED = "①②③④⑤"
RE_QUESTION = re.compile(r"^\s*(\d{1,4})\s*\.\s*(.*)")
RE_ANSWER = re.compile(r"^\s*■\s*정\s*답\s*[:：]\s*(.*)")
RE_EXPLAIN = re.compile(r"^\s*■\s*해\s*설\s*[:：]?\s*(.*)")
RE_BULLET = re.compile(r"^\s*■\s*(.*)")
RE_OPT_SPLIT = re.compile(r"(?=[①②③④⑤])")
# 보기 번호 표기 통일 (PDF 일부에 ⓛ, ➀ 등이 섞여 있음)
NORMALIZE = str.maketrans({"ⓛ": "①", "➀": "①", "➁": "②", "➂": "③", "➃": "④", "➄": "⑤"})
RIGHT_COL_X = 470   # 이보다 오른쪽에서 시작하는 줄 = 그림 옆 '상황 설명' 열
WRAP_FULL = 730     # 한 단짜리 줄이 여기까지 차 있으면 자동 줄바꿈
WRAP_LEFT = 440     # 두 단 페이지의 왼쪽 열 기준


def join(prev: str, text: str, wrapped) -> str:
    """wrapped: False(줄 끝에서 끝난 줄) / "glue"(단어 중간 줄바꿈) / "space"(단어 경계 줄바꿈)"""
    if not prev:
        return text
    glue = wrapped == "glue" and prev[-1] not in ".,:;)]」』”’"
    return prev + ("" if glue else " ") + text


def read_lines(doc):
    """페이지별 (y0, x0, x1, text) 목록. 위→아래, 왼→오 순."""
    pages = []
    for page in doc:
        rows = []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                raw = "".join(sp["text"] for sp in ln["spans"])
                t = raw.strip().translate(NORMALIZE)
                if t:
                    x0, y0, x1, _ = ln["bbox"]
                    # 오른쪽 끝까지 찬 줄의 끝에 공백이 없으면 '단어 중간 줄바꿈' -> 다음 줄과 붙여 씀
                    rows.append((round(y0), x0, x1, t, raw != raw.rstrip()))
        rows.sort()
        pages.append(rows)
    return pages


def parse(doc, dump=False):
    pages = read_lines(doc)
    if dump:
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "data" / "raw.txt").write_text("\n".join(
            f"[p{p + 1} y{y} x{x0:.0f}-{x1:.0f}] {t}" for p, rows in enumerate(pages) for y, x0, x1, t, _ in rows),
            encoding="utf-8")

    qs, cur, mode = [], None, None
    wrap_left = wrap_right = False
    expected = 1
    for pno, rows in enumerate(pages):
        two_col = any(x0 > RIGHT_COL_X and not re.match(r"[①②③④⑤]", t) for _, x0, _, t, _ in rows)
        for y, x0, x1, text, trailing in rows:
            kind = "space" if trailing else "glue"
            m = RE_QUESTION.match(text)
            if m and int(m.group(1)) == expected and x0 < RIGHT_COL_X:
                cur = {"id": expected, "question": m.group(2).strip(), "options": [], "situation": [],
                       "answer": [], "explanation": "", "pos": (pno, y), "ans_pos": None}
                qs.append(cur)
                expected += 1
                mode, wrap_left = "q", (x1 > WRAP_FULL) and kind
                continue
            if cur is None:
                continue

            # 그림 옆 상황 설명 열
            if x0 > RIGHT_COL_X and mode in ("q", "opt") and cur["id"] > 680 and not re.match(r"[①②③④⑤]", text):
                b = RE_BULLET.match(text)
                if b or not cur["situation"]:
                    cur["situation"].append((b.group(1) if b else text).strip())
                else:
                    cur["situation"][-1] = join(cur["situation"][-1], text, wrap_right)
                wrap_right = (x1 > WRAP_FULL) and kind
                continue

            wrapped = wrap_left
            wrap_left = (x1 > (WRAP_LEFT if two_col and x1 < WRAP_FULL else WRAP_FULL)) and kind

            if mode in ("q", "opt", "sit"):
                ma = RE_ANSWER.match(text)
                if ma:
                    cur["answer"] = sorted({int(n) - 1 for n in re.findall(r"[1-5]", ma.group(1))})
                    cur["ans_pos"] = (pno, y)
                    mode = "ans"
                    continue
            if mode == "ans":
                me = RE_EXPLAIN.match(text) or RE_BULLET.match(text)
                cur["explanation"] = (me.group(1) if me else text).strip()
                mode = "exp"
                continue
            if mode == "exp":
                cur["explanation"] = join(cur["explanation"], text, wrapped) if wrapped and x0 < 125 \
                    else cur["explanation"] + "\n" + text
                continue

            b = RE_BULLET.match(text)
            if b:  # 한 단짜리 상황 설명 (■ ...)
                cur["situation"].append(b.group(1).strip())
                mode = "sit"
                continue
            if mode == "sit" and not re.match(r"[①②③④⑤]", text):
                cur["situation"][-1] = join(cur["situation"][-1], text, wrapped)
                continue

            if re.search(r"[①②③④⑤]", text):
                for part in (p.strip() for p in RE_OPT_SPLIT.split(text)):
                    if not part:
                        continue
                    if part[0] in CIRCLED:
                        cur["options"].append(part[1:].strip())
                    elif cur["options"]:
                        cur["options"][-1] = join(cur["options"][-1], part, wrapped)
                    else:
                        cur["question"] = join(cur["question"], part, wrapped)
                mode = "opt"
            elif mode == "q":
                cur["question"] = join(cur["question"], text, wrapped)
            elif mode == "opt" and cur["options"]:
                cur["options"][-1] = join(cur["options"][-1], text, wrapped)
    return qs


def attach_images(doc, qs):
    """그림을 그 위치 직전에 시작한 문제에 붙인다. 정답 줄보다 아래에 있으면 해설 그림."""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for old in IMG_DIR.glob("*"):
        old.unlink()
    found = {}
    for pno, page in enumerate(doc):
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            if (x1 - x0) < 30 or (y1 - y0) < 30:
                continue
            # 그림은 문제 첫 줄보다 조금 위에서 시작하는 경우가 많다
            owner = next((q for q in qs if q["pos"][0] == pno and y0 - 20 <= q["pos"][1] <= y0 + 80), None)
            if owner is None:
                for q in qs:
                    if q["pos"] <= (pno, y0):
                        owner = q
                    else:
                        break
            if owner:
                kind = "exp_images" if owner["ans_pos"] and owner["ans_pos"] < (pno, y0) else "images"
                found.setdefault((owner["id"], kind), []).append((pno, pymupdf.Rect(info["bbox"])))
    for q in qs:
        for kind in ("images", "exp_images"):
            q[kind] = []
            for n, (pno, rect) in enumerate(found.get((q["id"], kind), []), 1):
                name = f"q{q['id']:04d}{'_e' if kind == 'exp_images' else ''}_{n}.jpg"
                doc[pno].get_pixmap(clip=rect, dpi=144).save(IMG_DIR / name, jpg_quality=85)
                q[kind].append(f"data/images/{name}")


# 문제은행 구성(번호 구간): 문장형 1~680, 사진형 681~780, 일러스트형 781~865, 안전표지 866~965, 동영상 966~1000
SECTIONS = [(680, "text"), (780, "photo"), (865, "illust"), (965, "sign"), (1000, "video")]


def classify(q):
    return next(t for last, t in SECTIONS if q["id"] <= last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--dump", action="store_true", help="원문 줄 목록을 data/raw.txt 로 저장")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    qs = parse(doc, args.dump)
    attach_images(doc, qs)
    for q in qs:
        q["type"] = classify(q)
        q["multi"] = len(q["answer"]) > 1
        del q["pos"], q["ans_pos"]

    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text("window.QUESTIONS = " + json.dumps(qs, ensure_ascii=False, separators=(",", ":")) + ";\n",
                      encoding="utf-8")

    types = {}
    for q in qs:
        k = q["type"] + ("(2답)" if q["multi"] else "")
        types[k] = types.get(k, 0) + 1
    bad = [q["id"] for q in qs if len(q["options"]) < 4 or not q["answer"] or not (q["explanation"] or q["exp_images"])
           or any(a >= len(q["options"]) for a in q["answer"])]
    print(f"추출된 문제: {len(qs)}개 -> {OUT_JS}")
    print("유형별:", types)
    print("그림 포함 문제:", sum(1 for q in qs if q["images"]), "/ 해설 그림:", sum(1 for q in qs if q["exp_images"]))
    if bad:
        print(f"[경고] 인식 이상 {len(bad)}개: {bad}")
    return 0 if len(qs) == 1000 and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
