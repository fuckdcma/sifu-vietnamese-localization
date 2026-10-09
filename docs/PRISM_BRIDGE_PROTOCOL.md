# Prism → ChatGPT → GitHub: quy trình chuyển artifact thử nghiệm

**Trạng thái:** Manual bridge — chưa tự động hóa.  
**Repository:** `fuckdcma/sifu-vietnamese-localization` (Public).

## Mục đích

Prism hiện chưa được xác minh có quyền ghi vào `.git` hoặc kết nối HTTPS đến GitHub. Vì thế, **không coi Git push từ Prism là hoạt động**. Cầu nối hiện tại gồm:

1. Prism tạo artifact không chứa nội dung game và tính SHA-256 trên **bytes gốc**.
2. Người vận hành đính kèm file đó vào cuộc trò chuyện ChatGPT (ưu tiên file nguyên bản, không chép-dán).
3. ChatGPT kiểm tra định dạng, SHA-256, độ dài và tính phù hợp để công khai.
4. ChatGPT ghi artifact đã kiểm tra qua kết nối GitHub có quyền ghi, rồi **đọc lại từ nhánh main**.
5. Đối chiếu SHA-256 của bytes được đọc lại, lưu commit SHA và đường dẫn vào báo cáo.

Chỉ đánh dấu `REMOTE_ARTIFACT_VERIFIED` khi đã kiểm tra đủ bước 4–5. Việc tạo README trực tiếp trên GitHub **không** chứng minh cầu nối Prism → GitHub đã hoạt động.

## Phép thử đầu tiên

Prism tạo duy nhất `PRISM_BRIDGE_PROBE_001.txt`, chỉ chứa dữ liệu giả lập, ví dụ:

```text
transfer_id=PRISM-BRIDGE-001
project=sifu-vietnamese-localization
payload_class=PUBLIC_SAFE_TEST_ONLY
message=prism-to-github-artifact-transfer-test
```

Ghi **UTF-8 không BOM**, kết thúc **một LF** (`\n`) ở cuối file. Tính:

- `byte_count`
- `raw_sha256` (64 ký tự hex)
- `transfer_id`
- `source_filename`

Prism phải đọc lại artifact sau khi ghi, rồi cung cấp file cho người vận hành. **Không tự tuyên bố đã upload lên GitHub.**

Đường dẫn dự kiến trên GitHub sau khi kiểm tra: `tests/persistence/PRISM_BRIDGE_PROBE_001.txt`.

## Quy tắc kiểm chứng

- Hash Git blob (`sha` từ GitHub API) **khác loại** với SHA-256 của bytes gốc; không so sánh chúng trực tiếp.
- Không sửa newline, dấu BOM hoặc ký tự chỉ để làm khớp hash. Nếu bytes khác, báo rõ `HASH_MISMATCH`.
- Sau khi commit, đọc lại file từ `main`; đối chiếu bytes SHA-256, kích thước và nội dung.
- Không đánh dấu `PRISM_DIRECT_GIT_VERIFIED` vì cầu nối này có bước chuyển thủ công.
- Không coi SHA-256 là chữ ký xác thực nguồn; nó chỉ phát hiện sai lệch bytes trong phép thử này.

## Phạm vi nội dung Public

Chỉ gửi dữ liệu `PUBLIC_SAFE` đã được kiểm tra. Không công khai dữ liệu trích xuất từ game, toàn bộ câu thoại, asset, PAK, LOCRES, bản dịch chưa kiểm tra quyền phân phối, token, credential hoặc checkpoint có thông tin nhạy cảm.

Các CSV thật và Translation Ledger phải được rà soát quyền sử dụng và phân phối riêng trước khi đưa vào repository công khai. Khi cần lưu dữ liệu không thể công khai, dùng kho lưu trữ riêng được kiểm chứng; không tự coi repository Public là bản sao lưu đầy đủ của dự án.

## Sau phép thử

Nếu thành công, phương thức này dùng được cho **bàn giao artifact thủ công**. Để tự động hóa hàng loạt batch, cần một cơ chế chuyển file không cần người dùng thao tác mà vẫn có quyền và kết nối được xác minh (ví dụ một worker riêng với quyền GitHub được giới hạn). Không bắt đầu Batch 002-R1 dựa trên phép thử này nếu dữ liệu QA_PASS vẫn chưa có đường sao lưu an toàn.
