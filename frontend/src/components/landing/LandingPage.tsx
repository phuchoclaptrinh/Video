import Link from 'next/link';
import {
  ArrowRight,
  AudioLines,
  CheckCircle2,
  Clapperboard,
  Film,
  ImagePlus,
  Layers,
  ListChecks,
  Megaphone,
  RefreshCw,
  ScanEye,
  Sparkles,
  Wand2,
} from 'lucide-react';

const steps = [
  {
    icon: ImagePlus,
    title: 'Tải ảnh và mô tả sản phẩm',
    text: 'Tối đa 6 ảnh cùng thông tin bạn muốn nói: chất liệu, size, giá, ưu đãi, khách hàng hướng tới.',
  },
  {
    icon: ListChecks,
    title: 'Duyệt kịch bản',
    text: 'AI chia video thành từng cảnh với câu hook mở đầu, hình ảnh và lời thoại tiếng Việt. Sửa thoải mái trước khi duyệt.',
  },
  {
    icon: Film,
    title: 'Nhận video hoàn chỉnh',
    text: 'Hệ thống tạo lần lượt từng cảnh, kiểm tra giọng đọc rồi tự ghép thành một video để tải về.',
  },
];

const features = [
  {
    icon: Sparkles,
    title: 'Kịch bản có hook',
    text: 'Câu mở đầu gây tò mò, lời thoại theo nhịp video ngắn, biến thông số sản phẩm thành lợi ích người xem cảm nhận được.',
  },
  {
    icon: AudioLines,
    title: 'Giọng tiếng Việt tự nhiên',
    text: 'Người trong video nói tiếng Việt với khẩu hình khớp lời. Mã sản phẩm được viết theo cách người bán đọc thành tiếng.',
  },
  {
    icon: ScanEye,
    title: 'Giữ đúng sản phẩm qua mọi cảnh',
    text: 'Ảnh tham chiếu và khung hình nối cảnh giữ nguyên nhân vật, trang phục và sản phẩm từ đầu đến cuối.',
  },
  {
    icon: RefreshCw,
    title: 'Tự kiểm tra, tự làm lại',
    text: 'Cảnh đọc thiếu hoặc đọc lặp được viết lại lời thoại và tạo lại tự động, tối đa 3 lần. Cảnh lỗi không ra video được hoàn credit.',
  },
  {
    icon: Megaphone,
    title: 'Chiến dịch nhiều video',
    text: 'Từ một mô tả sản phẩm, lập nhiều kịch bản với góc tiếp cận khác nhau để thử xem video nào chạy tốt nhất.',
  },
  {
    icon: Wand2,
    title: 'Học phong cách từ video viral',
    text: 'Đưa một video bạn thích, VO3 học nhịp kể và cách dựng để áp vào sản phẩm của bạn, không sao chép nội dung.',
  },
];

const automation = [
  'Tạo ảnh mốc và khung hình cho từng cảnh',
  'Render lần lượt các cảnh trên model video đã chọn',
  'Nghe lại lời đọc và kiểm tra hình ảnh từng cảnh',
  'Viết lại lời thoại và tạo lại cảnh lỗi, tối đa 3 lần',
  'Ghép các cảnh thành video hoàn chỉnh',
  'Hoàn credit cho cảnh lỗi không ra được video',
];

const questions = [
  {
    q: 'Làm sao để có tài khoản?',
    a: 'Tài khoản do quản trị viên cấp. Liên hệ người quản lý VO3 Studio của bạn để nhận email và mật khẩu đăng nhập.',
  },
  {
    q: 'Tạo một video mất bao lâu?',
    a: 'Kịch bản thường có sau một đến vài phút. Mỗi cảnh video mất thêm vài phút để tạo, tuỳ model và hàng đợi. Bạn có thể đóng trang, video vẫn tiếp tục được tạo.',
  },
  {
    q: 'Chi phí được tính thế nào?',
    a: 'Mỗi kịch bản, ảnh tham chiếu và cảnh video trừ một số credit, hiển thị trước khi bạn bấm tạo. Video chỉ bắt đầu khi bạn xác nhận đồng ý phát sinh phí.',
  },
  {
    q: 'Dùng những model AI nào?',
    a: 'Video: Google Veo 3.1, Omni Flash, Seedance. Ảnh tham chiếu: GPT Image và Nano Banana. Danh sách model có thể khác nhau theo tài khoản.',
  },
];

