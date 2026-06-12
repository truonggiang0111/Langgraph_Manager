# Facebook Demo Runbook

## Goal

Quay một demo ngắn, nhìn rõ 3 ý:

1. `LangGraph` nhận yêu cầu tự nhiên.
2. `Host worker` mở browser thật, quét nhiều tab, lọc kết quả.
3. Flow trả về các bài có link thật, rồi dọn sạch tab sau khi xong.

## Best Demo Query

Ưu tiên query đã test ổn:

- `thuc tap devops hcm`
- `intern devops hcm`

Tránh dùng khi quay:

- `tuyển dụng intern devops hcm`
- query quá dài, quá tự nhiên, hoặc có nhiều ràng buộc trong một câu

## Pre-Flight

Trước khi quay:

1. Chạy script chuẩn bị demo:

```bash
cd /home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager
bash tools/demo_prep.sh
```

2. Mở UI:

- `http://127.0.0.1:8899`

3. Đảm bảo:

- Brave đã login Facebook bằng profile research/debug.
- Không có popup lạ.
- Tắt notification desktop.
- Chỉ giữ những cửa sổ cần quay.

## 60-90s Script

### Scene 1: Prompt

Trong chat nhập:

```text
tìm cho tôi 5 bài thực tập devops ở hcm trên facebook
```

Điểm cần cho người xem thấy:

- đây là câu tự nhiên
- không dùng API Facebook
- dùng browser/session local thật

### Scene 2: Browser Action

Để camera quay phần Brave:

- browser mở search thật
- nhiều tab quét song song
- có scroll
- sau đó có tab được đọc/lấy permalink

Điểm cần nói:

- `parallel tab scan`
- `host-side browser automation`
- `real Facebook session`

### Scene 3: Result

Quay lại UI LangGraph và highlight:

- `parallel_tabs_used`
- `total_found`
- `filtered_count`
- các item có `url`

Nếu cần, zoom vào 1-2 kết quả có link thật.

### Scene 4: Cleanup

Đợi job xong để quay:

- tab quét bị đóng
- browser debug không để tab rác

Điểm cần nói:

- flow có cleanup
- không để state browser bẩn sau mỗi job

## 2-3 Minute Portfolio Version

### Part 1

Mở bằng 1 câu:

`This is a LangGraph-based desktop research assistant with a host-side browser worker.`

### Part 2

Giải thích ngắn kiến trúc:

- `LangGraph = planner + orchestration`
- `Host worker = browser/session/mouse/screenshot`
- `Facebook research = scan -> filter -> permalink resolution -> cleanup`

### Part 3

Demo:

- nhập prompt
- browser chạy
- kết quả trả về
- highlight `resolved permalink`

### Part 4

Kết bằng 1 slide hoặc terminal note:

- real local browser
- multi-tab scanning
- host control
- usable recruiter-style result filtering

## CV Copy

### Short

Built a LangGraph-based desktop research assistant that automates a real local browser session to search Facebook job posts, filter results, resolve usable links, and clean up browser state after each run.

### Stronger

Designed and implemented a host-side browser worker for LangGraph, enabling parallel-tab Facebook job research, permalink extraction, host mouse control, debug-browser screenshots, and stable post-run cleanup.

## Social Post Copy

Built a LangGraph assistant that can use a real local browser session to research Facebook job posts, scan in parallel tabs, resolve usable links, and clean up state after each run.

## Recording Notes

- Quay ở độ phân giải 16:9.
- Dùng query đã test ổn, không improvise khi đang record.
- Nếu cần retake, chạy lại `tools/demo_prep.sh`.
- Không quay account/profile cá nhân nếu không muốn lộ session thật.
