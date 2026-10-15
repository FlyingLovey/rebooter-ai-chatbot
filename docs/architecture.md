# แผนภาพระบบ Rebooter AI

แผนภาพทั้งสองตรงกับโค้ดใน repo นี้ (GitHub แสดงผล Mermaid ได้ทันที / ส่งออกเป็นรูปได้ที่ https://mermaid.live)

## 1. แผนภาพสถาปัตยกรรม (Architecture)

```mermaid
flowchart LR
    U["ลูกค้า<br/>Web Browser"]

    subgraph BE["Backend · FastAPI :8000 (backend/)"]
        UI["Web UI<br/>GET /"]
        API["Chat API<br/>POST /api/v1/chat<br/>POST /api/v1/chat/reset"]
        HEALTH["GET /health<br/>GET /api/v1/kb/search"]
        PIPE["Pipeline<br/>services/pipeline.py"]
        VIS["Vision<br/>services/vision.py"]
        RAG["RAG Retriever<br/>services/rag.py"]
        SES[("Session Store<br/>ประวัติแชท in-memory")]
    end

    subgraph GD["Guard Service · FastAPI :8001 (guard/)"]
        CHECK["POST /check"]
        L1["ชั้น 1 Blocklist"]
        L2["ชั้น 2 กฎของร้าน<br/>BIZ_*"]
        L3["ชั้น 3 Llama Guard 4"]
    end

    subgraph DATA["ข้อมูล"]
        KB["knowledge/<br/>Rebooter.md"]
        VDB[("ChromaDB<br/>Vector DB")]
        EMB["Embedding<br/>multilingual-e5-small"]
    end

    subgraph OR["OpenRouter (Cloud)"]
        LLM["Chat LLM<br/>gpt-4o-mini"]
        VLM["Vision LLM<br/>gpt-4o-mini"]
        LG["llama-guard-4-12b"]
    end

    U -- "HTTP" --> UI
    U -- "ข้อความ / ภาพ (multipart)" --> API
    API --> PIPE
    PIPE <--> SES
    PIPE -- "ตรวจ input / output" --> CHECK
    CHECK --> L1 --> L2 --> L3
    L2 -. "อ่านราคา/เบอร์ร้าน" .-> KB
    L3 --> LG
    PIPE --> VIS --> VLM
    PIPE --> RAG
    RAG --> EMB
    RAG <--> VDB
    KB -- "ตัด chunk ตอนเริ่มระบบ" --> VDB
    PIPE -- "prompt + context" --> LLM
```

| ส่วนประกอบ | หน้าที่ | ไฟล์ |
| :--- | :--- | :--- |
| Web UI | หน้าแชท แสดงสถานะกำลังประมวลผล ข้อผิดพลาด แนบภาพ ปุ่มคำถามด่วน | `backend/app/static/` |
| Chat API | รับข้อความ/ภาพ ตรวจรูปแบบไฟล์และขนาด จัดการ session cookie | `backend/app/routers/chat.py` |
| Pipeline | ควบคุมลำดับ Guard → Vision → RAG → LLM → Guard และจับเวลาแต่ละขั้น | `backend/app/services/pipeline.py` |
| Vision | ส่งภาพให้ Vision LLM สรุปเป็น JSON (อุปกรณ์ ข้อความ Error อาการ) | `backend/app/services/vision.py` |
| RAG | ตัดคลังความรู้ตามหัวข้อ `##` → embedding → ChromaDB → ค้น top-k | `backend/app/services/rag.py` |
| Guard Service | ตรวจข้อความ 3 ชั้น ตอบ safe/unsafe + ข้อความปฏิเสธภาษาไทย (fail-closed) | `guard/app/` |
| Knowledge Base | ข้อมูลร้าน บริการ 20 รายการ นโยบาย 8 ข้อ ตาราง Error→บริการ | `knowledge/Rebooter.md` |

### Endpoint ทั้งหมด

| Service | Method | Path | หน้าที่ |
| :--- | :--- | :--- | :--- |
| Backend | GET | `/` | หน้าแรกของร้าน + ปุ่มแชทลอย |
| Backend | GET | `/chat` | หน้าแชท |
| Backend | GET | `/health` | สถานะระบบ จำนวน chunk และสถานะ Guard |
| Backend | POST | `/api/v1/chat` | ส่งข้อความ (`message`) และ/หรือภาพ (`image`) แบบ multipart |
| Backend | POST | `/api/v1/chat/reset` | ล้างประวัติแชทของ session |
| Backend | GET | `/api/v1/kb/search?q=` | ดูผลค้นคืนของ RAG (ใช้ทดสอบ/สาธิต) |
| Guard | POST | `/check` | ตรวจข้อความ `{message, direction: input/output}` |
| Guard | GET | `/health` | สถานะ Guard |

## 2. แผนภาพการไหลของข้อมูล 1 ข้อความ (Data Flow)

ตัวอย่าง: ลูกค้าส่งภาพจอฟ้าพร้อมข้อความ "คอมขึ้นแบบนี้ครับ ซ่อมได้ไหม"

```mermaid
sequenceDiagram
    autonumber
    actor C as ลูกค้า
    participant UI as Web UI
    participant API as Backend /api/v1/chat
    participant G as Guard /check
    participant V as Vision LLM
    participant R as RAG (ChromaDB)
    participant L as Chat LLM

    C->>UI: พิมพ์ข้อความ + แนบภาพ
    UI->>API: POST multipart (message, image)
    Note over UI: แสดง "กำลังตรวจข้อความ… / วิเคราะห์ภาพ… / ค้นข้อมูล…"
    API->>API: ตรวจไฟล์ (.jpg .png .webp ≤ 5 MB) และความยาวข้อความ
    API->>G: ตรวจข้อความลูกค้า (direction=input)
    alt ไม่ปลอดภัย (Blocklist / BIZ_* / Llama Guard)
        G-->>API: unsafe + ข้อความปฏิเสธ
        API-->>UI: ข้อความปฏิเสธ (blocked_by=guard_input) — ไม่เรียก LLM
    else ปลอดภัย
        G-->>API: safe
        API->>V: ภาพ (base64) + vision prompt
        V-->>API: JSON {device, visible_text: "CRITICAL_PROCESS_DIED", symptoms: ["จอฟ้า (BSOD)"]}
        API->>R: คำค้น = ข้อความ + ผลวิเคราะห์ภาพ
        R-->>API: top-4 chunks เช่น S03, ERROR_MAP, S01
        API->>L: system prompt + context คลังความรู้ + ผลวิเคราะห์ภาพ + ประวัติแชท
        L-->>API: คำตอบ เช่น "ซ่อมจอฟ้า เริ่มต้น 800 บาท ไม่รวมค่าอะไหล่…"
        API->>G: ตรวจคำตอบบอท (direction=output)
        alt ไม่ปลอดภัย (เช่น ราคาไม่มีในคลังความรู้)
            G-->>API: unsafe + ข้อความปฏิเสธ
            API-->>UI: ข้อความปฏิเสธแทนคำตอบเดิม (blocked_by=guard_output)
        else ปลอดภัย
            G-->>API: safe
            API->>API: บันทึกประวัติแชท
            API-->>UI: {reply, sources, vision, timings_ms}
        end
    end
    UI-->>C: แสดงคำตอบ + เวลาตอบ + แหล่งข้อมูล
    Note over API,G: Guard เรียกไม่ได้ → HTTP 503 (fail-closed) ไม่ส่งข้อความที่ยังไม่ผ่านการตรวจ
```
