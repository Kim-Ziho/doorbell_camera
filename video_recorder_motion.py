import os
import time
import datetime
from collections import deque

import cv2 as cv
import numpy as np

# ======================
# settings
# ======================
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

# 자동 녹화 민감도 (프레임의 흰 픽셀 비율; 0.01 = 1%)
MOTION_START_RATIO = 0.015   # 이 이상이면 "움직임 발생"으로 간주하고 녹화 시작
MOTION_STOP_RATIO  = 0.005   # 이 이하가 일정 시간 지속되면 녹화 중지
START_PERSISTENCE_FRAMES = 3  # 시작 판정에 필요한 연속 프레임 수
QUIET_SECONDS_TO_STOP    = 2.0  # "조용" 지속 시간 (초) → 녹화 중지
PREROLL_SECONDS          = 2.0  # 녹화 시작 전 포함할 프레임 길이(초)
POSTROLL_SECONDS         = 2.0  # 녹화 중지 후 더 기록할 길이(초)

# ======================
# utils
# ======================
def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def draw_rec_overlay(img):
    cv.circle(img, (20, 20), 8, (0, 0, 255), -1)
    cv.putText(img, "REC", (40, 26), cv.FONT_HERSHEY_DUPLEX, 0.6, (0, 0, 0), 2)
    cv.putText(img, "REC", (40, 26), cv.FONT_HERSHEY_DUPLEX, 0.6, (0, 0, 255), 1)

def open_writer(frame_size, fps, base_dir="records", base_name="motion"):
    os.makedirs(base_dir, exist_ok=True)
    stamp = now_stamp()
    trials = [
        (os.path.join(base_dir, f"{base_name}_{stamp}.mp4"), cv.VideoWriter_fourcc(*"mp4v")),
        (os.path.join(base_dir, f"{base_name}_{stamp}.mp4"), cv.VideoWriter_fourcc(*"avc1")),
        (os.path.join(base_dir, f"{base_name}_{stamp}.avi"), cv.VideoWriter_fourcc(*"MJPG")),
    ]
    for path, fourcc in trials:
        w = cv.VideoWriter(path, fourcc, fps, frame_size, True)
        if w.isOpened():
            print(f"[Writer] Opened -> {path}")
            return w, path
    print("[Writer] 열기에 실패했습니다. 코덱/확장자를 변경해 보세요.")
    return None, None

# ======================
# camera open
# ======================
def try_open(idx, backend=None, name=""):
    print(f"\n== Try idx={idx}, backend={name} ==")
    cap = cv.VideoCapture(idx, backend) if backend is not None else cv.VideoCapture(idx)
    if not cap.isOpened():
        print("  ✗ cap.isOpened() == False")
        return None
    cap.set(cv.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv.CAP_PROP_CONVERT_RGB, 1)
    cap.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*"MJPG"))

    w = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv.CAP_PROP_FPS)
    print(f"  ✓ Opened. size={w}x{h} fps={fps}")

    ok = False
    for _ in range(10):
        ok, _ = cap.read()
        if ok: break
        time.sleep(0.05)
    print(f"  read() after warmup -> {ok}")
    return cap if ok else None

def open_camera():
    candidates = [
        (0, cv.CAP_AVFOUNDATION, "AVFOUNDATION"),
        (1, cv.CAP_AVFOUNDATION, "AVFOUNDATION"),
        (0, None, "DEFAULT"),
        (1, None, "DEFAULT"),
    ]
    for idx, be, name in candidates:
        cap = try_open(idx, be, name)
        if cap:
            return cap
    return None

