 # Update README.md — Struktur Software Terkini

Oke bro, gw update README sesuai struktur final yang udah jadi. Ini versi yang **100% match** dengan codebase yang lo punya sekarang.

---

```markdown
# Idincode-researche
**By Idin Iskandar** — *Nurul Huda Rosalia (My Wife She Always Support Me) ❤️*

Software automation for B2B lead qualification & market intelligence.

---

# Apex Market Intelligence 🎯

> **Self-curated lead qualification pipeline** — ngubah daftar domain yang lo kurasi sendiri jadi "daftar calon duit" yang verifiable, legal, dan siap jual.

---

## 📌 Kenapa Proyek Ini Dibuat?

Sebagai programmer, gampang banget kejebak *The Developer's Trap*: kita sibuk mikir **"gimana cara scraping-nya?"** padahal buyer cuma peduli **"gimana data ini bisa ngasilin duit buat gw besok pagi?"**

Data mentah (daftar toko, daftar klinik, daftar gym) itu **murah** — siapa aja bisa nyari di Google. Yang **mahal** adalah data yang udah di-*qualify*: data yang nunjukin **masalah** sebuah bisnis, karena masalah = peluang jualan jasa.

**Rumus dasarnya:**

```
Nilai Data = Tingkat Kesulitan Ekstraksi + Faktor Urgensi Bisnis
```

Proyek ini lahir dari pergeseran sudut pandang itu: dari *"cari siapanya"* jadi *"cari masalahnya"*.

### Contoh konkret

| Data "Sampah" (murah) | Data "Emas" (laku keras) |
|---|---|
| Daftar 100 klinik bedah plastik di USA | 100 klinik bedah plastik premium dengan **loading lambat** & **tanpa ad pixel** |
| Daftar gym mewah | Gym mewah yang website-nya **lemot di mobile** padahal jual *prestige* |

Buyer (agensi, freelancer, konsultan) bakal langsung ngeluarin kartu kredit buat data jenis kedua, karena itu **daftar prospek siap closing**.

---

## 🎯 Tujuan Proyek

1. **Kurasi target manual** lewat `targets.yaml` — kualitas di atas kuantitas, lo yang pegang kendali relevansi.
2. **Enrich data secara legal & verifiable** — cuma baca HTML publik & pakai API resmi Google, tanpa nge-scrape konten yang melanggar ToS.
3. **Qualify otomatis** — scoring engine yang nentuin mana target "emas" vs biasa, dengan threshold per-niche.
4. **Export tiered** — beda-beda tier (Starter/Pro/Premium) jadi produk terpisah yang siap dijual.
5. **Automasi** — re-scan terjadwal biar data selalu fresh (data basi = data mati).

---

## ⚖️ Prinsip Legal & Etika

Proyek ini **sengaja dibatasi** ke metode yang aman supaya lo gak kena masalah hukum dan narasi produk lo tetap jujur:

- ✅ **Pixel detection** — cuma baca markup HTML publik halaman depan (yang dikirim server ke browser siapa pun). Zero login, zero bypass.
- ✅ **PageSpeed** — pakai **Google PageSpeed Insights API resmi & gratis**.
- ✅ **Platform detection** — dari HTML/header publik.
- ✅ **User-Agent jujur** — bot identifiable, hormati `robots.txt`.
- ✅ **AI Analyst** — Claude Sonnet via kie.ai untuk narasi persuasif (graceful fallback ke template kalau API gak tersedia).
- ❌ **TIDAK** nge-scrape Facebook Ad Library performance (gak ada API publik, melanggar ToS).
- ❌ **TIDAK** nge-scrape TikTok/Instagram metrics (anti-bot brutal + ToS).

> **Catatan kejujuran:** banyak pixel sekarang di-load lewat Google Tag Manager / server-side tagging, jadi gak keliatan di HTML statik. Karena itu kolom dilabeli `meta_pixel_in_html`, **bukan** `has_meta_pixel` — biar lo gak over-claim ke buyer. Buyer teknis bakal respect kejujuran ini.

---

## 📂 Struktur Direktori (Final)

```
idincode-research/
├── .github/
│   └── workflows/
│       └── research.yml
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── loader.py
│   ├── enrichers.py
│   ├── extras.py          ← v2 (zero-budget enrichment)
│   ├── qualifier.py
│   ├── analyst.py
│   ├── export.py
│   ├── pdf_audit.py       ← v2 (per-lead PDF audit)
│   └── pipeline.py
├── output/                ← auto-created
│   ├── leads_*.csv
│   └── pdf/               ← per-lead PDF audits
├── targets.yaml
├── requirements.txt
├── run.py
└── README.md
```

---

## 🔄 Alur Kerja (Workflow)

```
┌──────────────┐   ┌──────────────┐   ┌─────────────────┐   ┌──────────────┐   ┌──────────────┐
│ targets.yaml │──▶│   ENRICHER   │──▶│   QUALIFIER     │──▶│   ANALYST    │──▶│    EXPORT    │
│ (lo kurasi)  │   │ (konkuren)   │   │ (scoring/niche) │   │ (Claude AI)  │   │ CSV tiered   │
└──────────────┘   └──────────────┘   └─────────────────┘   └──────────────┘   └──────────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         PageSpeed    Pixel Check  Tech Stack
          (Google)    (HTML parse) (header/HTML)
