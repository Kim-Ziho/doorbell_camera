# Doorbell Camera (OpenCV + Motion Recording)

> 간단한 도어벨 카메라 데모: 카메라 프리뷰를 띄우고 **사람/물체의 움직임을 감지하면 자동으로 녹화**합니다. 스페이스로 수동 녹화도 가능합니다.
## 실행 결과
![Demo](docs/doorbell_demo.gif)

## 주요 기능
- **실시간 프리뷰** (OpenCV HighGUI)
- **자동 녹화 (Motion-triggered)**: 움직임이 일정 임계치 이상이면 자동 시작, 조용해지면 포스트롤 후 정지
- **프리롤/포스트롤**: 시작 전 N초, 종료 후 N초 포함
- **수동 녹화**: `Space`로 즉시 시작/종료
- **오버레이**: 녹화 중 화면 좌상단 `REC` 빨간 점, 움직임 박스 시각화
- **파일 저장**: `records/` 폴더에 날짜-시간 기반 파일명(MP4 우선, 안 되면 AVI-MJPG)

---

## 작업 환경
- **OS**: macOS **13.7.6**
- **Python**: **3.11.9** (pyenv/pyenv-virtualenv)
- **카메라 인덱스(테스트 기준)**  
  - `0`: **iPhone 카메라** (연속성 카메라)  
  - `1`: **MacBook 내장 카메라**
- **iOS 버전**: **18.6.2**
- (주의) macOS에서 **연속성 카메라** 활성/비활성 여부에 따라 인덱스가 달라질 수 있습니다.

---

## 세팅(Setup)

### 1) pyenv/virtualenv
```bash
# Python 설치
pyenv install 3.11.9

# 가상환경 생성
pyenv virtualenv 3.11.9 doorbell-py3.11.9

# 프로젝트 디렉터리에서 가상환경 적용
cd <doorbell_camera_directory>
pyenv local doorbell-py3.11.9
```

### 2) 의존성 설치(requirements)
가상환경 활성 상태에서:
```bash
# (처음 세팅) 필요 패키지 설치
pip install -r requirements.txt

# (내 환경을 기록하고 싶을 때) 현재 설치 패키지를 파일로 추출
pip freeze > requirements.txt
```

---

## 실행 방법(Usage)

### 기본 실행
```bash
python video_recorder_motion.py
```
키보드 단축키:
- **Space**: 수동 녹화 **토글**
- **A**: 자동 녹화 **ON/OFF** 토글
- **ESC**: 종료

출력:
- 녹화 파일은 `records/`에 `motion_YYYYmmdd_HHMMSS.mp4` 또는 `manual_YYYYmmdd_HHMMSS.mp4`로 저장됩니다.
- 코덱은 `mp4v → avc1 → MJPG(avi)` 순으로 자동 폴백합니다.

---

## 인터페이스 안내

아래 로그는 사용자 인터랙션(키 입력)에 따라 **자동/수동 모드 전환**과 **녹화 시작/종료**가 어떻게 동작하는지 보여줍니다.

```
== Try idx=0, backend=AVFOUNDATION ==
  ✓ Opened. size=1280x720 fps=30.0
  read() after warmup -> True

== Preview start (Space: 수동 녹화 토글, A: 자동 ON/OFF, ESC: 종료) ==
[Writer] Opened -> records/motion_20250915_134036.mp4
[Auto] 움직임 감지 → 녹화 시작
[Auto] 조용 상태 → 포스트롤 기록 후 중지 예정
[Auto] 저장 완료: records/motion_20250915_134036.mp4
[Auto] 비활성화
[Manual] 녹화 시작
[Manual] 저장 완료: records/manual_20250915_134131.mp4
```

- 위 시나리오에서는 **자동 감지 1회**( `motion_...134036.mp4`)와 **수동 녹화 1회**(`manual_...134131.mp4`)가 이루어졌습니다.
- `[Auto] 비활성화/활성화`는 **A 키**로 자동 감지 기능을 토글한 흔적입니다.
- 수동 녹화는 **Space**로 시작/종료했으며, 파일이 `records/`에 즉시 생성/저장되었습니다.

---

## MotionDetector 원리

본 프로젝트의 움직임 감지는 **배경 차분(Background Subtraction)** 기반으로 동작합니다.

1. **전처리**: 입력 프레임을 그레이스케일 변환 후 **Gaussian Blur**로 노이즈를 줄입니다.  
2. **배경 차분 (MOG2)**: `cv.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)`  
   - 가우시안 혼합 모델(GMM)로 배경을 학습합니다.  
   - 출력 마스크 값은 `0(배경)`, `127(그림자)`, `255(전경)`이며, **그림자는 후속 단계에서 제거**합니다.
3. **이진화 & 후처리**: 마스크를 **임계값 200**으로 이진화(그림자 제거) → **열기(Opening)** 로 점 잡음 제거 → **팽창(Dilation)** 으로 끊긴 영역 연결.  
4. **움직임 비율 계산**: `(흰 픽셀 수) / (전체 픽셀 수)`로 **motion ratio**를 산출합니다.  
5. **히스테리시스 판정**:  
   - **시작 임계치** `MOTION_START_RATIO` 이상이 **연속** `START_PERSISTENCE_FRAMES` 프레임 발생 시 **녹화 시작**.  
   - **정지 임계치** `MOTION_STOP_RATIO` 이하가 **QUIET_SECONDS_TO_STOP** 동안 유지되면 **포스트롤**(예: 2초) 기록 후 **정지**.  
   - 이 구조는 미세한 흔들림/노이즈로 인한 **깜빡임(false trigger)** 을 줄입니다.
6. **프리롤/포스트롤**: `deque` 버퍼로 최근 N초 프레임을 유지해 **사건의 전후 맥락**을 보존합니다.  
7. **시각화**: `findContours`로 전경 외곽을 찾아 **바운딩 박스**를 그려주며, 녹화 중에는 화면 좌상단에 **빨간 점(REC)** 오버레이를 표시합니다.

이 방식은 단순 프레임 차분보다 **조명 변화에 강하고**, 사람이 천천히 들어오는 상황에서도 안정적으로 감지할 수 있는 장점이 있습니다. 필요에 따라 ROI(관심영역) 제한, 시간대별 민감도 조절(주/야간), 추가 후처리(클로징 등)을 적용해 오탐/미탐을 더 줄일 수 있습니다.
