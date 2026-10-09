# Sifu — Dự án Việt hóa cộng đồng

Dự án cộng đồng nhằm xây dựng bản dịch tiếng Việt có chất lượng cho trò chơi **Sifu**, với trọng tâm là thuật ngữ võ thuật nhất quán, lời thoại tự nhiên và khả năng kiểm tra tính toàn vẹn của dữ liệu localization.

> **Trạng thái:** Đang phát triển. Repository này hiện dùng để công khai quy trình và công cụ; chưa phải bản phát hành Việt hóa hoàn chỉnh.

## Mục tiêu

- Dịch tiếng Anh sang tiếng Việt, sử dụng bản tiếng Trung để đối chiếu ngữ cảnh khi phù hợp.
- Xây dựng glossary và hướng dẫn văn phong thống nhất giữa giao diện, hướng dẫn chiến đấu và cốt truyện.
- Bảo vệ Key, placeholder, markup, escape sequence và các thành phần kỹ thuật không được thay đổi.
- Kiểm tra độc lập bản dịch trước khi đưa vào tập bản dịch đã duyệt (*Translation Ledger*).
- Lưu vết thay đổi, báo cáo QA và trạng thái xử lý để có thể tiếp tục công việc sau khi gián đoạn.

## Quy trình

```text
Phân tích dữ liệu → Phân loại → Glossary & ngữ cảnh
                                 ↓
                       Dịch theo từng batch
                                 ↓
                         Kiểm tra QA
                                 ↓
                  Translation Ledger (QA PASS)
                                 ↓
                  Dựng CSV tích lũy và kiểm tra
                                 ↓
                      Lưu phiên bản / phát hành
```

Quy trình đang được phát triển với hỗ trợ của các AI agent qua Prism. Kết quả của agent phải được xác minh bằng artifact thực tế và các phép kiểm tra độc lập; không coi việc agent báo hoàn thành là bằng chứng kiểm thử trong game.

## Nguyên tắc kỹ thuật

- Không ghi đè file nguồn.
- Chỉ cập nhật trường văn bản được phép dịch trong bản xuất; giữ nguyên cấu trúc và định danh.
- Chỉ hợp nhất các mục **QA_PASS**; bản dịch chưa đạt phải giữ nguyên hoặc chờ xem xét.
- Kiểm tra tính nhất quán của các batch cũ khi tạo bản tích lũy mới.
- Phân biệt **kiểm tra cấu trúc CSV** với **kiểm thử import và chạy thực tế trong game**.

## Phạm vi công khai

Repository công khai này dành cho tài liệu, quy trình và mã công cụ có thể phân phối hợp lệ. **Không mặc định công khai dữ liệu gốc trích xuất từ game, toàn bộ lời thoại, asset, tệp PAK/LOCRES, thông tin xác thực hoặc dữ liệu dự án chưa được kiểm tra quyền phân phối.** Các bản dịch và bản phát hành (nếu có) sẽ được đánh giá riêng về quyền sử dụng và phân phối.

## Đóng góp

Bạn có thể mở **Issue** để đề xuất thuật ngữ, góp ý văn phong hoặc báo lỗi trong công cụ. Vui lòng không đăng tải nội dung game có bản quyền với dung lượng lớn, file game nguyên bản hoặc thông tin nhạy cảm.

## Tuyên bố độc lập

Đây là dự án người hâm mộ, **không chính thức**, không liên kết, đại diện hay được bảo trợ bởi **Sloclap** hoặc các chủ sở hữu quyền liên quan. **Sifu** và các nhãn hiệu, nội dung trò chơi thuộc về chủ sở hữu tương ứng.