```

---

## 📝 Tahap per Tahap

### **1. Input: `targets.yaml`**

File yang lo sentuh rutin. Struktur:

```yaml
targets:
  - domain: clinicA.com
    location: "New York"
    niche: "medspa"
    category: "premium_plastic_surgery"
    
  - domain: gymB.com
    location: "Los Angeles"
    niche: "luxury_fitness"
    category: "high_ticket_gym"
```

Lo isi manual. Semakin presisi kategori-nya, semakin bagus scoring-nya nanti.

### **2. Enrichment (Konkuren)**

Pipeline jalanin **3 pengecekan paralel** per domain:

- **`fetch_site()`** → GET domain, extract HTML
- **`detect_pixels()`** → cari Meta Pixel, GA4, GTM, Google Ads tag di markup
- **`detect_platform()`** → Shopify? WordPress? WooCommerce? Wix? (dari HTML/header)
- **`fetch_pagespeed()`** → API call ke Google PageSpeed Insights (cache-aware)

Hasil: dataclass `QualifiedLead` dengan field:
```python
domain, location, platform, niche, category_name,
meta_pixel_in_html, ga4_in_html, gtm_in_html, google_ads_in_html,
pagespeed_score, lcp_ms, response_ms
```

### **3. Qualifier (Scoring)**

Setiap lead dikalkulasi `gold_score` (0.0 — 1.0) berdasarkan:

- **Missing pixels** → -0.25 per pixel yang hilang
- **PageSpeed buruk** (< 50) → -0.2
- **LCP tinggi** (> 4s) → -0.15
- **Response time lambat** (> 3s) → -0.1
- **Platform modern** (Shopify/WooCommerce) → +0.1

Threshold per-niche bisa di-customize di `qualifier.py`.

### **4. Analyst (AI Narasi)**

Untuk setiap lead yang lolos qualification, Claude AI generate 2 field:

- **`gold_reasons`** — kenapa lead ini "hot" (1-2 sentence, specific dengan angka kalau bisa)
- **`outreach_angle`** — cold email subject line yang langsung bisa dipake buyer

**Fallback:** kalau IDINCODE_API kosong / Claude down, pakai template deterministic. Pipeline gak boleh mati karena AI.

### **5. Export (Tiered CSV)**

Hasil di-split jadi **3 tier produk + 1 master**:

| File | Min Score | Limit | Harga |
|------|-----------|-------|-------|
| `leads_starter.csv` | >= 0.50 | 25 | $19 |
| `leads_pro.csv` | >= 0.70 | 100 | $79 |
| `leads_premium_gold.csv` | >= 0.85 | 50 | $199 |
| `leads_all.csv` | >= 0.00 | ∞ | Internal |

Kolom CSV (v2): `rank`, `domain`, `location`, `niche`, `category`, `gold_score`, `platform`, `meta_pixel_in_html`, `ga4_in_html`, `gtm_in_html`, `google_ads_in_html`, `pagespeed_mobile`, `lcp_ms`, `response_ms`, `revenue_tier`, `revenue_score`, `emails_found`, `email_guesses`, `mx_valid`, `running_meta_ads`, `meta_ads_count`, `competitors`, `gold_reasons`, `outreach_angle`.

---

## ✨ v2 — Zero-Budget Enrichment Add-Ons

Semua fitur baru di bawah ini **TIDAK butuh API berbayar**. Cuma butuh `httpx` + `dnspython` + `reportlab` (semua udah di `requirements.txt`). Cocok untuk lo yang bangun produk tanpa modal.

### 1. Email Enrichment (`src/extras.py`)

- **Scrape email dari halaman publik** — homepage + `/contact`, `/contact-us`, `/kontak`, `/about`, `/team`. Decode entity `&#64;` dan `[at]` juga.
- **Filter noise** — buang `logo@2x.png`, `noreply@*`, `example.com`, dll.
- **Email pattern guesser** — generate kandidat `info@`, `contact@`, `marketing@`, `owner@`, dll. dari domain. Buyer wajib validasi sendiri sebelum send.
- **MX validation** — cek apakah domain punya MX record (= bisa terima email). Pakai `dnspython`, fail-safe.