function Brand() {
  return (
    <span className="lp-brand">
      <span className="flow-brand-mark">
        <Clapperboard size={17} />
      </span>
      <span>
        VO3 <b>Studio</b>
      </span>
    </span>
  );
}

/** A storyboard-to-video preview drawn in CSS, so the hero shows the product without stock media. */
export function HeroPreview() {
  return (
    <div className="lp-preview" aria-hidden="true">
      <div className="lp-phone">
        <div className="lp-phone-screen">
          <span className="lp-phone-badge">
            <span className="lp-live-dot" /> Cảnh 1 · Hook
          </span>
          <div className="lp-phone-subject">
            <span className="lp-phone-head" />
            <span className="lp-phone-body" />
            <span className="lp-phone-product" />
          </div>
          <p className="lp-phone-caption">
            Mẹ ơi, chọn size cho con thì xem cân nặng trước tiên nhé.
          </p>
          <div className="lp-phone-progress">
            <span />
          </div>
        </div>
      </div>
      <div className="lp-float lp-float-script">
        <span className="lp-float-icon">
          <ListChecks size={16} />
        </span>
        <span>
          <b>Kịch bản sẵn sàng</b>
          <small>3 cảnh · 24 giây · giọng Bắc</small>
        </span>
      </div>
      <div className="lp-float lp-float-check">
        <span className="lp-float-icon ok">
          <CheckCircle2 size={16} />
        </span>
        <span>
          <b>Lời đọc khớp kịch bản</b>
          <small>Cảnh 2 đã kiểm tra</small>
        </span>
      </div>
      <div className="lp-float lp-float-scenes">
        {['Hook', 'Lợi ích', 'Chốt đơn'].map((label, index) => (
          <span key={label} className={index < 2 ? 'done' : undefined}>
            <i />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

export function LandingPage() {
  return (
    <div className="lp">
      <header className="lp-nav">
        <div className="lp-wrap lp-nav-inner">
          <Link href="/" aria-label="VO3 Studio">
            <Brand />
          </Link>
          <nav aria-label="Trang giới thiệu">
            <a href="#cach-hoat-dong">Cách hoạt động</a>
            <a href="#tinh-nang">Tính năng</a>
            <a href="#cau-hoi">Câu hỏi</a>
          </nav>
          <Link className="flow-button primary" href="/login">
            Đăng nhập
          </Link>
        </div>
      </header>

      <main>
        <section className="lp-hero">
          <div className="lp-wrap lp-hero-inner">
            <div className="lp-hero-copy">
              <span className="lp-eyebrow">
                <Sparkles size={14} /> Video quảng cáo AI cho người bán hàng
              </span>
              <h1>
                Từ ảnh sản phẩm đến <em>video bán hàng</em> có lời thoại hoàn chỉnh.
              </h1>
              <p>
                Tải ảnh, mô tả sản phẩm. VO3 Studio viết kịch bản có hook, tạo từng cảnh với người
                nói tiếng Việt tự nhiên rồi ghép thành video sẵn sàng đăng TikTok, Reels và Shorts.
              </p>
              <div className="lp-cta">
                <Link className="flow-button primary lp-cta-main" href="/login">
                  Đăng nhập để bắt đầu <ArrowRight size={17} />
                </Link>
                <a className="flow-button lp-cta-ghost" href="#cach-hoat-dong">
                  Xem cách hoạt động
                </a>
              </div>
              <ul className="lp-hero-points">
                <li>
                  <CheckCircle2 size={15} /> Chỉ cần duyệt kịch bản và bấm tạo
                </li>
                <li>
                  <CheckCircle2 size={15} /> Tự kiểm tra giọng đọc từng cảnh
                </li>
              </ul>
            </div>
            <HeroPreview />
          </div>
        </section>

        <section className="lp-models" aria-label="Model AI được hỗ trợ">
          <div className="lp-wrap">
            <span>Chạy trên các model AI hàng đầu</span>
            <ul>
              {['Google Veo 3.1', 'Omni Flash', 'Seedance', 'GPT Image', 'Nano Banana'].map(
                (name) => (
                  <li key={name}>{name}</li>
                ),
              )}
            </ul>
          </div>
        </section>

        <section className="lp-section" id="cach-hoat-dong">
          <div className="lp-wrap">
            <div className="lp-section-head">
              <span className="lp-eyebrow">Cách hoạt động</span>
              <h2>Ba bước, không cần biết dựng video</h2>
              <p>
                Bạn lo phần sản phẩm và câu chuyện muốn kể. Phần quay, dựng và lồng tiếng để AI làm.
              </p>
            </div>
            <ol className="lp-steps">
              {steps.map(({ icon: Icon, title, text }, index) => (
                <li key={title}>
                  <span className="lp-step-number">{index + 1}</span>
                  <span className="lp-step-icon">
                    <Icon size={20} />
                  </span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="lp-section lp-section-muted" id="tinh-nang">
          <div className="lp-wrap">
            <div className="lp-section-head">
              <span className="lp-eyebrow">Tính năng</span>
              <h2>Mọi thứ một video bán hàng cần</h2>
              <p>Từ câu hook đầu tiên đến lời chốt đơn, mỗi cảnh đều phục vụ việc bán sản phẩm.</p>
            </div>
            <div className="lp-features">
              {features.map(({ icon: Icon, title, text }) => (
                <article key={title}>
                  <span className="lp-feature-icon">
                    <Icon size={20} />
                  </span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-wrap lp-split">
            <div className="lp-split-copy">
              <span className="lp-eyebrow">Tự động từ đầu đến cuối</span>
              <h2>Bạn làm 2 việc. Phần còn lại để VO3 lo.</h2>
              <div className="lp-you">
                <div>
                  <span>1</span>
                  <b>Duyệt kịch bản</b>
                </div>
                <div>
                  <span>2</span>
                  <b>Xác nhận tạo video</b>
                </div>
              </div>
              <p>
                Sau khi bạn xác nhận, hệ thống tự chạy hết các bước và không hỏi lại. Bạn chỉ quay
                lại để tải video.
              </p>
            </div>
            <div className="lp-auto">
              <header>
                <Layers size={16} /> VO3 Studio tự làm
              </header>
              <ul>
                {automation.map((item) => (
                  <li key={item}>
                    <CheckCircle2 size={16} />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="lp-section lp-section-muted" id="cau-hoi">
          <div className="lp-wrap lp-faq-wrap">
            <div className="lp-section-head">
              <span className="lp-eyebrow">Câu hỏi thường gặp</span>
              <h2>Trước khi bắt đầu</h2>
            </div>
            <div className="lp-faq">
              {questions.map(({ q, a }) => (
                <details key={q}>
                  <summary>{q}</summary>
                  <p>{a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final">
          <div className="lp-wrap lp-final-inner">
            <h2>Video bán hàng tiếp theo của bạn bắt đầu từ một tấm ảnh.</h2>
            <p>Đăng nhập bằng tài khoản được cấp và tạo video đầu tiên ngay hôm nay.</p>
            <Link className="flow-button primary lp-cta-main" href="/login">
              Đăng nhập <ArrowRight size={17} />
            </Link>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-inner">
          <Brand />
          <small>Tạo video quảng cáo có kịch bản từ ảnh sản phẩm.</small>
        </div>
      </footer>
    </div>
  );
}