# ======================
# motion detector
# ======================
class MotionDetector:
    def __init__(self):
        # MOG2: 그림자(127) 무시를 위해 이후 threshold 사용
        self.bg = cv.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)
        self.kernel = cv.getStructuringElement(cv.MORPH_RECT, (3, 3))

    def process(self, frame):
        """return mask(binary 0/255), motion_ratio(0~1), contours(list)"""
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        blur = cv.GaussianBlur(gray, (5, 5), 0)
        fg = self.bg.apply(blur)                       # 0, 127(shadow), 255
        _, binmask = cv.threshold(fg, 200, 255, cv.THRESH_BINARY)  # 200↑만 객체
        binmask = cv.morphologyEx(binmask, cv.MORPH_OPEN, self.kernel, iterations=1)
        binmask = cv.dilate(binmask, self.kernel, iterations=2)
        ratio = cv.countNonZero(binmask) / float(binmask.size)
        contours, _ = cv.findContours(binmask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        return binmask, ratio, contours

# ======================
# 메인: 수동 + 자동 녹화
# ======================
def main():
    cap = open_camera()
    if cap is None:
        raise SystemExit("카메라를 열 수 없습니다. 권한/백엔드/인덱스를 확인하세요.")

    fps = cap.get(cv.CAP_PROP_FPS)
    if not fps or fps <= 0 or fps > 240:
        fps = 30.0

    detector = MotionDetector()
    recording = False
    auto_mode = True   # 자동 녹화 ON (A 키로 토글)

    writer, out_path = None, None
    h, w = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
    frame_size = (w, h)

    # 프리롤/포스트롤 관리
    preroll_maxlen = max(1, int(PREROLL_SECONDS * fps))
    preroll = deque(maxlen=preroll_maxlen)
    postroll_remaining = 0

    # 시작/정지 판정 보조 상태
    start_streak = 0
    quiet_frames_to_stop = max(1, int(QUIET_SECONDS_TO_STOP * fps))
    quiet_counter = 0

    last_tick = time.time()
    frame_count = 0

    print("\n== Preview start (Space: 수동 녹화 토글, A: 자동 ON/OFF, ESC: 종료) ==")
    while True:
        ok, frame = cap.read()
        if not ok:
            print("read() 실패 — 스트림이 끊겼습니다.")
            break

        # 프리롤 버퍼에 저장
        preroll.append(frame.copy())

        # 움직임 감지
        mask, motion_ratio, contours = detector.process(frame)

        # 자동 모드 로직
        if auto_mode:
            if not recording:
                if motion_ratio >= MOTION_START_RATIO:
                    start_streak += 1
                else:
                    start_streak = 0
                if start_streak >= START_PERSISTENCE_FRAMES:
                    # 녹화 시작
                    writer, out_path = open_writer(frame_size, fps)
                    if writer is not None:
                        # 프리롤 먼저 기록
                        for f in list(preroll):
                            writer.write(f)
                        recording = True
                        postroll_remaining = 0
                        quiet_counter = 0
                        print("[Auto] 움직임 감지 → 녹화 시작")
                    start_streak = 0  # 초기화
            else:
                # 녹화 중: 조용해지면 중단 예약(포스트롤)
                if motion_ratio <= MOTION_STOP_RATIO:
                    quiet_counter += 1
                else:
                    quiet_counter = 0
                if quiet_counter >= quiet_frames_to_stop and postroll_remaining == 0:
                    postroll_remaining = max(1, int(POSTROLL_SECONDS * fps))
                    print("[Auto] 조용 상태 → 포스트롤 기록 후 중지 예정")

        # 화면 오버레이
        view = frame.copy()
        # 움직임 박스(가시화)
        for c in contours:
            if cv.contourArea(c) < 200:  # 너무 작은 노이즈 박스 제거
                continue
            x, y, ww, hh = cv.boundingRect(c)
            cv.rectangle(view, (x, y), (x+ww, y+hh), (0, 255, 255), 2)

        # 상태 텍스트
        cv.putText(view, f"Auto: {'ON' if auto_mode else 'OFF'}  "
                         f"Motion: {motion_ratio*100:.1f}%", (10, h-12),
                  cv.FONT_HERSHEY_DUPLEX, 0.55, (0, 0, 0), 2)
        cv.putText(view, f"Auto: {'ON' if auto_mode else 'OFF'}  "
                         f"Motion: {motion_ratio*100:.1f}%", (10, h-12),
                  cv.FONT_HERSHEY_DUPLEX, 0.55, (255, 255, 255), 1)

        # 녹화 중이면 쓰기 + REC 표시
        if recording:
            draw_rec_overlay(view)
            if writer is not None:
                writer.write(frame)
            if postroll_remaining > 0:
                postroll_remaining -= 1
                if postroll_remaining == 0:
                    # 녹화 중지
                    if writer is not None:
                        writer.release()
                        print(f"[Auto] 저장 완료: {out_path}")
                        writer, out_path = None, None
                    recording = False
                    preroll.clear()

        # 수동 토글(스페이스)
        # 자동 모드와 수동 모드는 병행 가능: 수동으로 시작/중지하면 자동 스케줄은 초기화
        key = cv.waitKey(1) & 0xFF
        if key == 27:
            break
        elif key == 32:  # Space
            if not recording:
                writer, out_path = open_writer(frame_size, fps, base_name="manual")
                if writer is not None:
                    for f in list(preroll):
                        writer.write(f)
                    recording = True
                    postroll_remaining = 0
                    quiet_counter = 0
                    print("[Manual] 녹화 시작")
            else:
                # 즉시 중지
                if writer is not None:
                    writer.release()
                    print(f"[Manual] 저장 완료: {out_path}")
                    writer, out_path = None, None
                recording = False
                preroll.clear()
                start_streak = 0
                quiet_counter = 0
                postroll_remaining = 0
        elif key in (ord('a'), ord('A')):
            auto_mode = not auto_mode
            print(f"[Auto] {'활성화' if auto_mode else '비활성화'}")

        # FPS 표시(정보용)
        frame_count += 1
        now = time.time()
        if now - last_tick >= 1.0:
            inst_fps = frame_count / (now - last_tick)
            cv.setWindowTitle("Preview", f"Preview  |  {inst_fps:.1f} fps")
            last_tick, frame_count = now, 0

        # 프리뷰 창 갱신
        cv.imshow("Preview", view)
        # (참고) 마스크 확인하고 싶으면 아래 주석 해제
        # cv.imshow("MotionMask", mask)

    # 종료 처리
    if recording and writer is not None:
        writer.release()
        print(f"[Record] 저장 완료: {out_path}")
    cap.release()
    cv.destroyAllWindows()

if __name__ == "__main__":
    main()