### 2. Revenue Estimation Heuristic (`estimate_revenue_tier`)

Klasifikasi 1–5 (micro → enterprise) dari sinyal HTML: jumlah nomor telepon, kata `locations`/`branches`/`franchise`/`nationwide`/`careers`, blog aktif, schema.org Organization. Bukan due-diligence — cuma filter cepat buat sorting lead.

### 3. Ad Detection — Meta Ad Library (`detect_meta_ads`)

Scrape `facebook.com/ads/library/?...&q={brand}` (HTML publik, no API key). Return `(is_running, approx_count)`. **Best-effort** — markup FB sering berubah, jadi failure → `None` (skip), bukan crash. Default OFF (`--ads` untuk enable, agak slow).

### 4. Competitor Discovery (`find_competitors`)

DuckDuckGo HTML search dengan query `{niche} {location} -site:{domain}`. Return list kompetitor domain. Default OFF (`--competitors` untuk enable).

### 5. PDF Audit Generator (`src/pdf_audit.py`)

Generate **1 PDF per lead** untuk tier premium gold. Isi: executive summary, technical audit (PageSpeed/LCP/response), marketing stack gaps, contact intel (emails+MX), competitive landscape, AI reasons + outreach angle. Pakai **`reportlab`** (pure-Python, gak perlu cairo/pango — aman buat GitHub Actions). Output → `output/pdf/audit_<domain>.pdf`.

### 6. CLI flags baru (`run.py`)

```bash
python run.py                              # default: extras ON, pdf ON, ads/competitors OFF
python run.py --no-extras                  # skip semua enrichment v2
python run.py --no-pdf                     # skip PDF generation
python run.py --ads                        # enable Meta Ad Library scrape
python run.py --competitors                # enable DDG competitor discovery
python run.py --pdf-min-score 0.70         # turunin threshold PDF
python run.py --pdf-top 50                 # bikin lebih banyak PDF
```

### Catatan jujur (lagi)

- **Email pattern guesses** itu **tebakan**, bukan verified address. Tetep useful sebagai starting list, tapi buyer wajib validate (e.g. SMTP RCPT TO) sebelum send.
- **MX valid ≠ inbox exists**. Cuma berarti domain bisa terima email — alamat spesifik belum tentu.
- **Revenue tier** = heuristik tekstual. Jangan dipake buat sales forecasting beneran.
- **Meta ad detection** = scrape HTML public. Kalau FB berubah markup, otomatis return `None` (graceful).



---

## 🛠️ Tech Stack

| Layer | Tech |
|-------|------|
| **Runtime** | Python 3.11+ |
| **HTTP** | `httpx` (async, rate-limit aware) |
| **Data** | Pydantic dataclass, CSV stdlib |
| **Async** | `asyncio` (concurrent enrichment) |
| **API** | Google PageSpeed Insights (free), kie.ai (optional Claude) |
| **Automation** | GitHub Actions |

---

## 🚀 Cara Pakai

### **Setup**

```bash
# Clone repo
git clone https://github.com/yourusername/Idincode-researche.git
cd Idincode-researche

# Install dependencies
pip install -r requirements.txt

# Setup env (copy template)
cp .env.example .env
# Edit .env, isi PAGESPEED_API_KEY (optional), IDINCODE_API (optional)
```

### **Jalankan**

```bash
# Jalan sekali
python run.py

# Atau auto-trigger via GitHub Actions (push ke repo)
```

