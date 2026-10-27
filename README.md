# 우리집 TV 깨우기

폰 설정(비공개 `tvhome-data`)의 요일별 알람을 30분마다 확인하고, **알람 1분 전에 지니TV에 전원 신호**를 보내요.
지니TV가 켜지면 HDMI-CEC로 TV도 켜지고, 알람 시각에 우리집 홈 앱이 노래를 틀어요.

- 비밀 값(Settings → Secrets): `GENIE_CERT`, `GENIE_KEY`(리모컨 인증서), `DATA_TOKEN`(데이터 읽기 토큰)
- 변수(Settings → Variables): `GENIE_HOST`(지니TV 공인 IP)
- 시험: Actions → 지니TV 깨우기 → Run workflow → "지금 바로 깨우기" 체크
