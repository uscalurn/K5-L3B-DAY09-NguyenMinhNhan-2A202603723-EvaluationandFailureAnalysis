# Judge audit

Chạy trong `src`: `.\.venv\Scripts\python.exe -X utf8 -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 5`; lặp lại với `judge_helpful_public.csv`.

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | PASS | Dùng cho tone trên replay này |
| helpful | 100 | 0.375 | 1.000 | 0.300 | FAIL | Loại khỏi release gate |

Lớp dương là **FAIL**. Tone: TP=24, FP=4, FN=6, TN=66; observed fail=0.28, Rogan–Gladen=0.30. Helpful: TP=9, FP=0, FN=21, TN=70; observed fail=0.09, hiệu chỉnh=0.30. CLI tone exit 0; helpful exit 1. Kappa không xác định bị loại, precision/recall có mẫu số 0 trả 0; Rogan–Gladen không xác định khi sensitivity + specificity = 1, kết quả hữu hạn được clip về [0,1].

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Nhận định có căn cứ | Hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 | PASS | FAIL | Đồng ý người: “Em…nha…check” suồng sã; note CSV nêu cùng lý do. | Thêm ví dụ phân biệt thân thiện và suồng sã; chuẩn xưng hô CS. |
| 2 | H-017 | PASS | FAIL | Note CSV nói khách lớn tuổi; reply dùng “bạn ơi…nha”. Reply riêng không cho biết tuổi, nên kết luận dựa vào note, chưa độc lập xác minh ngữ cảnh. | Cấp ngữ cảnh xưng hô hoặc dùng “anh/chị” khi chưa biết tuổi; đánh dấu thiếu ngữ cảnh khi audit. |
| 3 | H-029 | PASS | FAIL | Đồng ý người: “không cần nhắn thêm” mang giọng ra lệnh dù có “Dạ, vui lòng”. | Chấm toàn câu; thêm negative example có mở đầu lịch sự nhưng kết thúc áp đặt. |
| 4 | H-042 | FAIL | PASS | Đồng ý người ở tiêu chí tone: có “Dạ…ạ”, thông tin rõ; note cho biết judge phạt thiếu xin lỗi. CSV không đủ xác minh khoản hoàn thực tế. | Không bắt buộc xin lỗi trong mọi câu trả lời; tách tone khỏi độ dài và correctness. |
| 5 | H-051 | FAIL | PASS | Đồng ý người theo note: LogiX cho phép “refund”; phần còn lại lịch sự. Không dùng nhãn này xác nhận số tiền đúng. | Thêm danh mục thuật ngữ được phép; không tự phạt mọi từ tiếng Anh. |
| 6 | HL-015 | PASS | FAIL | Reply 55.000đ, note ghi ledger 40.000đ; ủng hộ nhãn người theo bằng chứng CSV, chưa mở ledger gốc của case này. | Chuyển kiểm tra số tiền sang code đối chiếu ledger; judge chỉ chấm cách diễn đạt. |
| 7 | HL-016 | PASS | FAIL | Lời xin lỗi dài nhưng reply 55.000đ, note ledger 35.000đ; căn cứ CSV ủng hộ người, không suy diễn động cơ judge. | Thêm ví dụ lịch sự nhưng sai tiền; kiểm correctness bằng code trước khi chấm helpful. |

## Kết luận
- Helpful bỏ sót 21/30 lỗi (recall 0.30), kappa 0.375; precision 1.0 không bù được hai sàn thiếu. Không dùng helpful làm gate.
- Tone đạt cả ba sàn trên tập replay, còn 10 bất đồng; dùng có giới hạn cho tone và audit định kỳ, không thay thế kiểm tra tiền/approval.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật.