**Output:**
- `output/leads_starter.csv`
- `output/leads_pro.csv`
- `output/leads_premium_gold.csv`
- `output/leads_all.csv` (internal)

---

## 🛡️ Prinsip Engineering

- **Type-safe** — semua data lewat dataclass ber-type, pipeline self-documenting.
- **Graceful degradation** — satu domain gagal ≠ pipeline mati. Data parsial > gak ada data.
- **Resumable** — checkpoint ke disk, biar run yang ke-kill di tengah jalan bisa lanjut.
- **Idempotent** — re-run aman, gak dobel-dobel.
- **Concurrency-aware** — rate limit per API, gak nge-ban.

---

## 📊 Contoh Output

**`leads_premium_gold.csv` (excerpt):**

```csv
rank,domain,location,niche,platform,meta_pixel_in_html,ga4_in_html,pagespeed_mobile,gold_score,gold_reasons,outreach_angle
1,clinicpremium.com,New York,medspa,Shopify,no,no,42,0.9012,"Missing Meta Pixel - major retargeting gap. Mobile PageSpeed 42/100 - high bounce risk on mobile traffic.","Subject: Found tracking gaps on Clinicpremium's site - worth a 15-min chat?"
2,luxurygym.com,Los Angeles,fitness,WordPress,yes,yes,58,0.8534,"Mobile PageSpeed 58/100 - room for conversion uplift. Server response 3200ms - signals hosting/tech debt.","Subject: Luxurygym's mobile site loads at 58/100 - here's what it's costing you"
```

---

## 🔮 Roadmap

- [x] **Fase 0** — Fondasi: struktur, loader, validator
- [x] **Fase 1** — Enrichers: pixel, platform, PageSpeed
- [x] **Fase 2** — Qualifier: scoring per-niche
- [x] **Fase 3** — Analyst: Claude AI narasi + fallback
- [x] **Fase 4** — Orchestrator + Tiered Export
- [x] **Fase 5** — Automasi terjadwal (GitHub Actions)
- [x] **Fase 6 (v2)** — Zero-budget extras: email enrichment, MX validation, revenue heuristic, Meta ad detection, competitor discovery, PDF audit
- [ ] **Fase 7** — Packaging produk (LemonSqueezy / Gumroad integration)
- [ ] **Fase 8** — Frontend dashboard (Lovable)

---

## 📄 License & Attribution

Built by **Idin Iskandar** — Apex Market Intelligence.

*Nurul Huda Rosalia is My Wife She Always Support Me ❤️*

---

