# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```powershell
cd src
.\.venv\Scripts\python.exe -X utf8 -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
.\.venv\Scripts\python.exe -X utf8 -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case đã replay

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, trace run `R-seeded_regression-C-002-s11`; item E-002 có `provenance.from_trace` trỏ tới trace này. Report run ID xem eval card. |
| Triệu chứng | Lab8 FAIL `status resolved != denied`; ledger 20.000đ/1 refund thay vì 0đ/0; reply event 6 cũng nói 20.000đ. |
| Bước sai đầu tiên | Event 3 `model_override {"delay_days": 5}`. |
| Vì sao là bước *đầu tiên* | Event 1 handoff chỉ cho công cụ đọc; event 2 investigator đọc đúng O-202 delay=2. Event 3 ghi đè delay bằng số 5 trong ticket; event 4 mới tính quote sai 20.000đ. |
| Failure mode | FM-03: mâu thuẫn trực tiếp giữa event 2 DB và event 3. Không phải FM-02: approval hợp lệ; không phải FM-08: reply khớp ledger dù cả quyết định hoàn sai. |
| Đối chứng | `R-fixed-C-002-s11`: không có model_override; event 3 quote=0, reason `no_entitlement`; event 4 trả lời từ chối; status denied, ledger 0đ/0, Lab8 PASS. |
| Regression test | Item E-002 được chạy trên 5 seed; `DbFactKept=False`, `RefundAmountCorrect=False`, `TrialPass=False` trên lỗi; cả ba True trên fixed@11; đoạn kiểm tra bên dưới tái lập trực tiếp. |
| Chưa chắc chắn | Không suy ra ý định của model hoặc chất lượng LLM thật; trace không ghi thời điểm approval/commit nên không đủ chứng minh thứ tự chi tiết trong host. |

Regression check chạy trong `src` bằng PowerShell, không tạo test file hoặc sửa bộ test được cung cấp:

```powershell
@'
from types import SimpleNamespace
from lab9.trace_case import investigate
from lab9.graders import DbFactKept, RefundAmountCorrect, TrialPass
for shape, expected in [('seeded_regression', False), ('fixed', True)]:
    case = investigate(shape, 'C-002', 11)
    ctx = SimpleNamespace(output=case['trial'], expected_output=case['label'])
    for grader in (DbFactKept(), RefundAmountCorrect(), TrialPass()):
        assert grader.evaluate(ctx).value is expected, (shape, type(grader).__name__)
print('trace regression PASS')
'@ | .\.venv\Scripts\python.exe -X utf8 -
```

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