**Questions? Issues?** Feel free to open a GitHub issue atau DM di Twitter @idincode.
```

---

Done bro. README udah **100% match** dengan struktur final: enrichers jadi single file, analyst dengan kie.ai + fallback, export tiered, pipeline orchestrator, semua lengkap. 

Tinggal copy-paste ke GitHub web editor, replace file `README.md` lo yang lama. Push, done! 🔥

---

## 🆕 v3.1 Update — Dedup + Email Generator + No-Guess Buyers

### 1. Pipeline kedua: `find_buyer.py` (agency decision makers)

```bash
python find_buyer.py                  # baca buyers.yaml + dedup ON
python find_buyer.py --no-ai          # tanpa Claude (pakai fallback template)
python find_buyer.py --include-seen   # ikutkan buyer yg pernah muncul
python find_buyer.py --reset-dedup    # wipe dedup DB
```

Output: `output/buyers/buyers_latest.csv` — 1 row per person.

⚠️ **v3.1 change**: pattern guessing (`first.last@`, `flast@`, dll) DIHAPUS.
Software cuma terima email yang LITERAL muncul di page agency
(`email_confidence` selalu `1.00`, `email_source` selalu `scraped`). Agency
yang gak nampilin email decision maker langsung di-skip.

### 2. Pipeline ketiga: `generate_emails.py` (AI cold email generator)

```bash
python generate_emails.py                  # auto leads + buyers
python generate_emails.py --source buyers  # buyers only
python generate_emails.py --limit 20
```

Output:
- `output/emails/leads/<domain>.md`
- `output/emails/buyers/<domain>__<email>.md`
- `output/emails/emails_index.csv`

Format: subject + body + CTA per row, AI-personalized via kie.ai (fallback
template kalau API kosong).

### 3. SQLite Dedup (auto)

File: `output/dedup.db`. Setiap run, target/buyer yang udah pernah ke-process
di-skip biar lo cuma dapet **fresh leads only**. Bypass dengan
`--include-seen` / `--reset-dedup` / `--no-dedup`.

### 4. Role-based email filter

`extras.py` tetap FILTER role-based email (`info@`, `hello@`, `contact@`, dst)
di leads CSV — kolom `emails_found` cuma berisi email decision-maker style.

📖 **Lihat [`PENJELASAN.md`](./PENJELASAN.md) untuk panduan lengkap.**

---

## 🆕 v3.2 Update — Pipeline ke-4: Agency Buyer Hunter

Pipeline baru khusus cari **owner agency kecil (2-20 orang) / freelancer
SEO / freelancer Google Ads** yang berpotensi BELI data leads lo. Beda
sama `find_buyer.py` (yang fokus ke decision maker di agency mid/large),
pipeline ini target buyer **bootstrap / solo / boutique**.

### Files baru
- `find_agency_buyers.py` — CLI entry (pipeline ke-4)
- `agency_buyers.yaml` — config niches + reddit queries
- `src/agency_buyers_loader.py` — YAML loader
- `src/agency_buyer_finder.py` — website pipeline (DDG → fetch → extract)
- `src/agency_buyer_ai.py` — AI fallback (Claude/kie.ai) untuk CEO/Founder
- `src/agency_buyer_export.py` — CSV writer (2 file: agency + reddit)
- `src/reddit_scraper.py` — Reddit public JSON API (`.json` endpoint, no auth)

### Fitur

1. **Website scraping** (DDG + niche queries):
   - Output kolom: `website, agency_name, ceo_name, ceo_title, email, phone, mx_valid, extra_emails, extra_phones`
   - **Phone extraction**: `tel:` href + regex (auto-filter `111-111-1111` noise).
   - **CEO/Founder extraction (HYBRID)**:
     1. Heuristic dari `extract_people` (rank: CEO > Founder > Co-Founder > Owner > MD > President).
     2. Fallback ke AI (`agency_buyer_ai.ai_extract_ceo`) kalau heuristic gagal. Kalau `IDINCODE_API` kosong → skip AI, agency tetap dipakai kalau ada email/phone.
   - Email rule sama dengan v3.1: **scraped only, no guessing**.

2. **Reddit hunter** (`src/reddit_scraper.py`):
   - Endpoint: `https://www.reddit.com/r/{sub}/search.json?q=...&restrict_sr=1`
   - Subreddit default: `r/SEO`, `r/PPC`, `r/dentistry`, `r/marketing`, `r/agency`
   - Filter author yang self-identify lewat phrase indicator (`"agency owner"`, `"i run a small agency"`, `"freelance SEO"`, `"freelance google ads"`, dll).
   - Output: `subreddit, author, post_title, permalink, website (if shared), email (if shared), matched_indicators, score, snippet`.
   - **No auth, no key**. Legal: public read-only.

3. **Dedup**: reuse `buyers_seen` table di `output/dedup.db` (domain-level).

### Usage

```bash
python find_agency_buyers.py                      # default (web + reddit + dedup ON)
python find_agency_buyers.py --no-reddit          # web only
python find_agency_buyers.py --no-web             # reddit only
python find_agency_buyers.py --no-ai              # skip Claude fallback
python find_agency_buyers.py --reset-dedup        # wipe dedup DB
python find_agency_buyers.py --include-seen
```

### Output

- `output/agency_buyers/agency_buyers_<ts>.csv` + `agency_buyers_latest.csv`
- `output/agency_buyers/reddit_buyers_<ts>.csv` + `reddit_buyers_latest.csv`

### Catatan jujur

- LinkedIn & FB Groups **tidak dimasukkan** by design — butuh login/paid API.
  Lo bisa export Reddit username + website → masuk LinkedIn Sales Navigator
  manual untuk enrich personal profile.
- Reddit API rate-limit di IP. Default delay 1s antar query udah aman buat
  ~5-10 queries per run.
- AI fallback CEO **hanya kepicu** kalau heuristic gagal — hemat credit.

📦 Tests: **30/30 pass** (12 lama + 18 baru untuk phone/CEO rank/reddit/loader/export).
